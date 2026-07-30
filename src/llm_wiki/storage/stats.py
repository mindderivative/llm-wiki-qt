"""Vault-wide statistics for the Dashboard panel (Phase 21).

Distinct from `lint.run_lint()`'s per-run health score -- this is a plain
aggregation over `notes`/`queue`/`links`, not a quality check.
"""

import sqlite3
from pathlib import Path

from pydantic import BaseModel

from llm_wiki.graph import count_wikilink_occurrences


class VaultStats(BaseModel):
    """Note-type counts, ingestion history, and link counts for one vault."""

    concepts: int
    entities: int
    sources: int
    synthesis: int
    total_ingested: int
    failures: int
    total_wikilinks: int
    total_backlinks: int


def get_vault_stats(conn: sqlite3.Connection, vault_root: Path | str) -> VaultStats:
    by_type = dict(conn.execute("SELECT type, COUNT(*) FROM notes GROUP BY type").fetchall())
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM queue GROUP BY status").fetchall())
    total_backlinks = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
    return VaultStats(
        concepts=by_type.get("concept", 0),
        entities=by_type.get("entity", 0),
        sources=by_type.get("source", 0),
        synthesis=by_type.get("synthesis", 0),
        # A queue item that errored out wasn't actually ingested -- keeping
        # this to `completed` only makes it a distinct, meaningful number
        # from `failures`, not both just sub-counts of "everything queued".
        total_ingested=by_status.get("completed", 0),
        failures=by_status.get("error", 0),
        # Raw [[wikilink]] occurrences (total_wikilinks) can exceed the
        # deduped edge count (total_backlinks) -- a note referencing the
        # same target twice in its prose is one edge in `links` but two
        # occurrences on disk.
        total_wikilinks=count_wikilink_occurrences(conn, vault_root),
        total_backlinks=total_backlinks,
    )
