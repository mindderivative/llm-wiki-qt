"""Phase 16a: the Flet shell -- theme, vault/settings controller, graph
canvas, resize handles, dock tab strips, menu bar, and dialogs.

Flet's own integration testing (`flet test`) provisions a Flutter test host
and needs the Flutter SDK, which isn't available here. Flet controls are
plain dataclasses, though, so the whole layer is exercised by building real
control trees and asserting on their structure -- no display required.
"""

import ast
import json
import re
import sqlite3
from pathlib import Path

import flet as ft
import networkx as nx
import pytest

from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dialogs import build_settings_dialog, build_vault_dialog
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.graph_canvas import GraphCanvas
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.splitter import ResizeHandle
from llm_wiki.models import LLMWikiError
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


def _fixture_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_edge("alpha", "beta")
    graph.add_edge("beta", "gamma")
    return graph


def test_graph_canvas_lays_out_a_fixture_graph() -> None:
    canvas = GraphCanvas()
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    assert sorted(canvas.node_positions) == ["alpha", "beta", "gamma"]


def test_graph_canvas_builds_a_shape_per_edge_and_two_per_node() -> None:
    canvas = GraphCanvas()
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    # 2 edges + 3 nodes x (circle + label)
    assert len(canvas.build_shapes()) == 8


def test_graph_canvas_handles_an_empty_graph() -> None:
    canvas = GraphCanvas()
    canvas._compute_layout()

    assert canvas.node_positions == {}
    assert canvas.build_shapes() == []


def test_graph_canvas_zoom_is_clamped() -> None:
    canvas = GraphCanvas()
    for _ in range(50):
        canvas.zoom_in()
    assert canvas.zoom == 2.0

    for _ in range(50):
        canvas.zoom_out()
    assert canvas.zoom == 0.5

    canvas.zoom_reset()
    assert canvas.zoom == 1.0


def test_graph_canvas_hit_testing_finds_the_node_under_the_cursor() -> None:
    canvas = GraphCanvas()
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    slug, (x, y) = next(iter(canvas.node_positions.items()))
    assert canvas._node_at(x, y) == slug
    assert canvas._node_at(-5000, -5000) is None


def test_dragging_a_node_moves_only_that_node() -> None:
    canvas = GraphCanvas()
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    before = dict(canvas.node_positions)

    slug, (x, y) = next(iter(before.items()))
    canvas._dragging = slug
    canvas._on_pan_update(
        ft.DragUpdateEvent(
            name="pan_update",
            control=canvas,
            data=None,
            local_position=ft.Offset(x + 40, y + 40),
            global_position=ft.Offset(0, 0),
            local_delta=ft.Offset(40, 40),
            global_delta=ft.Offset(40, 40),
            primary_delta=None,
            timestamp=0,
        )
    )

    assert canvas.node_positions[slug] != before[slug]
    others = [s for s in before if s != slug]
    assert all(canvas.node_positions[s] == before[s] for s in others)


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
        on_zoom_reset=_noop,
        on_toggle_left=_noop,
        on_toggle_right=_noop,
        on_toggle_bottom=_noop,
    )


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


def test_vault_dialog_has_open_and_new_tabs() -> None:
    controller = AppController()
    dialog = build_vault_dialog(controller, on_close=lambda: None, on_error=lambda _m: None)

    assert [title for title, _panel in dialog.content.content.panels] == [
        "Open Vault",
        "New Vault",
    ]


def test_vault_dialog_surfaces_an_error_for_a_bad_path(tmp_path: Path) -> None:
    controller = AppController()
    errors: list[str] = []
    dialog = build_vault_dialog(
        controller, on_close=lambda: None, on_error=errors.append
    )

    open_panel = dialog.content.content.panels[0][1]
    path_field = open_panel.content.controls[-2].controls[1]
    path_field.value = str(tmp_path / "does-not-exist")
    open_panel.content.controls[-1].on_click(None)

    assert errors and "vault" in errors[0].lower()


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
