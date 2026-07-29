"""Background-thread adapter for `compiler.pipeline_runner.run_pipeline()`
(Phase 16c) -- the Flet counterpart to Phase 15c's `QThread` adapter, and
the only place in the GUI layer Flet's threading model is a concern.

`page.run_thread()` runs the batch on a worker thread from the page's
executor. Flet controls are *not* thread-safe to touch directly from
there -- `Page.update()` ultimately does a bare `asyncio.Queue.put_nowait()`
on the connection's send queue, which assumes the event-loop thread.
`page.run_task()` is: it schedules a coroutine via
`asyncio.run_coroutine_threadsafe()`, so progress callbacks hop back onto
the event loop through it before touching any control -- the Flet
equivalent of a QThread's implicitly-queued cross-thread signal emission.

Each run opens its own SQLite connection on the worker thread rather than
reusing one created on the GUI thread -- `sqlite3.Connection` objects
aren't safe to share across threads.
"""

import threading
from collections.abc import Callable
from pathlib import Path

import flet as ft

from llm_wiki.compiler import run_pipeline
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.models import QueueItem
from llm_wiki.storage import connect


class PipelineAdapter:
    """Runs batch/step pipeline execution on a worker thread."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._vault_root: Path | None = None
        self._client: LlamaClient | None = None
        self._chat_model = ""
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()
        self.running = False

        # Assigned by the owning Shell; all fire on the UI (event-loop) thread.
        self.on_run_started: Callable[[int], None] | None = None
        self.on_item_started: Callable[[str], None] | None = None
        self.on_item_completed: Callable[[str], None] | None = None
        self.on_item_errored: Callable[[str, str], None] | None = None
        self.on_run_finished: Callable[[], None] | None = None
        self.on_state_changed: Callable[[], None] | None = None

    def configure(
        self,
        vault_root: str | Path,
        client: LlamaClient,
        chat_model: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        """Points this adapter at a vault + LLM client (called on vault load)."""
        self._vault_root = Path(vault_root)
        self._client = client
        self._chat_model = chat_model
        self._embedding_model = embedding_model

    @property
    def paused(self) -> bool:
        return self._pause_event.is_set()

    # --- Controls -------------------------------------------------------

    def start_batch(self, batch_size: int) -> None:
        self._start(batch_size)

    def step_once(self) -> None:
        self._start(1)

    def pause_run(self) -> None:
        self._pause_event.set()
        self._notify_state_changed()

    def resume_run(self) -> None:
        self._pause_event.clear()
        self._notify_state_changed()

    def stop_run(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()  # don't leave a stopped run parked in "paused"

    def _start(self, batch_size: int) -> None:
        if self.running or self._vault_root is None or self._client is None:
            return

        # Note: _pause_event is deliberately NOT cleared here. By the time
        # any run reaches "finished", pause is already false -- either it
        # was resumed to completion, or stop_run() (which also clears
        # pause) ended it. Not clearing it also means pause_run() can be
        # called before start_batch()/step_once() to pre-arm a paused run.
        self._stop_event.clear()
        self.running = True
        self._notify_state_changed()
        if self.on_run_started:
            self.on_run_started(batch_size)
        self.page.run_thread(self._worker, batch_size)

    # --- Worker thread ------------------------------------------------------

    def _worker(self, batch_size: int) -> None:
        assert self._vault_root is not None
        assert self._client is not None
        conn = connect(self._vault_root / ".llm-wiki" / "db.sqlite3")
        try:

            def on_progress(item: QueueItem, event: str) -> None:
                self.page.run_task(self._dispatch_progress, item.title, item.error, event)

            run_pipeline(
                conn,
                self._client,
                self._vault_root,
                batch_size=batch_size,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
                on_progress=on_progress,
                should_pause=self._pause_event.is_set,
                should_stop=self._stop_event.is_set,
            )
        finally:
            conn.close()
        self.page.run_task(self._dispatch_finished)

    # --- Back on the UI thread, via page.run_task() --------------------------

    async def _dispatch_progress(self, title: str, error: str | None, event: str) -> None:
        if event == "starting" and self.on_item_started:
            self.on_item_started(title)
        elif event == "completed" and self.on_item_completed:
            self.on_item_completed(title)
        elif event == "error" and self.on_item_errored:
            self.on_item_errored(title, error or "compilation failed")

    async def _dispatch_finished(self) -> None:
        self.running = False
        self._notify_state_changed()
        if self.on_run_finished:
            self.on_run_finished()

    def _notify_state_changed(self) -> None:
        if self.on_state_changed:
            self.on_state_changed()
