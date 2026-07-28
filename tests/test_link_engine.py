"""Phase 10: incremental link sync -- re-runs touch zero rows, edits touch one."""

from pathlib import Path

import pytest

from llm_wiki.graph import degrees_of_separation, get_graph_data, rebuild_full, sync_links
from llm_wiki.storage import connect, upsert_note_from_file


def _note(*, slug: str, title: str, body: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        "type: concept\n"
        "tags: []\n"
        "sources: []\n"
        "---\n\n"
        f"{body}\n"
    )


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "db.sqlite3")
    yield connection
    connection.close()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "wiki" / "concepts").mkdir(parents=True)
    return root


def _write_chain(vault_root: Path, conn) -> dict[str, Path]:
    """a -> b -> c -> d, each a plain [[wikilink]] to the next; d is a leaf."""
    notes = {
        "a": _note(slug="a", title="A", body="See [[b]] for details."),
        "b": _note(slug="b", title="B", body="Related to [[c]]."),
        "c": _note(slug="c", title="C", body="Connects to [[d]]."),
        "d": _note(slug="d", title="D", body="End of the chain."),
    }
    paths = {}
    for slug, content in notes.items():
        path = vault_root / "wiki" / "concepts" / f"{slug}.md"
        path.write_text(content, encoding="utf-8")
        upsert_note_from_file(conn, vault_root, path)
        paths[slug] = path
    return paths


def test_first_sync_processes_every_note_and_populates_links(conn, vault_root) -> None:
    _write_chain(vault_root, conn)

    processed = sync_links(conn, vault_root)

    assert processed == 4
    edges = {
        (row["source_slug"], row["target_slug"])
        for row in conn.execute("SELECT source_slug, target_slug FROM links").fetchall()
    }
    assert edges == {("a", "b"), ("b", "c"), ("c", "d")}


def test_resync_with_no_changes_touches_zero_rows(conn, vault_root) -> None:
    _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    before = conn.execute("SELECT * FROM links ORDER BY source_slug").fetchall()
    processed = sync_links(conn, vault_root)
    after = conn.execute("SELECT * FROM links ORDER BY source_slug").fetchall()

    assert processed == 0
    assert [dict(r) for r in before] == [dict(r) for r in after]


def test_changing_one_note_only_reprocesses_that_notes_edges(conn, vault_root) -> None:
    paths = _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    # Point b at d instead of c; a, c, d are untouched.
    paths["b"].write_text(
        _note(slug="b", title="B", body="Related to [[d]] now."), encoding="utf-8"
    )
    upsert_note_from_file(conn, vault_root, paths["b"])

    processed = sync_links(conn, vault_root)

    assert processed == 1
    edges = {
        (row["source_slug"], row["target_slug"])
        for row in conn.execute("SELECT source_slug, target_slug FROM links").fetchall()
    }
    assert edges == {("a", "b"), ("b", "d"), ("c", "d")}


def test_rebuild_full_reprocesses_everything_even_without_changes(conn, vault_root) -> None:
    _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    processed = rebuild_full(conn, vault_root)

    assert processed == 4


def test_get_graph_data_reflects_nodes_and_edges(conn, vault_root) -> None:
    _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    graph = get_graph_data(conn)

    assert set(graph.nodes) == {"a", "b", "c", "d"}
    assert set(graph.edges) == {("a", "b"), ("b", "c"), ("c", "d")}


def test_degrees_of_separation_within_and_beyond_limit(conn, vault_root) -> None:
    _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    assert degrees_of_separation(conn, "a", "b") == 1
    assert degrees_of_separation(conn, "a", "d") == 3
    assert degrees_of_separation(conn, "a", "d", max_degrees=2) is None


def test_degrees_of_separation_unreachable_or_unknown_returns_none(conn, vault_root) -> None:
    _write_chain(vault_root, conn)
    sync_links(conn, vault_root)

    assert degrees_of_separation(conn, "d", "a") is None  # edges are directional
    assert degrees_of_separation(conn, "a", "nonexistent") is None
