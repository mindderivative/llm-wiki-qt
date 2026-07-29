"""Phase 16d: the AI Chat panel -- `ChatPanel`'s worker thread wrapping
`llm.chat.ask()`.

Uses the same real-thread-crossing `_FakePage` double as
`test_gui_pipeline.py`/`test_gui_toolbar.py`: `page.run_thread()` spawns a
real background thread, `page.run_task()` schedules onto a dedicated event
loop thread via `run_coroutine_threadsafe`, exercising the same mechanics
`ChatPanel` actually uses rather than stubbing them out.
"""

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.gui.chat_panel import ChatPanel
from llm_wiki.llm.client import LlamaClient
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
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._loop_thread.start()

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


class _FakeEmbeddings:
    def create(self, **kwargs):
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * 768) for _ in kwargs["input"]]
        )


class _FakeCompletions:
    def __init__(self, answer: str) -> None:
        self._answer = answer

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._answer))]
        )


def _make_client(answer: str = "Here's what I found.") -> LlamaClient:
    fake = SimpleNamespace(
        embeddings=_FakeEmbeddings(), chat=SimpleNamespace(completions=_FakeCompletions(answer))
    )
    return LlamaClient(client=fake)


def test_send_message_appends_user_then_assistant_reply(page, vault_root: Path) -> None:
    panel = ChatPanel(page)
    panel.configure(vault_root, _make_client("Ada Lovelace wrote it."), "test-model")

    panel.send_message("Who wrote the first algorithm?")

    assert panel.messages == [("user", "Who wrote the first algorithm?")]  # synchronous
    assert panel.busy is True

    _wait_until(lambda: not panel.busy)

    assert panel.messages == [
        ("user", "Who wrote the first algorithm?"),
        ("assistant", "Ada Lovelace wrote it."),
    ]
    assert len(panel._message_list.controls) == 2


def test_send_message_ignores_empty_text(page, vault_root: Path) -> None:
    panel = ChatPanel(page)
    panel.configure(vault_root, _make_client(), "test-model")

    panel.send_message("   ")

    assert panel.messages == []
    assert panel.busy is False


def test_send_message_ignored_while_busy(page, vault_root: Path) -> None:
    panel = ChatPanel(page)
    panel.configure(vault_root, _make_client(), "test-model")

    panel.send_message("First question")
    assert panel.busy is True

    panel.send_message("Second question, sent too soon")
    _wait_until(lambda: not panel.busy)

    # Only the first question's user+assistant pair -- the second call was a no-op.
    assert len(panel.messages) == 2
    assert panel.messages[0] == ("user", "First question")


def test_send_message_without_configure_is_a_no_op(page) -> None:
    panel = ChatPanel(page)

    panel.send_message("Anyone there?")

    assert panel.messages == []
    assert panel.busy is False


def test_chat_worker_failure_surfaces_an_inline_error_and_resets_busy(
    page, vault_root: Path
) -> None:
    """Failures show as a chat bubble rather than a modal dialog -- a popup
    per failed query would be far more intrusive here than in the
    single-shot dialogs (`_show_error`) elsewhere in the shell.
    """

    class _FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("llama-server unreachable")

    fake = SimpleNamespace(
        embeddings=_FakeEmbeddings(), chat=SimpleNamespace(completions=_FailingCompletions())
    )
    panel = ChatPanel(page)
    panel.configure(vault_root, LlamaClient(client=fake), "test-model")

    panel.send_message("Will this fail?")
    _wait_until(lambda: not panel.busy)

    assert panel.messages[0] == ("user", "Will this fail?")
    assert panel.messages[1][0] == "assistant"
    assert "llama-server unreachable" in panel.messages[1][1]


def test_input_field_is_cleared_after_sending(page, vault_root: Path) -> None:
    panel = ChatPanel(page)
    panel.configure(vault_root, _make_client(), "test-model")
    panel._input.value = "What's in this vault?"

    panel._on_submit(None)

    assert panel._input.value == ""
    _wait_until(lambda: not panel.busy)


def test_typing_indicator_visible_only_while_busy(page, vault_root: Path) -> None:
    panel = ChatPanel(page)
    panel.configure(vault_root, _make_client(), "test-model")
    assert panel._typing_indicator.visible is False

    panel.send_message("Ping")
    assert panel._typing_indicator.visible is True

    _wait_until(lambda: not panel.busy)
    assert panel._typing_indicator.visible is False
