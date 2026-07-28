"""SQLite + sqlite-vec cache layer (queue, notes, chunks, links, lint findings).

Everything here is a rebuildable derived store per ARCHITECTURE.md §6 —
`storage.db.rebuild_from_vault()` reconstructs it purely from `wiki/`.
"""

from llm_wiki.storage.db import SCHEMA_VERSION, connect, rebuild_from_vault

__all__ = ["SCHEMA_VERSION", "connect", "rebuild_from_vault"]
