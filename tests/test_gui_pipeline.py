"""Phase 15c: the QThread pipeline adapter and toolbar automation wiring.

Runs a real worker QThread against a scripted mocked LLM -- these tests
cross a real thread boundary, so they use qtbot.waitUntil rather than a
manual sleep/processEvents loop.
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from llm_wiki.gui.pipeline_adapter import PipelineAdapter
from llm_wiki.ingest import enqueue_file, get_queue_item
from llm_wiki.llm.client import LlamaClient
from llm_wiki.models import QueueStatus
from llm_wiki.storage import connect
from llm_wiki.vault import create_vault


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(root, "Test Vault", "desc")
    return root


class _ScriptedCompletions:
    """A small artificial delay per call gives tests a real, reliably
    observable window between pipeline items -- with everything mocked,
    a batch can otherwise finish well inside a single event-loop tick,
    making "pause between items" impossible to synchronize on at all.
    """

    def __init__(self, responses: list[str], *, delay: float = 0.05) -> None:
        self._responses = list(responses)
        self._delay = delay

    def create(self, **kwargs):
        time.sleep(self._delay)
        content = self._responses.pop(0) if self._responses else "{}"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))]
        )


def _make_client(monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> LlamaClient:
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    monkeypatch.setattr(
        real_client.chat.completions, "create", _ScriptedCompletions(responses).create
    )

    def fake_embeddings_create(**kwargs):
        vectors = [[0.1] * 768 for _ in kwargs["input"]]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])

    monkeypatch.setattr(real_client.embeddings, "create", fake_embeddings_create)
    return LlamaClient(client=real_client)


def _summary_and_empty_entities() -> list[str]:
    return ["## Overview\n\nA summary.", json.dumps({"entities": []})]


def _enqueue(conn, vault_root: Path, tmp_path: Path, title: str):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(exist_ok=True)
    source_file = upload_dir / f"{title}.txt"
    source_file.write_text(f"Raw content for {title}.", encoding="utf-8")
    return enqueue_file(conn, vault_root, source_file, title=title)


# --- PipelineAdapter (direct, no full QML scene) ---------------------------


def test_step_once_processes_one_item(qapp, qtbot, vault_root: Path, tmp_path: Path, monkeypatch):
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    item = _enqueue(conn, vault_root, tmp_path, "doc-a")
    client = _make_client(monkeypatch, _summary_and_empty_entities())

    adapter = PipelineAdapter()
    adapter.configure(str(vault_root), client, "test-model")
    assert adapter.running is False

    adapter.stepOnce()
    assert adapter.running is True

    qtbot.waitUntil(lambda: not adapter.running, timeout=5000)

    updated = get_queue_item(connect(vault_root / ".llm-wiki" / "db.sqlite3"), item.id)
    assert updated.status is QueueStatus.COMPLETED


def test_start_batch_respects_batch_size(
    qapp, qtbot, vault_root: Path, tmp_path: Path, monkeypatch
):
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    for i in range(3):
        _enqueue(conn, vault_root, tmp_path, f"doc-{i}")
    client = _make_client(monkeypatch, _summary_and_empty_entities() * 2)

    adapter = PipelineAdapter()
    adapter.configure(str(vault_root), client, "test-model")
    adapter.startBatch(2)

    qtbot.waitUntil(lambda: not adapter.running, timeout=5000)

    conn2 = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    statuses = [row["status"] for row in conn2.execute("SELECT status FROM queue").fetchall()]
    assert statuses.count("completed") == 2
    assert statuses.count("queued") == 1


def test_stop_run_halts_the_batch(qapp, qtbot, vault_root: Path, tmp_path: Path, monkeypatch):
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    for i in range(3):
        _enqueue(conn, vault_root, tmp_path, f"doc-{i}")
    client = _make_client(monkeypatch, _summary_and_empty_entities() * 3)

    adapter = PipelineAdapter()
    adapter.configure(str(vault_root), client, "test-model")

    completed = []
    adapter.itemCompleted.connect(completed.append)

    adapter.startBatch(3)
    qtbot.waitUntil(lambda: len(completed) >= 1, timeout=5000)
    adapter.stopRun()
    qtbot.waitUntil(lambda: not adapter.running, timeout=5000)

    conn2 = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    statuses = [row["status"] for row in conn2.execute("SELECT status FROM queue").fetchall()]
    assert statuses.count("queued") >= 1  # at least one item never got started


def test_pause_then_resume_still_completes(
    qapp, qtbot, vault_root: Path, tmp_path: Path, monkeypatch
):
    """Pre-arms pause before starting: with everything mocked, a run can
    finish within a single event-loop tick, so reacting to an
    already-in-flight run (e.g. "pause after item 1 completes") is a race
    the test can't reliably win -- pauseRun() before start is the only
    deterministic way to observe a run genuinely held open.
    """
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    item_a = _enqueue(conn, vault_root, tmp_path, "doc-a")
    item_b = _enqueue(conn, vault_root, tmp_path, "doc-b")
    client = _make_client(monkeypatch, _summary_and_empty_entities() * 2)

    adapter = PipelineAdapter()
    adapter.configure(str(vault_root), client, "test-model")

    adapter.pauseRun()
    assert adapter.paused is True

    adapter.startBatch(2)
    qtbot.wait(150)
    assert adapter.running is True  # held open, paused before the first item
    assert get_queue_item(connect(vault_root / ".llm-wiki" / "db.sqlite3"), item_a.id).status is (
        QueueStatus.QUEUED
    )

    adapter.resumeRun()
    assert adapter.paused is False
    qtbot.waitUntil(lambda: not adapter.running, timeout=5000)

    conn3 = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    assert get_queue_item(conn3, item_a.id).status is QueueStatus.COMPLETED
    assert get_queue_item(conn3, item_b.id).status is QueueStatus.COMPLETED


def test_starting_a_run_while_one_is_active_is_a_no_op(
    qapp, qtbot, vault_root: Path, tmp_path: Path, monkeypatch
):
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    _enqueue(conn, vault_root, tmp_path, "doc-a")
    _enqueue(conn, vault_root, tmp_path, "doc-b")
    client = _make_client(monkeypatch, _summary_and_empty_entities())

    adapter = PipelineAdapter()
    adapter.configure(str(vault_root), client, "test-model")
    adapter.pauseRun()  # keep the first run parked so a second call is unambiguous
    adapter.stepOnce()
    assert adapter.running is True

    adapter.stepOnce()  # ignored -- a run is already active

    adapter.resumeRun()
    qtbot.waitUntil(lambda: not adapter.running, timeout=5000)

    conn2 = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    completed = conn2.execute(
        "SELECT COUNT(*) FROM queue WHERE status = 'completed'"
    ).fetchone()[0]
    assert completed == 1
