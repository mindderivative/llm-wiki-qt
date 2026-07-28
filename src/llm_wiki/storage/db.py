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

from llm_wiki.models import NoteType

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).parent / "schema.sql"
_SYSTEM_DIR_NAME = ".system"


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Opens a connection with sqlite-vec loaded and the schema ensured."""
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
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    conn.commit()


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
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    _rebuild_notes(conn, vault_root)
    conn.commit()
    return conn


def _rebuild_notes(conn: sqlite3.Connection, vault_root: Path) -> None:
    wiki_dir = vault_root / "wiki"
    if not wiki_dir.exists():
        return

    now = datetime.now(UTC).isoformat()
    for note_path in sorted(wiki_dir.rglob("*.md")):
        if _SYSTEM_DIR_NAME in note_path.parts:
            continue

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
                "updated_at": now,
            },
        )
