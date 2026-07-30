"""Phase 19: `PtyService` -- spawns a real PTY-backed shell, not a mock.

Forces `$SHELL=/bin/bash` regardless of the real environment: verified
directly that modern shells with interactive capability-probing at
startup (fish in particular) block indefinitely waiting for terminal
query responses (DA1, XTGETTCAP) that only a real terminal emulator
(`flet_terminal`'s xterm.dart, in the actual app) provides -- bash sends
none of these, so it's the deterministic, fast choice for headless tests.
"""

import os
import time
from pathlib import Path

import pytest

from llm_wiki.terminal.pty_service import PtyService


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


def test_not_running_before_start() -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    assert svc.running is False


def test_start_launches_a_live_shell_and_write_is_echoed_back(tmp_path: Path) -> None:
    chunks: list[bytes] = []
    svc = PtyService(on_output=chunks.append, on_error=lambda m: pytest.fail(m))
    try:
        svc.start(tmp_path)
        _wait_until(lambda: svc.running)

        svc.write(b"echo hello_from_pty\n")
        _wait_until(lambda: b"hello_from_pty" in b"".join(chunks))

        output = b"".join(chunks)
        assert output.count(b"hello_from_pty") >= 1
    finally:
        svc.stop()


def test_resize_does_not_raise_against_a_live_session(tmp_path: Path) -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    try:
        svc.start(tmp_path)
        _wait_until(lambda: svc.running)
        svc.resize(120, 40)  # would raise on failure -- no return value to assert
    finally:
        svc.stop()


def test_resize_and_write_before_start_are_harmless_no_ops() -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    svc.write(b"echo unreachable\n")
    svc.resize(80, 24)  # neither should raise


def test_stop_terminates_the_child_process(tmp_path: Path) -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    svc.start(tmp_path)
    _wait_until(lambda: svc.running)
    pid = svc._pid

    svc.stop()

    assert svc.running is False
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)  # already reaped -- no such child left behind


def test_start_while_already_running_does_not_spawn_a_second_process(tmp_path: Path) -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    try:
        svc.start(tmp_path)
        _wait_until(lambda: svc.running)
        first_pid = svc._pid

        svc.start(tmp_path)

        assert svc._pid == first_pid
    finally:
        svc.stop()


def test_start_after_the_shell_exited_on_its_own_starts_a_fresh_session(tmp_path: Path) -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    try:
        svc.start(tmp_path)
        _wait_until(lambda: svc.running)
        first_pid = svc._pid

        svc.write(b"exit\n")
        _wait_until(lambda: not svc.running)

        svc.start(tmp_path)
        _wait_until(lambda: svc.running)

        assert svc._pid != first_pid
    finally:
        svc.stop()


def test_stop_when_never_started_is_a_no_op() -> None:
    svc = PtyService(on_output=lambda _d: None, on_error=lambda _m: None)
    svc.stop()  # must not raise
    assert svc.running is False
