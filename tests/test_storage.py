"""Phase 2: sqlite-vec loads and rebuild_from_vault() reconstructs `notes`."""

import json
import sqlite3
from pathlib import Path

from llm_wiki.storage import SCHEMA_VERSION, connect, rebuild_from_vault

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
