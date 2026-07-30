"""Phase 21: `get_vault_stats()` -- plain aggregation over `notes`/`queue`/
`links` for the Dashboard panel, distinct from `lint.run_lint()`'s quality
score.
"""

from pathlib import Path

import pytest

from llm_wiki.graph import sync_links
from llm_wiki.storage import connect, get_vault_stats, upsert_note_from_file
from llm_wiki.vault import create_vault


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(
        root, "Test Vault", "A test knowledge base.", recent_vaults_path=tmp_path / "r.json"
    )
    return root


@pytest.fixture
def conn(vault_root: Path):
    connection = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    yield connection
    connection.close()


def _add_note(conn, *, slug: str, note_type: str) -> None:
    conn.execute(
        "INSERT INTO notes (path, slug, type, title, tags, sources, content_hash, updated_at) "
        "VALUES (:path, :slug, :type, :title, '[]', '[]', 'hash', '2026-01-01T00:00:00Z')",
        {"path": f"wiki/{note_type}s/{slug}.md", "slug": slug, "type": note_type, "title": slug},
    )
    conn.commit()


def _add_queue_item(conn, *, title: str, status: str) -> None:
    conn.execute(
        "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
        "VALUES (:title, :raw_path, :status, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        {"title": title, "raw_path": f"raw/{title}.txt", "status": status},
    )
    conn.commit()


def test_empty_vault_reports_all_zeros(conn, vault_root: Path) -> None:
    stats = get_vault_stats(conn, vault_root)

    assert stats.concepts == 0
    assert stats.entities == 0
    assert stats.sources == 0
    assert stats.synthesis == 0
    assert stats.total_ingested == 0
    assert stats.failures == 0
    assert stats.total_wikilinks == 0
    assert stats.total_backlinks == 0


def test_counts_notes_by_type(conn, vault_root: Path) -> None:
    _add_note(conn, slug="c1", note_type="concept")
    _add_note(conn, slug="c2", note_type="concept")
    _add_note(conn, slug="e1", note_type="entity")
    _add_note(conn, slug="s1", note_type="source")
    _add_note(conn, slug="y1", note_type="synthesis")

    stats = get_vault_stats(conn, vault_root)

    assert stats.concepts == 2
    assert stats.entities == 1
    assert stats.sources == 1
    assert stats.synthesis == 1


def test_completed_and_error_queue_items_land_in_separate_counts(
    conn, vault_root: Path
) -> None:
    _add_queue_item(conn, title="doc-a", status="completed")
    _add_queue_item(conn, title="doc-b", status="completed")
    _add_queue_item(conn, title="doc-c", status="error")
    _add_queue_item(conn, title="doc-d", status="queued")  # neither ingested nor failed

    stats = get_vault_stats(conn, vault_root)

    assert stats.total_ingested == 2
    assert stats.failures == 1


def test_wikilink_occurrences_can_exceed_deduped_backlink_edges(
    conn, vault_root: Path
) -> None:
    """The concrete case the distinction exists for: a note referencing
    the same target twice in its prose is two raw occurrences on disk,
    but `sync_links()` dedupes it down to a single edge.
    """
    note_dir = vault_root / "wiki" / "concepts"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / "a.md"
    note_path.write_text(
        "---\ntitle: A\nslug: a\ntype: concept\ntags: []\nsources: []\n---\n\n"
        "See [[index]] here and [[index]] again.\n",
        encoding="utf-8",
    )
    upsert_note_from_file(conn, vault_root, note_path)
    sync_links(conn, vault_root)

    stats = get_vault_stats(conn, vault_root)

    assert stats.total_wikilinks == 2
    assert stats.total_backlinks == 1
