"""Watches a vault's `raw/` directory for files dropped in outside the app
(a file manager, `rsync`, another process) and fires a callback so the
caller can pick them up -- typically via `scan_raw_directory()`.

Plain Python, no GUI framework: a `watchdog.observers.Observer` runs on its
own thread regardless of caller, so `on_change` always fires off the
caller's thread. Debounced, since a single file copy can raise several
raw filesystem events (`created` then `modified`) in quick succession.
"""

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

_DEBOUNCE_SECONDS = 0.5


class _DebouncedHandler(FileSystemEventHandler):
    def __init__(self, on_change: Callable[[], None], delay: float) -> None:
        self._on_change = on_change
        self._delay = delay
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        # `.sources/` is the archive enqueue_file() itself writes to --
        # reacting to those writes would just re-discover what enqueue_file()
        # already queued.
        if ".sources" in Path(event.src_path).parts:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._on_change)
            self._timer.daemon = True
            self._timer.start()

    def cancel_pending(self) -> None:
        """Cancels a debounce timer mid-wait -- otherwise a change seen just
        before `RawWatcher.stop()` still fires `on_change()` up to `delay`
        seconds after the watch was supposedly torn down.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None


class RawWatcher:
    """Starts/stops a debounced watch over one vault's `raw/` directory."""

    def __init__(self, on_change: Callable[[], None], *, delay: float = _DEBOUNCE_SECONDS) -> None:
        self._on_change = on_change
        self._delay = delay
        self._observer: Observer | None = None
        self._handler: _DebouncedHandler | None = None

    @property
    def running(self) -> bool:
        return self._observer is not None

    def start(self, vault_root: Path | str) -> None:
        """No-op if already running -- call `stop()` first to retarget."""
        if self.running:
            return
        raw_dir = Path(vault_root) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        handler = _DebouncedHandler(self._on_change, self._delay)
        observer = Observer()
        observer.schedule(handler, str(raw_dir), recursive=True)
        observer.start()
        self._observer = observer
        self._handler = handler

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5)
        self._observer = None
        if self._handler is not None:
            self._handler.cancel_pending()
            self._handler = None
