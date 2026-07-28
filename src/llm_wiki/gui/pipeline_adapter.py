"""`QThread` adapter for `compiler.pipeline_runner.run_pipeline()` (Phase 15c).

The only place in this project Qt threading concepts exist, per
ARCHITECTURE.md §9: a thin wrapper subscribing to the engine's
plain-Python progress callbacks and re-emitting them as Qt signals.
`pause`/`stop` are a `threading.Event` the UI thread sets from the
toolbar's controls; the worker thread polls them exactly as
`run_pipeline()`'s `should_pause`/`should_stop` callables expect.

Each run opens its own SQLite connection on the worker thread rather than
reusing one created on the GUI thread -- `sqlite3.Connection` objects
aren't safe to share across threads.
"""

import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot
from PySide6.QtQml import QmlElement

from llm_wiki.compiler import run_pipeline
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.models import QueueItem
from llm_wiki.storage import connect

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


class _PipelineWorker(QThread):
    """Runs `run_pipeline()` on a worker thread; emits progress as Qt signals."""

    itemStarted = Signal(str)
    itemCompleted = Signal(str)
    itemErrored = Signal(str, str)

    def __init__(
        self,
        vault_root: Path,
        client: LlamaClient,
        *,
        batch_size: int,
        chat_model: str,
        embedding_model: str,
        pause_event: threading.Event,
        stop_event: threading.Event,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._vault_root = vault_root
        self._client = client
        self._batch_size = batch_size
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._pause_event = pause_event
        self._stop_event = stop_event

    def run(self) -> None:
        conn = connect(self._vault_root / ".llm-wiki" / "db.sqlite3")
        try:

            def on_progress(item: QueueItem, event: str) -> None:
                if event == "starting":
                    self.itemStarted.emit(item.title)
                elif event == "completed":
                    self.itemCompleted.emit(item.title)
                elif event == "error":
                    self.itemErrored.emit(item.title, item.error or "compilation failed")

            run_pipeline(
                conn,
                self._client,
                self._vault_root,
                batch_size=self._batch_size,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
                on_progress=on_progress,
                should_pause=self._pause_event.is_set,
                should_stop=self._stop_event.is_set,
            )
        finally:
            conn.close()


@QmlElement
class PipelineAdapter(QObject):
    """QML-facing controller for batch/step pipeline execution."""

    runningChanged = Signal()
    pausedChanged = Signal()
    itemStarted = Signal(str)
    itemCompleted = Signal(str)
    itemErrored = Signal(str, str)
    runFinished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vault_root: Path | None = None
        self._client: LlamaClient | None = None
        self._chat_model = ""
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._worker: _PipelineWorker | None = None
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self._running = False

    def configure(
        self,
        vault_root: str,
        client: LlamaClient,
        chat_model: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        """Points this adapter at a vault + LLM client (called on vault load)."""
        self._vault_root = Path(vault_root)
        self._client = client
        self._chat_model = chat_model
        self._embedding_model = embedding_model

    def _start(self, batch_size: int) -> None:
        if self._running or self._vault_root is None or self._client is None:
            return

        # Note: _pause_event is deliberately NOT cleared here. By the time
        # any run reaches "finished", pause is already false -- either it
        # was resumed to completion, or stopRun() (which also clears pause)
        # ended it. Not clearing it also means pauseRun() can be called
        # before startBatch()/stepOnce() to pre-arm a paused run.
        self._stop_event.clear()
        self._worker = _PipelineWorker(
            self._vault_root,
            self._client,
            batch_size=batch_size,
            chat_model=self._chat_model,
            embedding_model=self._embedding_model,
            pause_event=self._pause_event,
            stop_event=self._stop_event,
            parent=self,
        )
        self._worker.itemStarted.connect(self.itemStarted)
        self._worker.itemCompleted.connect(self.itemCompleted)
        self._worker.itemErrored.connect(self.itemErrored)
        self._worker.finished.connect(self._on_worker_finished)

        self._running = True
        self.runningChanged.emit()
        self._worker.start()

    @Slot(int)
    def startBatch(self, batch_size: int) -> None:
        self._start(batch_size)

    @Slot()
    def stepOnce(self) -> None:
        self._start(1)

    @Slot()
    def pauseRun(self) -> None:
        self._pause_event.set()
        self.pausedChanged.emit()

    @Slot()
    def resumeRun(self) -> None:
        self._pause_event.clear()
        self.pausedChanged.emit()

    @Slot()
    def stopRun(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()  # don't leave a stopped run parked in "paused"

    def _on_worker_finished(self) -> None:
        self._running = False
        self.runningChanged.emit()
        self.runFinished.emit()

    @Property(bool, notify=runningChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=pausedChanged)
    def paused(self) -> bool:
        return self._pause_event.is_set()
