"""Flet desktop app entry point -- the thin UI shell over the stable engine
API (ARCHITECTURE.md, Design Principle 5).

No UI framework is imported anywhere in `llm_wiki`'s engine, CLI, or MCP
packages; this module and its siblings under `llm_wiki.gui` are the only
place Flet appears at all.
"""

import contextlib
import sys

import flet as ft
from loguru import logger

from llm_wiki.graph import get_graph_data
from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dialogs import build_settings_dialog, build_vault_dialog
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.git_panel import GitPanel
from llm_wiki.gui.graph_canvas import GraphCanvas
from llm_wiki.gui.health_panel import HealthPanel
from llm_wiki.gui.items_panel import ItemsPanel
from llm_wiki.gui.log_panel import LogPanel
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.pipeline_adapter import PipelineAdapter
from llm_wiki.gui.splitter import ResizeHandle
from llm_wiki.gui.toolbar import Toolbar
from llm_wiki.ingest import RawWatcher, enqueue_file, scan_raw_directory
from llm_wiki.llm.client import DEFAULT_API_KEY, LlamaClient
from llm_wiki.mcp.process import McpProcess
from llm_wiki.models import LLMWikiError

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
        self.file_picker = ft.FilePicker()
        page.overlay.append(self.file_picker)
        self.raw_watcher = RawWatcher(on_change=self._on_raw_changed)
        self.items_panel = ItemsPanel(on_add_file=self._add_file, on_check_raw=self._check_raw)
        self.git_panel = GitPanel(on_error=self._show_error)
        self.left_dock = DockArea([("Items", self.items_panel), ("Git", self.git_panel)])
        self.health_panel = HealthPanel()
        self.right_dock = DockArea(
            [("Health", self.health_panel), ("AI Chat", _placeholder("AI chat"))],
            selected=1,
        )
        self.log_panel = LogPanel()
        self.bottom_dock = DockArea([("Pipeline Log", self.log_panel)])

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

        self.pipeline_adapter = PipelineAdapter(page)
        self.mcp_process = McpProcess()
        self.toolbar = Toolbar(self.controller, self.pipeline_adapter, self.mcp_process)
        self.toolbar.on_sync = self._sync_status_bar
        self._batch_total = 0
        self._batch_done = 0
        self._wire_pipeline_events()

        self.status_file = ft.Text("—", size=11, color=theme.TEXT_STAT)
        self.status_stage = ft.Text("Idle", size=11, color=theme.TEXT_SECONDARY)
        self.progress_bar = ft.ProgressBar(
            value=0, width=120, height=5, border_radius=3, bgcolor=theme.INPUT_BG
        )
        self.progress_label = ft.Text("0%", size=11, color=theme.TEXT_SUBTLE)
        self.mcp_status_text = ft.Text("MCP: Stopped", size=11, color=theme.TEXT_SUBTLE)
        self.active_set_text = ft.Text("", size=11, color=theme.TEXT_SUBTLE)
        self.menu_container = ft.Container(content=self._build_menu())

        page.add(
            ft.Column(
                spacing=0,
                expand=True,
                controls=[
                    self.menu_container,
                    self.toolbar,
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
                    self.progress_bar,
                    self.progress_label,
                    ft.Container(expand=True),
                    self.mcp_status_text,
                    self.active_set_text,
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
        self.mcp_process.stop()  # don't leave an orphaned subprocess behind
        self.raw_watcher.stop()
        self.page.run_task(self.page.window.close)

    def _on_vault_changed(self) -> None:
        self.menu_container.content = self._build_menu()
        if self.controller.conn is not None:
            self.graph.set_graph(get_graph_data(self.controller.conn))
            self.health_panel.set_connection(self.controller.conn)
            self.items_panel.set_connection(self.controller.conn)
        self.git_panel.set_vault_path(self.controller.vault_path)

        llm = self.controller.settings.llm_provider
        client = LlamaClient(base_url=llm.base_url, api_key=llm.api_key or DEFAULT_API_KEY)
        self.pipeline_adapter.configure(self.controller.vault_path, client, llm.chat_model)
        self._sync_raw_watcher()

        self.page.title = (
            f"LLM-Wiki -- {self.controller.vault_name}"
            if self.controller.has_vault
            else "LLM-Wiki"
        )
        self.toolbar.sync()
        self.page.update()

    def _sync_raw_watcher(self) -> None:
        """(Re)targets the watcher at the active vault, respecting the
        Settings dialog's "Watch raw/ for new files" toggle. Called on
        every vault change and after Settings is saved, so flipping the
        toggle takes effect without reopening the vault.
        """
        self.raw_watcher.stop()
        if self.controller.has_vault and self.controller.settings.vault.auto_watch_raw:
            self.raw_watcher.start(self.controller.vault_path)

    def _on_raw_changed(self) -> None:
        """`RawWatcher`'s callback -- fires on watchdog's own thread, so it
        has to hop back to the UI/event-loop thread via `run_task()` before
        touching `AppController.conn`, same as `pipeline_adapter.py`.
        """
        self.page.run_task(self._dispatch_raw_changed)

    async def _dispatch_raw_changed(self) -> None:
        if self.controller.conn is None or self.controller.vault_path is None:
            return
        discovered = scan_raw_directory(self.controller.conn, self.controller.vault_path)
        if discovered:
            self.items_panel.refresh()

    async def _add_file(self, _e: ft.Event) -> None:
        """Stages one or more picked files into the queue -- the missing half
        of what the CLI's `llm-wiki ingest` does in one shot (it also
        immediately compiles; here that's a separate step, via the toolbar's
        already-working Run/Step controls).
        """
        if self.controller.conn is None or self.controller.vault_path is None:
            return
        files = await self.file_picker.pick_files(
            dialog_title="Select documents to ingest", allow_multiple=True
        )
        for picked in files:
            try:
                enqueue_file(self.controller.conn, self.controller.vault_path, picked.path)
            except LLMWikiError as exc:
                self._show_error(str(exc))
        if files:
            self.items_panel.refresh()

    def _check_raw(self, _e: ft.Event) -> None:
        """Queues any files sitting in `raw/` that aren't tracked yet -- e.g.
        dropped in directly through a file manager rather than picked here.
        """
        if self.controller.conn is None or self.controller.vault_path is None:
            return
        discovered = scan_raw_directory(self.controller.conn, self.controller.vault_path)
        if discovered:
            self.items_panel.refresh()
        else:
            self._show_error("No new files found in raw/.")

    # --- Pipeline adapter events (all fire on the UI thread) ----------------

    def _wire_pipeline_events(self) -> None:
        self.pipeline_adapter.on_run_started = self._on_run_started
        self.pipeline_adapter.on_item_started = self._on_item_started
        self.pipeline_adapter.on_item_completed = self._on_item_completed
        self.pipeline_adapter.on_item_errored = self._on_item_errored
        self.pipeline_adapter.on_run_finished = self._on_run_finished
        self.pipeline_adapter.on_state_changed = self.toolbar.sync

    def _on_run_started(self, batch_size: int) -> None:
        self._batch_total = batch_size
        self._batch_done = 0
        self._update_progress()

    def _on_item_started(self, title: str) -> None:
        self.status_file.value = title
        self.status_stage.value = "Processing"
        self.status_stage.color = theme.STAGE_INGEST
        self.page.update()

    def _on_item_completed(self, title: str) -> None:
        self._batch_done += 1
        self.status_stage.value = "Completed"
        self.status_stage.color = theme.STAGE_LINK
        self._update_progress()
        self.items_panel.refresh()
        self.health_panel.refresh()
        self.page.update()

    def _on_item_errored(self, title: str, error: str) -> None:
        self._batch_done += 1
        self.status_file.value = title
        self.status_stage.value = "Error"
        self.status_stage.color = theme.ERROR
        self._update_progress()
        self.items_panel.refresh()
        self.page.update()
        logger.error(f"{title}: {error}")

    def _on_run_finished(self) -> None:
        self.status_file.value = "—"
        self.status_stage.value = "Idle"
        self.status_stage.color = theme.TEXT_SECONDARY
        self.progress_bar.value = 0
        self.progress_label.value = "0%"
        self.page.update()

    def _update_progress(self) -> None:
        fraction = self._batch_done / self._batch_total if self._batch_total else 0
        self.progress_bar.value = fraction
        self.progress_label.value = f"{round(fraction * 100)}%"

    def _sync_status_bar(self) -> None:
        self.mcp_status_text.value = f"MCP: {'Running' if self.mcp_process.running else 'Stopped'}"
        self.active_set_text.value = f"Active set: {self.toolbar.batch_size} notes"
        with contextlib.suppress(RuntimeError):
            self.mcp_status_text.update()
            self.active_set_text.update()

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
        dialog = build_settings_dialog(self.controller, self._close_settings_dialog)
        self.page.show_dialog(dialog)

    def _close_settings_dialog(self) -> None:
        """Distinct from `_close_dialog`: the "Watch raw/" toggle lives in
        Settings, so closing it (Save or Cancel; re-syncing on Cancel is
        just a harmless no-op re-read) is the one point to re-sync the
        watcher without requiring the vault to be reopened.
        """
        self._close_dialog()
        self._sync_raw_watcher()

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
