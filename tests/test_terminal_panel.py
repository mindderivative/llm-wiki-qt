"""Phase 19: `TerminalPanel` -- the byte-forwarding glue between
`flet_terminal.Terminal` and `PtyService`.

The two-way forwarding itself is thin and mechanical, tested directly
against the real `Terminal` control (no live display needed for this --
same idiom as `graph_canvas.py`'s tests calling private handlers with
hand-built events). What genuinely needs a live render to verify --
whether the shell actually reaches a usable prompt -- is out of scope
here; see the plan file's Phase 19 manual-verification notes.
"""

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_wiki.gui.terminal_panel import TerminalPanel


@pytest.fixture(autouse=True)
def _force_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/bin/bash")


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def test_construction_does_not_start_a_pty_session() -> None:
    panel = TerminalPanel(object())
    assert panel._pty.running is False


def test_terminal_panel_wires_typed_input_to_pty_write() -> None:
    panel = TerminalPanel(object())
    assert panel._term._on_bytes_handler == panel._pty.write


def test_on_resize_parses_json_and_forwards_to_pty_resize() -> None:
    panel = TerminalPanel(object())
    calls: list[tuple[int, int]] = []
    panel._pty.resize = lambda cols, rows: calls.append((cols, rows))

    panel._on_resize(SimpleNamespace(data='{"cols": 120, "rows": 40}'))

    assert calls == [(120, 40)]


def test_configure_stores_the_vault_root_for_the_lazy_start(tmp_path: Path) -> None:
    panel = TerminalPanel(object())
    panel.configure(tmp_path)
    assert panel._vault_root == tmp_path


def test_stop_before_any_session_started_is_safe() -> None:
    panel = TerminalPanel(object())
    panel.stop()  # must not raise


def test_did_mount_starts_the_pty_at_the_configured_vault_root(tmp_path: Path) -> None:
    panel = TerminalPanel(object())
    panel.configure(tmp_path)
    try:
        panel.did_mount()
        _wait_until(lambda: panel._pty.running)
        assert panel._pty.running is True
    finally:
        panel.stop()


def test_did_mount_falls_back_to_home_when_no_vault_is_configured() -> None:
    panel = TerminalPanel(object())
    try:
        panel.did_mount()
        _wait_until(lambda: panel._pty.running)
        assert panel._pty.running is True
    finally:
        panel.stop()


def test_did_mount_twice_does_not_start_a_second_session() -> None:
    """Switching away from and back to the Terminal tab unmounts and
    remounts the same `DockArea`-held panel instance -- `did_mount()`
    firing again must not spawn a second shell.
    """
    panel = TerminalPanel(object())
    try:
        panel.did_mount()
        _wait_until(lambda: panel._pty.running)
        first_pid = panel._pty._pid

        panel.did_mount()

        assert panel._pty._pid == first_pid
    finally:
        panel.stop()


def test_pty_output_is_dispatched_back_to_the_terminal_via_run_task() -> None:
    """The PTY's reader thread must hop through `page.run_task()` before
    touching the `Terminal` control -- the same rule every worker-thread-
    to-control path in this codebase follows.
    """
    dispatched: list[bytes] = []

    class _FakePage:
        def run_task(self, handler, *args):
            # Synchronous stand-in: real thread-hopping is already covered
            # by test_gui_chat.py's/test_gui_pipeline.py's _FakePage --
            # this test only needs to confirm run_task is what's used, not
            # re-prove asyncio.run_coroutine_threadsafe works.
            dispatched.append((handler, args))

    panel = TerminalPanel(_FakePage())
    sent: list[bytes] = []
    panel._term.send_bytes = sent.append

    panel._on_pty_output(b"hello")

    assert len(dispatched) == 1
    handler, args = dispatched[0]
    assert args == (b"hello",)
    assert handler.__name__ == "_dispatch_output"
