"""Phase 15c: run_pipeline()/step_one() batch execution over the queue --
plain Python, no Qt, tested headlessly with a scripted mocked LLM (same
pattern as Phase 9's compiler tests).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from llm_wiki.compiler import run_pipeline, step_one
from llm_wiki.ingest import enqueue_file, list_queue
from llm_wiki.llm.client import LlamaClient
from llm_wiki.models import QueueStatus
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
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    scripted = _ScriptedCompletions(responses)
    monkeypatch.setattr(real_client.chat.completions, "create", scripted.create)

    def fake_embeddings_create(**kwargs):
        vectors = [[0.1] * 768 for _ in kwargs["input"]]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])

    monkeypatch.setattr(real_client.embeddings, "create", fake_embeddings_create)
    return LlamaClient(client=real_client), scripted


def _summary_and_empty_entities() -> list[str]:
    """The 2 scripted responses one compile_queued_item() call consumes when
    the extraction step finds no entities -- no merge call follows.
    """
    return ["## Overview\n\nA summary.", json.dumps({"entities": []})]


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(root, "Test Vault", "desc", recent_vaults_path=tmp_path / "recent.json")
    return root


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / ".llm-wiki" / "db.sqlite3")
    yield connection
    connection.close()


def _enqueue(conn, vault_root: Path, tmp_path: Path, title: str):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    source_file = upload_dir / f"{title}.txt"
    source_file.write_text(f"Raw content for {title}.", encoding="utf-8")
    return enqueue_file(conn, vault_root, source_file, title=title)


def test_run_pipeline_processes_up_to_batch_size(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    items = [_enqueue(conn, vault_root, tmp_path, f"doc-{i}") for i in range(3)]
    responses = _summary_and_empty_entities() * 2  # only 2 of the 3 items get processed
    client, scripted = _make_client(monkeypatch, responses)

    result = run_pipeline(conn, client, vault_root, batch_size=2, chat_model="test-model")

    assert len(result.processed) == 2
    assert {i.id for i in result.processed} == {items[0].id, items[1].id}
    remaining = list_queue(conn, status=QueueStatus.QUEUED)
    assert [i.id for i in remaining] == [items[2].id]


def test_run_pipeline_processes_all_when_batch_size_exceeds_queue(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    _enqueue(conn, vault_root, tmp_path, "doc-a")
    _enqueue(conn, vault_root, tmp_path, "doc-b")
    responses = _summary_and_empty_entities() * 2
    client, _ = _make_client(monkeypatch, responses)

    result = run_pipeline(conn, client, vault_root, batch_size=10, chat_model="test-model")

    assert len(result.processed) == 2
    assert result.stopped_early is False


def test_run_pipeline_should_stop_halts_mid_batch(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    for i in range(3):
        _enqueue(conn, vault_root, tmp_path, f"doc-{i}")
    responses = _summary_and_empty_entities()  # only enough for 1 item
    client, _ = _make_client(monkeypatch, responses)

    calls = {"n": 0}

    def should_stop() -> bool:
        # False for the first item, true from then on -- stops after 1.
        calls["n"] += 1
        return calls["n"] > 1

    result = run_pipeline(
        conn,
        client,
        vault_root,
        batch_size=3,
        chat_model="test-model",
        should_stop=should_stop,
    )

    assert len(result.processed) == 1
    assert result.stopped_early is True


def test_run_pipeline_on_progress_fires_expected_events(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    item = _enqueue(conn, vault_root, tmp_path, "doc-a")
    client, _ = _make_client(monkeypatch, _summary_and_empty_entities())

    events: list[tuple[int, str]] = []
    run_pipeline(
        conn,
        client,
        vault_root,
        batch_size=1,
        chat_model="test-model",
        on_progress=lambda queue_item, event: events.append((queue_item.id, event)),
    )

    assert events == [(item.id, "starting"), (item.id, "completed")]


def test_run_pipeline_partial_batch_failure_does_not_abort_rest(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    item_a = _enqueue(conn, vault_root, tmp_path, "doc-a")
    item_b = _enqueue(conn, vault_root, tmp_path, "doc-b")
    # Delete item A's staged raw file so compiling it fails before any LLM
    # call -- keeps the scripted response count unambiguous for item B.
    (vault_root / item_a.raw_path).unlink()

    client, _ = _make_client(monkeypatch, _summary_and_empty_entities())

    result = run_pipeline(conn, client, vault_root, batch_size=2, chat_model="test-model")

    assert item_a.id in result.errors
    assert result.processed == [item_b]


def test_step_one_processes_exactly_one_item(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    _enqueue(conn, vault_root, tmp_path, "doc-a")
    _enqueue(conn, vault_root, tmp_path, "doc-b")
    client, _ = _make_client(monkeypatch, _summary_and_empty_entities())

    result = step_one(conn, client, vault_root, chat_model="test-model")

    assert len(result.processed) == 1
    assert len(list_queue(conn, status=QueueStatus.QUEUED)) == 1


def test_run_pipeline_should_pause_blocks_then_proceeds(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path, tmp_path: Path
) -> None:
    item = _enqueue(conn, vault_root, tmp_path, "doc-a")
    client, _ = _make_client(monkeypatch, _summary_and_empty_entities())

    calls = {"n": 0}

    def should_pause() -> bool:
        calls["n"] += 1
        return calls["n"] <= 3  # pauses for 3 polls, then lets processing continue

    result = run_pipeline(
        conn,
        client,
        vault_root,
        batch_size=1,
        chat_model="test-model",
        should_pause=should_pause,
    )

    assert result.processed == [item]
    assert calls["n"] >= 4


def test_run_pipeline_with_no_queued_items_returns_empty_result(
    monkeypatch: pytest.MonkeyPatch, conn, vault_root: Path
) -> None:
    client, scripted = _make_client(monkeypatch, [])

    result = run_pipeline(conn, client, vault_root, batch_size=5, chat_model="test-model")

    assert result.processed == []
    assert result.stopped_early is False
    assert scripted.calls == []
