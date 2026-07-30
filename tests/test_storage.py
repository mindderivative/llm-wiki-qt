"""Phase 2: sqlite-vec loads and rebuild_from_vault() reconstructs `notes`."""

import json
import sqlite3
from pathlib import Path

from llm_wiki.models import Chunk
from llm_wiki.storage import (
    SCHEMA_VERSION,
    connect,
    insert_chunk,
    rebuild_from_vault,
    upsert_note_from_file,
)
from llm_wiki.vault import create_vault

_ENTITY_NOTE = """\
---
title: Ada Lovelace
slug: ada-lovelace
type: entity
tags: [person, mathematician]
sources: [1843-notes-on-the-engine]
---

Ada Lovelace worked on the Analytical Engine.
"""

_SOURCE_NOTE = """\
---
title: Notes on the Engine
slug: 1843-notes-on-the-engine
type: source
tags: [primary-source]
sources: []
---

Original 1843 notes.
"""


def _build_fixture_vault(vault_root: Path) -> None:
    (vault_root / "wiki" / "entities").mkdir(parents=True)
    (vault_root / "wiki" / "sources").mkdir(parents=True)
    (vault_root / "wiki" / ".system" / "prompts").mkdir(parents=True)

    (vault_root / "wiki" / "entities" / "ada-lovelace.md").write_text(
        _ENTITY_NOTE, encoding="utf-8"
    )
    (vault_root / "wiki" / "sources" / "1843-notes-on-the-engine.md").write_text(
        _SOURCE_NOTE, encoding="utf-8"
    )
    # Should never be picked up as a note.
    (vault_root / "wiki" / ".system" / "prompts" / "atomizer.md").write_text(
        "not a real note", encoding="utf-8"
    )


def test_connect_ensures_schema_and_loads_sqlite_vec(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite3")

    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }
    assert {"queue", "notes", "chunks", "vec_chunks", "links", "lint_findings"} <= tables

    version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version == SCHEMA_VERSION

    assert conn.execute("SELECT vec_version()").fetchone()[0].startswith("v")


def test_rebuild_from_vault_populates_notes_table(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    _build_fixture_vault(vault_root)
    db_path = tmp_path / ".llm-wiki" / "db.sqlite3"

    conn = rebuild_from_vault(vault_root, db_path)

    rows = conn.execute("SELECT * FROM notes ORDER BY slug").fetchall()
    assert len(rows) == 2

    notes_source, ada = rows
    assert ada["slug"] == "ada-lovelace"
    assert ada["type"] == "entity"
    assert ada["title"] == "Ada Lovelace"
    assert json.loads(ada["tags"]) == ["person", "mathematician"]
    assert json.loads(ada["sources"]) == ["1843-notes-on-the-engine"]
    assert ada["path"] == "wiki/entities/ada-lovelace.md"
    assert len(ada["content_hash"]) == 64  # sha256 hex digest

    assert notes_source["slug"] == "1843-notes-on-the-engine"
    assert notes_source["type"] == "source"


def test_rebuild_from_vault_never_ingests_index_or_log_as_notes(tmp_path: Path) -> None:
    """Regression (Phase 18): wiki/index.md and wiki/log.md are reserved,
    never-tracked system files -- a full rebuild must not silently ingest
    them as bogus "source" notes (they have no frontmatter, so they'd
    default to type=source, slug=index/log).
    """
    vault_root = tmp_path / "vault"
    create_vault(vault_root, "Test Vault", "desc", recent_vaults_path=tmp_path / "recent.json")
    db_path = tmp_path / ".llm-wiki" / "db.sqlite3"

    conn = rebuild_from_vault(vault_root, db_path)

    slugs = {row[0] for row in conn.execute("SELECT slug FROM notes").fetchall()}
    assert "index" not in slugs
    assert "log" not in slugs


def test_rebuild_from_vault_is_idempotent_and_wipes_stale_rows(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    _build_fixture_vault(vault_root)
    db_path = tmp_path / ".llm-wiki" / "db.sqlite3"

    rebuild_from_vault(vault_root, db_path)

    # Remove a note from the vault, then rebuild again.
    (vault_root / "wiki" / "sources" / "1843-notes-on-the-engine.md").unlink()
    conn = rebuild_from_vault(vault_root, db_path)

    slugs = {row[0] for row in conn.execute("SELECT slug FROM notes").fetchall()}
    assert slugs == {"ada-lovelace"}


def test_rebuild_from_vault_with_no_wiki_dir_produces_empty_notes(tmp_path: Path) -> None:
    vault_root = tmp_path / "empty-vault"
    vault_root.mkdir()
    db_path = tmp_path / ".llm-wiki" / "db.sqlite3"

    conn = rebuild_from_vault(vault_root, db_path)

    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0


def test_connect_reopening_existing_db_does_not_duplicate_schema_version(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db.sqlite3"
    connect(db_path).close()
    conn = connect(db_path)

    count = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]
    assert count == 1


def test_db_path_accepts_string(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "db.sqlite3"))
    assert isinstance(conn, sqlite3.Connection)


def test_upsert_note_from_file_inserts_then_updates(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    (vault_root / "wiki" / "entities").mkdir(parents=True)
    note_path = vault_root / "wiki" / "entities" / "ada-lovelace.md"
    note_path.write_text(_ENTITY_NOTE, encoding="utf-8")

    conn = connect(tmp_path / "db.sqlite3")
    upsert_note_from_file(conn, vault_root, note_path)

    row = conn.execute(
        "SELECT * FROM notes WHERE path = ?", ("wiki/entities/ada-lovelace.md",)
    ).fetchone()
    assert row["slug"] == "ada-lovelace"
    first_hash = row["content_hash"]

    note_path.write_text(_ENTITY_NOTE + "\nMore detail.\n", encoding="utf-8")
    upsert_note_from_file(conn, vault_root, note_path)

    rows = conn.execute("SELECT * FROM notes").fetchall()
    assert len(rows) == 1  # updated in place, not duplicated
    assert rows[0]["content_hash"] != first_hash


def test_insert_chunk_persists_and_returns_id(tmp_path: Path) -> None:
    conn = connect(tmp_path / "db.sqlite3")
    conn.execute(
        "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
        "VALUES ('Doc', 'raw/doc.md', 'queued', '2026-01-01', '2026-01-01')"
    )
    queue_item_id = conn.execute("SELECT id FROM queue").fetchone()[0]

    chunk = Chunk(
        queue_item_id=queue_item_id, ordinal=0, title="Intro", content="Body text.", word_count=2
    )
    chunk_id = insert_chunk(conn, chunk)

    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    assert row["title"] == "Intro"
    assert row["content"] == "Body text."
    assert row["queue_item_id"] == queue_item_id
    assert row["note_id"] is None


def test_connect_migrates_a_pre_phase10_database(tmp_path: Path) -> None:
    """Regression test for the ALTER TABLE step added in Phase 10: a `notes`
    table created before `links_synced_hash` existed must be migrated in
    place, without losing the row already there.
    """
    db_path = tmp_path / "legacy.sqlite3"
    raw_conn = sqlite3.connect(db_path)
    raw_conn.execute(
        """
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL UNIQUE,
            slug TEXT NOT NULL,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '[]',
            sources TEXT NOT NULL DEFAULT '[]',
            content_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    raw_conn.execute(
        "INSERT INTO notes (path, slug, type, title, content_hash, updated_at) "
        "VALUES ('wiki/a.md', 'a', 'concept', 'A', 'abc123', '2026-01-01')"
    )
    raw_conn.commit()
    raw_conn.close()

    conn = connect(db_path)

    columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
    assert "links_synced_hash" in columns

    row = conn.execute(
        "SELECT slug, links_synced_hash FROM notes WHERE path = 'wiki/a.md'"
    ).fetchone()
    assert row["slug"] == "a"
    assert row["links_synced_hash"] is None
