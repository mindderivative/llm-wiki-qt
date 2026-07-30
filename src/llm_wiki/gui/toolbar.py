"""Pipeline automation + MCP server controls (Phase 16c).

The mockup's Ingest/Atomize/Link/Lint chips dim under Automated mode and
brighten under Manual, implying they're per-stage manual actions -- but
`pipeline_runner.step_one()` only ever runs a `QUEUED` item all the way
through `compile_queued_item()`; there's no engine primitive for running a
single stage in isolation. They're kept as a legend, dimmed/brightened the
same way, rather than wired to an action that doesn't exist.

Pause/resume/stop aren't in the mockup at all -- its progress bar just
animates on a timer, since it's a static prototype. They're added here
anyway: `pipeline_runner.py` already supports them (`should_pause`/
`should_stop`), Phase 15c's QML shipped them, and dropping a working,
tested capability during a UI port isn't a call to make silently.
"""

import contextlib
from collections.abc import Callable

import flet as ft

from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.pipeline_adapter import PipelineAdapter
from llm_wiki.mcp.process import McpProcess

_STAGE_LEGEND = (
    ("Ingest", theme.STAGE_INGEST),
    ("Atomize", theme.STAGE_ATOMIZE),
    ("Link", theme.STAGE_LINK),
    ("Lint", theme.STAGE_LINT),
)

_MCP_STATUS_COLOR = {
    "running": theme.STAGE_LINK,
    "stopped": theme.TEXT_MUTED,
    "restarting": theme.STAGE_LINT,
}


def _chip(label: str, colour: str) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(9, 5, 9, 5),
        bgcolor=theme.CARD_BG,
        border=ft.Border.all(1, theme.BORDER_STRONG),
        border_radius=6,
        content=ft.Row(
            spacing=6,
            controls=[
                ft.Container(width=7, height=7, bgcolor=colour, border_radius=2),
                ft.Text(label, size=12, color=theme.TEXT),
            ],
        ),
    )


def _icon_button(icon: str, on_click) -> ft.IconButton:
    return ft.IconButton(
        icon=icon,
        icon_size=16,
        icon_color=theme.TEXT_LIST,
        disabled=on_click is None,
        on_click=on_click,
        style=ft.ButtonStyle(bgcolor={ft.ControlState.DEFAULT: theme.BUTTON_BG}),
    )


def _divider() -> ft.Control:
    return ft.Container(width=1, height=22, bgcolor=theme.BORDER)


class Toolbar(ft.Container):
    """Automated/manual pipeline controls plus MCP server start/stop/restart."""

    def __init__(
        self,
        controller: AppController,
        pipeline: PipelineAdapter,
        mcp: McpProcess,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.pipeline = pipeline
        self.mcp = mcp
        self.automated = True
        self.batch_size = 25
        # Assigned by the shell: fired after every action, so the bottom
        # status bar's MCP/active-set fields can stay in sync too.
        self.on_sync: Callable[[], None] | None = None

        self.height = 46
        self.bgcolor = theme.CHROME_BG
        self.border = ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER))
        self.padding = ft.Padding(12, 0, 12, 0)

        self._row = ft.Row(spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.content = self._row
        self._render()

    # --- Actions ------------------------------------------------------------

    def _set_mode(self, automated: bool) -> None:
        self.automated = automated
        self._after_action()

    def _run_or_step(self, _e: ft.Event) -> None:
        if self.automated:
            self.pipeline.start_batch(self.batch_size)
        else:
            self.pipeline.step_once()
        self._after_action()

    def _pause(self, _e: ft.Event) -> None:
        self.pipeline.pause_run()
        self._after_action()

    def _resume(self, _e: ft.Event) -> None:
        self.pipeline.resume_run()
        self._after_action()

    def _stop(self, _e: ft.Event) -> None:
        self.pipeline.stop_run()
        self._after_action()

    def _set_batch_size(self, e: ft.Event) -> None:
        with contextlib.suppress(ValueError):
            self.batch_size = max(1, int(e.control.value))

    def mcp_start(self, _e: ft.Event | None = None) -> None:
        if self.controller.vault_path is None:
            return
        settings = self.controller.settings.mcp_server
        self.mcp.start(
            self.controller.vault_path,
            host=settings.host,
            port=settings.port,
        )
        self._after_action()

    def mcp_stop(self, _e: ft.Event | None = None) -> None:
        self.mcp.stop()
        self._after_action()

    def mcp_restart(self, _e: ft.Event | None = None) -> None:
        if self.controller.vault_path is None:
            return
        settings = self.controller.settings.mcp_server
        self.mcp.restart(
            self.controller.vault_path,
            host=settings.host,
            port=settings.port,
        )
        self._after_action()

    # --- Called by the shell whenever pipeline state changes ---------------

    def sync(self) -> None:
        self._after_action()

    def _after_action(self) -> None:
        self._render()
        self._update_if_attached()
        if self.on_sync:
            self.on_sync()

    def _update_if_attached(self) -> None:
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    # --- Rendering ------------------------------------------------------

    def _mode_pill(self, label: str, *, selected_when: bool, on_click) -> ft.Control:
        return ft.Container(
            padding=ft.Padding(12, 5, 12, 5),
            border_radius=5,
            bgcolor=theme.ACCENT if selected_when else "#00000000",
            on_click=on_click,
            content=ft.Text(
                label,
                size=12,
                weight=ft.FontWeight.W_600,
                color=theme.APP_BG if selected_when else theme.TEXT_TOGGLE_OFF,
            ),
        )

    def _render(self) -> None:
        running = self.pipeline.running
        paused = self.pipeline.paused
        has_vault = self.controller.has_vault
        mcp_status = "running" if self.mcp.running else "stopped"

        self._row.controls = [
            ft.Container(
                bgcolor=theme.INPUT_BG,
                border_radius=7,
                padding=2,
                content=ft.Row(
                    spacing=0,
                    controls=[
                        self._mode_pill(
                            "Automated",
                            selected_when=self.automated,
                            on_click=lambda _e: self._set_mode(True),
                        ),
                        self._mode_pill(
                            "Manual",
                            selected_when=not self.automated,
                            on_click=lambda _e: self._set_mode(False),
                        ),
                    ],
                ),
            ),
            ft.Row(
                spacing=6,
                visible=self.automated,
                controls=[
                    ft.Text("Batch size", size=12, color=theme.TEXT_SECONDARY),
                    ft.TextField(
                        value=str(self.batch_size),
                        width=52,
                        text_size=12,
                        keyboard_type=ft.KeyboardType.NUMBER,
                        content_padding=ft.Padding(6, 4, 6, 4),
                        bgcolor=theme.INPUT_BG,
                        border_color=theme.BORDER_STRONG,
                        color=theme.TEXT,
                        on_change=self._set_batch_size,
                        disabled=not has_vault,
                    ),
                ],
            ),
            _divider(),
            ft.Row(
                spacing=4,
                controls=[
                    ft.Container(
                        padding=ft.Padding(11, 6, 11, 6),
                        bgcolor=theme.BUTTON_BG,
                        border=ft.Border.all(1, theme.BORDER_STRONG),
                        border_radius=6,
                        on_click=None if running or not has_vault else self._run_or_step,
                        content=ft.Text(
                            "Run" if self.automated else "Step",
                            size=12,
                            color=theme.TEXT if has_vault and not running else theme.TEXT_MUTED,
                        ),
                    ),
                    _icon_button(
                        ft.Icons.PAUSE,
                        self._pause if running and not paused else None,
                    ),
                    _icon_button(
                        ft.Icons.PLAY_ARROW,
                        self._resume if running and paused else None,
                    ),
                    _icon_button(ft.Icons.STOP, self._stop if running else None),
                ],
            ),
            _divider(),
            ft.Row(spacing=6, controls=[_chip(label, colour) for label, colour in _STAGE_LEGEND]),
            ft.Container(expand=True),
            ft.Row(
                spacing=8,
                controls=[
                    ft.Text("MCP", size=11, color=theme.TEXT_MUTED),
                    ft.Row(
                        spacing=5,
                        controls=[
                            _text_button("▶ Start", self.mcp_start),
                            _text_button("■ Stop", self.mcp_stop),
                            _text_button("↻ Restart", self.mcp_restart),
                        ],
                    ),
                    ft.Container(
                        padding=ft.Padding(10, 4, 10, 4),
                        bgcolor=theme.CARD_BG,
                        border=ft.Border.all(1, theme.BORDER_STRONG),
                        border_radius=12,
                        content=ft.Row(
                            spacing=6,
                            controls=[
                                ft.Container(
                                    width=7,
                                    height=7,
                                    bgcolor=_MCP_STATUS_COLOR[mcp_status],
                                    border_radius=4,
                                ),
                                ft.Text(
                                    mcp_status.capitalize(),
                                    size=11.5,
                                    color=theme.TEXT_SECONDARY,
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        ]


def _text_button(label: str, on_click) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(9, 5, 9, 5),
        bgcolor=theme.CARD_BG,
        border=ft.Border.all(1, theme.BORDER_STRONG),
        border_radius=5,
        on_click=on_click,
        content=ft.Text(label, size=12, color=theme.TEXT),
    )
