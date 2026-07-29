"""Pipeline Log panel: a scrolling, colour-coded view of loguru output."""

import contextlib

import flet as ft

from llm_wiki.gui import theme
from llm_wiki.gui.log_bridge import LogLine, subscribe

_LEVEL_COLORS = {
    "TRACE": theme.TEXT_MUTED,
    "DEBUG": theme.TEXT_MUTED,
    "INFO": theme.ACCENT,
    "SUCCESS": theme.STAGE_LINK,
    "WARNING": theme.STAGE_LINT,
    "ERROR": theme.ERROR,
    "CRITICAL": theme.ERROR,
}


def _row(line: LogLine) -> ft.Control:
    colour = _LEVEL_COLORS.get(line.level, theme.TEXT_LOG)
    return ft.Container(
        padding=ft.Padding(10, 3, 10, 3),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.CANVAS_DOT)),
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Text(line.time, size=11, color=theme.TEXT_MUTED, font_family="monospace"),
                ft.Text(
                    line.level,
                    size=11,
                    color=colour,
                    weight=ft.FontWeight.W_700,
                    width=52,
                    font_family="monospace",
                ),
                ft.Text(
                    line.message,
                    size=11,
                    color=theme.TEXT_LOG,
                    expand=True,
                    font_family="monospace",
                ),
            ],
        ),
    )


class LogPanel(ft.Container):
    """Accumulates loguru records fed via `gui.log_bridge`."""

    def __init__(self) -> None:
        super().__init__()
        self.expand = True
        self.lines: list[LogLine] = []
        self._body = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, auto_scroll=True, expand=True)
        self.content = self._body
        subscribe(self._append)

    def _append(self, line: LogLine) -> None:
        self.lines.append(line)
        self._body.controls.append(_row(line))
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    def clear(self) -> None:
        self.lines.clear()
        self._body.controls.clear()
        with contextlib.suppress(RuntimeError):
            self.update()
