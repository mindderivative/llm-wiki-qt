"""Git Controls panel: status/init/stage/commit, wired to vcs.git_engine
(Phase 4). Backend failures (no repo yet, nothing to commit, ...) are
surfaced as `errorOccurred` messages rather than crashing the app -- the
GUI is a thin consumer, per Design Principle 5.
"""

from pathlib import Path

import pygit2
from PySide6.QtCore import Property, QAbstractListModel, QModelIndex, QObject, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

from llm_wiki.vcs import git_engine

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class ChangedFilesModel(QAbstractListModel):
    """Modified + untracked file paths from the latest `git status`."""

    PathRole = Qt.ItemDataRole.UserRole + 1
    KindRole = Qt.ItemDataRole.UserRole + 2

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, str]] = []

    def set_status(self, modified: list[str], untracked: list[str]) -> None:
        self.beginResetModel()
        self._rows = [(p, "modified") for p in modified] + [
            (p, "untracked") for p in untracked
        ]
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        path, kind = self._rows[index.row()]
        if role == self.PathRole:
            return path
        if role == self.KindRole:
            return kind
        return None

    def roleNames(self) -> dict[int, bytes]:
        return {self.PathRole: b"path", self.KindRole: b"kind"}


@QmlElement
class GitController(QObject):
    """Exposes vcs.git_engine's init/status/stage/commit to QML."""

    statusChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vault_path: Path | None = None
        self._branch = ""
        self._clean = True
        self._is_initialized = False
        self._changed_files = ChangedFilesModel(self)

    def set_vault_path(self, path: str) -> None:
        self._vault_path = Path(path) if path else None
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        if self._vault_path is None:
            self._branch, self._clean, self._is_initialized = "", True, False
            self._changed_files.set_status([], [])
            self.statusChanged.emit()
            return

        try:
            result = git_engine.status(self._vault_path)
        except pygit2.GitError:
            self._branch, self._clean, self._is_initialized = "", True, False
            self._changed_files.set_status([], [])
            self.statusChanged.emit()
            return

        self._is_initialized = True
        self._branch = result.branch or ""
        self._clean = result.clean
        self._changed_files.set_status(result.modified, result.untracked)
        self.statusChanged.emit()

    @Slot()
    def initRepo(self) -> None:
        if self._vault_path is None:
            return
        try:
            git_engine.init(self._vault_path)
        except Exception as exc:  # noqa: BLE001 -- surfaced to QML, not re-raised
            self.errorOccurred.emit(str(exc))
            return
        self.refresh()

    @Slot()
    def stageAll(self) -> None:
        if self._vault_path is None:
            return
        try:
            git_engine.stage_all(self._vault_path)
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))
            return
        self.refresh()

    @Slot(str)
    def commit(self, message: str) -> None:
        if self._vault_path is None:
            return
        try:
            git_engine.commit(self._vault_path, message)
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(str(exc))
            return
        self.refresh()

    @Property(str, notify=statusChanged)
    def branch(self) -> str:
        return self._branch

    @Property(bool, notify=statusChanged)
    def clean(self) -> bool:
        return self._clean

    @Property(bool, notify=statusChanged)
    def isInitialized(self) -> bool:
        return self._is_initialized

    @Property(QObject, constant=True)
    def changedFiles(self) -> ChangedFilesModel:
        return self._changed_files
