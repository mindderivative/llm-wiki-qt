"""Vault-wide statistics for the Dashboard panel (Phase 21).

Distinct from `lint.run_lint()`'s per-run health score -- this is a plain
aggregation over `notes`/`queue`, not a quality check.
"""

import sqlite3

from pydantic import BaseModel


class VaultStats(BaseModel):
    """Note-type counts plus ingestion history for one vault."""

    concepts: int
    entities: int
    sources: int
    synthesis: int
    total_ingested: int
    failures: int


def get_vault_stats(conn: sqlite3.Connection) -> VaultStats:
    by_type = dict(conn.execute("SELECT type, COUNT(*) FROM notes GROUP BY type").fetchall())
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM queue GROUP BY status").fetchall())
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
    )
