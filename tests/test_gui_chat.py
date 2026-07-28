"""Phase 15d: the AI Chat panel -- ChatController/ChatMessageModel and the
QThread worker wrapping llm.chat.ask().
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

# Importing these registers their @QmlElement types with the QML engine.
import llm_wiki.gui.app_controller  # noqa: E402, F401
import llm_wiki.gui.log_model  # noqa: E402, F401
from llm_wiki.gui.chat_controller import ChatController, ChatMessageModel
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
    fake = SimpleNamespace(embeddings=_FakeEmbeddings(), chat=SimpleNamespace(
        completions=_FakeCompletions(answer)
    ))
    return LlamaClient(client=fake)


# --- ChatMessageModel -------------------------------------------------


def test_message_model_starts_empty(qapp) -> None:
    model = ChatMessageModel()
    assert model.rowCount() == 0


def test_message_model_append_and_roles(qapp) -> None:
    model = ChatMessageModel()
    model.append_message("user", "Hello?")
    model.append_message("assistant", "Hi there.")

    assert model.rowCount() == 2
    assert model.data(model.index(0, 0), ChatMessageModel.RoleRole) == "user"
    assert model.data(model.index(0, 0), ChatMessageModel.ContentRole) == "Hello?"
    assert model.data(model.index(1, 0), ChatMessageModel.RoleRole) == "assistant"


# --- ChatController -----------------------------------------------------


def test_send_message_appends_user_then_assistant_reply(qapp, qtbot, vault_root: Path) -> None:
    controller = ChatController()
    controller.configure(str(vault_root), _make_client("Ada Lovelace wrote it."), "test-model")

    controller.sendMessage("Who wrote the first algorithm?")

    assert controller.messages.rowCount() == 1  # user message appended synchronously
    assert controller.busy is True

    qtbot.waitUntil(lambda: not controller.busy, timeout=5000)

    assert controller.messages.rowCount() == 2
    second_message = controller.messages.index(1, 0)
    assert (
        controller.messages.data(second_message, ChatMessageModel.ContentRole)
        == "Ada Lovelace wrote it."
    )


def test_send_message_ignores_empty_text(qapp, vault_root: Path) -> None:
    controller = ChatController()
    controller.configure(str(vault_root), _make_client(), "test-model")

    controller.sendMessage("   ")

    assert controller.messages.rowCount() == 0
    assert controller.busy is False


def test_send_message_ignored_while_busy(qapp, qtbot, vault_root: Path) -> None:
    controller = ChatController()
    controller.configure(str(vault_root), _make_client(), "test-model")

    controller.sendMessage("First question")
    assert controller.busy is True

    controller.sendMessage("Second question, sent too soon")
    qtbot.waitUntil(lambda: not controller.busy, timeout=5000)

    # Only the first question's user+assistant pair -- the second call was a no-op.
    assert controller.messages.rowCount() == 2


def test_send_message_without_configure_is_a_no_op(qapp) -> None:
    controller = ChatController()

    controller.sendMessage("Anyone there?")

    assert controller.messages.rowCount() == 0
    assert controller.busy is False


def test_chat_worker_failure_surfaces_error_and_resets_busy(
    qapp, qtbot, vault_root: Path
) -> None:
    class _FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("llama-server unreachable")

    fake = SimpleNamespace(
        embeddings=_FakeEmbeddings(), chat=SimpleNamespace(completions=_FailingCompletions())
    )
    controller = ChatController()
    controller.configure(str(vault_root), LlamaClient(client=fake), "test-model")

    errors = []
    controller.errorOccurred.connect(errors.append)

    controller.sendMessage("Will this fail?")
    qtbot.waitUntil(lambda: not controller.busy, timeout=5000)

    assert errors and "llama-server unreachable" in errors[0]
    # The user's message is still recorded; no assistant reply was appended.
    assert controller.messages.rowCount() == 1
