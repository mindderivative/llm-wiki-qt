"""Phase 16d (follow-up): the GUI's ingest entry points -- "+ Add File...",
"Check Raw", and the `auto_watch_raw` Settings toggle backed by
`RawWatcher`. Together these are the missing half of what the CLI's
`llm-wiki ingest` does in one shot (enqueue_file() + compile): the toolbar's
Run/Step/Automated controls already handle the compile half (Phase 16c);
these are what actually gets something into the queue in the first place.

Uses the same real-thread-crossing `_FakePage` double as
`test_gui_pipeline.py`/`test_gui_toolbar.py` -- `RawWatcher`'s callback
fires on watchdog's own thread, exactly the kind of cross-thread dispatch
that needs a real event loop behind it, not a synchronous stub.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from llm_wiki.gui.app import Shell
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
    """See `test_gui_pipeline.py`'s `_FakePage` for why this crosses a real
    thread boundary rather than stubbing it out. `window` is unused by
    anything exercised here, so it's omitted -- only `_exit()` needs it.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._loop_thread.start()
        self.title = ""
        self.overlay: list[object] = []

    def add(self, *_controls) -> None:
        pass

    def update(self, *_controls) -> None:
        pass

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


def _on_loop(page, fn, *args):
    """Runs `fn(*args)` on the fake page's loop thread and blocks for the
    result -- see `test_gui_toolbar.py::_on_loop` for why this matters:
    `AppController.conn` is created and later read by loop-thread-dispatched
    callbacks, and real Flet control events are always on that thread too.
    """

    async def _call():
        return fn(*args)

    return page.run_task(_call).result(timeout=5)


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


@dataclass
class _FakePickedFile:
    path: str


# --- "+ Add File..." -------------------------------------------------------


def test_add_file_stages_the_picked_files(page, vault_root: Path, tmp_path: Path):
    shell = Shell(page)
    try:
        _on_loop(page, shell.controller.open_vault, vault_root)

        source = tmp_path / "picked.txt"
        source.write_text("hello", encoding="utf-8")

        async def fake_pick_files(**_kwargs):
            return [_FakePickedFile(path=str(source))]

        shell.file_picker.pick_files = fake_pick_files

        page.run_task(shell._add_file, None).result(timeout=5)
        _wait_until(lambda: len(shell.items_panel.queue_items) == 1)

        assert shell.items_panel.queue_items[0].title == "picked"
    finally:
        shell.raw_watcher.stop()  # opening the vault started it (auto_watch_raw defaults on)


def test_add_file_without_a_vault_is_a_no_op(page):
    shell = Shell(page)
    calls = []

    async def fake_pick_files(**_kwargs):
        calls.append(1)
        return []

    shell.file_picker.pick_files = fake_pick_files

    page.run_task(shell._add_file, None).result(timeout=5)

    assert calls == []  # returned before ever opening the picker


# --- "Check Raw" ------------------------------------------------------------


def test_check_raw_queues_files_dropped_in_directly(page, vault_root: Path):
    shell = Shell(page)
    try:
        _on_loop(page, shell.controller.open_vault, vault_root)
        (vault_root / "raw" / "dropped.txt").write_text("hi", encoding="utf-8")

        _on_loop(page, shell._check_raw, None)

        assert [i.title for i in shell.items_panel.queue_items] == ["dropped"]
    finally:
        shell.raw_watcher.stop()  # opening the vault started it (auto_watch_raw defaults on)


def test_check_raw_with_nothing_new_shows_an_error(page, vault_root: Path):
    shell = Shell(page)
    try:
        _on_loop(page, shell.controller.open_vault, vault_root)

        errors = []
        shell._show_error = errors.append

        _on_loop(page, shell._check_raw, None)

        assert errors == ["No new files found in raw/."]
    finally:
        shell.raw_watcher.stop()  # opening the vault started it (auto_watch_raw defaults on)


# --- RawWatcher integration -------------------------------------------------


def test_watcher_starts_automatically_on_vault_open(page, vault_root: Path):
    shell = Shell(page)

    assert shell.controller.settings.vault.auto_watch_raw is True  # the engine default
    assert shell.raw_watcher.running is False

    _on_loop(page, shell.controller.open_vault, vault_root)

    assert shell.raw_watcher.running is True
    shell.raw_watcher.stop()


def test_watcher_picks_up_a_file_dropped_in_outside_the_app(page, vault_root: Path):
    shell = Shell(page)
    _on_loop(page, shell.controller.open_vault, vault_root)

    (vault_root / "raw" / "auto-detected.txt").write_text("hi", encoding="utf-8")

    try:
        _wait_until(lambda: len(shell.items_panel.queue_items) == 1)
        assert shell.items_panel.queue_items[0].title == "auto-detected"
    finally:
        shell.raw_watcher.stop()


def test_disabling_auto_watch_in_settings_stops_the_watcher(page, vault_root: Path):
    shell = Shell(page)
    _on_loop(page, shell.controller.open_vault, vault_root)
    assert shell.raw_watcher.running is True

    shell.controller.settings.vault.auto_watch_raw = False
    _on_loop(page, shell._sync_raw_watcher)

    assert shell.raw_watcher.running is False


def test_exit_stops_the_watcher(page, vault_root: Path):
    shell = Shell(page)
    _on_loop(page, shell.controller.open_vault, vault_root)
    assert shell.raw_watcher.running is True

    shell.mcp_process.stop = lambda: None  # not under test here

    async def _fake_close():
        pass

    shell.page.window = type("W", (), {"close": staticmethod(_fake_close)})()
    shell._exit()

    assert shell.raw_watcher.running is False
