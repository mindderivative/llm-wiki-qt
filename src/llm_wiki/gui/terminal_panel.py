"""Terminal panel: a real PTY-backed shell inside the bottom dock (Phase 19).

`flet_terminal.Terminal` only renders the terminal canvas -- `PtyService`
(the actual shell process) is owned here, and bytes are forwarded both
ways: `Terminal.set_on_bytes()` delivers what the user types, forwarded
straight to `PtyService.write()` (already on the event-loop thread, via
Flet's own dispatch -- no `run_task` needed, same as `_on_pan_start`/
`_on_submit` elsewhere); `PtyService`'s background reader thread hops back
via `page.run_task()` before calling `Terminal.send_bytes()`, the same
threading rule every worker-thread-to-control path in this codebase
already follows (`chat_panel.py`, `pipeline_adapter.py`, `graph_canvas.py`).
"""

import json
from pathlib import Path

import flet as ft
from flet_terminal import Terminal
from loguru import logger

from llm_wiki.gui import theme
from llm_wiki.terminal.pty_service import PtyService


def _terminal_theme() -> dict[str, str]:
    """A custom theme matching the app's own palette, not a generic preset
    (`flet_terminal.BUILTIN_THEMES` ships Dracula/JetBrains Dark/Matrix
    Green -- none of them this app's actual colours).
    """
    return {
        "background": theme.TERMINAL_BG,
        "foreground": theme.TEXT,
        "cursor": theme.ACCENT,
        "selection": theme.INPUT_BG,
        "black": theme.PANEL_BG,
        "red": theme.ERROR,
        "green": theme.STAGE_LINK,
        "yellow": theme.STAGE_LINT,
        "blue": "#5B8DEF",  # no direct equivalent elsewhere in theme.py
        "magenta": theme.ACCENT,
        "cyan": theme.STAGE_ATOMIZE,
        "white": theme.TEXT_BRIGHT,
    }


class TerminalPanel(ft.Container):
    """A real shell (`$SHELL`) running inside the bottom dock."""

    def __init__(self, page: ft.Page) -> None:
        super().__init__()
        self._page = page  # NOT self.page -- ft.Container reserves that name
        self.expand = True
        self.bgcolor = theme.TERMINAL_BG
        self._vault_root: Path | None = None

        self._pty = PtyService(on_output=self._on_pty_output, on_error=self._on_pty_error)
        self._term = Terminal(expand=True, theme=_terminal_theme(), font_size=12.5)
        self._term.set_on_bytes(self._pty.write)
        self._term.on_resize = self._on_resize
        self.content = self._term

    def configure(self, vault_root: str | Path) -> None:
        """Points a not-yet-started session at a vault (called on vault load).

        Has no effect on an already-running session -- switching vaults
        deliberately doesn't restart the shell out from under the user,
        same reasoning as `chat_panel.py`/`graph_canvas.py` not tearing
        anything down on a vault switch either.
        """
        self._vault_root = Path(vault_root)

    def did_mount(self) -> None:
        """Starts the shell the first time this tab is actually shown --
        lazily, not at construction, so headless tests that build a
        `TerminalPanel()` without attaching it to a page never spawn a
        real process. `PtyService.start()` is a no-op if already running,
        so switching away from and back to this tab (which unmounts and
        remounts the same panel instance -- `DockArea` never recreates a
        panel, just reattaches it) never starts a second session.
        """
        super().did_mount()
        self._pty.start(self._vault_root or Path.home())

    def stop(self) -> None:
        """Terminates the shell -- wired into `Shell._exit()` alongside
        `McpProcess.stop()`/`RawWatcher.stop()` so no orphaned shell
        process survives the app closing.
        """
        self._pty.stop()

    # --- PTY -> terminal (background thread -> UI thread) --------------------

    def _on_pty_output(self, data: bytes) -> None:
        self._page.run_task(self._dispatch_output, data)

    async def _dispatch_output(self, data: bytes) -> None:
        self._term.send_bytes(data)

    def _on_pty_error(self, message: str) -> None:
        logger.error(f"Terminal panel: {message}")

    # --- Terminal -> PTY (already on the UI thread, via Flet's own dispatch) -

    def _on_resize(self, e: ft.Event) -> None:
        dims = json.loads(e.data)
        self._pty.resize(dims["cols"], dims["rows"])
