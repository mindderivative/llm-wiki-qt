"""Phase 16a: the Flet shell -- theme, vault/settings controller, graph
canvas, resize handles, dock tab strips, menu bar, and dialogs.

Flet's own integration testing (`flet test`) provisions a Flutter test host
and needs the Flutter SDK, which isn't available here. Flet controls are
plain dataclasses, though, so the whole layer is exercised by building real
control trees and asserting on their structure -- no display required.
"""

import ast
import asyncio
import inspect
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

import flet as ft
import flet.canvas as cv
import networkx as nx
import pytest

from llm_wiki.gui import app as app_module
from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dialogs import (
    build_new_vault_dialog,
    build_open_vault_dialog,
    build_settings_dialog,
)
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.graph_canvas import GraphCanvas
from llm_wiki.gui.health_panel import HealthPanel
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.splitter import ResizeHandle
from llm_wiki.models import LintFindingKind, LLMWikiError
from llm_wiki.storage import connect
from llm_wiki.vault import create_vault


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(root, "Test Vault", "desc")
    return root


def _drag(handle: ResizeHandle, dx: float, dy: float = 0) -> None:
    handle._on_drag(
        ft.DragUpdateEvent(
            name="pan_update",
            control=handle,
            data=None,
            local_position=ft.Offset(0, 0),
            global_position=ft.Offset(0, 0),
            local_delta=ft.Offset(dx, dy),
            global_delta=ft.Offset(dx, dy),
            primary_delta=None,
            timestamp=0,
        )
    )


# --- Theme ------------------------------------------------------------


def test_every_palette_colour_is_a_valid_hex_string() -> None:
    colours = {
        name: value
        for name, value in vars(theme).items()
        if name.isupper() and isinstance(value, str) and name != "DEFAULT_CATEGORY"
    }
    assert colours, "expected the palette module to expose colour constants"
    for name, value in colours.items():
        assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name}={value}"


def test_category_colours_cover_the_mockups_six_buckets() -> None:
    assert len(theme.CATEGORY_COLORS) == 6
    assert theme.DEFAULT_CATEGORY in theme.CATEGORY_COLORS


# --- AppController ----------------------------------------------------


def test_controller_starts_with_no_active_vault() -> None:
    controller = AppController()
    assert controller.has_vault is False
    assert controller.vault_path is None
    assert controller.conn is None


def test_create_vault_sets_active_vault(tmp_path: Path) -> None:
    controller = AppController()
    controller.create_vault(tmp_path / "new-vault", "New Vault", "a description")

    assert controller.has_vault is True
    assert controller.vault_name == "New Vault"
    assert controller.conn is not None


def test_open_vault_on_a_non_vault_directory_raises(tmp_path: Path) -> None:
    controller = AppController()
    not_a_vault = tmp_path / "empty"
    not_a_vault.mkdir()

    with pytest.raises(LLMWikiError):
        controller.open_vault(not_a_vault)
    assert controller.has_vault is False


def test_subscribers_are_notified_when_the_vault_changes(vault_root: Path) -> None:
    controller = AppController()
    calls: list[str] = []
    controller.subscribe(lambda: calls.append(controller.vault_name))

    controller.open_vault(vault_root)

    assert calls == ["Test Vault"]


def test_settings_round_trip_through_dot_llm_wiki_config(vault_root: Path) -> None:
    controller = AppController()
    controller.open_vault(vault_root)

    controller.settings.llm_provider.chat_model = "qwen3-8b"
    controller.settings.llm_provider.host_port = 9999
    controller.save_settings()

    raw = json.loads((vault_root / ".llm-wiki-config").read_text(encoding="utf-8"))
    assert raw["llm_provider"]["chat_model"] == "qwen3-8b"
    assert raw["llm_provider"]["host_port"] == 9999
    # Vault identity fields, written by vault.manager, survive the save.
    assert raw["vault_name"] == "Test Vault"


def test_saving_settings_without_a_vault_raises() -> None:
    controller = AppController()
    with pytest.raises(LLMWikiError):
        controller.save_settings()


def test_reopening_a_vault_closes_the_previous_connection(
    vault_root: Path, tmp_path: Path
) -> None:
    controller = AppController()
    controller.open_vault(vault_root)
    first_conn = controller.conn

    controller.create_vault(tmp_path / "second", "Second", "")

    assert controller.conn is not first_conn
    with pytest.raises(sqlite3.ProgrammingError):
        first_conn.execute("SELECT 1")


# --- Graph canvas -----------------------------------------------------


def _page_stub() -> object:
    """A `page` placeholder for tests that never touch `set_graph()`'s
    threaded path -- everything else on `GraphCanvas` reads/writes plain
    state and never calls `self._page`.
    """
    return object()


class _FakePage:
    """A real thread-crossing double, matching `test_gui_chat.py`'s: a real
    background thread for `run_thread`, a dedicated event-loop thread for
    `run_task` via `run_coroutine_threadsafe` -- for the one test that
    exercises `set_graph()`'s actual `run_thread` -> `run_task` path.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._loop_thread.start()

    def run_thread(self, handler, *args, **kwargs) -> None:
        threading.Thread(target=handler, args=args, kwargs=kwargs, daemon=True).start()

    def run_task(self, handler, *args, **kwargs):
        return asyncio.run_coroutine_threadsafe(handler(*args, **kwargs), self.loop)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._loop_thread.join(timeout=2)


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def _fixture_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edge("alpha", "beta")
    graph.add_edge("beta", "gamma")
    return graph


def _fixture_graph_with_attrs() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_node("alpha", title="Alpha Note", type="concept", tags=["core", "physics"])
    graph.add_node("beta", title="Beta Note", type="entity", tags=[])
    graph.add_edge("alpha", "beta")
    return graph


def _pan_start_at(canvas: GraphCanvas, x: float, y: float) -> None:
    canvas._on_pan_start(
        ft.DragStartEvent(
            name="pan_start",
            control=canvas,
            data=None,
            kind=ft.PointerDeviceType.MOUSE,
            local_position=ft.Offset(x, y),
            global_position=ft.Offset(x, y),
            timestamp=0,
        )
    )


def _pan_update_to(canvas: GraphCanvas, x: float, y: float, dx: float, dy: float) -> None:
    canvas._on_pan_update(
        ft.DragUpdateEvent(
            name="pan_update",
            control=canvas,
            data=None,
            local_position=ft.Offset(x, y),
            global_position=ft.Offset(0, 0),
            local_delta=ft.Offset(dx, dy),
            global_delta=ft.Offset(dx, dy),
            primary_delta=None,
            timestamp=0,
        )
    )


def test_graph_canvas_lays_out_a_fixture_graph() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    assert sorted(canvas.node_positions) == ["alpha", "beta", "gamma"]


def test_graph_canvas_builds_a_shape_per_edge_and_two_per_node() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    # 2 edges + 3 nodes x (circle + label)
    assert len(canvas.build_shapes()) == 8


def test_graph_canvas_edges_use_a_visible_paint() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    lines = [shape for shape in canvas.build_shapes() if isinstance(shape, cv.Line)]
    assert len(lines) == 2
    assert all(line.paint.color == theme.GRAPH_EDGE for line in lines)
    assert all(line.paint.stroke_width > 1.2 for line in lines)  # visibly thicker than before


def test_graph_canvas_handles_an_empty_graph() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._compute_layout()

    assert canvas.node_positions == {}
    assert canvas.build_shapes() == []


def test_graph_canvas_zoom_is_clamped() -> None:
    canvas = GraphCanvas(_page_stub())
    for _ in range(50):
        canvas.zoom_in()
    assert canvas.zoom == 2.0

    for _ in range(50):
        canvas.zoom_out()
    assert canvas.zoom == 0.5

    canvas.zoom_reset()
    assert canvas.zoom == 1.0


def test_graph_canvas_hit_testing_finds_the_node_under_the_cursor() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    slug, (x, y) = next(iter(canvas.node_positions.items()))
    assert canvas._node_at(x, y) == slug
    assert canvas._node_at(-5000, -5000) is None


def test_graph_canvas_hit_testing_accounts_for_pan() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    slug, (x, y) = next(iter(canvas.node_positions.items()))
    canvas._pan_x, canvas._pan_y = 30.0, -20.0

    assert canvas._node_at(x, y) is None  # original screen position, now empty
    assert canvas._node_at(x + 30.0, y - 20.0) == slug  # panned screen position


def test_dragging_a_node_moves_only_that_node() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    before = dict(canvas.node_positions)

    slug, (x, y) = next(iter(before.items()))
    canvas._dragging = slug
    _pan_update_to(canvas, x + 40, y + 40, dx=40, dy=40)

    assert canvas.node_positions[slug] != before[slug]
    others = [s for s in before if s != slug]
    assert all(canvas.node_positions[s] == before[s] for s in others)


def test_dragging_a_node_while_panned_still_lands_at_the_cursor() -> None:
    """Regression: without subtracting the pan offset first, dragging a node
    while the canvas is panned would place it at the wrong data-space point.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    canvas._pan_x, canvas._pan_y = 50.0, -30.0

    slug = next(iter(canvas.node_positions))
    canvas._dragging = slug
    _pan_update_to(canvas, 200, 150, dx=1, dy=1)

    data_x, data_y = canvas.node_positions[slug]
    # The node's screen position (data * zoom + pan) must land back at the
    # cursor (200, 150), not 50/-30 pixels off from it.
    assert data_x * canvas.zoom + canvas._pan_x == pytest.approx(200)
    assert data_y * canvas.zoom + canvas._pan_y == pytest.approx(150)


def test_panning_empty_background_moves_the_view_not_the_nodes() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    before = dict(canvas.node_positions)

    _pan_start_at(canvas, -9999, -9999)  # empty background, no node hit
    assert canvas._panning is True
    assert canvas._dragging is None

    _pan_update_to(canvas, -9959, -9979, dx=40, dy=20)

    assert canvas._pan_x == 40
    assert canvas._pan_y == 20
    assert canvas.node_positions == before  # untouched


def test_selecting_a_node_shows_the_info_overlay() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph_with_attrs()
    canvas._compute_layout()

    x, y = canvas.node_positions["alpha"]
    _pan_start_at(canvas, x, y)

    assert canvas._selected == "alpha"
    assert canvas._info_overlay.visible is True
    assert canvas._info_title.value == "Alpha Note"
    assert "concept" in canvas._info_type.value
    assert "core" in canvas._info_tags.value
    assert "1 out" in canvas._info_links.value


def test_clicking_empty_canvas_deselects_and_hides_the_overlay() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph_with_attrs()
    canvas._compute_layout()

    x, y = canvas.node_positions["alpha"]
    _pan_start_at(canvas, x, y)
    assert canvas._info_overlay.visible is True

    _pan_start_at(canvas, -9999, -9999)  # empty background

    assert canvas._selected is None
    assert canvas._info_overlay.visible is False


def test_selection_callback_fires_on_select_and_deselect() -> None:
    seen: list[str | None] = []
    canvas = GraphCanvas(_page_stub(), on_node_selected=seen.append)
    canvas._graph = _fixture_graph_with_attrs()
    canvas._compute_layout()

    x, y = canvas.node_positions["alpha"]
    _pan_start_at(canvas, x, y)
    _pan_start_at(canvas, -9999, -9999)

    assert seen == ["alpha", None]


def test_focal_zoom_keeps_the_data_point_under_the_cursor_fixed() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._pan_x, canvas._pan_y = 10.0, -25.0  # a non-trivial starting pan

    focal = (120.0, 80.0)
    # Whatever data point currently sits under `focal`...
    data_under_focal_x = (focal[0] - canvas._pan_x) / canvas.zoom
    data_under_focal_y = (focal[1] - canvas._pan_y) / canvas.zoom

    canvas._set_zoom(canvas.zoom + 0.3, focal=focal)

    # ...must still map to the same screen point after the zoom.
    screen_x = data_under_focal_x * canvas.zoom + canvas._pan_x
    screen_y = data_under_focal_y * canvas.zoom + canvas._pan_y
    assert screen_x == pytest.approx(focal[0])
    assert screen_y == pytest.approx(focal[1])


def test_zoom_buttons_stay_origin_anchored_and_dont_pan() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._pan_x, canvas._pan_y = 15.0, -5.0

    canvas.zoom_in()

    assert canvas._pan_x == 15.0
    assert canvas._pan_y == -5.0


def test_scroll_up_zooms_in_and_scroll_down_zooms_out() -> None:
    canvas = GraphCanvas(_page_stub())
    start_zoom = canvas.zoom

    canvas._on_scroll(
        ft.ScrollEvent(
            name="scroll",
            control=canvas,
            data=None,
            local_position=ft.Offset(100, 100),
            global_position=ft.Offset(100, 100),
            scroll_delta=ft.Offset(0, -120),  # wheel up
        )
    )
    assert canvas.zoom > start_zoom

    zoomed_in = canvas.zoom
    canvas._on_scroll(
        ft.ScrollEvent(
            name="scroll",
            control=canvas,
            data=None,
            local_position=ft.Offset(100, 100),
            global_position=ft.Offset(100, 100),
            scroll_delta=ft.Offset(0, 120),  # wheel down
        )
    )
    assert canvas.zoom < zoomed_in


def test_set_graph_computes_layout_on_a_worker_thread() -> None:
    """Exercises the real `page.run_thread()` -> `page.run_task()` path --
    the direct `_compute_layout()` tests above bypass it entirely.
    """
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas.set_graph(_fixture_graph())

        _wait_until(lambda: canvas.node_positions != {})

        assert sorted(canvas.node_positions) == ["alpha", "beta", "gamma"]
    finally:
        fake_page.close()


# --- Resize handles ---------------------------------------------------


def test_resize_handle_grows_and_shrinks_its_target() -> None:
    pane = ft.Container(width=280)
    handle = ResizeHandle(pane, sign=1, min_size=160, max_size=500)

    _drag(handle, 50)
    assert pane.width == 330
    _drag(handle, -100)
    assert pane.width == 230


def test_resize_handle_clamps_to_min_and_max() -> None:
    pane = ft.Container(width=280)
    handle = ResizeHandle(pane, sign=1, min_size=160, max_size=500)

    _drag(handle, -5000)
    assert pane.width == 160
    _drag(handle, 5000)
    assert pane.width == 500


def test_resize_handle_with_negative_sign_grows_toward_the_origin() -> None:
    """A right-hand pane grows when the handle is dragged left."""
    pane = ft.Container(width=320)
    handle = ResizeHandle(pane, sign=-1, min_size=200, max_size=600)

    _drag(handle, -40)
    assert pane.width == 360


def test_vertical_resize_handle_adjusts_height() -> None:
    dock = ft.Container(height=220)
    handle = ResizeHandle(dock, horizontal=False, sign=-1, min_size=100, max_size=520)

    _drag(handle, 0, -40)
    assert dock.height == 260


# --- Dock areas -------------------------------------------------------


def test_dock_area_shows_the_first_panel_by_default() -> None:
    dock = DockArea([("Items", ft.Text("items")), ("Git", ft.Text("git"))])

    assert [tab.content.value for tab in dock._strip.controls] == ["Items", "Git"]
    assert dock._body.content.value == "items"


def test_dock_area_switches_panels_and_moves_the_underline() -> None:
    dock = DockArea([("Items", ft.Text("items")), ("Git", ft.Text("git"))])

    dock.select(1)

    assert dock._body.content.value == "git"
    assert dock._strip.controls[1].border.bottom.color == theme.ACCENT
    assert dock._strip.controls[0].border.bottom.color != theme.ACCENT


def test_dock_area_honours_an_explicit_initial_selection() -> None:
    dock = DockArea([("Health", ft.Text("h")), ("AI Chat", ft.Text("c"))], selected=1)
    assert dock._body.content.value == "c"


# --- Menu bar ---------------------------------------------------------


def _noop(_e=None) -> None:
    return None


def _menu(recent: list[str]) -> ft.Control:
    return build_menu_bar(
        vault_label="LLM-Wiki",
        recent_vaults=recent,
        on_new_vault=_noop,
        on_open_vault=_noop,
        on_open_recent=lambda _p: None,
        on_settings=_noop,
        on_exit=_noop,
        on_reindex_vault=_noop,
        on_zoom_reset=_noop,
        on_toggle_left=_noop,
        on_toggle_right=_noop,
        on_toggle_bottom=_noop,
    )


def test_tools_menu_reindex_vault_is_wired_to_a_real_callback() -> None:
    calls: list[bool] = []
    bar = build_menu_bar(
        vault_label="LLM-Wiki",
        recent_vaults=[],
        on_new_vault=_noop,
        on_open_vault=_noop,
        on_open_recent=lambda _p: None,
        on_settings=_noop,
        on_exit=_noop,
        on_reindex_vault=lambda _e: calls.append(True),
        on_zoom_reset=_noop,
        on_toggle_left=_noop,
        on_toggle_right=_noop,
        on_toggle_bottom=_noop,
    )
    menu_bar = bar.content.controls[0]
    tools_menu = next(m for m in menu_bar.controls if m.content.value == "Tools")
    reindex_item = next(i for i in tools_menu.controls if i.content.value == "Reindex Vault")

    assert reindex_item.on_click is not None
    reindex_item.on_click(None)
    assert calls == [True]


def test_menu_bar_has_the_mockups_five_menus() -> None:
    bar = _menu([])
    menu_bar = bar.content.controls[0]
    assert [m.content.value for m in menu_bar.controls] == [
        "File",
        "Edit",
        "Tools",
        "View",
        "Help",
    ]


def test_file_menu_lists_recent_vaults_when_there_are_any() -> None:
    bar = _menu(["/vaults/one", "/vaults/two"])
    file_menu = bar.content.controls[0].controls[0]
    labels = [c.content.value for c in file_menu.controls]

    assert "Recent" in labels
    assert "/vaults/one" in labels
    assert "/vaults/two" in labels


def test_file_menu_omits_the_recent_header_when_there_are_none() -> None:
    bar = _menu([])
    file_menu = bar.content.controls[0].controls[0]
    labels = [c.content.value for c in file_menu.controls]

    assert "Recent" not in labels
    assert labels == ["New Vault…", "Open Vault…", "Exit"]


# --- Dialogs ----------------------------------------------------------


def test_settings_dialog_exposes_a_tab_per_settings_group(vault_root: Path) -> None:
    controller = AppController()
    controller.open_vault(vault_root)

    dialog = build_settings_dialog(controller, on_close=lambda: None)
    tabs = dialog.content.content

    assert [title for title, _panel in tabs.panels] == [
        "General",
        "AI Provider",
        "LLM",
        "MCP",
    ]


def test_settings_dialog_save_writes_edited_fields_to_disk(vault_root: Path) -> None:
    controller = AppController()
    controller.open_vault(vault_root)
    closed: list[bool] = []

    dialog = build_settings_dialog(controller, on_close=lambda: closed.append(True))
    # The AI Provider tab's host-port field, then Save.
    provider_panel = dialog.content.content.panels[1][1]
    host_port_field = provider_panel.content.controls[2].controls[1]
    host_port_field.value = "12345"

    save_button = dialog.actions[1]
    save_button.on_click(None)

    raw = json.loads((vault_root / ".llm-wiki-config").read_text(encoding="utf-8"))
    assert raw["llm_provider"]["host_port"] == 12345
    assert closed == [True]


def _path_field_and_browse_button(
    dialog: ft.AlertDialog, index: int = -1
) -> tuple[ft.TextField, ft.Control]:
    """Reaches into a vault dialog's Location/Vault Path row: `_field(...)`
    -> `_path_field_with_browse(...)`'s Row -> [Container(field), Browse].
    `index` picks the field among the dialog's top-level controls -- the
    Open Vault dialog has it last, New Vault has it in the middle.
    """
    row = dialog.content.content.controls[index].controls[1]
    return row.controls[0].content, row.controls[1]


def test_new_vault_and_open_vault_are_separate_dialogs() -> None:
    """Regression: a single tabbed dialog meant New Vault... could land on
    the Open Vault tab (two File-menu entry points sharing one dialog).
    Each now has its own, with no tabs to land on the wrong one.
    """
    controller = AppController()
    file_picker = ft.FilePicker()

    open_dialog = build_open_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )
    new_dialog = build_new_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )

    assert open_dialog.title.value == "Open Vault"
    assert new_dialog.title.value == "New Vault"
    assert not hasattr(open_dialog.content.content, "panels")  # no shared tabs
    assert not hasattr(new_dialog.content.content, "panels")


def test_open_vault_dialog_has_a_browsable_path_field_and_lists_recents() -> None:
    controller = AppController()
    file_picker = ft.FilePicker()

    dialog = build_open_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )

    path_field, browse_button = _path_field_and_browse_button(dialog)
    assert path_field.value == ""
    assert inspect.iscoroutinefunction(browse_button.on_click)
    assert dialog.actions[-1].content.value == "Open"


def test_open_vault_dialog_surfaces_an_error_for_a_bad_path(tmp_path: Path) -> None:
    controller = AppController()
    errors: list[str] = []
    file_picker = ft.FilePicker()
    dialog = build_open_vault_dialog(
        controller, on_close=lambda: None, on_error=errors.append, file_picker=file_picker
    )

    path_field, _browse = _path_field_and_browse_button(dialog)
    path_field.value = str(tmp_path / "does-not-exist")
    dialog.actions[-1].on_click(None)

    assert errors and "vault" in errors[0].lower()


def test_open_vault_dialog_browse_fills_the_path_field_from_the_native_picker(
    tmp_path: Path,
) -> None:
    controller = AppController()
    file_picker = ft.FilePicker()
    picked = str(tmp_path / "some-vault")

    async def fake_get_directory_path(**_kwargs):
        return picked

    file_picker.get_directory_path = fake_get_directory_path
    dialog = build_open_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )
    path_field, browse_button = _path_field_and_browse_button(dialog)

    asyncio.run(browse_button.on_click(None))

    assert path_field.value == picked


def test_open_vault_dialog_browse_cancelled_leaves_the_field_untouched() -> None:
    controller = AppController()
    file_picker = ft.FilePicker()

    async def fake_get_directory_path(**_kwargs):
        return None  # user cancelled the native picker

    file_picker.get_directory_path = fake_get_directory_path
    dialog = build_open_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )
    path_field, browse_button = _path_field_and_browse_button(dialog)
    path_field.value = "unchanged"

    asyncio.run(browse_button.on_click(None))

    assert path_field.value == "unchanged"


def test_new_vault_dialog_has_name_location_and_description_fields() -> None:
    controller = AppController()
    file_picker = ft.FilePicker()

    dialog = build_new_vault_dialog(
        controller, on_close=lambda: None, on_error=lambda _m: None, file_picker=file_picker
    )

    labels = [field.controls[0].value for field in dialog.content.content.controls]
    assert labels == ["Vault Name", "Location", "Description"]
    location_field, browse_button = _path_field_and_browse_button(dialog, index=1)
    assert str(Path.home() / "Vaults") in location_field.value
    assert inspect.iscoroutinefunction(browse_button.on_click)
    assert dialog.actions[-1].content.value == "Create"


# --- Async window APIs ------------------------------------------------


def test_every_awaitable_page_api_the_shell_calls_is_scheduled() -> None:
    """Regression: File > Exit called `page.window.close()` directly.

    It is a coroutine, so calling it did nothing but emit a never-awaited
    RuntimeWarning -- the menu item silently failed. Any coroutine method
    reached off `self.page` has to go through `run_task`/`run_thread`, so
    assert that no bare call to a known-async API survives in app.py.
    """
    async_page_apis = {
        name
        for name in dir(ft.Window)
        if not name.startswith("_")
        and inspect.iscoroutinefunction(getattr(ft.Window, name, None))
    }
    assert "close" in async_page_apis, "expected Window.close to still be async"

    source = Path(app_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    offenders = []
    for node in ast.walk(tree):
        # A bare `...window.<async_api>()` call statement, not wrapped in
        # run_task (which passes the method rather than calling it).
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in async_page_apis:
            continue
        if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "window":
            offenders.append(node.func.attr)

    assert offenders == [], f"awaitable Window API called directly in app.py: {offenders}"


def test_resize_handles_use_pane_divider_cursors() -> None:
    """`col-resize`/`row-resize` are the conventional splitter cursors."""
    column = ResizeHandle(ft.Container(width=200), horizontal=True)
    row = ResizeHandle(ft.Container(height=200), horizontal=False)

    assert column.mouse_cursor is ft.MouseCursor.RESIZE_COLUMN
    assert row.mouse_cursor is ft.MouseCursor.RESIZE_ROW


# --- Health panel (flet-charts) ---------------------------------------


def test_health_panel_defaults_to_100_with_no_connection() -> None:
    panel = HealthPanel()

    assert panel.score == 100
    assert set(panel.counts.values()) == {0}


def test_health_panel_chart_has_a_bar_per_finding_kind() -> None:
    chart = HealthPanel().build_chart()

    assert [label.label.value for label in chart.bottom_axis.labels] == [
        "Schema",
        "Links",
        "Isolated",
    ]
    assert [group.rods[0].to_y for group in chart.groups] == [0, 0, 0]


def test_health_panel_chart_max_y_never_collapses_to_zero() -> None:
    """An empty vault still needs a drawable axis."""
    assert HealthPanel().build_chart().max_y == 1


def test_health_panel_reflects_a_real_lint_run(vault_root: Path) -> None:
    panel = HealthPanel()
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")

    panel.set_connection(conn)

    assert panel.score == 100
    assert sum(panel.counts.values()) == 0


def test_health_panel_counts_broken_links_and_drops_the_score(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    conn.execute(
        "INSERT INTO notes (path, title, slug, type, tags, sources, content_hash, updated_at) "
        "VALUES ('wiki/a.md', 'A', 'a', 'concept', '[]', '[]', 'hash', '2026-01-01T00:00:00Z')"
    )
    conn.execute("INSERT INTO links (source_slug, target_slug) VALUES ('a', 'nope')")
    conn.commit()

    panel = HealthPanel()
    panel.set_connection(conn)

    assert panel.counts[LintFindingKind.BROKEN_LINK] == 1
    assert panel.score < 100
    # The chart scales to the real finding count.
    assert panel.build_chart().max_y >= 1


# --- Architectural guard ----------------------------------------------


def test_no_ui_framework_import_anywhere_in_the_engine_cli_or_mcp_packages() -> None:
    """Design Principle 5: the UI framework exists only under llm_wiki.gui."""
    src_root = Path(__file__).parent.parent / "src" / "llm_wiki"
    offending: list[str] = []

    for py_file in src_root.rglob("*.py"):
        if "gui" in py_file.relative_to(src_root).parts:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.startswith(("flet", "PySide6")) for name in names):
                offending.append(str(py_file))

    assert offending == []
