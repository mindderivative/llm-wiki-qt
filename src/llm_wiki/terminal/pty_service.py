"""Spawns a real PTY-backed shell and streams bytes both ways (Phase 19).

`flet_terminal.Terminal` only renders the terminal canvas -- it doesn't
spawn or manage a shell process itself, so this owns that half. Linux-only:
this project only ever builds `flet build linux`.

Uses `pty.fork()`, not `subprocess.Popen()` + `pty.openpty()` -- verified
directly (spawning a real fish shell) that the latter leaves the child
without a proper controlling terminal: fish printed `tcgetpgrp failed`/
`setpgid: Inappropriate ioctl for device` and exited immediately, because
handing an already-open slave fd to `Popen(stdin=slave_fd, ...,
start_new_session=True)` doesn't reliably make the PTY slave the child's
controlling terminal on Linux. `pty.fork()` does this correctly (session
leader + controlling terminal + stdin/stdout/stderr, per its own stdlib
docs) -- confirmed the same repro starts cleanly with it instead.
"""

import contextlib
import os
import pty
import termios
import threading
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger


class PtyService:
    """Owns one PTY-backed shell subprocess, streaming its output via callback."""

    def __init__(
        self, on_output: Callable[[bytes], None], on_error: Callable[[str], None]
    ) -> None:
        self._on_output = on_output
        self._on_error = on_error
        self._master_fd: int | None = None
        self._pid: int | None = None

    @property
    def running(self) -> bool:
        if self._pid is None:
            return False
        try:
            done_pid, _status = os.waitpid(self._pid, os.WNOHANG)
        except ChildProcessError:
            return False  # already reaped
        return done_pid == 0

    def start(self, cwd: Path | str) -> None:
        """No-op if already running -- call `stop()` first to restart.

        Also cleans up a stale handle from a shell that exited on its own
        (e.g. the user typed `exit`) without an intervening `stop()` call,
        so repeated `start()`s never leak a file descriptor.
        """
        if self.running:
            return
        self.stop()

        shell = os.environ.get("SHELL", "/bin/bash")
        try:
            pid, master_fd = pty.fork()
        except OSError as exc:
            logger.error(f"Failed to fork a PTY: {exc}")
            self._on_error(str(exc))
            return

        if pid == 0:
            # Child -- pty.fork() already made us a session leader with the
            # PTY slave as our controlling terminal; just chdir and exec.
            # Any failure here must never return into forked-duplicate
            # Python code, hence os._exit() rather than letting an
            # exception propagate.
            try:
                os.chdir(str(cwd))
                # A GUI-launched app often has no $TERM at all (unlike a
                # process started from a real terminal) -- set it explicitly
                # rather than trust whatever's inherited. xterm-256color,
                # not xterm.dart's actual capability class, but this is what
                # it genuinely supports (verified directly against its own
                # source: it implements a real DA1 response and 256-color
                # rendering) -- a shell that probes capabilities based on
                # $TERM (fish does, at interactive startup) needs an
                # accurate answer to get the full experience rather than
                # silently degrading.
                os.environ["TERM"] = "xterm-256color"
                os.execvp(shell, [shell, "-l"])
            except OSError:
                pass
            os._exit(1)

        self._master_fd = master_fd
        self._pid = pid
        threading.Thread(target=self._read_loop, args=(master_fd,), daemon=True).start()
        logger.info(f"PTY shell started ({shell}, pid {pid}, cwd {cwd})")

    def _read_loop(self, master_fd: int) -> None:
        while self._master_fd == master_fd:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            self._on_output(data)

    def write(self, payload: bytes) -> None:
        if self._master_fd is None:
            return
        # Suppressed: the shell may have exited between the write and the
        # check above.
        with contextlib.suppress(OSError):
            os.write(self._master_fd, payload)

    def resize(self, cols: int, rows: int) -> None:
        if self._master_fd is None:
            return
        with contextlib.suppress(OSError):
            termios.tcsetwinsize(self._master_fd, (rows, cols))

    def stop(self, *, timeout: float = 5.0) -> None:
        master_fd, self._master_fd = self._master_fd, None
        if master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(master_fd)

        pid, self._pid = self._pid, None
        if pid is None:
            return

        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)  # SIGTERM
        if not self._wait_for_exit(pid, timeout):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, 9)  # SIGKILL
            self._wait_for_exit(pid, timeout)
        logger.info("PTY shell stopped")

    def _wait_for_exit(self, pid: int, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                done_pid, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return True  # already reaped
            if done_pid == pid:
                return True
            time.sleep(0.05)
        return False
