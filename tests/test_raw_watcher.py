"""Phase 16d (follow-up): `RawWatcher` -- debounced filesystem watching over
a vault's `raw/` directory, backing the Settings dialog's "Watch raw/ for
new files" toggle. Uses a real `watchdog` observer against a real temp
directory rather than mocking the filesystem, since the whole point is
confirming real OS-level events actually reach the callback.
"""

import time
from pathlib import Path

import pytest

from llm_wiki.ingest import RawWatcher

_DELAY = 0.15


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "raw" / ".sources").mkdir(parents=True)
    return root


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_not_running_before_start() -> None:
    watcher = RawWatcher(lambda: None)
    assert watcher.running is False


def test_start_creates_raw_dir_if_missing(tmp_path: Path) -> None:
    root = tmp_path / "no-raw-yet"
    root.mkdir()
    watcher = RawWatcher(lambda: None, delay=_DELAY)

    try:
        watcher.start(root)
        assert (root / "raw").is_dir()
    finally:
        watcher.stop()


def test_dropping_a_file_fires_the_callback(vault_root: Path) -> None:
    events = []
    watcher = RawWatcher(lambda: events.append(1), delay=_DELAY)
    try:
        watcher.start(vault_root)

        (vault_root / "raw" / "dropped.txt").write_text("hi", encoding="utf-8")

        _wait_until(lambda: len(events) >= 1)
    finally:
        watcher.stop()


def test_a_burst_of_writes_debounces_to_one_callback(vault_root: Path) -> None:
    events = []
    watcher = RawWatcher(lambda: events.append(1), delay=_DELAY)
    try:
        watcher.start(vault_root)

        for i in range(5):
            (vault_root / "raw" / f"burst-{i}.txt").write_text("x", encoding="utf-8")
            time.sleep(0.02)

        time.sleep(_DELAY * 4)
        assert len(events) == 1
    finally:
        watcher.stop()


def test_writes_inside_dot_sources_are_ignored(vault_root: Path) -> None:
    events = []
    watcher = RawWatcher(lambda: events.append(1), delay=_DELAY)
    try:
        watcher.start(vault_root)

        (vault_root / "raw" / ".sources" / "archived.txt").write_text("x", encoding="utf-8")
        time.sleep(_DELAY * 4)

        assert events == []
    finally:
        watcher.stop()


def test_stop_stops_delivering_events(vault_root: Path) -> None:
    events = []
    watcher = RawWatcher(lambda: events.append(1), delay=_DELAY)
    watcher.start(vault_root)
    watcher.stop()
    assert watcher.running is False

    (vault_root / "raw" / "after-stop.txt").write_text("late", encoding="utf-8")
    time.sleep(_DELAY * 4)

    assert events == []


def test_stop_cancels_a_pending_debounce_timer(vault_root: Path) -> None:
    """Regression: a change seen just before `stop()` armed a debounce timer
    that `stop()` didn't touch -- `on_change()` still fired up to `delay`
    seconds after the watch was supposedly torn down.
    """
    events = []
    watcher = RawWatcher(lambda: events.append(1), delay=_DELAY)
    watcher.start(vault_root)

    (vault_root / "raw" / "just-before-stop.txt").write_text("x", encoding="utf-8")
    watcher.stop()  # immediately, before the debounce delay elapses

    time.sleep(_DELAY * 4)
    assert events == []


def test_stop_without_starting_is_a_no_op() -> None:
    watcher = RawWatcher(lambda: None)
    watcher.stop()  # must not raise
    assert watcher.running is False


def test_start_while_already_running_is_a_no_op(vault_root: Path, tmp_path: Path) -> None:
    watcher = RawWatcher(lambda: None, delay=_DELAY)
    try:
        watcher.start(vault_root)
        first_observer = watcher._observer

        other_root = tmp_path / "other-vault"
        (other_root / "raw").mkdir(parents=True)
        watcher.start(other_root)  # ignored -- still watching vault_root

        assert watcher._observer is first_observer
    finally:
        watcher.stop()
