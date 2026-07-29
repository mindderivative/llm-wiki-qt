"""Manages the MCP server as a subprocess (ARCHITECTURE.md §3/§9).

Plain Python, no GUI framework -- launches `python -m llm_wiki.mcp.server`
as a child process and tracks its liveness via `Popen.poll()`. The GUI's
Start/Stop/Restart toolbar controls are the only place this becomes
UI-flavored.
"""

import subprocess
import sys
from pathlib import Path


class McpProcess:
    """Starts/stops/restarts the MCP server as a managed subprocess."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(
        self,
        vault_root: Path | str,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        transport: str = "stdio",
    ) -> None:
        """No-op if already running -- call `stop()` (or `restart()`) instead."""
        if self.running:
            return
        self._proc = subprocess.Popen(  # noqa: S603 -- fixed argv, no shell, no user input
            [
                sys.executable,
                "-m",
                "llm_wiki.mcp.server",
                "--vault",
                str(vault_root),
                "--transport",
                transport,
                "--host",
                host,
                "--port",
                str(port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self, *, timeout: float = 5.0) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None

    def restart(
        self,
        vault_root: Path | str,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
        transport: str = "stdio",
    ) -> None:
        self.stop()
        self.start(vault_root, host=host, port=port, transport=transport)
