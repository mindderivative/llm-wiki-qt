"""SQLite + sqlite-vec cache layer (queue, notes, chunks, links, lint findings).

Everything here is a rebuildable derived store per ARCHITECTURE.md §6 —
`storage.db.rebuild_from_vault()` reconstructs it purely from `wiki/`.
"""

from llm_wiki.storage.db import (
    SCHEMA_VERSION,
    connect,
    insert_chunk,
    rebuild_from_vault,
    upsert_note_from_file,
)
from llm_wiki.storage.stats import VaultStats, get_vault_stats
from llm_wiki.storage.vector_search import (
    EMBEDDING_DIMENSIONS,
    similarity_search,
    upsert_chunk_embedding,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "SCHEMA_VERSION",
    "VaultStats",
    "connect",
    "get_vault_stats",
    "insert_chunk",
    "rebuild_from_vault",
    "similarity_search",
    "upsert_chunk_embedding",
    "upsert_note_from_file",
]
