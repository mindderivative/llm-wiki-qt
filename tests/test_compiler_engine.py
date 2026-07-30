"""Phase 9: end-to-end /wiki-ingest pipeline, against a scripted mocked LLM."""

import json
from pathlib import Path
from types import SimpleNamespace

import frontmatter
import openai
import pytest

from llm_wiki.compiler import compile_queued_item
from llm_wiki.ingest import enqueue_file, get_queue_item
from llm_wiki.llm.client import LlamaClient
from llm_wiki.models import CompilationError, CompileStage, QueueStatus
from llm_wiki.storage import connect
from llm_wiki.vault import create_vault


def _fake_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))]
    )


class _ScriptedCompletions:
    """Returns canned chat-completion responses in call order."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("No more scripted LLM responses available")
        return _fake_response(self._responses.pop(0))


def _make_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[str]
) -> tuple[LlamaClient, _ScriptedCompletions]:
    """A LlamaClient wrapping a real openai.OpenAI instance with both chat completions
    and embeddings mocked (outlines.from_openai() requires a genuine OpenAI instance).
    """
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    scripted = _ScriptedCompletions(responses)
    monkeypatch.setattr(real_client.chat.completions, "create", scripted.create)

    def fake_embeddings_create(**kwargs):
        vectors = [[0.1] * 768 for _ in kwargs["input"]]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])

    monkeypatch.setattr(real_client.embeddings, "create", fake_embeddings_create)
    return LlamaClient(client=real_client), scripted


def _extraction_response(*, slug: str, content: str, sources: list[str] | None = None) -> str:
    return json.dumps(
        {
            "entities": [
                {
                    "frontmatter": {
                        "title": "Ada Lovelace",
                        "slug": slug,
                        "type": "entity",
                        "tags": ["mathematician"],
                        "sources": sources or [],
                    },
                    "content": content,
                }
            ]
        }
    )


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(
        root, "Test Vault", "A test knowledge base.", recent_vaults_path=tmp_path / "recent.json"
    )
    return root


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / ".llm-wiki" / "db.sqlite3")
    yield connection
    connection.close()


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "ada.txt"
    path.write_text(
        "Ada Lovelace was an English mathematician and writer, chiefly known for her "
        "work on Charles Babbage's proposed mechanical general-purpose computer, the "
        "Analytical Engine.\n",
        encoding="utf-8",
    )
    return path


def test_compile_creates_source_summary_and_new_entity_note(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    source_slug = Path(item.raw_path).stem

    responses = [
        "## Overview\n\nAda Lovelace pioneered the concept of programmable computing.",
        _extraction_response(
            slug="ada-lovelace",
            content="Ada Lovelace was a mathematician who worked with Charles Babbage.",
        ),
    ]
    client, scripted = _make_client(monkeypatch, responses)

    result = compile_queued_item(conn, client, vault_root, item.id, chat_model="test-model")

    # Source summary note.
    assert result.source_path == vault_root / "wiki" / "sources" / f"{source_slug}.md"
    source_post = frontmatter.load(result.source_path)
    assert source_post.metadata["type"] == "source"
    assert "Ada Lovelace pioneered" in source_post.content
    # Bidirectional: the source note links forward to what was extracted
    # from it, not just back to index.
    assert "[[index]]" in source_post.content
    assert "[[ada-lovelace]]" in source_post.content

    # Newly-created entity note: written as-is, no merge call needed.
    assert len(result.entity_paths) == 1
    entity_path = result.entity_paths[0]
    assert entity_path == vault_root / "wiki" / "entities" / "ada-lovelace.md"
    entity_post = frontmatter.load(entity_path)
    assert entity_post.metadata["type"] == "entity"
    assert entity_post.content.startswith(
        "Ada Lovelace was a mathematician who worked with Charles Babbage."
    )
    # Deterministic backlinks: index + its source, regardless of LLM output.
    assert "[[index]]" in entity_post.content
    assert f"[[{source_slug}]]" in entity_post.content
    assert entity_post.metadata["sources"] == [source_slug]
    assert len(scripted.calls) == 2  # summary + extraction, no merge call

    # notes table has both the source and entity note.
    note_paths = {row[0] for row in conn.execute("SELECT path FROM notes").fetchall()}
    assert note_paths == {
        f"wiki/sources/{source_slug}.md",
        "wiki/entities/ada-lovelace.md",
    }

    # Chunks were persisted and embedded.
    assert len(result.chunk_ids) >= 1
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert chunk_count == len(result.chunk_ids)
    vec_count = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    assert vec_count == len(result.chunk_ids)

    assert get_queue_item(conn, item.id).status is QueueStatus.COMPLETED

    # Post-compile maintenance ran automatically -- no separate sync_links()
    # call needed for the link graph to reflect the deterministic backlinks.
    links = conn.execute("SELECT source_slug, target_slug FROM links").fetchall()
    assert ("ada-lovelace", "index") in {(r[0], r[1]) for r in links}
    assert ("ada-lovelace", source_slug) in {(r[0], r[1]) for r in links}

    index_text = (vault_root / "wiki" / "index.md").read_text(encoding="utf-8")
    assert f"[[{source_slug}]]" in index_text
    assert "[[ada-lovelace]]" in index_text

    log_text = (vault_root / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "Ada Lovelace Biography" in log_text


def test_compile_merges_into_existing_entity_note(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    entities_dir = vault_root / "wiki" / "entities"
    existing_post = frontmatter.Post(
        # Already has a Related block, as if compiled once before under
        # Phase 18 -- the merge prompt must never see it (see the
        # `merge_call_content` assertion below).
        "Ada Lovelace was born in 1815.\n\n"
        "<!-- llm-wiki:related -->\n## Related\n\n- [[index]]\n- [[earlier-source]]\n",
        title="Ada Lovelace",
        slug="ada-lovelace",
        type="entity",
        tags=["person"],
        sources=["earlier-source"],
    )
    (entities_dir / "ada-lovelace.md").write_text(
        frontmatter.dumps(existing_post) + "\n", encoding="utf-8"
    )

    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    source_slug = Path(item.raw_path).stem
    merged_content = (
        "MERGED: Ada Lovelace was born in 1815 and later worked with Charles Babbage "
        "on the Analytical Engine."
    )

    responses = [
        "## Overview\n\nAda Lovelace's later work is discussed here.",
        _extraction_response(
            slug="ada-lovelace",
            content="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
        ),
        merged_content,
    ]
    client, scripted = _make_client(monkeypatch, responses)

    result = compile_queued_item(conn, client, vault_root, item.id, chat_model="test-model")

    entity_post = frontmatter.load(result.entity_paths[0])
    assert entity_post.content.startswith(merged_content)
    assert "[[index]]" in entity_post.content
    assert "[[earlier-source]]" in entity_post.content
    assert f"[[{source_slug}]]" in entity_post.content
    assert set(entity_post.metadata["sources"]) == {"earlier-source", source_slug}
    assert set(entity_post.metadata["tags"]) == {"person", "mathematician"}
    assert len(scripted.calls) == 3  # summary + extraction + merge

    # The Related block must never reach the merge LLM as "existing
    # content" -- otherwise it could be mangled or duplicated.
    merge_call_content = scripted.calls[2]["messages"][1]["content"]
    assert "llm-wiki:related" not in merge_call_content

    # The merge path also upserts the notes row (not just a fresh insert).
    row = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE path = 'wiki/entities/ada-lovelace.md'"
    ).fetchone()
    assert row[0] == 1


def test_compile_reports_each_stage_checkpoint_in_order(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    """Powers the GUI's sub-item progress bar (see gui/app.py's
    _on_item_stage) -- must fire exactly these four, in this order, once
    each, for one successful compile.
    """
    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    responses = [
        "## Overview\n\nAda Lovelace pioneered the concept of programmable computing.",
        _extraction_response(slug="ada-lovelace", content="Ada Lovelace bio."),
    ]
    client, _ = _make_client(monkeypatch, responses)

    stages: list[CompileStage] = []
    compile_queued_item(
        conn, client, vault_root, item.id, chat_model="test-model", on_stage=stages.append
    )

    assert stages == [
        CompileStage.ATOMIZED,
        CompileStage.EXTRACTED,
        CompileStage.LINKED,
        CompileStage.EMBEDDED,
    ]


def test_compile_stops_reporting_stages_once_it_fails(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    client, _ = _make_client(monkeypatch, responses=[])  # first LLM call raises

    stages: list[CompileStage] = []
    with pytest.raises(CompilationError):
        compile_queued_item(
            conn, client, vault_root, item.id, chat_model="test-model", on_stage=stages.append
        )

    # atomize() never touches the LLM, so it reaches ATOMIZED before the
    # summary call (the first real LLM call) raises and aborts the rest.
    assert stages == [CompileStage.ATOMIZED]


def test_post_compile_maintenance_failure_does_not_fail_the_compile(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    """A sync_links()/rebuild_index()/log-append failure is a maintenance
    hiccup, not an ingestion failure -- it must not flip an otherwise-
    successful item to ERROR (Phase 18).
    """
    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    responses = [
        "## Overview\n\nAda Lovelace pioneered the concept of programmable computing.",
        _extraction_response(slug="ada-lovelace", content="Ada Lovelace bio."),
    ]
    client, _ = _make_client(monkeypatch, responses)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("llm_wiki.compiler.compiler_engine.sync_links", _boom)

    result = compile_queued_item(conn, client, vault_root, item.id, chat_model="test-model")

    assert result.entity_paths  # the compile itself still succeeded
    assert get_queue_item(conn, item.id).status is QueueStatus.COMPLETED


def test_compile_marks_item_error_and_reraises_on_failure(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, source_file: Path
) -> None:
    item = enqueue_file(conn, vault_root, source_file, title="Ada Lovelace Biography")
    # No scripted responses -- the first LLM call will raise.
    client, _ = _make_client(monkeypatch, responses=[])

    with pytest.raises(CompilationError):
        compile_queued_item(conn, client, vault_root, item.id, chat_model="test-model")

    assert get_queue_item(conn, item.id).status is QueueStatus.ERROR
