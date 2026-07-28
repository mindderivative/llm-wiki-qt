"""A dock area: a tab strip over one visible panel at a time.

The mockup's dock areas use a thin custom tab strip (label + a 2px accent
underline on the active tab) rather than Material's `TabBar`, so the strip
is drawn here instead of restyling `TabBar` into something it isn't.
"""

import contextlib
from collections.abc import Callable

import flet as ft

from llm_wiki.gui import theme


class DockArea(ft.Container):
    """Holds `(title, panel)` pairs and shows one at a time."""

    def __init__(
        self,
        panels: list[tuple[str, ft.Control]],
        *,
        selected: int = 0,
        strip_height: float = 34,
    ) -> None:
        super().__init__()
        self.bgcolor = theme.PANEL_BG
        self.panels = panels
        self.selected = selected
        self._strip_height = strip_height

        self._strip = ft.Row(spacing=0, height=strip_height)
        self._body = ft.Container(expand=True)
        self.content = ft.Column(
            spacing=0,
            expand=True,
            controls=[
                ft.Container(
                    content=self._strip,
                    height=strip_height,
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
                ),
                self._body,
            ],
        )
        self._render()

    def select(self, index: int) -> None:
        self.selected = index
        self._render()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    def _tab(self, index: int, title: str) -> ft.Control:
        active = index == self.selected
        return ft.Container(
            padding=ft.Padding(14, 8, 14, 8),
            border=ft.Border.only(
                bottom=ft.BorderSide(2, theme.ACCENT if active else "#00000000")
            ),
            on_click=self._on_tab_click(index),
            content=ft.Text(
                title,
                size=12,
                color=theme.TEXT if active else theme.TEXT_INACTIVE,
            ),
        )

    def _on_tab_click(self, index: int) -> Callable[[ft.Event], None]:
        def handler(_e: ft.Event) -> None:
            self.select(index)

        return handler

    def _render(self) -> None:
        self._strip.controls = [
            self._tab(i, title) for i, (title, _panel) in enumerate(self.panels)
        ]
        self._body.content = self.panels[self.selected][1] if self.panels else None
