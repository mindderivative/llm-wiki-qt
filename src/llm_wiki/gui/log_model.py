"""Pipeline Log panel: loguru -> Qt signal bridge (ARCHITECTURE.md §10).

The engine itself stays Qt-free -- this bridge just subscribes to whatever
`loguru.logger` emits process-wide. A single loguru sink is registered once
(idempotent) and fans out to every `LogModel` instance via a shared Qt
signal, so multiple instances (e.g. across tests) never register duplicate
loguru handlers.
"""

from loguru import logger
from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


class _LogDispatcher(QObject):
    """Process-wide Qt signal source fed by the single registered loguru sink."""

    messageLogged = Signal(str)


_dispatcher = _LogDispatcher()
_handler_id: int | None = None


def _ensure_sink_registered() -> None:
    global _handler_id
    if _handler_id is not None:
        return
    _handler_id = logger.add(
        lambda message: _dispatcher.messageLogged.emit(message.strip()),
        format="{time:HH:mm:ss} | {level: <8} | {message}",
        level="INFO",
    )


@QmlElement
class LogModel(QAbstractListModel):
    """Accumulates loguru records for display in the Pipeline Log panel."""

    MessageRole = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lines: list[str] = []
        _ensure_sink_registered()
        _dispatcher.messageLogged.connect(self._append)

    def _append(self, line: str) -> None:
        self.beginInsertRows(QModelIndex(), len(self._lines), len(self._lines))
        self._lines.append(line)
        self.endInsertRows()

    @Slot()
    def clear(self) -> None:
        self.beginResetModel()
        self._lines.clear()
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._lines)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._lines)):
            return None
        if role in (self.MessageRole, Qt.ItemDataRole.DisplayRole):
            return self._lines[index.row()]
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {self.MessageRole: b"message"}
