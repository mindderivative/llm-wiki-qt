"""Stage raw files and manage the ingestion queue as DB rows (ARCHITECTURE.md §8).

The queue lives entirely in the `queue` SQLite table -- every status
transition is a single row UPDATE, not a full-file rewrite of the old
`queue.json`.
"""

import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from llm_wiki.models import IngestionError, QueueItem, QueueStatus


def enqueue_file(
    conn: sqlite3.Connection,
    vault_root: Path | str,
    source_file: Path | str,
    *,
    title: str | None = None,
) -> QueueItem:
    """Archives the original, stages a working copy, and inserts a QUEUED row.

    The untouched original is copied to `raw/.sources/`; a working copy
    named `{date}_{slug}{ext}` is copied to `raw/` for downstream
    atomization. Filename collisions are disambiguated with a numeric
    suffix rather than overwritten.
    """
    vault_root = Path(vault_root)
    source_file = Path(source_file)
    if not source_file.is_file():
        raise IngestionError(f"Source file not found: {source_file}")

    resolved_title = title or source_file.stem

    archive_dir = vault_root / "raw" / ".sources"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = _unique_path(archive_dir / source_file.name)
    shutil.copy2(source_file, archive_path)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    staged_name = f"{date_str}_{_slugify(resolved_title)}{source_file.suffix}"
    raw_dir = vault_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    staged_path = _unique_path(raw_dir / staged_name)
    shutil.copy2(source_file, staged_path)

    now = datetime.now(UTC)
    rel_raw_path = staged_path.relative_to(vault_root)
    rel_archive_path = archive_path.relative_to(vault_root)

    cursor = conn.execute(
        """
        INSERT INTO queue (title, raw_path, archive_path, status, error, created_at, updated_at)
        VALUES (:title, :raw_path, :archive_path, :status, NULL, :created_at, :updated_at)
        """,
        {
            "title": resolved_title,
            "raw_path": str(rel_raw_path),
            "archive_path": str(rel_archive_path),
            "status": QueueStatus.QUEUED.value,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        },
    )
    conn.commit()

    return QueueItem(
        id=cursor.lastrowid,
        title=resolved_title,
        raw_path=rel_raw_path,
        archive_path=rel_archive_path,
        status=QueueStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )


def update_status(
    conn: sqlite3.Connection,
    item_id: int,
    status: QueueStatus,
    *,
    error: str | None = None,
) -> QueueItem:
    """Persists a queue item's status transition and returns the updated row."""
    now = datetime.now(UTC)
    cursor = conn.execute(
        "UPDATE queue SET status = ?, error = ?, updated_at = ? WHERE id = ?",
        (status.value, error, now.isoformat(), item_id),
    )
    if cursor.rowcount == 0:
        raise IngestionError(f"No queue item with id {item_id}")
    conn.commit()

    row = conn.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()
    return _row_to_queue_item(row)


def list_queue(conn: sqlite3.Connection, status: QueueStatus | None = None) -> list[QueueItem]:
    """Lists queue items, optionally filtered to a single status, oldest first."""
    if status is None:
        rows = conn.execute("SELECT * FROM queue ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM queue WHERE status = ? ORDER BY id", (status.value,)
        ).fetchall()
    return [_row_to_queue_item(row) for row in rows]


def _row_to_queue_item(row: sqlite3.Row) -> QueueItem:
    return QueueItem(
        id=row["id"],
        title=row["title"],
        raw_path=Path(row["raw_path"]),
        archive_path=Path(row["archive_path"]) if row["archive_path"] else None,
        status=QueueStatus(row["status"]),
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    candidate, counter = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        counter += 1
    return candidate
