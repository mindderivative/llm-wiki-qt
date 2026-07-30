"""Manages the MCP server in-process, on a background thread (Phase 20).

Originally a subprocess (`python -m llm_wiki.mcp.server`), which broke
inside a `flet build` bundle: that bundle embeds Python via the
`serious_python` Flutter plugin rather than a standalone `python3`
executable, so `sys.executable` resolves to the app's own compiled
binary -- spawning it just re-launched the whole packaged app as a
second window instead of running the MCP server module. Running the
server as a thread inside this same process sidesteps the problem
entirely: there's no subprocess, so no re-exec to go wrong.

`streamable-http` only, not `stdio` -- `stdio` structurally cannot work
as a "click Start, it becomes a long-running background service" model
(it needs the *client* to spawn the process, not the reverse); that
transport stays fully available via `mcp/server.py`'s own CLI, for an
external MCP client to spawn directly, independent of this class.
"""

import asyncio
import threading
import time
from pathlib import Path

import uvicorn
from loguru import logger

from llm_wiki.mcp.server import create_mcp_server


class McpProcess:
    """Starts/stops/restarts the MCP server as a managed background thread."""

    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, vault_root: Path | str, *, host: str = "127.0.0.1", port: int = 8000) -> None:
        """No-op if already running -- call `stop()` (or `restart()`) instead."""
        if self.running:
            return

        mcp = create_mcp_server(vault_root, host=host, port=port)
        app = mcp.streamable_http_app()
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                loop.run_until_complete(server.serve())
            finally:
                loop.close()

        self._server = server
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        # `server.started` (not a readiness event set before `serve()` is
        # even called) -- uvicorn only flips this once the listening socket
        # is genuinely bound, at the end of its own startup() coroutine.
        deadline = time.monotonic() + 5.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)

        logger.info(f"MCP server started (streamable-http on {host}:{port})")

    def stop(self, *, timeout: float = 5.0) -> None:
        if self._thread is None:
            return
        if self._loop is not None and self._server is not None:
            # The thread-safe way to touch another thread's event loop --
            # uvicorn.Server.main_loop() polls `should_exit` each tick and
            # unwinds gracefully, closing its listening socket before
            # serve() returns.
            self._loop.call_soon_threadsafe(setattr, self._server, "should_exit", True)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            # No subprocess to SIGKILL here -- a thread that won't stop can
            # only be waited on longer or leaked, so this is the honest
            # outcome rather than a silent hang.
            logger.error("MCP server did not stop within timeout")
        else:
            logger.info("MCP server stopped")
        self._server = None
        self._thread = None
        self._loop = None

    def restart(self, vault_root: Path | str, *, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.stop()
        self.start(vault_root, host=host, port=port)
