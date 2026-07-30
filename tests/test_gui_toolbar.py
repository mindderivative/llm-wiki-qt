"""Phase 16c: the pipeline automation toolbar, MCP server controls, and
the shell's wiring between the pipeline adapter and the status bar.

Toolbar tests build the control directly (see test_gui_shell.py's module
docstring for why -- no live Flutter host in this sandbox). The last
section constructs a real `Shell` against a fake `ft.Page` (the same
thread-crossing double `test_gui_pipeline.py` uses) to confirm the actual
wiring between vault-open, a real pipeline run, and the status bar/panels
works end to end, not just each piece in isolation.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest

from llm_wiki.gui.app import Shell
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.pipeline_adapter import PipelineAdapter
from llm_wiki.gui.toolbar import Toolbar
from llm_wiki.ingest import enqueue_file, get_queue_item
from llm_wiki.llm.client import LlamaClient
from llm_wiki.mcp.process import McpProcess
from llm_wiki.models import CompileStage, QueueStatus
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


class _FakePage:
    """See `test_gui_pipeline.py`'s `_FakePage` -- same real thread-crossing
    double, extended with the handful of no-op attributes `Shell.__init__`
    and its handlers touch (`add`, `title`, `update`, `services`) so a real
    `Shell` can be constructed without a live Flet session.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._loop_thread.start()
        self.title = ""
        self.services: list[object] = []

    def add(self, *_controls) -> None:
        pass

    def update(self, *_controls) -> None:
        pass

    def run_thread(self, handler, *args, **kwargs) -> None:
        threading.Thread(target=handler, args=args, kwargs=kwargs, daemon=True).start()

    def run_task(self, handler, *args, **kwargs):
        return asyncio.run_coroutine_threadsafe(handler(*args, **kwargs), self.loop)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(timeout=2)


@pytest.fixture
def page():
    fake = _FakePage()
    yield fake
    fake.close()


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def _on_loop(page, fn, *args):
    """Runs `fn(*args)` on the fake page's loop thread and blocks for the
    result -- real Flet control events (a button's `on_click`, etc.) are
    always dispatched on the session's event-loop thread (confirmed via
    `Session.dispatch_event`, scheduled with `asyncio.create_task`), the
    same thread `page.run_task()` targets. Calling `open_vault()` or a
    toolbar action directly from the test thread would create a call that
    can't happen in the real app -- `AppController.conn` gets created and
    later read by `page.run_task()`-dispatched pipeline callbacks on
    different threads, and `sqlite3.Connection` objects reject that.
    """

    async def _call():
        return fn(*args)

    return page.run_task(_call).result(timeout=5)


# --- Toolbar (direct) -----------------------------------------------------


def test_toolbar_starts_automated_with_the_run_button() -> None:
    controller = AppController()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), McpProcess())

    assert toolbar.automated is True
    assert toolbar.batch_size == 25


def test_toolbar_manual_mode_disables_the_batch_size_row() -> None:
    controller = AppController()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), McpProcess())

    toolbar._set_mode(False)

    batch_row = toolbar._row.controls[1]
    assert batch_row.visible is False


def test_toolbar_batch_size_change_is_clamped_to_at_least_one() -> None:
    controller = AppController()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), McpProcess())

    toolbar._set_batch_size(SimpleNamespace(control=SimpleNamespace(value="0")))
    assert toolbar.batch_size == 1

    toolbar._set_batch_size(SimpleNamespace(control=SimpleNamespace(value="not a number")))
    assert toolbar.batch_size == 1  # unchanged, not raised


def test_toolbar_on_sync_fires_after_every_action() -> None:
    controller = AppController()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), McpProcess())
    calls = []
    toolbar.on_sync = lambda: calls.append(1)

    toolbar._set_mode(False)
    toolbar._set_mode(True)

    assert len(calls) == 2


def test_toolbar_mcp_start_stop_without_a_vault_is_a_no_op() -> None:
    controller = AppController()
    mcp = McpProcess()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), mcp)

    toolbar.mcp_start()

    assert mcp.running is False


def test_toolbar_mcp_start_stop_with_a_vault(vault_root: Path) -> None:
    controller = AppController()
    controller.open_vault(vault_root)
    mcp = McpProcess()
    toolbar = Toolbar(controller, PipelineAdapter(_UnusedPage()), mcp)

    try:
        toolbar.mcp_start()
        _wait_until(lambda: mcp.running)
        assert mcp.running is True

        toolbar.mcp_stop()
        assert mcp.running is False
    finally:
        mcp.stop()


class _UnusedPage:
    """`PipelineAdapter` needs a `page` at construction but these tests
    never actually start a run -- only its presence is required.
    """


# --- Shell integration (real thread-crossing, no live Flet session) -------


class _ScriptedCompletions:
    def __init__(self, responses: list[str], *, delay: float = 0.05) -> None:
        self._responses = list(responses)
        self._delay = delay

    def create(self, **kwargs):
        time.sleep(self._delay)
        content = self._responses.pop(0) if self._responses else "{}"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))]
        )


def _stub_out_llama_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Shell._on_vault_changed` builds its own `LlamaClient` from settings.

    Rather than patch `openai.OpenAI` at the class level (fragile -- its
    resource classes aren't guaranteed to route every call through the same
    bound method), build one real client with its `chat.completions`/
    `embeddings` methods stubbed on the *instance* -- the same technique
    `test_gui_pipeline.py::_make_client` already uses successfully -- and
    monkeypatch the `LlamaClient` name `app.py` imports so
    `Shell._on_vault_changed`'s own construction call returns it. This
    still exercises the real wiring path end to end; only the LLM at the
    very bottom is swapped out.
    """
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    responses = ["## Overview\n\nA summary.", json.dumps({"entities": []})]
    monkeypatch.setattr(
        real_client.chat.completions, "create", _ScriptedCompletions(responses).create
    )

    def fake_embeddings_create(**kwargs):
        vectors = [[0.1] * 768 for _ in kwargs["input"]]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])

    monkeypatch.setattr(real_client.embeddings, "create", fake_embeddings_create)

    fake_client = LlamaClient(client=real_client)
    monkeypatch.setattr("llm_wiki.gui.app.LlamaClient", lambda **_kwargs: fake_client)


def test_single_item_step_moves_through_all_six_stage_checkpoints(page):
    """A Step (batch_size=1) should sweep the progress bar through six
    equal-width checkpoints -- not one 0%/100% jump -- since a single item
    still has real, separately-reported sub-stages.
    """
    shell = Shell(page)
    shell._on_run_started(25)  # requested batch size -- deliberately not what's used below
    shell._on_batch_size_known(1)  # the actual count run_pipeline() found queued

    shell._on_item_started("doc")
    assert shell.progress_bar.value == pytest.approx(1 / 6)
    assert shell.status_stage.value == "Ingesting"

    shell._on_item_stage("doc", CompileStage.ATOMIZED)
    assert shell.progress_bar.value == pytest.approx(2 / 6)
    assert shell.status_stage.value == "Atomized"

    shell._on_item_stage("doc", CompileStage.EXTRACTED)
    assert shell.progress_bar.value == pytest.approx(3 / 6)
    assert shell.status_stage.value == "Extracted"

    shell._on_item_stage("doc", CompileStage.LINKED)
    assert shell.progress_bar.value == pytest.approx(4 / 6)
    assert shell.status_stage.value == "Linked"

    shell._on_item_stage("doc", CompileStage.EMBEDDED)
    assert shell.progress_bar.value == pytest.approx(5 / 6)
    assert shell.status_stage.value == "Embedded"

    shell._on_item_completed("doc")
    assert shell.progress_bar.value == pytest.approx(1.0)
    assert shell.status_stage.value == "Completed"


def test_batch_progress_combines_completed_items_and_current_item_stage(page):
    """A 2-item Automated batch: the first item's stages move the bar within
    its 0-50% band, completing it lands exactly on 50%, and the second
    item's stages move within the 50-100% band.
    """
    shell = Shell(page)
    shell._on_run_started(2)
    shell._on_batch_size_known(2)  # matches what was requested here, unlike the test above

    shell._on_item_started("doc-a")
    assert shell.progress_bar.value == pytest.approx(1 / 6 / 2)

    shell._on_item_stage("doc-a", CompileStage.EMBEDDED)
    assert shell.progress_bar.value == pytest.approx(5 / 6 / 2)

    shell._on_item_completed("doc-a")
    assert shell.progress_bar.value == pytest.approx(0.5)

    shell._on_item_started("doc-b")
    assert shell.progress_bar.value == pytest.approx(0.5 + 1 / 6 / 2)

    shell._on_item_completed("doc-b")
    assert shell.progress_bar.value == pytest.approx(1.0)


def test_on_run_finished_does_not_blank_the_completed_status(page):
    """Regression: `_on_item_completed` and `_on_run_finished` fire
    back-to-back on the same event-loop thread with no yield between them
    (`PipelineAdapter._worker()` schedules both `run_task()` calls in
    immediate succession), so the old reset-to-Idle/0% in `_on_run_finished`
    could overwrite the completed state before the client ever rendered a
    frame showing it -- the progress bar looked like it "never moved."
    """
    shell = Shell(page)

    shell._on_run_started(1)
    shell._on_batch_size_known(1)
    shell._on_item_started("doc")
    shell._on_item_completed("doc")
    assert shell.status_stage.value == "Completed"
    assert shell.progress_bar.value == 1

    shell._on_run_finished()

    assert shell.status_stage.value == "Completed"
    assert shell.status_file.value == "doc"
    assert shell.progress_bar.value == 1
    assert shell.progress_label.value == "100%"


def test_shell_wires_vault_open_through_a_real_pipeline_run(
    page, vault_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _stub_out_llama_client(monkeypatch)
    shell = Shell(page)

    try:
        _on_loop(page, shell.controller.open_vault, vault_root)
        assert shell.pipeline_adapter._vault_root == vault_root
        assert isinstance(shell.pipeline_adapter._client, LlamaClient)

        conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
        upload_dir = tmp_path / "uploads"
        upload_dir.mkdir()
        source = upload_dir / "doc.txt"
        source.write_text("Raw content.", encoding="utf-8")
        item = enqueue_file(conn, vault_root, source, title="doc")

        stages_seen = []
        real_on_item_stage = shell.pipeline_adapter.on_item_stage
        real_on_item_completed = shell.pipeline_adapter.on_item_completed

        def _spy_on_item_stage(title: str, stage: CompileStage) -> None:
            real_on_item_stage(title, stage)
            stages_seen.append((title, shell.status_stage.value))

        def _spy_on_item_completed(title: str) -> None:
            real_on_item_completed(title)  # Shell's own handler: refreshes the panels
            stages_seen.append((title, shell.status_stage.value))

        shell.pipeline_adapter.on_item_stage = _spy_on_item_stage
        shell.pipeline_adapter.on_item_completed = _spy_on_item_completed

        _on_loop(page, shell.toolbar._set_mode, False)  # Manual -> Step
        _on_loop(page, shell.toolbar._run_or_step, None)

        _wait_until(lambda: not shell.pipeline_adapter.running)

        # Real per-stage progress through an actual compile, not just the
        # old single starting/completed jump.
        assert stages_seen == [
            ("doc", "Atomized"),
            ("doc", "Extracted"),
            ("doc", "Linked"),
            ("doc", "Embedded"),
            ("doc", "Completed"),
        ]

        # Settled state: on_run_finished deliberately leaves the status bar
        # showing the last item's result rather than blanking it back to
        # Idle/0% (see its docstring -- that reset used to race the client
        # out of ever rendering the completed frame at all). get_queue_item
        # confirms the compile itself actually succeeded.
        assert shell.status_stage.value == "Completed"
        assert shell.status_file.value == "doc"
        assert shell.progress_bar.value == 1
        assert get_queue_item(
            connect(vault_root / ".llm-wiki" / "db.sqlite3"), item.id
        ).status is QueueStatus.COMPLETED
        assert shell.items_panel.raw_items  # refreshed post-completion

        # Regression: the graph canvas used to only pick up new nodes/edges
        # on the next vault open or a manual Reindex Vault -- _on_item_completed
        # now re-fetches the graph itself, so this shouldn't need a restart.
        _wait_until(lambda: shell.graph.node_positions)
        assert shell.graph.node_positions
    finally:
        shell.raw_watcher.stop()  # opening the vault started it (auto_watch_raw defaults on)
