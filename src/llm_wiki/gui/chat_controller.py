"""AI Chat panel: a lightweight RAG-style test tool (Phase 15d), per the
plan "for active LLM-Wiki testing" -- not a production feature. Wraps
`llm.chat.ask()` on a worker `QThread` (mirroring `pipeline_adapter.py`)
so a live LLM call never blocks the UI.
"""

from pathlib import Path

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtQml import QmlElement

from llm_wiki.llm import ask
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.storage import connect

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


class _ChatWorkerThread(QThread):
    """Runs one `ask()` call on a worker thread -- same pattern as
    `pipeline_adapter._PipelineWorker`, opening its own SQLite connection
    since connections aren't safe to share across threads.
    """

    answered = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        vault_root: Path,
        client: LlamaClient,
        query: str,
        *,
        chat_model: str,
        embedding_model: str,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._vault_root = vault_root
        self._client = client
        self._query = query
        self._chat_model = chat_model
        self._embedding_model = embedding_model

    def run(self) -> None:
        conn = connect(self._vault_root / ".llm-wiki" / "db.sqlite3")
        try:
            answer = ask(
                conn,
                self._client,
                self._query,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
            )
            self.answered.emit(answer)
        except Exception as exc:  # noqa: BLE001 -- surfaced to QML, not re-raised
            self.failed.emit(str(exc))
        finally:
            conn.close()


@QmlElement
class ChatMessageModel(QAbstractListModel):
    """Chat history: alternating user/assistant messages."""

    RoleRole = Qt.ItemDataRole.UserRole + 1
    ContentRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._messages: list[tuple[str, str]] = []

    def append_message(self, role: str, content: str) -> None:
        self.beginInsertRows(QModelIndex(), len(self._messages), len(self._messages))
        self._messages.append((role, content))
        self.endInsertRows()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._messages)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._messages)):
            return None
        message_role, content = self._messages[index.row()]
        if role == self.RoleRole:
            return message_role
        if role == self.ContentRole:
            return content
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {self.RoleRole: b"role", self.ContentRole: b"content"}


@QmlElement
class ChatController(QObject):
    """Exposes chat message history + `sendMessage()` to QML."""

    busyChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vault_root: Path | None = None
        self._client: LlamaClient | None = None
        self._chat_model = ""
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._messages = ChatMessageModel(self)
        self._worker: _ChatWorkerThread | None = None
        self._busy = False

    def configure(
        self,
        vault_root: str,
        client: LlamaClient,
        chat_model: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        self._vault_root = Path(vault_root)
        self._client = client
        self._chat_model = chat_model
        self._embedding_model = embedding_model

    @Slot(str)
    def sendMessage(self, text: str) -> None:
        if self._busy or not text.strip() or self._vault_root is None or self._client is None:
            return

        self._messages.append_message("user", text)
        self._busy = True
        self.busyChanged.emit()

        self._worker = _ChatWorkerThread(
            self._vault_root,
            self._client,
            text,
            chat_model=self._chat_model,
            embedding_model=self._embedding_model,
            parent=self,
        )
        self._worker.answered.connect(self._on_answered)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_answered(self, answer: str) -> None:
        self._messages.append_message("assistant", answer)

    def _on_failed(self, error: str) -> None:
        self.errorOccurred.emit(error)

    def _on_finished(self) -> None:
        self._busy = False
        self.busyChanged.emit()

    @Property(QObject, constant=True)
    def messages(self) -> ChatMessageModel:
        return self._messages

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy
