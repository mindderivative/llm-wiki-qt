"""Vector similarity search over `vec_chunks` (ARCHITECTURE.md §6).

Wires `llm.embeddings` output into the sqlite-vec cache and exposes
nearest-neighbor search over it. No LLM/network dependency here --
callers are responsible for producing the embedding vectors.
"""

import sqlite3

import sqlite_vec

EMBEDDING_DIMENSIONS = 768


def upsert_chunk_embedding(
    conn: sqlite3.Connection, chunk_id: int, embedding: list[float]
) -> None:
    """Inserts or replaces the embedding for `chunk_id`.

    `vec0` virtual tables reject `ON CONFLICT`/`INSERT OR REPLACE` on their
    primary key, so an upsert is a delete followed by an insert.
    """
    _check_dimensions(embedding)
    conn.execute("DELETE FROM vec_chunks WHERE chunk_id = ?", (chunk_id,))
    conn.execute(
        "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, sqlite_vec.serialize_float32(embedding)),
    )
    conn.commit()


def similarity_search(
    conn: sqlite3.Connection, query_embedding: list[float], top_k: int = 5
) -> list[tuple[int, float]]:
    """Returns up to `top_k` `(chunk_id, distance)` pairs, nearest first."""
    _check_dimensions(query_embedding)
    rows = conn.execute(
        """
        SELECT chunk_id, distance
        FROM vec_chunks
        WHERE embedding MATCH ? AND k = ?
        ORDER BY distance
        """,
        (sqlite_vec.serialize_float32(query_embedding), top_k),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _check_dimensions(embedding: list[float]) -> None:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"Expected a {EMBEDDING_DIMENSIONS}-dim embedding, got {len(embedding)}"
        )
