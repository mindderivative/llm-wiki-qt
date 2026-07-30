"""Flet desktop app entry point -- the thin UI shell over the stable engine
API (ARCHITECTURE.md, Design Principle 5).

No UI framework is imported anywhere in `llm_wiki`'s engine, CLI, or MCP
packages; this module and its siblings under `llm_wiki.gui` are the only
place Flet appears at all.
"""

import contextlib
import sys

import flet as ft

from llm_wiki.graph import get_graph_data
from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.chat_panel import ChatPanel
from llm_wiki.gui.dashboard_panel import DashboardPanel
from llm_wiki.gui.dialogs import (
    build_new_vault_dialog,
    build_open_vault_dialog,
    build_settings_dialog,
)
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.git_panel import GitPanel
from llm_wiki.gui.graph_canvas import GraphCanvas, GraphDisplaySettings, GraphFilterState
from llm_wiki.gui.health_panel import HealthPanel
from llm_wiki.gui.items_panel import ItemsPanel
from llm_wiki.gui.log_panel import LogPanel
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.pipeline_adapter import PipelineAdapter
from llm_wiki.gui.splitter import ResizeHandle
from llm_wiki.gui.terminal_panel import TerminalPanel
from llm_wiki.gui.toolbar import Toolbar
from llm_wiki.ingest import RawWatcher, enqueue_file, scan_raw_directory
from llm_wiki.llm.client import DEFAULT_API_KEY, LlamaClient
from llm_wiki.mcp.process import McpProcess
from llm_wiki.models import CompileStage, LLMWikiError
from llm_wiki.vault import reindex_vault

LEFT_WIDTH = 280
RIGHT_WIDTH = 320
BOTTOM_HEIGHT = 220
STATUS_HEIGHT = 26

# Sub-item progress: "starting" (labeled "Ingesting") through each
# CompileStage checkpoint to "completed" -- 6 equal-width steps, so the
# progress bar moves within a single item's compile, not just per item.
_TOTAL_STAGES = 6
_STAGE_LABELS = {
    CompileStage.ATOMIZED: "Atomized",
    CompileStage.EXTRACTED: "Extracted",
    CompileStage.LINKED: "Linked",
    CompileStage.EMBEDDED: "Embedded",
}
_STAGE_INDEX = {
    CompileStage.ATOMIZED: 2,
    CompileStage.EXTRACTED: 3,
    CompileStage.LINKED: 4,
    CompileStage.EMBEDDED: 5,
}


class Shell:
    """Builds the window layout and keeps it in sync with the active vault."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.controller = AppController()
        self.controller.subscribe(self._on_vault_changed)

        self.graph = GraphCanvas(
            page,
            on_settings_panel_toggled=self._on_graph_settings_panel_toggled,
            on_filters_changed=self._on_graph_filters_changed,
            on_display_settings_changed=self._on_graph_display_settings_changed,
        )
        self.file_picker = ft.FilePicker()
        # FilePicker is a Service, not a visual Control -- it belongs on
        # page.services (attached to the root view's service lifecycle), not
        # page.overlay (for on-screen controls like SnackBar). Putting it on
        # overlay renders it as an unrecognized widget: "Unknown Control
        # FilePicker".
        page.services.append(self.file_picker)
        self.raw_watcher = RawWatcher(on_change=self._on_raw_changed)
        self.items_panel = ItemsPanel(on_add_file=self._add_file, on_check_raw=self._check_raw)
        self.git_panel = GitPanel(on_error=self._show_error)
        self.left_dock = DockArea([("Items", self.items_panel), ("Git", self.git_panel)])
        self.health_panel = HealthPanel()
        self.chat_panel = ChatPanel(page)
        self.dashboard_panel = DashboardPanel()
        self.right_dock = DockArea(
            [
                ("Health", self.health_panel),
                ("AI Chat", self.chat_panel),
                ("Dashboard", self.dashboard_panel),
            ],
            selected=1,
        )
        self.log_panel = LogPanel()
        self.terminal_panel = TerminalPanel(page)
        self.bottom_dock = DockArea(
            [("Pipeline Log", self.log_panel), ("Terminal", self.terminal_panel)]
        )

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
            on_new_vault=lambda _e: self._open_new_vault_dialog(),
            on_open_vault=lambda _e: self._open_open_vault_dialog(),
            on_open_recent=self._open_recent,
            on_settings=lambda _e: self._open_settings_dialog(),
            on_exit=lambda _e: self._exit(),
            on_reindex_vault=self._reindex_vault,
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
        self.terminal_panel.stop()
        self.page.run_task(self.page.window.close)

    def _on_vault_changed(self) -> None:
        self.menu_container.content = self._build_menu()

        # Post-25 fix: synced *before* set_graph() below, not after --
        # node_spacing is a genuine layout input (unlike Phase 25's
        # colors/physics/zoom-invert, all render/drag-time only), so the
        # vault's *first* layout must already see the persisted spacing
        # rather than computing once at the default and immediately
        # relaying out again once this sync runs.
        self.graph.set_settings_panel_expanded(
            self.controller.settings.graph_view.settings_panel_expanded
        )
        gv = self.controller.settings.graph_view
        self.graph.set_filters(
            GraphFilterState(
                types=frozenset(gv.filter_types),
                tags=frozenset(gv.filter_tags),
                search=gv.filter_search,
                date_from=gv.filter_date_from,
                date_to=gv.filter_date_to,
                degrees=gv.filter_degrees,
                filters_enabled=gv.filters_enabled,
                types_enabled=gv.filter_types_enabled,
                tags_enabled=gv.filter_tags_enabled,
                search_enabled=gv.filter_search_enabled,
                date_enabled=gv.filter_date_enabled,
                degrees_enabled=gv.filter_degrees_enabled,
            )
        )
        self.graph.set_display_settings(
            GraphDisplaySettings(
                type_colors=dict(gv.type_colors),
                simulation_enabled=gv.simulation_enabled,
                simulation_strength=gv.simulation_strength,
                invert_scroll_zoom=gv.invert_scroll_zoom,
                min_zoom=gv.min_zoom,
                max_zoom=gv.max_zoom,
                node_spacing=gv.node_spacing,
            )
        )

        if self.controller.conn is not None:
            self.graph.set_graph(get_graph_data(self.controller.conn))
            self.health_panel.set_connection(self.controller.conn)
            self.dashboard_panel.set_connection(self.controller.conn, self.controller.vault_path)
            self.items_panel.set_connection(self.controller.conn)
        self.git_panel.set_vault_path(self.controller.vault_path)

        llm = self.controller.settings.llm_provider
        client = LlamaClient(base_url=llm.base_url, api_key=llm.api_key or DEFAULT_API_KEY)
        self.pipeline_adapter.configure(self.controller.vault_path, client, llm.chat_model)
        self.chat_panel.configure(self.controller.vault_path, client, llm.chat_model)
        self.terminal_panel.configure(self.controller.vault_path)
        self._sync_raw_watcher()

        self.page.title = (
            f"LLM-Wiki -- {self.controller.vault_name}"
            if self.controller.has_vault
            else "LLM-Wiki"
        )
        self.toolbar.sync()
        self.page.update()

    def _on_graph_settings_panel_toggled(self, expanded: bool) -> None:
        """Persists the graph canvas Settings panel's expand/collapse state
        (Phase 23) -- a no-op before any vault is open, since
        `save_settings()` requires a vault path.
        """
        if not self.controller.has_vault:
            return
        self.controller.settings.graph_view.settings_panel_expanded = expanded
        self.controller.save_settings()

    def _on_graph_filters_changed(self, state: GraphFilterState) -> None:
        """Persists the graph canvas Filters (Phase 24) -- a no-op before
        any vault is open, mirroring `_on_graph_settings_panel_toggled`.
        """
        if not self.controller.has_vault:
            return
        gv = self.controller.settings.graph_view
        gv.filter_types = sorted(state.types)
        gv.filter_tags = sorted(state.tags)
        gv.filter_search = state.search
        gv.filter_date_from = state.date_from
        gv.filter_date_to = state.date_to
        gv.filter_degrees = state.degrees
        gv.filters_enabled = state.filters_enabled
        gv.filter_types_enabled = state.types_enabled
        gv.filter_tags_enabled = state.tags_enabled
        gv.filter_search_enabled = state.search_enabled
        gv.filter_date_enabled = state.date_enabled
        gv.filter_degrees_enabled = state.degrees_enabled
        self.controller.save_settings()

    def _on_graph_display_settings_changed(self, state: GraphDisplaySettings) -> None:
        """Persists the graph canvas Colors/Physics/Zoom-Pan (Phase 25) --
        a no-op before any vault is open, mirroring
        `_on_graph_filters_changed`.
        """
        if not self.controller.has_vault:
            return
        gv = self.controller.settings.graph_view
        gv.type_colors = dict(state.type_colors)
        gv.simulation_enabled = state.simulation_enabled
        gv.simulation_strength = state.simulation_strength
        gv.invert_scroll_zoom = state.invert_scroll_zoom
        gv.min_zoom = state.min_zoom
        gv.max_zoom = state.max_zoom
        gv.node_spacing = state.node_spacing
        self.controller.save_settings()

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

    def _reindex_vault(self, _e: ft.Event) -> None:
        """Backfills every note's Related block, re-syncs the link graph,
        and regenerates index.md -- the retroactive fix for a vault
        compiled before Phase 18, or otherwise out of sync.
        """
        if self.controller.conn is None or self.controller.vault_path is None:
            self._show_error("No active vault -- open or create one first.")
            return
        try:
            reindex_vault(self.controller.conn, self.controller.vault_path)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not re-raised
            self._show_error(str(exc))
            return
        self.graph.set_graph(get_graph_data(self.controller.conn))
        self.health_panel.set_connection(self.controller.conn)
        self.dashboard_panel.set_connection(self.controller.conn, self.controller.vault_path)
        self.items_panel.refresh()
        self.page.update()

    # --- Pipeline adapter events (all fire on the UI thread) ----------------

    def _wire_pipeline_events(self) -> None:
        self.pipeline_adapter.on_run_started = self._on_run_started
        self.pipeline_adapter.on_batch_size = self._on_batch_size_known
        self.pipeline_adapter.on_item_started = self._on_item_started
        self.pipeline_adapter.on_item_stage = self._on_item_stage
        self.pipeline_adapter.on_item_completed = self._on_item_completed
        self.pipeline_adapter.on_item_errored = self._on_item_errored
        self.pipeline_adapter.on_run_finished = self._on_run_finished
        self.pipeline_adapter.on_state_changed = self.toolbar.sync

    def _on_run_started(self, batch_size: int) -> None:
        # _batch_total is deliberately NOT set to batch_size here -- that's
        # what was *requested*, not what's actually queued.
        # on_batch_size (fired moments later, once run_pipeline() has
        # queried the real count) sets it correctly; a 1-item run against
        # the default batch_size=25 used to land at 1/25 = 4% on completion.
        self._batch_total = 0
        self._batch_done = 0
        self._update_progress()

    def _on_batch_size_known(self, count: int) -> None:
        self._batch_total = count
        self._update_progress()

    def _on_item_started(self, title: str) -> None:
        self.status_file.value = title
        self.status_stage.value = "Ingesting"
        self.status_stage.color = theme.STAGE_INGEST
        self._update_progress(stage_index=1)
        self.page.update()

    def _on_item_stage(self, title: str, stage: CompileStage) -> None:
        self.status_file.value = title
        self.status_stage.value = _STAGE_LABELS[stage]
        self.status_stage.color = theme.STAGE_INGEST
        self._update_progress(stage_index=_STAGE_INDEX[stage])
        self.page.update()

    def _on_item_completed(self, title: str) -> None:
        self._batch_done += 1
        self.status_stage.value = "Completed"
        self.status_stage.color = theme.STAGE_LINK
        self._update_progress()
        self.items_panel.refresh()
        self.health_panel.refresh()
        self.dashboard_panel.refresh()
        # compile_queued_item() already ran sync_links() for this item
        # (Phase 18), so the DB reflects the new node/edges immediately --
        # without this, the canvas only picked them up on the next vault
        # open (_on_vault_changed()) or manual Reindex Vault.
        if self.controller.conn is not None:
            self.graph.set_graph(get_graph_data(self.controller.conn))
        self.page.update()

    def _on_item_errored(self, title: str, error: str) -> None:
        # No logger.error() here: compile_queued_item() already logs the
        # failure with more context (which stage, the real exception) --
        # duplicating it at this layer would just double the log line.
        self._batch_done += 1
        self.status_file.value = title
        self.status_stage.value = "Error"
        self.status_stage.color = theme.ERROR
        self._update_progress()
        self.items_panel.refresh()
        self.page.update()

    def _on_run_finished(self) -> None:
        """Deliberately leaves the status bar showing the last item's result
        rather than resetting to Idle/0% -- `on_item_completed`'s
        `page.update()` and this callback's used to fire back-to-back on the
        same event-loop thread with no yield between them (`_worker()`
        schedules both `run_task()` calls immediately in sequence), so the
        completed state was overwritten before the client ever rendered a
        frame with it. The next run's `on_run_started` resets the progress
        bar to 0% when there's actually new work to show progress on.
        """

    def _update_progress(self, stage_index: int = 0) -> None:
        """`stage_index` is how many of `_TOTAL_STAGES` the *current* item
        has reached (0 before it starts). Combined with completed items in
        the batch, so a single Step and a multi-item Automated batch both
        show real sub-item motion instead of one 0%/100% jump per item.
        """
        if not self._batch_total:
            fraction = 0
        else:
            fraction = (self._batch_done + stage_index / _TOTAL_STAGES) / self._batch_total
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

    def _open_new_vault_dialog(self) -> None:
        dialog = build_new_vault_dialog(
            self.controller, self._close_dialog, self._show_error, self.file_picker
        )
        self.page.show_dialog(dialog)

    def _open_open_vault_dialog(self) -> None:
        dialog = build_open_vault_dialog(
            self.controller, self._close_dialog, self._show_error, self.file_picker
        )
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
