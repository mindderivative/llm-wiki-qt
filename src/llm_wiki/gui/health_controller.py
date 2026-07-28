"""Health Dashboard panel: score + findings-by-kind, wired to `lint.run_lint()`
(Phase 11).
"""

import sqlite3

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from llm_wiki.lint import run_lint
from llm_wiki.models import LintFindingKind

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class HealthController(QObject):
    """Exposes the latest lint report's score and findings breakdown to QML."""

    reportChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._conn: sqlite3.Connection | None = None
        self._score = 100
        self._schema_violations = 0
        self._broken_links = 0
        self._isolated_notes = 0

    def set_connection(self, conn: sqlite3.Connection | None) -> None:
        self._conn = conn
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        """Runs a fresh lint pass (persisted, per Phase 11) and updates the summary."""
        if self._conn is None:
            self._score = 100
            self._schema_violations = self._broken_links = self._isolated_notes = 0
            self.reportChanged.emit()
            return

        report = run_lint(self._conn)
        self._score = report.score
        self._schema_violations = sum(
            1 for f in report.findings if f.kind is LintFindingKind.SCHEMA_VIOLATION
        )
        self._broken_links = sum(
            1 for f in report.findings if f.kind is LintFindingKind.BROKEN_LINK
        )
        self._isolated_notes = sum(
            1 for f in report.findings if f.kind is LintFindingKind.ISOLATED_NOTE
        )
        self.reportChanged.emit()

    @Property(int, notify=reportChanged)
    def score(self) -> int:
        return self._score

    @Property(int, notify=reportChanged)
    def schemaViolations(self) -> int:
        return self._schema_violations

    @Property(int, notify=reportChanged)
    def brokenLinks(self) -> int:
        return self._broken_links

    @Property(int, notify=reportChanged)
    def isolatedNotes(self) -> int:
        return self._isolated_notes
