"""SQLite + sqlite-vec cache layer (queue, notes, chunks, links, lint findings).

Everything here is a rebuildable derived store per ARCHITECTURE.md §6 —
`storage.db.rebuild_from_vault()` reconstructs it purely from `wiki/`.
"""

from llm_wiki.storage.db import SCHEMA_VERSION, connect, rebuild_from_vault
from llm_wiki.storage.vector_search import (
    EMBEDDING_DIMENSIONS,
    similarity_search,
    upsert_chunk_embedding,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "SCHEMA_VERSION",
    "connect",
    "rebuild_from_vault",
    "similarity_search",
    "upsert_chunk_embedding",
]
