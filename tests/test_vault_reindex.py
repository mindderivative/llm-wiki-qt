"""Phase 18: vault self-maintenance -- Related-block rendering, index.md
regeneration, log.md appends, and the retroactive `reindex_vault()` fix.
"""

from pathlib import Path

import frontmatter
import pytest

from llm_wiki.models import NoteFrontmatter, NoteType
from llm_wiki.related_links import RELATED_MARKER, render_related_block, strip_related_block
from llm_wiki.storage import connect, upsert_note_from_file
from llm_wiki.vault import (
    append_log_entry,
    backfill_related_blocks,
    create_vault,
    rebuild_index,
    reindex_vault,
)


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


def _write_note(
    vault_root: Path,
    conn,
    *,
    subdir: str,
    slug: str,
    title: str,
    note_type: str = "entity",
    tags: list[str] | None = None,
    sources: list[str] | None = None,
    body: str = "Some content.",
) -> Path:
    note_dir = vault_root / "wiki" / subdir
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{slug}.md"
    post = frontmatter.Post(
        body,
        title=title,
        slug=slug,
        type=note_type,
        tags=tags or [],
        sources=sources or [],
    )
    note_path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    upsert_note_from_file(conn, vault_root, note_path)
    return note_path


# --- render_related_block / strip_related_block ------------------------


def test_render_related_block_includes_index_and_sources() -> None:
    fm = NoteFrontmatter(title="A", slug="a", type=NoteType.ENTITY, sources=["src-1", "src-2"])

    block = render_related_block(fm)

    assert "[[index]]" in block
    assert "[[src-1]]" in block
    assert "[[src-2]]" in block


def test_render_related_block_excludes_self_reference() -> None:
    fm = NoteFrontmatter(title="A", slug="a", type=NoteType.SOURCE, sources=["a"])

    block = render_related_block(fm)

    assert "[[a]]" not in block  # self-link filtered out
    assert "[[index]]" in block


def test_render_related_block_includes_extra_targets_without_duplicates() -> None:
    fm = NoteFrontmatter(title="A", slug="a", type=NoteType.SOURCE, sources=["a"])

    block = render_related_block(fm, extra_targets=["entity-1", "entity-2", "a", "entity-1"])

    assert block.count("[[entity-1]]") == 1
    assert "[[entity-2]]" in block
    assert "[[a]]" not in block


def test_strip_related_block_removes_a_trailing_block() -> None:
    fm = NoteFrontmatter(title="A", slug="a", type=NoteType.ENTITY, sources=["src"])
    content = "Real body content." + render_related_block(fm)

    stripped = strip_related_block(content)

    assert stripped == "Real body content."


def test_strip_related_block_is_a_no_op_without_a_marker() -> None:
    assert strip_related_block("Just a plain note body.") == "Just a plain note body."


def test_strip_related_block_uses_rfind_not_find() -> None:
    """If the marker text ever appears mid-body (echoed in prose, or in a
    quoted source document) rather than as the true trailing block, only
    the real trailing block must be stripped -- not everything after the
    first occurrence.
    """
    content = (
        f"This note mentions the {RELATED_MARKER} convention in passing.\n\n"
        "More real content follows this mention."
        f"\n\n{RELATED_MARKER}\n## Related\n\n- [[index]]\n"
    )

    stripped = strip_related_block(content)

    assert "More real content follows this mention." in stripped
    assert "## Related" not in stripped


# --- rebuild_index -------------------------------------------------------


def test_rebuild_index_lists_notes_under_the_right_sections(vault_root: Path, conn) -> None:
    _write_note(
        vault_root, conn, subdir="sources", slug="src-1", title="Source One", note_type="source"
    )
    _write_note(
        vault_root, conn, subdir="entities", slug="ent-1", title="Entity One", note_type="entity"
    )
    _write_note(
        vault_root,
        conn,
        subdir="concepts",
        slug="con-1",
        title="Concept One",
        note_type="concept",
    )

    rebuild_index(conn, vault_root)

    text = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    sources_section = text.split("## Sources")[1].split("## Entities")[0]
    entities_section = text.split("## Entities")[1].split("## Concepts")[0]
    concepts_section = text.split("## Concepts")[1].split("## Synthesis")[0]

    assert "[[src-1]] — Source One" in sources_section
    assert "[[ent-1]] — Entity One" in entities_section
    assert "[[con-1]] — Concept One" in concepts_section


def test_rebuild_index_preserves_the_header(vault_root: Path, conn) -> None:
    before = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    header = before.split("## Sources")[0]

    rebuild_index(conn, vault_root)

    after = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert after.startswith(header.rstrip())


def test_rebuild_index_is_idempotent_and_drops_stale_entries(vault_root: Path, conn) -> None:
    note_path = _write_note(vault_root, conn, subdir="entities", slug="ent-1", title="Entity One")
    rebuild_index(conn, vault_root)
    assert "[[ent-1]]" in (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")

    # Note deleted from disk and DB -- a renamed/deleted note must not
    # linger in a future regeneration (the failure mode incremental
    # append would have).
    note_path.unlink()
    conn.execute("DELETE FROM notes WHERE slug = 'ent-1'")
    conn.commit()

    rebuild_index(conn, vault_root)

    assert "[[ent-1]]" not in (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")


def test_rebuild_index_handles_no_notes(vault_root: Path, conn) -> None:
    rebuild_index(conn, vault_root)

    text = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "*(none yet)*" in text


# --- append_log_entry ------------------------------------------------------


def test_append_log_entry_adds_a_line_and_preserves_prior_ones(vault_root: Path) -> None:
    before = (vault_root / "wiki" / "log.md").read_text(encoding="utf-8")

    append_log_entry(vault_root, "Something happened")

    after = (vault_root / "wiki" / "log.md").read_text(encoding="utf-8")
    assert after.startswith(before)
    assert "Something happened" in after
    new_line = after[len(before) :]
    assert new_line.startswith("- [")  # "- [YYYY-MM-DD HH:MM:SS] message"


# --- backfill_related_blocks -----------------------------------------------


def test_backfill_related_blocks_adds_a_block_to_notes_missing_one(vault_root: Path, conn) -> None:
    note_path = _write_note(
        vault_root, conn, subdir="entities", slug="ent-1", title="Entity One", sources=["src-1"]
    )

    count = backfill_related_blocks(conn, vault_root)

    assert count == 1
    text = note_path.read_text(encoding="utf-8")
    assert "[[index]]" in text
    assert "[[src-1]]" in text


def test_backfill_related_blocks_is_idempotent(vault_root: Path, conn) -> None:
    note_path = _write_note(
        vault_root, conn, subdir="entities", slug="ent-1", title="Entity One", sources=["src-1"]
    )

    backfill_related_blocks(conn, vault_root)
    first_pass = note_path.read_text(encoding="utf-8")
    backfill_related_blocks(conn, vault_root)
    second_pass = note_path.read_text(encoding="utf-8")

    assert first_pass == second_pass
    assert first_pass.count("## Related") == 1


# --- reindex_vault (end-to-end retroactive fix) -----------------------------


def test_reindex_vault_fixes_a_vault_compiled_before_phase_18(vault_root: Path, conn) -> None:
    """Mimics the reported broken state: notes exist with `sources` set,
    but no Related block and an empty `links` table/stale index.md.
    """
    _write_note(
        vault_root,
        conn,
        subdir="sources",
        slug="doc-1",
        title="Doc One",
        note_type="source",
        sources=["doc-1"],
        body="A plain summary, no related links.",
    )
    _write_note(
        vault_root,
        conn,
        subdir="entities",
        slug="ent-1",
        title="Entity One",
        sources=["doc-1"],
        body="A plain entity note, no related links.",
    )
    assert conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0

    reindex_vault(conn, vault_root)

    entity_text = (vault_root / "wiki" / "entities" / "ent-1.md").read_text(encoding="utf-8")
    assert "[[index]]" in entity_text
    assert "[[doc-1]]" in entity_text

    links = {
        (r["source_slug"], r["target_slug"])
        for r in conn.execute("SELECT source_slug, target_slug FROM links").fetchall()
    }
    assert ("ent-1", "index") in links
    assert ("ent-1", "doc-1") in links

    index_text = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "[[doc-1]]" in index_text
    assert "[[ent-1]]" in index_text

    log_text = (vault_root / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "Reindexed vault" in log_text
