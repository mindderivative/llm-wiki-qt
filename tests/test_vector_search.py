"""Phase 8: upsert_chunk_embedding() + similarity_search() over vec_chunks."""

from pathlib import Path

import pytest

from llm_wiki.storage import (
    EMBEDDING_DIMENSIONS,
    connect,
    similarity_search,
    upsert_chunk_embedding,
)


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "db.sqlite3")
    yield connection
    connection.close()


def _embedding(*leading_values: float) -> list[float]:
    padding = EMBEDDING_DIMENSIONS - len(leading_values)
    return [*leading_values, *([0.0] * padding)]


def test_similarity_search_orders_nearest_first(conn) -> None:
    upsert_chunk_embedding(conn, 1, _embedding(1.0, 0.0, 0.0))  # exact match to query
    upsert_chunk_embedding(conn, 2, _embedding(0.0, 1.0, 0.0))  # far
    upsert_chunk_embedding(conn, 3, _embedding(0.9, 0.1, 0.0))  # close

    query = _embedding(1.0, 0.0, 0.0)
    results = similarity_search(conn, query, top_k=3)

    assert [chunk_id for chunk_id, _ in results] == [1, 3, 2]
    assert results[0][1] == pytest.approx(0.0, abs=1e-6)
    distances = [distance for _, distance in results]
    assert distances == sorted(distances)


def test_similarity_search_respects_top_k(conn) -> None:
    upsert_chunk_embedding(conn, 1, _embedding(1.0))
    upsert_chunk_embedding(conn, 2, _embedding(0.0, 1.0))
    upsert_chunk_embedding(conn, 3, _embedding(0.0, 0.0, 1.0))

    results = similarity_search(conn, _embedding(1.0), top_k=2)

    assert len(results) == 2
    assert results[0][0] == 1


def test_upsert_replaces_existing_embedding_not_duplicates(conn) -> None:
    upsert_chunk_embedding(conn, 1, _embedding(1.0, 0.0))
    upsert_chunk_embedding(conn, 2, _embedding(0.0, 1.0))

    count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    assert count == 2

    # Move chunk 2's embedding to match the query exactly.
    upsert_chunk_embedding(conn, 2, _embedding(1.0, 0.0))

    count_after = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    assert count_after == 2

    results = similarity_search(conn, _embedding(1.0, 0.0), top_k=2)
    assert {chunk_id for chunk_id, distance in results if distance == pytest.approx(0.0)} == {
        1,
        2,
    }


def test_upsert_rejects_wrong_dimension_embedding(conn) -> None:
    with pytest.raises(ValueError, match="768"):
        upsert_chunk_embedding(conn, 1, [1.0, 2.0, 3.0])


def test_similarity_search_rejects_wrong_dimension_query(conn) -> None:
    upsert_chunk_embedding(conn, 1, _embedding(1.0))
    with pytest.raises(ValueError, match="768"):
        similarity_search(conn, [1.0, 2.0], top_k=1)
