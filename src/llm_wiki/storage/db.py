"""SQLite + sqlite-vec cache layer — fully rebuildable from wiki/ + raw/.

See ARCHITECTURE.md §6 for the schema and Design Principle 1: losing
`.llm-wiki/db.sqlite3` must only cost rebuild time, never information.
"""

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
import sqlite_vec

from llm_wiki.models import Chunk, NoteType

SCHEMA_VERSION = 2  # v2: notes.links_synced_hash, added for Phase 10's link engine
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SYSTEM_DIR_NAME = ".system"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Opens a connection with sqlite-vec loaded and the schema ensured."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
    _run_migrations(conn)

    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Explicit migration steps for DBs created before the current SCHEMA_VERSION.

    `CREATE TABLE IF NOT EXISTS` in schema.sql only helps brand-new
    databases -- it doesn't add columns to a `notes` table that already
    exists on disk from an earlier schema version.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "links_synced_hash" not in columns:
        conn.execute("ALTER TABLE notes ADD COLUMN links_synced_hash TEXT")


def rebuild_from_vault(vault_root: Path | str, db_path: Path | str) -> sqlite3.Connection:
    """Wipes and reconstructs the DB purely from the vault's `wiki/` contents.

    Safe to call after deleting `.llm-wiki/db.sqlite3` entirely — the
    returned connection reflects only what's currently on disk under
    `wiki/`, per Design Principle 1.
    """
    vault_root = Path(vault_root)
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()

    conn = connect(db_path)
    _rebuild_notes(conn, vault_root)
    conn.commit()
    return conn


_RESERVED_TOP_LEVEL_FILES = {"index.md", "log.md"}


def _rebuild_notes(conn: sqlite3.Connection, vault_root: Path) -> None:
    wiki_dir = vault_root / "wiki"
    if not wiki_dir.exists():
        return

    for note_path in sorted(wiki_dir.rglob("*.md")):
        if _SYSTEM_DIR_NAME in note_path.parts:
            continue
        # wiki/index.md and wiki/log.md are reserved, never-tracked system
        # files (vault/manager.py, vault/reindex.py) -- excluded only at
        # the top level, so a legitimately-slugged note elsewhere (e.g.
        # wiki/entities/index.md, about database indexing) isn't skipped.
        if note_path.parent == wiki_dir and note_path.name in _RESERVED_TOP_LEVEL_FILES:
            continue
        upsert_note_from_file(conn, vault_root, note_path)


def upsert_note_from_file(conn: sqlite3.Connection, vault_root: Path, note_path: Path) -> None:
    """Parses one note's frontmatter and upserts its `notes` row.

    Shared by `rebuild_from_vault()` and the compiler (which calls this
    right after writing a source/entity/concept note to disk, so the DB
    cache stays in sync without a full vault re-walk).
    """
    raw_text = note_path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    post = frontmatter.loads(raw_text)
    rel_path = str(note_path.relative_to(vault_root))

    conn.execute(
        """
        INSERT INTO notes (path, slug, type, title, tags, sources, content_hash, updated_at)
        VALUES (:path, :slug, :type, :title, :tags, :sources, :content_hash, :updated_at)
        ON CONFLICT (path) DO UPDATE SET
            slug = excluded.slug,
            type = excluded.type,
            title = excluded.title,
            tags = excluded.tags,
            sources = excluded.sources,
            content_hash = excluded.content_hash,
            updated_at = excluded.updated_at
        """,
        {
            "path": rel_path,
            "slug": str(post.get("slug", note_path.stem)),
            "type": str(post.get("type", NoteType.SOURCE)),
            "title": str(post.get("title", note_path.stem)),
            "tags": json.dumps(list(post.get("tags", []))),
            "sources": json.dumps(list(post.get("sources", []))),
            "content_hash": content_hash,
            "updated_at": datetime.now(UTC).isoformat(),
        },
    )
    conn.commit()


def insert_chunk(conn: sqlite3.Connection, chunk: Chunk) -> int:
    """Inserts one atomizer-produced `Chunk` row; returns its DB-assigned id."""
    cursor = conn.execute(
        """
        INSERT INTO chunks (note_id, queue_item_id, ordinal, title, content, word_count)
        VALUES (:note_id, :queue_item_id, :ordinal, :title, :content, :word_count)
        """,
        chunk.model_dump(
            include={"note_id", "queue_item_id", "ordinal", "title", "content", "word_count"}
        ),
    )
    conn.commit()
    return cursor.lastrowid
