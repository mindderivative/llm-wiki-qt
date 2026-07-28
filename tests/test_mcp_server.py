"""Phase 13: MCP tools return correct data from a fixture vault; path traversal is rejected."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.graph import sync_links
from llm_wiki.ingest import enqueue_file
from llm_wiki.llm.client import LlamaClient
from llm_wiki.mcp.server import _read_note, _resolve_note_path, create_mcp_server
from llm_wiki.models import Chunk
from llm_wiki.storage import connect, insert_chunk, upsert_chunk_embedding, upsert_note_from_file
from llm_wiki.vault import create_vault


def _note(*, slug: str, title: str, note_type: str, body: str) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"type: {note_type}\n"
        "tags: []\n"
        "sources: []\n"
        "---\n\n"
        f"{body}\n"
    )


class _FakeEmbeddings:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def create(self, **kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=self._vector) for _ in kwargs["input"]]
        )


class _FakeOpenAI:
    def __init__(self, vector: list[float]) -> None:
        self.embeddings = _FakeEmbeddings(vector)


def _embedding(first: float) -> list[float]:
    return [first, *([0.0] * 767)]


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(
        root, "Test Vault", "A test knowledge base.", recent_vaults_path=tmp_path / "recent.json"
    )
    return root


def _run(coro):
    return asyncio.run(coro)


def test_resolve_note_path_rejects_traversal(vault_root: Path) -> None:
    with pytest.raises(ValueError, match="Rejected path"):
        _resolve_note_path(vault_root, "entities", "../../../etc/passwd")


def test_resolve_note_path_rejects_absolute_override(vault_root: Path) -> None:
    with pytest.raises(ValueError, match="Rejected path"):
        _resolve_note_path(vault_root, "entities", "/etc/passwd")


def test_resolve_note_path_accepts_normal_slug(vault_root: Path) -> None:
    path = _resolve_note_path(vault_root, "entities", "ada-lovelace")
    assert path == (vault_root / "wiki" / "entities" / "ada-lovelace.md").resolve()


def test_read_note_traversal_attempt_is_rejected_not_a_crash(vault_root: Path) -> None:
    result = _read_note(vault_root, "entities", "../../../etc/passwd")
    assert result["found"] is False
    assert "Rejected path" in result["error"]


def test_read_entity_profile_tool_via_mcp(vault_root: Path) -> None:
    entities_dir = vault_root / "wiki" / "entities"
    (entities_dir / "ada-lovelace.md").write_text(
        _note(slug="ada-lovelace", title="Ada Lovelace", note_type="entity", body="Mathematician."),
        encoding="utf-8",
    )

    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, structured = _run(mcp.call_tool("read_entity_profile", {"slug": "ada-lovelace"}))
    assert structured["found"] is True
    assert structured["title"] == "Ada Lovelace"
    assert "Mathematician" in structured["content"]

    _, missing = _run(mcp.call_tool("read_entity_profile", {"slug": "nobody"}))
    assert missing["found"] is False


def test_read_entity_profile_tool_rejects_path_traversal_via_mcp(vault_root: Path) -> None:
    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, structured = _run(
        mcp.call_tool("read_entity_profile", {"slug": "../../../../../../etc/passwd"})
    )

    assert structured["found"] is False
    assert "Rejected path" in structured["error"]


def test_read_synthesis_note_tool(vault_root: Path) -> None:
    synthesis_dir = vault_root / "wiki" / "synthesis"
    (synthesis_dir / "grand-theme.md").write_text(
        _note(
            slug="grand-theme",
            title="Grand Theme",
            note_type="synthesis",
            body="Everything connects.",
        ),
        encoding="utf-8",
    )

    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, structured = _run(mcp.call_tool("read_synthesis_note", {"slug": "grand-theme"}))
    assert structured["found"] is True
    assert structured["title"] == "Grand Theme"


def test_trace_network_path_within_degree_limit(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    concepts_dir = vault_root / "wiki" / "concepts"
    chain = {
        "a": "See [[b]].",
        "b": "See [[c]].",
        "c": "See [[d]].",
        "d": "End.",
    }
    for slug, body in chain.items():
        path = concepts_dir / f"{slug}.md"
        content = _note(slug=slug, title=slug.upper(), note_type="concept", body=body)
        path.write_text(content, encoding="utf-8")
        upsert_note_from_file(conn, vault_root, path)
    sync_links(conn, vault_root)
    conn.close()

    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, reachable = _run(
        mcp.call_tool("trace_network_path", {"source_slug": "a", "target_slug": "c"})
    )
    assert reachable["path_found"] is True
    assert reachable["path"] == ["a", "b", "c"]
    assert reachable["degrees"] == 2

    _, too_far = _run(
        mcp.call_tool(
            "trace_network_path", {"source_slug": "a", "target_slug": "d", "max_degrees": 2}
        )
    )
    assert too_far["path_found"] is False

    _, unreachable = _run(
        mcp.call_tool("trace_network_path", {"source_slug": "d", "target_slug": "a"})
    )
    assert unreachable["path_found"] is False

    _, unknown = _run(
        mcp.call_tool("trace_network_path", {"source_slug": "a", "target_slug": "nonexistent"})
    )
    assert unknown["path_found"] is False


def test_search_wiki_content_returns_matching_chunk(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    source_file = vault_root.parent / "note.txt"
    source_file.write_text("Ada Lovelace content", encoding="utf-8")
    item = enqueue_file(conn, vault_root, source_file, title="Ada Doc")
    chunk = Chunk(
        queue_item_id=item.id,
        ordinal=0,
        title="Intro",
        content="Ada Lovelace wrote the first algorithm.",
        word_count=6,
    )
    chunk_id = insert_chunk(conn, chunk)
    upsert_chunk_embedding(conn, chunk_id, _embedding(1.0))
    conn.close()

    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, structured = _run(mcp.call_tool("search_wiki_content", {"query": "algorithm", "top_k": 5}))

    results = structured["result"]
    assert len(results) == 1
    assert results[0]["chunk_id"] == chunk_id
    assert results[0]["title"] == "Ada Doc"
    assert results[0]["content"] == "Ada Lovelace wrote the first algorithm."
    assert results[0]["distance"] == pytest.approx(0.0)


def test_search_wiki_content_with_no_chunks_returns_empty(vault_root: Path) -> None:
    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    _, structured = _run(mcp.call_tool("search_wiki_content", {"query": "anything"}))

    assert structured["result"] == []


def test_all_four_tools_are_registered(vault_root: Path) -> None:
    client = LlamaClient(client=_FakeOpenAI(_embedding(1.0)))
    mcp = create_mcp_server(vault_root, client=client)

    tools = _run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert names == {
        "search_wiki_content",
        "read_entity_profile",
        "read_synthesis_note",
        "trace_network_path",
    }
