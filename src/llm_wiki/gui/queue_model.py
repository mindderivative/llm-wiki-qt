"""Ingestion queue list model, exposed to QML (Phase 15b)."""

import sqlite3

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Slot
from PySide6.QtQml import QmlElement

from llm_wiki.ingest import list_queue

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class QueueListModel(QAbstractListModel):
    """Read-only view of the `queue` table for the Queue & Raw List panel."""

    IdRole = Qt.ItemDataRole.UserRole + 1
    TitleRole = Qt.ItemDataRole.UserRole + 2
    StatusRole = Qt.ItemDataRole.UserRole + 3
    RawPathRole = Qt.ItemDataRole.UserRole + 4
    ErrorRole = Qt.ItemDataRole.UserRole + 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._conn: sqlite3.Connection | None = None
        self._items = []

    def set_connection(self, conn: sqlite3.Connection | None) -> None:
        """Points this model at a vault's DB connection and reloads."""
        self._conn = conn
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Re-reads the queue table from the current connection."""
        self.beginResetModel()
        self._items = list_queue(self._conn) if self._conn is not None else []
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        item = self._items[index.row()]
        if role == self.IdRole:
            return item.id
        if role == self.TitleRole:
            return item.title
        if role == self.StatusRole:
            return item.status.value
        if role == self.RawPathRole:
            return str(item.raw_path)
        if role == self.ErrorRole:
            return item.error or ""
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.IdRole: b"itemId",
            self.TitleRole: b"title",
            self.StatusRole: b"status",
            self.RawPathRole: b"rawPath",
            self.ErrorRole: b"error",
        }
