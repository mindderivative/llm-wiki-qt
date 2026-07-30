"""Keeps a vault's self-referential metadata live: the link graph,
`wiki/index.md`'s catalog, and `wiki/log.md`'s activity trail (Phase 18).

`reindex_vault()` is the user-facing "fix an already-broken vault"
action (GUI's Tools > Reindex Vault, `llm-wiki vault reindex`) --
`backfill_related_blocks()`/`sync_links()`/`rebuild_index()` stay
independently callable/testable since a bug report against "reindex
didn't fix my vault" needs to be isolable to one of the three steps.
`compile_queued_item()` only ever needs `rebuild_index()`/
`append_log_entry()` directly (plus the already-imported `sync_links()`),
since a fresh compile already writes correct Related blocks itself.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from loguru import logger

from llm_wiki.graph.link_engine import sync_links
from llm_wiki.models import NoteFrontmatter
from llm_wiki.related_links import render_related_block, strip_related_block
from llm_wiki.storage.db import upsert_note_from_file

_SECTION_ORDER = [
    ("Sources", "source"),
    ("Entities", "entity"),
    ("Concepts", "concept"),
    ("Synthesis", "synthesis"),
]


def backfill_related_blocks(conn: sqlite3.Connection, vault_root: Path) -> int:
    """Re-renders every note's Related block from its stored `sources` --
    for notes compiled before Phase 18 (or otherwise missing one).
    Returns the count of notes rewritten.
    """
    vault_root = Path(vault_root)
    rows = conn.execute("SELECT path, slug, type, title, tags, sources FROM notes").fetchall()

    count = 0
    for row in rows:
        note_path = vault_root / row["path"]
        if not note_path.exists():
            continue

        post = frontmatter.loads(note_path.read_text(encoding="utf-8"))
        fm = NoteFrontmatter(
            title=row["title"],
            slug=row["slug"],
            type=row["type"],
            tags=json.loads(row["tags"]),
            sources=json.loads(row["sources"]),
        )
        clean_body = strip_related_block(str(post.content).strip())
        full_body = clean_body + render_related_block(fm)
        new_post = frontmatter.Post(full_body.strip() + "\n", **fm.model_dump(mode="json"))
        note_path.write_text(frontmatter.dumps(new_post) + "\n", encoding="utf-8")
        upsert_note_from_file(conn, vault_root, note_path)
        count += 1

    logger.info(f"Backfilled Related blocks for {count} note(s)")
    return count


def rebuild_index(conn: sqlite3.Connection, vault_root: Path) -> None:
    """Regenerates `wiki/index.md`'s catalog sections from current `notes`
    rows. Preserves everything above the first `## Sources` heading (the
    vault name/description header written once by `create_vault()`) --
    idempotent and always correct given current DB state, unlike an
    incremental append, which would leave stale entries for renamed or
    deleted notes.
    """
    vault_root = Path(vault_root)
    index_path = vault_root / "wiki" / "index.md"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    header = (
        existing.split("## Sources")[0].rstrip() if "## Sources" in existing else existing.rstrip()
    )

    by_type: dict[str, list[tuple[str, str]]] = {}
    for row in conn.execute("SELECT slug, title, type FROM notes ORDER BY title").fetchall():
        by_type.setdefault(row["type"], []).append((row["slug"], row["title"]))

    lines = [header, ""] if header else []
    total = 0
    for heading, key in _SECTION_ORDER:
        lines.append(f"## {heading}")
        lines.append("")
        entries = by_type.get(key, [])
        if entries:
            lines.extend(f"- [[{slug}]] — {title}" for slug, title in entries)
            total += len(entries)
        else:
            lines.append("*(none yet)*")
        lines.append("")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info(f"Rebuilt index.md ({total} note(s))")


def append_log_entry(vault_root: Path, message: str) -> None:
    """Appends one timestamped line to `wiki/log.md`, matching the format
    `create_vault()` already writes its first line in.
    """
    vault_root = Path(vault_root)
    log_path = vault_root / "wiki" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"- [{timestamp}] {message}\n")


def reindex_vault(conn: sqlite3.Connection, vault_root: Path) -> None:
    """Full retroactive fix for a vault compiled before Phase 18 (or
    otherwise out of sync): backfills every note's Related block, re-syncs
    the link graph from the now-changed content, and regenerates
    `index.md`. The engine-level action behind the GUI's Reindex Vault
    menu item and `llm-wiki vault reindex`.
    """
    vault_root = Path(vault_root)
    rewritten = backfill_related_blocks(conn, vault_root)
    synced = sync_links(conn, vault_root)
    rebuild_index(conn, vault_root)
    append_log_entry(
        vault_root, f"Reindexed vault: {rewritten} note(s) backfilled, {synced} synced"
    )
    logger.info(f"Reindexed vault at {vault_root}")
