"""Flet desktop app entry point -- the thin UI shell over the stable engine
API (ARCHITECTURE.md, Design Principle 5).

No UI framework is imported anywhere in `llm_wiki`'s engine, CLI, or MCP
packages; this module and its siblings under `llm_wiki.gui` are the only
place Flet appears at all.
"""

import sys

import flet as ft

from llm_wiki.graph import get_graph_data
from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dialogs import build_settings_dialog, build_vault_dialog
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.graph_canvas import GraphCanvas
from llm_wiki.gui.health_panel import HealthPanel
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.splitter import ResizeHandle

LEFT_WIDTH = 280
RIGHT_WIDTH = 320
BOTTOM_HEIGHT = 220
STATUS_HEIGHT = 26


def _placeholder(label: str) -> ft.Control:
    """Stands in for a panel that a later sub-phase fills in."""
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(label, size=12, color=theme.TEXT_MUTED),
    )


class Shell:
    """Builds the window layout and keeps it in sync with the active vault."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.controller = AppController()
        self.controller.subscribe(self._on_vault_changed)

        self.graph = GraphCanvas()
        self.left_dock = DockArea(
            [("Items", _placeholder("Queue & raw items")), ("Git", _placeholder("Git controls"))]
        )
        self.health_panel = HealthPanel()
        self.right_dock = DockArea(
            [("Health", self.health_panel), ("AI Chat", _placeholder("AI chat"))],
            selected=1,
        )
        self.bottom_dock = DockArea([("Pipeline Log", _placeholder("Pipeline log"))])

        self.left_pane = ft.Container(
            width=LEFT_WIDTH,
            content=self.left_dock,
            border=ft.Border.only(right=ft.BorderSide(1, theme.BORDER)),
        )
        self.right_pane = ft.Container(
            width=RIGHT_WIDTH,
            content=self.right_dock,
            border=ft.Border.only(left=ft.BorderSide(1, theme.BORDER)),
        )
        self.bottom_pane = ft.Container(
            height=BOTTOM_HEIGHT,
            content=self.bottom_dock,
            border=ft.Border.only(top=ft.BorderSide(1, theme.BORDER)),
        )

        self.status_file = ft.Text("—", size=11, color=theme.TEXT_STAT)
        self.status_stage = ft.Text("Idle", size=11, color=theme.TEXT_SECONDARY)
        self.menu_container = ft.Container(content=self._build_menu())

        page.add(
            ft.Column(
                spacing=0,
                expand=True,
                controls=[
                    self.menu_container,
                    self._build_toolbar(),
                    self._build_body(),
                    self._build_status_bar(),
                ],
            )
        )

    # --- Layout -------------------------------------------------------------

    def _build_menu(self) -> ft.Control:
        label = (
            f"LLM-Wiki · {self.controller.vault_name}"
            if self.controller.has_vault
            else "LLM-Wiki"
        )
        return build_menu_bar(
            vault_label=label,
            recent_vaults=[str(p) for p in self.controller.recent_vaults()],
            on_new_vault=lambda _e: self._open_vault_dialog(),
            on_open_vault=lambda _e: self._open_vault_dialog(),
            on_open_recent=self._open_recent,
            on_settings=lambda _e: self._open_settings_dialog(),
            on_exit=lambda _e: self._exit(),
            on_zoom_reset=lambda _e: self.graph.zoom_reset(),
            on_toggle_left=lambda _e: self._toggle(self.left_pane),
            on_toggle_right=lambda _e: self._toggle(self.right_pane),
            on_toggle_bottom=lambda _e: self._toggle(self.bottom_pane),
        )

    def _build_toolbar(self) -> ft.Control:
        """Chrome only -- the automation and MCP controls are wired in 16c."""
        return ft.Container(
            height=46,
            bgcolor=theme.CHROME_BG,
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
            padding=ft.Padding(12, 0, 12, 0),
            content=ft.Row(
                spacing=10,
                controls=[ft.Text("Pipeline controls", size=12, color=theme.TEXT_MUTED)],
            ),
        )

    def _build_body(self) -> ft.Control:
        center = ft.Column(
            spacing=0,
            expand=True,
            controls=[
                self.graph,
                ResizeHandle(
                    self.bottom_pane, horizontal=False, sign=-1, min_size=100, max_size=520
                ),
                self.bottom_pane,
            ],
        )
        return ft.Row(
            spacing=0,
            expand=True,
            controls=[
                self.left_pane,
                ResizeHandle(self.left_pane, sign=1, min_size=180, max_size=560),
                center,
                ResizeHandle(self.right_pane, sign=-1, min_size=220, max_size=600),
                self.right_pane,
            ],
        )

    def _build_status_bar(self) -> ft.Control:
        return ft.Container(
            height=STATUS_HEIGHT,
            bgcolor=theme.CHROME_BG,
            border=ft.Border.only(top=ft.BorderSide(1, theme.BORDER)),
            padding=ft.Padding(12, 0, 12, 0),
            content=ft.Row(
                spacing=14,
                controls=[
                    ft.Row(
                        spacing=4,
                        controls=[
                            ft.Text("Processing:", size=11, color=theme.TEXT_SUBTLE),
                            self.status_file,
                        ],
                    ),
                    ft.Row(
                        spacing=4,
                        controls=[
                            ft.Text("Stage:", size=11, color=theme.TEXT_SUBTLE),
                            self.status_stage,
                        ],
                    ),
                ],
            ),
        )

    # --- Behaviour ----------------------------------------------------------

    def _toggle(self, pane: ft.Container) -> None:
        pane.visible = not pane.visible
        self.page.update()

    def _exit(self) -> None:
        """`Window.close()` is a coroutine, so it has to be scheduled rather
        than called -- calling it directly leaves File > Exit doing nothing
        but emitting a never-awaited RuntimeWarning.
        """
        self.page.run_task(self.page.window.close)

    def _on_vault_changed(self) -> None:
        self.menu_container.content = self._build_menu()
        if self.controller.conn is not None:
            self.graph.set_graph(get_graph_data(self.controller.conn))
            self.health_panel.set_connection(self.controller.conn)
        self.page.title = (
            f"LLM-Wiki -- {self.controller.vault_name}"
            if self.controller.has_vault
            else "LLM-Wiki"
        )
        self.page.update()

    def _open_recent(self, path: str) -> None:
        try:
            self.controller.open_vault(path)
        except Exception as exc:
            self._show_error(str(exc))

    def _open_vault_dialog(self) -> None:
        dialog = build_vault_dialog(self.controller, self._close_dialog, self._show_error)
        self.page.show_dialog(dialog)

    def _open_settings_dialog(self) -> None:
        if not self.controller.has_vault:
            self._show_error("No active vault -- open or create one first.")
            return
        dialog = build_settings_dialog(self.controller, self._close_dialog)
        self.page.show_dialog(dialog)

    def _close_dialog(self) -> None:
        self.page.pop_dialog()

    def _show_error(self, message: str) -> None:
        self.page.show_dialog(
            ft.AlertDialog(
                bgcolor=theme.CHROME_BG,
                title=ft.Text("Error", color=theme.TEXT),
                content=ft.Text(message, color=theme.TEXT_LIST),
            )
        )


def main(page: ft.Page) -> None:
    page.title = "LLM-Wiki"
    page.bgcolor = theme.APP_BG
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = theme.build_theme()
    page.padding = 0
    page.spacing = 0
    page.window.width = 1280
    page.window.height = 800
    Shell(page)


def run() -> int:
    ft.run(main)
    return 0


if __name__ == "__main__":
    sys.exit(run())
