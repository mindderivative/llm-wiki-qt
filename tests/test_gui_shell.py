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
import math
import re
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path

import flet as ft
import flet.canvas as cv
import networkx as nx
import pytest

from llm_wiki.graph import sync_links
from llm_wiki.gui import app as app_module
from llm_wiki.gui import graph_canvas, theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dashboard_panel import DashboardPanel
from llm_wiki.gui.dialogs import (
    build_new_vault_dialog,
    build_open_vault_dialog,
    build_settings_dialog,
)
from llm_wiki.gui.dock import DockArea
from llm_wiki.gui.graph_canvas import GraphCanvas, GraphDisplaySettings, GraphFilterState
from llm_wiki.gui.health_panel import HealthPanel
from llm_wiki.gui.menu import build_menu_bar
from llm_wiki.gui.splitter import ResizeHandle
from llm_wiki.models import LintFindingKind, LLMWikiError
from llm_wiki.storage import connect, upsert_note_from_file
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
        if name.isupper() and isinstance(value, str)
    }
    assert colours, "expected the palette module to expose colour constants"
    for name, value in colours.items():
        assert re.fullmatch(r"#[0-9A-F]{6}", value), f"{name}={value}"


def test_graph_swatch_palette_is_a_nonempty_tuple_of_valid_hex_colours() -> None:
    """`_GRAPH_SWATCH_PALETTE` (Phase 25) is a tuple, not a string constant
    -- `test_every_palette_colour_is_a_valid_hex_string()` only scans
    theme.py's string constants, so this needs its own check.
    """
    palette = graph_canvas._GRAPH_SWATCH_PALETTE
    assert palette
    for color in palette:
        assert re.fullmatch(r"#[0-9A-F]{6}", color), color


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


class _PageStub:
    """A `page` placeholder for tests that never touch `set_graph()`'s (or,
    Post-25 fix, `_on_node_spacing_change_end()`'s) threaded path --
    everything else on `GraphCanvas` reads/writes plain state. Both
    `run_task()` and `run_thread()` are no-ops: `_start_simulation()`
    (Phase 22) calls `run_task()` on any node-drag start, and the Node
    Spacing slider's `on_change_end` calls `run_thread()`, but neither is
    awaited or inspected here, so tests that don't care about the live
    simulation loop or a real relayout can ignore both entirely rather
    than needing the real `_FakePage` thread-crossing double.
    """

    def run_task(self, handler, *args, **kwargs) -> None:
        pass

    def run_thread(self, handler, *args, **kwargs) -> None:
        pass


def _page_stub() -> object:
    return _PageStub()


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


def _pan_end(canvas: GraphCanvas) -> None:
    canvas._on_pan_end(
        ft.DragEndEvent(
            name="pan_end",
            control=canvas,
            data=None,
            local_position=ft.Offset(0, 0),
            global_position=ft.Offset(0, 0),
            velocity=ft.Offset(0, 0),
            primary_velocity=None,
        )
    )


def _hover_event(canvas: GraphCanvas, x: float, y: float) -> ft.HoverEvent:
    return ft.HoverEvent(
        name="hover",
        control=canvas,
        data=None,
        kind=ft.PointerDeviceType.MOUSE,
        local_position=ft.Offset(x, y),
        global_position=ft.Offset(x, y),
        timestamp=0,
        device=0.0,
        pressure=1.0,
        pressure_min=1.0,
        pressure_max=1.0,
        distance=0.0,
        distance_max=0.0,
        size=0.0,
        radius_major=0.0,
        radius_minor=0.0,
        radius_min=0.0,
        radius_max=0.0,
        orientation=0.0,
        tilt=0.0,
    )


def _hover_at(canvas: GraphCanvas, x: float, y: float) -> None:
    canvas._on_hover(_hover_event(canvas, x, y))


def _exit_hover(canvas: GraphCanvas) -> None:
    canvas._on_exit(_hover_event(canvas, -9999, -9999))


def test_graph_canvas_lays_out_a_fixture_graph() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    assert sorted(canvas.node_positions) == ["alpha", "beta", "gamma"]


def test_graph_canvas_builds_a_shape_per_edge_and_one_per_node() -> None:
    """Labels are hover-only (Post-22 fix) -- build_shapes() is graph
    content only (edges + node circles), not the hover-label layer.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    # 2 edges + 3 nodes x 1 circle
    assert len(canvas.build_shapes()) == 5


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


def test_graph_canvas_layout_spreads_nodes_apart_at_a_realistic_size() -> None:
    """Regression: a fixed `k=0.15` in `nx.spring_layout()`, mapped into a
    canvas whose size never grew with node count, left nodes overlapping
    for any vault with more than a handful of notes (as little as 1-2px
    apart at 40-60 nodes -- verified against the pre-fix numbers directly).
    Mimics the real topology (every note backlinks to `[[index]]` *and*
    its source, so `index` and each source form hubs with many leaves
    around them -- the worst case for a force-directed layout). The exact
    pixel gap spring_layout converges to isn't deterministic enough across
    seeds/iteration counts to assert a tight number against, so this only
    asserts the one thing that must always hold: circles don't overlap.
    """
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    for i in range(17):
        graph.add_edge(f"note-{i}", "index")
        graph.add_edge(f"note-{i}", f"source-{i % 3}")
    canvas._graph = graph
    canvas._compute_layout()

    positions = list(canvas.node_positions.values())
    min_distance = min(
        ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5
        for i, (x1, y1) in enumerate(positions)
        for x2, y2 in positions[i + 1 :]
    )
    assert min_distance > 2 * graph_canvas._NODE_RADIUS


def test_graph_canvas_layout_scale_grows_with_node_count() -> None:
    canvas = GraphCanvas(_page_stub())

    small_graph = nx.DiGraph()
    small_graph.add_edge("a", "b")
    canvas._graph = small_graph
    assert canvas._layout_scale() == 1.0  # below the base node count -- unchanged size

    big_graph = nx.DiGraph()
    for i in range(50):
        big_graph.add_edge(f"note-{i}", "index")
    canvas._graph = big_graph
    assert canvas._layout_scale() == pytest.approx(51 / graph_canvas._LAYOUT_BASE_NODE_COUNT)


def test_index_is_anchored_at_the_canvas_center_when_present() -> None:
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    graph.add_edge("a", "index")
    graph.add_edge("b", "index")
    canvas._graph = graph
    canvas._compute_layout()

    assert canvas.node_positions["index"] == canvas._to_canvas(0.0, 0.0)


def test_layout_is_unaffected_when_there_is_no_index_node() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()  # alpha/beta/gamma, no "index"

    canvas._compute_layout()  # must not raise

    assert set(canvas.node_positions) == {"alpha", "beta", "gamma"}


def _bounding_radius_in_data_space(canvas: GraphCanvas) -> float:
    """Reverses `_to_canvas()`'s formula to recover the layout's bounding
    radius in spring_layout's own units, pre-canvas-mapping.
    """
    scale = canvas._layout_scale()
    half_w = (canvas._width - graph_canvas._LAYOUT_MARGIN * 2) / 2.0 * scale
    half_h = (canvas._height - graph_canvas._LAYOUT_MARGIN * 2) / 2.0 * scale

    def to_data_space(x: float, y: float) -> tuple[float, float]:
        return ((x - canvas._width / 2.0) / half_w, (y - canvas._height / 2.0) / half_h)

    return max(math.hypot(*to_data_space(x, y)) for x, y in canvas.node_positions.values())


def test_anchored_layout_bounding_radius_matches_spring_layouts_own_default() -> None:
    """The unanchored path already gets spring_layout's own scale=1
    auto-rescale (bounding radius ~1.0) for free; fixed= silently skips
    it -- this was the actual cause of "only half the graph fits on
    screen at the same zoom" once index became anchored. Locked in
    directly in data space here, not an indirect proxy metric.
    """
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    for i in range(17):
        graph.add_edge(f"note-{i}", "index")
        graph.add_edge(f"note-{i}", f"source-{i % 3}")
    canvas._graph = graph
    canvas._compute_layout()

    assert _bounding_radius_in_data_space(canvas) == pytest.approx(1.0, abs=0.01)


def test_anchored_bounding_radius_is_correct_regardless_of_node_count() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph([("a", "index"), ("b", "index")])
    canvas._compute_layout()

    assert _bounding_radius_in_data_space(canvas) == pytest.approx(1.0, abs=0.01)


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


# --- Settings panel shell (Phase 23) -------------------------------------


def test_settings_panel_defaults_to_expanded_with_the_legend_visible() -> None:
    canvas = GraphCanvas(_page_stub())

    assert canvas._panel_body.visible is True
    assert canvas._panel_chevron.value == "▾"
    # header + the capped/scrollable body beneath it -- built once, never
    # swapped out again (Post-24 fix #3).
    assert len(canvas._settings_panel.content.controls) == 2


def test_toggling_the_settings_panel_collapses_it_and_fires_the_callback() -> None:
    """Post-24 fix #3: expand/collapse mutates `_panel_body.visible` and
    the chevron glyph in place -- `_settings_panel.content` is never
    reassigned, unlike the old rebuild-based implementation.
    """
    seen: list[bool] = []
    canvas = GraphCanvas(_page_stub(), on_settings_panel_toggled=seen.append)
    panel_content_before = canvas._settings_panel.content

    canvas._toggle_settings_panel()

    assert canvas._settings_panel_expanded is False
    assert canvas._panel_body.visible is False
    assert canvas._panel_chevron.value == "▸"
    assert canvas._settings_panel.content is panel_content_before
    assert seen == [False]

    canvas._toggle_settings_panel()

    assert canvas._settings_panel_expanded is True
    assert canvas._panel_body.visible is True
    assert canvas._panel_chevron.value == "▾"
    assert canvas._settings_panel.content is panel_content_before
    assert seen == [False, True]


def test_set_settings_panel_expanded_syncs_without_firing_the_callback() -> None:
    seen: list[bool] = []
    canvas = GraphCanvas(_page_stub(), on_settings_panel_toggled=seen.append)

    canvas.set_settings_panel_expanded(False)

    assert canvas._settings_panel_expanded is False
    assert canvas._panel_body.visible is False
    assert seen == []  # syncing from settings is not a user toggle


def test_set_settings_panel_expanded_is_a_no_op_for_the_same_value() -> None:
    canvas = GraphCanvas(_page_stub())
    content_before = canvas._settings_panel.content

    canvas.set_settings_panel_expanded(True)  # already the default

    assert canvas._settings_panel.content is content_before
    assert canvas._panel_body.visible is True


# --- Filters (Phase 24) --------------------------------------------------


def _filters_fixture_graph() -> nx.DiGraph:
    """`index` carries no attributes at all -- matching how it's actually
    created in the real graph (implicitly, via edges, never an explicit
    `notes` row) -- so it also exercises the gravity-well filter exemption.
    """
    graph = nx.DiGraph()
    graph.add_node("index")
    graph.add_node(
        "note-a",
        title="Alpha",
        type="concept",
        tags=["core", "physics"],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    graph.add_node(
        "note-b",
        title="Beta",
        type="entity",
        tags=["physics"],
        updated_at="2026-06-01T00:00:00+00:00",
    )
    graph.add_node(
        "note-c",
        title="Gamma",
        type="source",
        tags=[],
        updated_at="2026-03-01T00:00:00+00:00",
    )
    graph.add_edge("note-a", "index")
    graph.add_edge("note-b", "index")
    graph.add_edge("note-c", "index")
    graph.add_edge("note-a", "note-b")
    return graph


def _filters_canvas() -> GraphCanvas:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _filters_fixture_graph()
    canvas._positions = {
        "index": (0.0, 0.0),
        "note-a": (10.0, 0.0),
        "note-b": (20.0, 0.0),
        "note-c": (30.0, 0.0),
    }
    # The tags popup was built against the empty graph __init__() started
    # with -- refresh it now that the fixture's real tag vocabulary is in
    # place, matching what a real layout completion would do.
    canvas._refresh_tag_popup()
    return canvas


def test_index_always_passes_filters_regardless_of_any_combination() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = set()  # would exclude every real note
    canvas._filter_tags = {"nonexistent"}
    canvas._filter_search = "nonexistent"
    canvas._filter_date_from = "2099-01-01"

    assert canvas._passes_filters("index") is True


def test_type_filter_excludes_unchecked_types() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = {"concept", "source"}

    assert canvas._passes_filters("note-a") is True  # concept
    assert canvas._passes_filters("note-b") is False  # entity, unchecked
    assert canvas._passes_filters("note-c") is True  # source


def test_tag_filter_matches_any_selected_tag() -> None:
    canvas = _filters_canvas()
    canvas._filter_tags = {"core"}
    assert canvas._passes_filters("note-a") is True  # has "core"
    assert canvas._passes_filters("note-b") is False  # only "physics"

    canvas._filter_tags = {"physics"}
    assert canvas._passes_filters("note-a") is True  # has "physics" too
    assert canvas._passes_filters("note-b") is True


def test_search_filter_matches_substring_and_genuine_fuzziness() -> None:
    canvas = _filters_canvas()

    canvas._filter_search = "alph"  # exact substring of "Alpha"
    assert canvas._passes_filters("note-a") is True
    assert canvas._passes_filters("note-b") is False

    canvas._filter_search = "alfa"  # not a substring, but close to "Alpha"
    assert canvas._passes_filters("note-a") is True
    assert canvas._passes_filters("note-b") is False


def test_date_filter_narrows_to_the_range() -> None:
    canvas = _filters_canvas()
    canvas._filter_date_from = "2026-02-01"
    canvas._filter_date_to = "2026-12-31"

    assert canvas._passes_filters("note-a") is False  # updated 2026-01-01
    assert canvas._passes_filters("note-b") is True  # updated 2026-06-01
    assert canvas._passes_filters("note-c") is True  # updated 2026-03-01


def test_degrees_filter_only_applies_with_a_selection() -> None:
    canvas = _filters_canvas()
    canvas._filter_degrees = 1
    canvas._filter_degrees_enabled = True
    # No selection -- the filter has no effect yet, per its own spec.
    assert canvas._passes_filters("note-c") is True

    canvas._selected = "note-a"
    canvas._update_degrees_from_selected()
    # note-a (itself) and note-b (direct edge) are within 1 hop; note-c
    # has no edge to note-a other than via index, which is excluded from
    # the traversal (see test_degrees_filter_does_not_route_through_index_
    # as_a_shortcut below) -- unreachable, not "2 hops away."
    assert canvas._passes_filters("note-a") is True
    assert canvas._passes_filters("note-b") is True
    assert canvas._passes_filters("index") is True  # exempt anyway
    assert canvas._passes_filters("note-c") is False


def test_degrees_filter_does_not_route_through_index_as_a_shortcut() -> None:
    """Regression: every note has a guaranteed direct edge to `index`
    (Phase 18's Related-block), so leaving it in the traversal graph would
    make `index` a length-2 shortcut between *any* two notes -- degree=2
    would always show the entire vault regardless of real topical
    distance. Excluding `index` forces hop-distance to reflect genuine
    content links instead.
    """
    canvas = _filters_canvas()
    canvas._filter_degrees_enabled = True
    canvas._selected = "note-a"
    canvas._update_degrees_from_selected()

    # note-c's only path to note-a is via index (note-a -> index ->
    # note-c) -- excluded from the traversal, so it's unreachable even at
    # a generous degree=2, not "2 hops away."
    canvas._filter_degrees = 2
    assert canvas._passes_filters("note-c") is False

    # Selecting index itself is unaffected -- there, every note genuinely
    # *is* one hop away, the correct answer for "how far from the hub,"
    # not a shortcut artifact to correct.
    canvas._selected = "index"
    canvas._update_degrees_from_selected()
    canvas._filter_degrees = 1
    assert canvas._passes_filters("note-c") is True


def test_degrees_filter_is_off_by_default_even_once_a_node_is_selected() -> None:
    """Regression: selecting a node is a normal browsing action, not a
    deliberate filter choice -- unlike the other four dimensions (which
    default enabled=True but start at no-op values), degrees has no no-op
    state, so it must default *disabled* or merely clicking a node would
    silently start hiding everything more than a few hops away.
    """
    canvas = _filters_canvas()
    assert canvas._filter_degrees_enabled is False

    canvas._selected = "note-a"
    canvas._update_degrees_from_selected()

    assert canvas._passes_filters("note-c") is True


def test_filtered_out_nodes_are_excluded_from_shapes_and_hit_testing() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = {"concept"}  # only note-a

    static_shapes = canvas._build_static_shapes()
    circles = [s for s in static_shapes if isinstance(s, cv.Circle)]
    lines = [s for s in static_shapes if isinstance(s, cv.Line)]
    # Only note-a and index (exempt) render -- 2 circles. note-a's only
    # surviving edge goes to index, but index's own edges are hidden
    # unconditionally unless index itself is selected (Post-26 fix) --
    # nothing is selected here, so no lines survive at all.
    assert len(circles) == 2
    assert len(lines) == 0

    # note-b's position (20.0, 0.0) is no longer hit-testable.
    assert canvas._node_at(20.0, 0.0) is None
    assert canvas._node_at(10.0, 0.0) == "note-a"


def test_filter_change_deselects_a_node_that_no_longer_passes() -> None:
    canvas = _filters_canvas()
    canvas._selected = "note-b"
    canvas._update_info_overlay()

    canvas._on_filter_type_changed("entity", False)  # excludes note-b

    assert canvas._selected is None
    assert canvas._info_overlay.visible is False


# --- Redraw/rebuild decoupling (Post-24 fix #3, IMPORTANT) ----------------
#
# Every one of these locks in the same guarantee: NO control interaction
# ever reassigns `_settings_panel.content` (or any other container's
# content/controls) -- not just the two "continuous" controls the first
# Post-24 fix exempted, but every single one, per the user's explicit
# correction. Each control mutates only itself (or, for a tag chip,
# itself + the trigger label) and calls .update() directly.


def test_search_field_change_does_not_rebuild_the_settings_panel() -> None:
    """Regression: rebuilding the whole panel on every keystroke replaced
    the TextField with a fresh instance each time, resetting its focus on
    the client -- the same class of bug this project already root-caused
    once (Post-16d, the chat input field). The graph must still redraw
    (that's the whole point of a filter changing); only the panel's own
    control tree must not be touched.
    """
    canvas = _filters_canvas()
    canvas._redraw_all()
    panel_content_before = canvas._settings_panel.content
    static_shapes_before = canvas._static_canvas.shapes

    canvas._on_filter_search_changed("al")

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filter_search == "al"
    assert canvas._static_canvas.shapes is not static_shapes_before  # graph did redraw


def test_degrees_slider_change_does_not_rebuild_the_settings_panel() -> None:
    canvas = _filters_canvas()
    canvas._selected = "note-a"  # caption text differs with vs without a selection
    canvas._redraw_all()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_degrees_changed(3)

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filter_degrees == 3
    assert canvas._degrees_caption.value == "Within 3 hop(s) of the selection"


def test_degrees_slider_on_change_does_not_persist_until_change_end() -> None:
    """Post-26 fix: `on_change` fires continuously while dragging -- each
    call must redraw live without persisting, or dragging a slider floods
    `.llm-wiki-config` (and the Pipeline Log, since every save logs) with
    hundreds of writes per drag, which measurably stuttered the graph.
    """
    seen: list[GraphFilterState] = []
    canvas = GraphCanvas(_page_stub(), on_filters_changed=seen.append)
    canvas._graph = _filters_fixture_graph()

    canvas._on_filter_degrees_changed(3)
    assert seen == []  # on_change alone must not persist

    canvas._persist_filter_change()  # the slider's on_change_end
    assert seen[-1].degrees == 3


def test_type_checkbox_change_does_not_rebuild_the_settings_panel() -> None:
    canvas = _filters_canvas()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_type_changed("entity", False)

    assert canvas._settings_panel.content is panel_content_before
    assert "entity" not in canvas._filter_types


def test_tag_chip_toggle_mutates_only_that_chip_and_the_trigger_label() -> None:
    """The bug this whole fix chases: clicking a chip used to tear down and
    rebuild the entire panel mid-interaction, leaving the PopupMenuButton
    "selected but stale". Now a chip click must touch only its own
    container -- not the panel, not any other chip.
    """
    canvas = _filters_canvas()
    panel_content_before = canvas._settings_panel.content
    other_chip = canvas._tag_chip_controls["physics"]
    other_chip_bgcolor_before = other_chip.bgcolor

    canvas._on_filter_tag_toggled("core")

    assert canvas._settings_panel.content is panel_content_before
    assert "core" in canvas._filter_tags
    assert canvas._tag_chip_controls["core"].bgcolor == theme.ACCENT
    assert canvas._tags_trigger_label.value == "Tags (1)"
    # A different tag's chip is untouched by toggling this one.
    assert canvas._tag_chip_controls["physics"].bgcolor == other_chip_bgcolor_before


def test_date_pick_mutates_only_its_own_label() -> None:
    canvas = _filters_canvas()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_date_from_changed(date(2026, 2, 1))

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filter_date_from == "2026-02-01"
    assert canvas._date_from_label.value == "From: 2026-02-01"


def test_switch_toggle_does_not_rebuild_the_settings_panel() -> None:
    canvas = _filters_canvas()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_master_toggled(False)

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filters_enabled is False


def test_selecting_a_node_updates_the_degrees_caption_directly() -> None:
    """Regression (Post-24 fix #3): the degrees caption reads
    `self._selected`, so it went stale the moment a node was (de)selected
    -- a second instance of the same "the panel doesn't know the graph
    changed" bug, fixed by mutating it directly from _notify_selection().
    """
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph_with_attrs()
    canvas._compute_layout()
    assert canvas._degrees_caption.value == "Applies once you select a node"

    x, y = canvas.node_positions["alpha"]
    _pan_start_at(canvas, x, y)

    assert canvas._degrees_caption.value == "Within 1 hop(s) of the selection"

    _pan_start_at(canvas, -9999, -9999)  # empty background -- deselects

    assert canvas._degrees_caption.value == "Applies once you select a node"


# --- Index Connections (Phase 26) ------------------------------------------


def test_compute_index_edge_limit_visible_ranks_by_updated_at_descending() -> None:
    canvas = _filters_canvas()
    canvas._filter_index_edge_limit = 2

    # note-b (2026-06-01) and note-c (2026-03-01) are more recently updated
    # than note-a (2026-01-01).
    assert canvas._compute_index_edge_limit_visible() == {"note-b", "note-c"}


def test_compute_index_edge_limit_visible_returns_everyone_under_the_limit() -> None:
    canvas = _filters_canvas()
    canvas._filter_index_edge_limit = 10

    assert canvas._compute_index_edge_limit_visible() == {"note-a", "note-b", "note-c"}


def test_compute_index_edge_limit_visible_excludes_neighbors_failing_other_filters() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = {"entity", "source"}  # excludes note-a (concept)
    canvas._filter_index_edge_limit = 10

    assert canvas._compute_index_edge_limit_visible() == {"note-b", "note-c"}


def test_compute_index_edge_limit_visible_is_empty_without_an_index_node() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph()
    canvas._graph.add_node("solo")

    assert canvas._compute_index_edge_limit_visible() == set()


def test_index_edge_visible_false_when_index_is_not_selected() -> None:
    """Post-26 fix: index's own edges are hidden unconditionally unless
    index itself is the current selection -- regardless of the enable
    switch, which only governs behavior once index *is* selected.
    """
    canvas = _filters_canvas()
    canvas._selected = "note-a"

    canvas._filter_index_edges_enabled = True
    assert canvas._index_edge_visible("note-b", set()) is False
    canvas._filter_index_edges_enabled = False
    assert canvas._index_edge_visible("note-b", set()) is False

    canvas._selected = None
    assert canvas._index_edge_visible("note-b", set()) is False


def test_index_edge_visible_true_when_switch_off() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._filter_index_edges_enabled = False

    assert canvas._index_edge_visible("note-a", set()) is True


def test_index_edge_visible_gated_by_the_precomputed_set() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._filter_index_edges_enabled = True

    assert canvas._index_edge_visible("note-b", {"note-b", "note-c"}) is True
    assert canvas._index_edge_visible("note-a", {"note-b", "note-c"}) is False


def test_capped_index_edge_is_excluded_but_the_neighbor_circle_still_renders() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._filter_index_edges_enabled = True
    canvas._filter_index_edge_limit = 1

    shapes = canvas._build_static_shapes()
    lines = [s for s in shapes if isinstance(s, cv.Line)]
    circles = [s for s in shapes if isinstance(s, cv.Circle)]

    # Only the most-recently-updated neighbor's edge to index survives,
    # plus the note-a/note-b edge (doesn't touch index, unaffected).
    assert len(lines) == 2
    assert len(circles) == 4  # every node's circle still renders


def test_index_edges_are_hidden_by_default_when_index_is_not_selected() -> None:
    """Post-26 fix: index's edges start hidden entirely -- not visible-
    then-capped -- restoring the original deferred-list ask ("hidden
    unless index node is selected"). Node circles and the note-a/note-b
    edge (doesn't touch index) are unaffected either way.
    """
    canvas = _filters_canvas()  # nothing selected, switch at its default (off)

    lines = [s for s in canvas._build_static_shapes() if isinstance(s, cv.Line)]
    circles = [s for s in canvas._build_static_shapes() if isinstance(s, cv.Circle)]

    assert len(lines) == 1  # only note-a/note-b -- all 3 index edges hidden
    assert len(circles) == 4  # every node's circle still renders

    canvas._selected = "note-a"  # selecting a non-index node changes nothing here
    lines = [s for s in canvas._build_static_shapes() if isinstance(s, cv.Line)]
    assert len(lines) == 1


def test_index_edges_switch_off_shows_all_connections_even_when_index_is_selected() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._filter_index_edges_enabled = False
    canvas._filter_index_edge_limit = 1

    lines = [s for s in canvas._build_static_shapes() if isinstance(s, cv.Line)]

    assert len(lines) == 4


def test_dragging_index_still_respects_the_cap_in_the_dynamic_shape_builder() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._filter_index_edges_enabled = True
    canvas._filter_index_edge_limit = 1
    canvas._dragging = "index"  # index itself is in the dynamic set while dragged

    shapes = canvas._build_dynamic_shapes()
    lines = [s for s in shapes if isinstance(s, cv.Line)]
    circles = [s for s in shapes if isinstance(s, cv.Circle)]

    assert len(lines) == 1  # only the most-recently-updated neighbor's edge
    assert len(circles) == 2  # index's own circle + that neighbor's shadow circle


def test_index_edges_switch_toggle_does_not_rebuild_the_settings_panel() -> None:
    canvas = _filters_canvas()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_index_edges_enabled_toggled(True)

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filter_index_edges_enabled is True


def test_index_edge_limit_slider_change_does_not_rebuild_the_settings_panel() -> None:
    canvas = _filters_canvas()
    canvas._selected = "index"
    canvas._redraw_all()
    panel_content_before = canvas._settings_panel.content

    canvas._on_filter_index_edge_limit_changed(5)

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._filter_index_edge_limit == 5


def test_index_edge_limit_slider_on_change_does_not_persist_until_change_end() -> None:
    seen: list[GraphFilterState] = []
    canvas = GraphCanvas(_page_stub(), on_filters_changed=seen.append)
    canvas._graph = _filters_fixture_graph()

    canvas._on_filter_index_edge_limit_changed(7)
    assert seen == []  # on_change alone must not persist

    canvas._persist_filter_change()  # the slider's on_change_end
    assert seen[-1].index_edge_limit == 7


def test_index_edges_caption_text_in_each_state() -> None:
    canvas = _filters_canvas()
    assert canvas._index_edges_caption_text() == "Hidden until index is selected"

    canvas._selected = "index"
    assert canvas._index_edges_caption_text() == "Showing all connections"

    canvas._filter_index_edges_enabled = True
    canvas._filter_index_edge_limit = 2
    assert canvas._index_edges_caption_text() == "Showing 2 of 3 connections"

    canvas._filter_index_edge_limit = 10
    assert canvas._index_edges_caption_text() == "Showing 3 of 3 connections"


def test_selecting_index_updates_the_index_edges_caption_directly() -> None:
    """Regression, the same staleness class Post-24 fix #3 already fixed
    once for the Degrees caption: the Index Connections caption reads
    `self._selected` too, so it must be mutated from `_notify_selection()`.
    """
    canvas = _filters_canvas()
    assert canvas._index_edges_caption.value == "Hidden until index is selected"

    canvas._selected = "index"
    canvas._notify_selection()

    assert canvas._index_edges_caption.value == "Showing all connections"

    canvas._selected = None
    canvas._notify_selection()

    assert canvas._index_edges_caption.value == "Hidden until index is selected"


# --- Master and per-dimension enable switches (Post-24 fix) ---------------


def test_master_switch_off_shows_everything_regardless_of_individual_filters() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = {"concept"}  # would normally exclude note-b/note-c
    canvas._filter_tags = {"nonexistent"}

    canvas._on_filter_master_toggled(False)

    assert canvas._passes_filters("note-a") is True
    assert canvas._passes_filters("note-b") is True
    assert canvas._passes_filters("note-c") is True
    # The configured values themselves are untouched, just not applied.
    assert canvas._filter_types == {"concept"}


def test_disabling_a_dimension_keeps_its_configured_value() -> None:
    canvas = _filters_canvas()
    canvas._filter_types = {"concept"}  # would normally exclude note-b

    canvas._on_filter_types_enabled_toggled(False)

    assert canvas._passes_filters("note-b") is True  # no longer applied
    assert canvas._filter_types == {"concept"}  # but still configured

    canvas._on_filter_types_enabled_toggled(True)

    assert canvas._passes_filters("note-b") is False  # applies again, unchanged


def test_filters_reset_restores_defaults_and_fires_the_callback() -> None:
    seen: list[GraphFilterState] = []
    canvas = GraphCanvas(_page_stub(), on_filters_changed=seen.append)
    canvas._graph = _filters_fixture_graph()
    canvas._filter_types = {"concept"}
    canvas._filter_tags = {"core"}
    canvas._filter_search = "x"
    canvas._filter_date_from = "2026-01-01"
    canvas._filter_degrees = 2
    canvas._filters_enabled = False
    canvas._filter_types_enabled = False
    canvas._filter_degrees_enabled = True
    canvas._filter_index_edges_enabled = True
    canvas._filter_index_edge_limit = 25

    canvas._on_filters_reset()

    assert canvas._filter_types == {"concept", "entity", "synthesis", "source"}
    assert canvas._filter_tags == set()
    assert canvas._filter_search == ""
    assert canvas._filter_date_from is None
    assert canvas._filter_degrees == 1
    assert canvas._filters_enabled is True
    assert canvas._filter_types_enabled is True
    assert canvas._filter_degrees_enabled is False  # the one exception -- see __init__
    assert canvas._filter_index_edges_enabled is False  # the one exception -- see __init__
    assert canvas._filter_index_edge_limit == 10
    assert seen[-1] == canvas._current_filter_state()
    # Post-24 fix #3: Reset syncs every stored control too, not just state.
    assert canvas._type_checkboxes["concept"].value is True
    assert canvas._search_field.value == ""
    assert canvas._date_from_label.value == "From: Any"
    assert canvas._degrees_slider.value == 1
    assert canvas._master_switch.value is True
    assert canvas._types_switch.value is True
    assert canvas._degrees_switch.value is False
    assert canvas._index_edges_switch.value is False
    assert canvas._index_edges_slider.value == 10


def test_set_filters_syncs_without_firing_the_callback() -> None:
    seen: list[GraphFilterState] = []
    canvas = GraphCanvas(_page_stub(), on_filters_changed=seen.append)
    canvas._graph = _filters_fixture_graph()
    canvas._refresh_tag_popup()  # picks up the fixture's real tag vocabulary

    state = GraphFilterState(
        types=frozenset({"concept"}),
        tags=frozenset({"core"}),
        search="alpha",
        date_from="2026-01-01",
        date_to=None,
        degrees=3,
        filters_enabled=True,
        types_enabled=True,
        tags_enabled=False,
        search_enabled=True,
        date_enabled=True,
        degrees_enabled=True,
        index_edges_enabled=True,
        index_edge_limit=7,
    )
    canvas.set_filters(state)

    assert canvas._filter_types == {"concept"}
    assert canvas._filter_tags == {"core"}
    assert canvas._filter_search == "alpha"
    assert canvas._filter_tags_enabled is False
    assert canvas._filter_degrees_enabled is True
    assert canvas._filter_index_edges_enabled is True
    assert canvas._filter_index_edge_limit == 7
    assert seen == []  # syncing from settings is not a user change
    # Post-24 fix #3: set_filters() syncs every stored control too.
    assert canvas._type_checkboxes["concept"].value is True
    assert canvas._type_checkboxes["entity"].value is False
    assert canvas._tag_chip_controls["core"].bgcolor == theme.ACCENT
    assert canvas._search_field.value == "alpha"
    assert canvas._date_from_label.value == "From: 2026-01-01"
    assert canvas._degrees_slider.value == 3
    assert canvas._tags_switch.value is False
    assert canvas._degrees_switch.value is True
    assert canvas._index_edges_slider.value == 7
    assert canvas._index_edges_switch.value is True


def test_set_filters_is_a_no_op_for_an_unchanged_state() -> None:
    canvas = GraphCanvas(_page_stub())
    state = canvas._current_filter_state()
    panel_content_before = canvas._settings_panel.content

    canvas.set_filters(state)

    assert canvas._settings_panel.content is panel_content_before


# --- Compact switches (Post-26 fix) -----------------------------------------


def test_compact_switch_scales_and_wraps_in_a_fixed_size_container() -> None:
    """`ft.Switch` has no dedicated size property -- this scales it via
    the generic `LayoutControl` transform and wraps it in a matching
    fixed-size Container, since `scale` alone only shrinks the paint, not
    the layout box a Switch reserves in its parent Row/Column.
    """
    canvas = GraphCanvas(_page_stub())
    switch = ft.Switch(value=True)

    wrapped = canvas._compact_switch(switch)

    assert switch.scale == pytest.approx(graph_canvas._COMPACT_SWITCH_SCALE)
    assert isinstance(wrapped, ft.Container)
    assert wrapped.width == graph_canvas._COMPACT_SWITCH_WIDTH
    assert wrapped.height == graph_canvas._COMPACT_SWITCH_HEIGHT
    assert wrapped.content is switch  # the same instance, not a copy


def test_every_settings_panel_switch_is_compact() -> None:
    """Locks in that all nine switches across Filters (master + six
    per-dimension) and Display settings (Simulation, Invert Scroll-Zoom)
    actually go through `_compact_switch()`, not just that the helper
    itself works.
    """
    canvas = GraphCanvas(_page_stub())
    switches = [
        canvas._master_switch,
        canvas._types_switch,
        canvas._tags_switch,
        canvas._search_switch,
        canvas._date_switch,
        canvas._degrees_switch,
        canvas._index_edges_switch,
        canvas._simulation_switch,
        canvas._invert_scroll_switch,
    ]
    assert all(s.scale == pytest.approx(graph_canvas._COMPACT_SWITCH_SCALE) for s in switches)


def test_compact_checkbox_scales_and_wraps_in_a_fixed_square_matching_switch_height() -> None:
    """Post-26 fix #5: same technique as `_compact_switch()` -- Checkbox's
    default Material footprint is likewise bigger than its visible box.
    The target square is sized to match the compact switch's own height,
    per the explicit "same height including its borders, square" ask.
    """
    canvas = GraphCanvas(_page_stub())
    checkbox = ft.Checkbox(value=True)

    wrapped = canvas._compact_checkbox(checkbox)

    assert checkbox.scale == pytest.approx(graph_canvas._COMPACT_CHECKBOX_SCALE)
    assert isinstance(wrapped, ft.Container)
    assert wrapped.width == graph_canvas._COMPACT_CHECKBOX_SIZE
    assert wrapped.height == graph_canvas._COMPACT_CHECKBOX_SIZE
    assert wrapped.width == wrapped.height  # square
    assert wrapped.width == graph_canvas._COMPACT_SWITCH_HEIGHT  # matches the switch's height
    assert wrapped.content is checkbox  # the same instance, not a copy


def test_every_type_checkbox_is_compact() -> None:
    canvas = GraphCanvas(_page_stub())
    assert all(
        cb.scale == pytest.approx(graph_canvas._COMPACT_CHECKBOX_SCALE)
        for cb in canvas._type_checkboxes.values()
    )


def test_category_swatches_match_the_switch_thumbs_diameter_and_tightened_spacing() -> None:
    """Locks in the swatch diameter (matching Material 3's 24dp "on"-thumb
    at the same 0.65 scale the compact switch already uses) and the two
    explicit spacing ratios: Type's checkbox rows tighten to 3/4 of their
    prior spacing, and Categories' swatch rows tighten to 3/4 of *that*.
    """
    canvas = GraphCanvas(_page_stub())

    for swatch in canvas._color_swatch_controls.values():
        assert swatch.width == graph_canvas._CATEGORY_SWATCH_DIAMETER
        assert swatch.height == graph_canvas._CATEGORY_SWATCH_DIAMETER
        assert swatch.border_radius == graph_canvas._CATEGORY_SWATCH_DIAMETER / 2

    assert pytest.approx(2.0 * 0.75) == graph_canvas._TYPE_CHECKBOX_ROW_SPACING
    assert pytest.approx(
        graph_canvas._TYPE_CHECKBOX_ROW_SPACING * 0.75
    ) == graph_canvas._CATEGORY_SWATCH_ROW_SPACING


# --- Colors (Phase 25) -----------------------------------------------------


def test_default_type_colors_match_the_old_filter_defaults_plus_index() -> None:
    canvas = GraphCanvas(_page_stub())

    assert canvas._type_colors["concept"] == theme.STAGE_ATOMIZE
    assert canvas._type_colors["entity"] == theme.ACCENT
    assert canvas._type_colors["source"] == theme.STAGE_LINK
    assert canvas._type_colors["synthesis"] == theme.STAGE_LINT
    assert canvas._type_colors["index"] == theme.TEXT_DIM


def test_selecting_a_type_color_updates_the_nodes_paint_colour() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph()
    canvas._graph.add_node("note-a", type="concept")
    canvas._graph.add_node("index")

    canvas._on_type_color_selected("concept", "#123456")
    assert canvas._node_color("note-a") == "#123456"

    canvas._on_type_color_selected("index", "#654321")
    assert canvas._node_color("index") == "#654321"


def test_selecting_a_type_color_mutates_only_that_types_checkbox() -> None:
    """Post-24 fix #3's discipline extends to Colors too: a color change
    must mutate only the affected checkbox's `fill_color` and never
    reassign `_settings_panel.content`.
    """
    canvas = GraphCanvas(_page_stub())
    panel_content_before = canvas._settings_panel.content
    other_fill_before = canvas._type_checkboxes["entity"].fill_color

    canvas._on_type_color_selected("concept", "#123456")

    assert canvas._settings_panel.content is panel_content_before
    assert canvas._type_checkboxes["concept"].fill_color == "#123456"
    assert canvas._type_checkboxes["entity"].fill_color == other_fill_before
    assert canvas._color_swatch_controls["concept"].bgcolor == "#123456"


def test_node_with_missing_type_falls_back_to_the_index_colour() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph()
    canvas._graph.add_node("mystery")  # no `type` attribute at all

    assert canvas._node_color("mystery") == canvas._type_colors["index"]


# --- Physics / Animation, Zoom & Pan (Phase 25) -----------------------------


def test_disabling_simulation_means_a_drag_moves_the_node_directly() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    canvas._simulation_enabled = False
    slug, (x, y) = next(iter(canvas.node_positions.items()))

    _pan_start_at(canvas, x, y)

    assert canvas._sim_active is False

    _pan_update_to(canvas, x + 40, y + 25, 40, 25)

    assert canvas.node_positions[slug] != (x, y)


def test_simulation_strength_scales_the_tick_forces() -> None:
    default_canvas = _simulation_canvas()
    _pan_start_at(default_canvas, 400.0, 300.0)
    _pan_update_to(default_canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    for _ in range(5):
        default_canvas._simulation_tick()
    default_displacement = math.dist(
        default_canvas._positions["bystander"], (430.0, 300.0)
    )

    strong_canvas = _simulation_canvas()
    strong_canvas._simulation_strength = 2.0
    _pan_start_at(strong_canvas, 400.0, 300.0)
    _pan_update_to(strong_canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    for _ in range(5):
        strong_canvas._simulation_tick()
    strong_displacement = math.dist(strong_canvas._positions["bystander"], (430.0, 300.0))

    assert strong_displacement > default_displacement


def test_simulation_strength_slider_on_change_does_not_persist_until_change_end() -> None:
    """Post-26 fix: same on_change/on_change_end persistence split as the
    Filters sliders -- dragging Strength shouldn't write to disk (and log)
    on every tick either.
    """
    seen: list[GraphDisplaySettings] = []
    canvas = GraphCanvas(_page_stub(), on_display_settings_changed=seen.append)

    canvas._on_simulation_strength_changed(1.75)
    assert seen == []

    canvas._persist_display_settings_change()  # the slider's on_change_end
    assert seen[-1].simulation_strength == 1.75


def test_invert_scroll_zoom_flips_the_wheel_direction() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._invert_scroll_zoom = True
    start_zoom = canvas.zoom

    canvas._on_scroll(
        ft.ScrollEvent(
            name="scroll",
            control=canvas,
            data=None,
            local_position=ft.Offset(100, 100),
            global_position=ft.Offset(100, 100),
            scroll_delta=ft.Offset(0, -120),  # wheel up -- now zooms OUT
        )
    )

    assert canvas.zoom < start_zoom


# --- Min/max zoom, Node Spacing (Post-25 fix) -------------------------------


def test_min_zoom_pushes_max_zoom_up_when_it_would_invert() -> None:
    canvas = GraphCanvas(_page_stub())

    canvas._on_min_zoom_changed(3.0)  # above the default 2.0 max

    assert canvas._min_zoom == 3.0
    assert canvas._max_zoom == 3.0  # pushed up to match, never inverted
    assert canvas._max_zoom_slider.value == 3.0


def test_max_zoom_pushes_min_zoom_down_when_it_would_invert() -> None:
    canvas = GraphCanvas(_page_stub())

    canvas._on_max_zoom_changed(0.2)  # below the default 0.5 min

    assert canvas._max_zoom == 0.2
    assert canvas._min_zoom == 0.2  # pushed down to match, never inverted
    assert canvas._min_zoom_slider.value == 0.2


def test_current_zoom_reclamps_when_the_range_narrows() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._zoom = 1.8

    canvas._on_max_zoom_changed(1.2)  # narrower than the current zoom

    assert canvas.zoom == 1.2


def test_min_max_zoom_changes_do_not_rebuild_the_settings_panel() -> None:
    canvas = GraphCanvas(_page_stub())
    panel_content_before = canvas._settings_panel.content

    canvas._on_min_zoom_changed(0.3)
    canvas._on_max_zoom_changed(3.0)

    assert canvas._settings_panel.content is panel_content_before


def test_min_max_zoom_sliders_on_change_do_not_persist_until_change_end() -> None:
    seen: list[GraphDisplaySettings] = []
    canvas = GraphCanvas(_page_stub(), on_display_settings_changed=seen.append)

    canvas._on_min_zoom_changed(0.3)
    canvas._on_max_zoom_changed(3.0)
    assert seen == []  # on_change alone must not persist

    canvas._persist_display_settings_change()  # either slider's on_change_end
    assert seen[-1].min_zoom == 0.3
    assert seen[-1].max_zoom == 3.0


def test_node_spacing_affects_computed_layout_distances() -> None:
    """Pure `_layout_positions()` sensitivity check (no threading
    involved) -- a higher spacing value should spread nodes further apart
    within the same rescaled bounding radius, not just render differently
    by coincidence. Verified directly before writing this assertion, same
    "verify, don't guess" habit this project already established.
    """

    def _min_pairwise_distance(canvas: GraphCanvas) -> float:
        positions = list(canvas.node_positions.values())
        return min(
            math.dist(a, b) for i, a in enumerate(positions) for b in positions[i + 1 :]
        )

    graph = nx.DiGraph()
    for i in range(17):
        graph.add_edge(f"note-{i}", "index")
        graph.add_edge(f"note-{i}", f"source-{i % 3}")

    tight_canvas = GraphCanvas(_page_stub())
    tight_canvas._graph = graph
    tight_canvas._node_spacing = 1.0
    tight_canvas._compute_layout()

    loose_canvas = GraphCanvas(_page_stub())
    loose_canvas._graph = graph
    loose_canvas._node_spacing = 8.0
    loose_canvas._compute_layout()

    assert _min_pairwise_distance(loose_canvas) > _min_pairwise_distance(tight_canvas)


def test_node_spacing_on_change_only_updates_the_caption() -> None:
    """`on_change` (fires continuously while dragging) must never trigger
    a relayout -- only `on_change_end` does. Uses `_page_stub()`, whose
    `run_thread()` is a no-op, so this only proves the caption path is
    reached without needing to distinguish "no relayout" from "relayout
    silently swallowed" -- the real relayout-triggering assertion is the
    `_FakePage`-based end-to-end test below.
    """
    canvas = GraphCanvas(_page_stub())

    canvas._on_node_spacing_changed(6.5)

    assert canvas._node_spacing == 6.5
    assert canvas._node_spacing_caption.value == "Node Spacing: 6.5"
    assert canvas._node_spacing_slider.value != 6.5  # e.control.value only reflects on real drag


def test_node_spacing_change_end_triggers_a_real_relayout() -> None:
    """Exercises the real `page.run_thread()` -> `page.run_task()` path,
    matching `test_set_graph_computes_layout_on_a_worker_thread`'s own
    precedent for `set_graph()`.
    """
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas._graph = _fixture_graph()
        canvas._node_spacing = 7.0

        canvas._on_node_spacing_change_end()

        _wait_until(lambda: canvas.node_positions != {})
        assert sorted(canvas.node_positions) == ["alpha", "beta", "gamma"]
    finally:
        fake_page.close()


# --- Display settings sync (Phase 25) ---------------------------------------


def test_set_display_settings_triggers_a_relayout_when_spacing_changes() -> None:
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas._graph = _fixture_graph()
        canvas._compute_layout()
        _wait_until(lambda: canvas.node_positions != {})
        positions_before = dict(canvas.node_positions)

        state = canvas._current_display_settings()._replace(node_spacing=7.0)
        canvas.set_display_settings(state)

        _wait_until(lambda: canvas.node_positions != positions_before)
        assert canvas._node_spacing == 7.0
    finally:
        fake_page.close()


def test_set_display_settings_is_a_no_op_for_an_unchanged_state() -> None:
    canvas = GraphCanvas(_page_stub())
    state = canvas._current_display_settings()
    panel_content_before = canvas._settings_panel.content

    canvas.set_display_settings(state)

    assert canvas._settings_panel.content is panel_content_before


def test_set_display_settings_merges_partial_type_colors_over_defaults() -> None:
    seen: list[GraphDisplaySettings] = []
    canvas = GraphCanvas(_page_stub(), on_display_settings_changed=seen.append)

    canvas.set_display_settings(
        GraphDisplaySettings(
            type_colors={"concept": "#123456"},
            simulation_enabled=False,
            simulation_strength=1.5,
            invert_scroll_zoom=True,
            min_zoom=0.5,
            max_zoom=2.0,
            node_spacing=4.0,
        )
    )

    assert canvas._type_colors["concept"] == "#123456"
    assert canvas._type_colors["entity"] == theme.ACCENT  # untouched, from defaults
    assert canvas._type_colors["index"] == theme.TEXT_DIM  # untouched, from defaults
    assert canvas._simulation_enabled is False
    assert canvas._simulation_strength == 1.5
    assert canvas._invert_scroll_zoom is True
    assert seen == []  # syncing from settings is not a user change
    # Controls sync too, same discipline set_filters()/_on_filters_reset() use.
    assert canvas._color_swatch_controls["concept"].bgcolor == "#123456"
    assert canvas._simulation_switch.value is False
    assert canvas._simulation_strength_slider.value == 1.5
    assert canvas._invert_scroll_switch.value is True


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


# --- Live local force simulation (Phase 22) ----------------------------


def _simulation_fixture_graph() -> nx.DiGraph:
    """anchor <-> neighbor (a direct edge); bystander and far have no edge
    to anchor. Positions are set directly (not via spring_layout) so the
    physics tests are fully deterministic.
    """
    graph = nx.DiGraph()
    graph.add_edge("anchor", "neighbor")
    graph.add_node("bystander")
    graph.add_node("far")
    return graph


def _simulation_canvas() -> GraphCanvas:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _simulation_fixture_graph()
    canvas._positions = {
        "anchor": (400.0, 300.0),
        "neighbor": (400.0, 420.0),  # a direct neighbor, outside repel radius
        "bystander": (430.0, 300.0),  # within repel radius, not a neighbor
        "far": (900.0, 900.0),  # untouched by anything
    }
    return canvas


def test_pan_start_on_a_node_snapshots_home_and_starts_the_simulation() -> None:
    canvas = _simulation_canvas()
    assert canvas._sim_active is False

    _pan_start_at(canvas, 400.0, 300.0)

    assert canvas._dragging == "anchor"
    assert canvas._home_positions == {
        "anchor": (400.0, 300.0),
        "neighbor": (400.0, 420.0),
        "bystander": (430.0, 300.0),
        "far": (900.0, 900.0),
    }
    assert canvas._sim_active is True


def test_simulation_tick_repels_bystanders_and_pulls_neighbors_toward_the_anchor() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)

    initial_neighbor_dist = math.dist(canvas._positions["neighbor"], canvas._positions["anchor"])
    initial_bystander_pos = canvas._positions["bystander"]
    far_pos = canvas._positions["far"]

    for _ in range(20):
        canvas._simulation_tick()

    assert (
        math.dist(canvas._positions["neighbor"], canvas._positions["anchor"])
        < initial_neighbor_dist
    )
    assert canvas._positions["bystander"] != initial_bystander_pos
    # Pushed away from the anchor, not toward it.
    assert canvas._positions["bystander"][0] < initial_bystander_pos[0]
    # Untouched: not a neighbor, and never within the repel radius.
    assert canvas._positions["far"] == far_pos


def test_gravity_well_cannot_be_moved_by_dragging_another_node() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph([("leaf", "index")])
    canvas._positions = {"leaf": (400.0, 300.0), "index": (400.0, 420.0)}

    _pan_start_at(canvas, 400.0, 300.0)  # hits "leaf"
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    for _ in range(10):
        canvas._simulation_tick()

    assert canvas._positions["index"] == (400.0, 420.0)
    assert "index" not in canvas._sim_active_nodes


def test_dragging_the_gravity_well_still_moves_its_neighbors() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph([("leaf", "index")])
    canvas._positions = {"leaf": (400.0, 420.0), "index": (400.0, 300.0)}

    _pan_start_at(canvas, 400.0, 300.0)  # hits "index"
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    for _ in range(10):
        canvas._simulation_tick()

    assert "leaf" in canvas._sim_active_nodes
    assert canvas._positions["leaf"] != (400.0, 420.0)


def test_simulation_tick_eases_perturbed_nodes_back_to_home_after_release() -> None:
    """Uses "bystander" (not "neighbor") -- Post-25 fix #2 changed what
    happens to a *direct* neighbor on release: it's now permanently
    repositioned near the dragged node's new spot (see
    `_on_pan_end()`/`_neighbor_reposition_targets()`), not eased back to
    its old home. A bystander (pulled in by repel, not a graph edge) is
    untouched by that hand-off and still exhibits the original spring-
    back-to-home behavior this test is actually about.
    """
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    for _ in range(15):
        canvas._simulation_tick()

    home = canvas._home_positions["bystander"]
    assert math.dist(canvas._positions["bystander"], home) > graph_canvas._SIM_SETTLE_DIST_EPSILON

    _pan_end(canvas)
    assert canvas._dragging is None

    for _ in range(graph_canvas._SIM_SETTLE_MAX_TICKS):
        canvas._simulation_tick()
        if not canvas._sim_active:
            break

    assert canvas._sim_active is False
    assert canvas._positions["bystander"] == home
    assert "bystander" not in canvas._sim_active_nodes


def test_simulation_stops_at_the_safety_cap_even_if_unconverged() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    canvas._simulation_tick()
    _pan_end(canvas)

    # Force a node to look perpetually unconverged so only the tick-count
    # safety cap (not real convergence) can end the loop.
    canvas._sim_active_nodes = {"neighbor"}
    canvas._home_positions["neighbor"] = (10_000.0, 10_000.0)

    for i in range(graph_canvas._SIM_SETTLE_MAX_TICKS + 5):
        canvas._simulation_tick()
        if not canvas._sim_active:
            assert i >= graph_canvas._SIM_SETTLE_MAX_TICKS
            break
    else:
        raise AssertionError("simulation never stopped despite the safety cap")


def test_a_plain_click_does_not_leave_the_simulation_running() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)  # hits "anchor"
    _pan_end(canvas)  # released before any pan_update -- a plain click

    canvas._simulation_tick()

    assert canvas._sim_active is False
    assert canvas._positions["neighbor"] == (400.0, 420.0)
    assert canvas._positions["bystander"] == (430.0, 300.0)


def test_pan_update_does_not_redraw_directly_while_the_simulation_is_running() -> None:
    """Regression: the tick loop already redraws at ~30fps while a node-drag
    is active (see _start_simulation()); a second, per-pointer-event redraw
    on top of that roughly doubles how often the full shape list gets
    serialized across the Python<->Flutter boundary during a drag.
    """
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)  # starts the simulation (also redraws once, for selection)
    assert canvas._sim_active is True
    static_after_select = canvas._static_canvas.shapes
    dynamic_after_select = canvas._dynamic_canvas.shapes

    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)

    # _redraw_static()/_redraw_dynamic() always build a fresh list -- the
    # same objects surviving prove _on_pan_update() didn't call either.
    assert canvas._static_canvas.shapes is static_after_select
    assert canvas._dynamic_canvas.shapes is dynamic_after_select


def test_pan_update_still_redraws_directly_if_the_simulation_is_not_running() -> None:
    """Safety net for e.g. a graph reload stopping the simulation mid-drag
    (set_graph() sets _sim_active = False but leaves _dragging alone) --
    the drag must keep rendering even without the tick loop.
    """
    canvas = _simulation_canvas()
    canvas._dragging = "anchor"  # dragging without going through _start_simulation()
    assert canvas._sim_active is False

    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)

    # "anchor" is the dragged node, so it (and its edge to "neighbor")
    # render on the dynamic canvas.
    assert canvas._dynamic_canvas.shapes != []


def test_set_graph_stops_an_in_flight_simulation() -> None:
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas._graph = _simulation_fixture_graph()
        canvas._positions = {
            "anchor": (400.0, 300.0),
            "neighbor": (400.0, 420.0),
            "bystander": (430.0, 300.0),
            "far": (900.0, 900.0),
        }
        _pan_start_at(canvas, 400.0, 300.0)
        _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
        assert canvas._sim_active is True

        canvas.set_graph(_fixture_graph())

        assert canvas._sim_active is False
        # Let the background layout worker finish before tearing down the
        # fake page's event loop, so it isn't left with an unawaited
        # coroutine (matching test_set_graph_computes_layout_on_a_worker_thread).
        _wait_until(lambda: sorted(canvas.node_positions) == ["alpha", "beta", "gamma"])
    finally:
        fake_page.close()


# --- Static/dynamic render split (Post-22 fix) --------------------------


def test_static_layer_holds_everything_before_any_drag() -> None:
    canvas = _simulation_canvas()

    assert canvas._dynamic_slugs() == set()
    assert canvas._build_dynamic_shapes() == []
    # 1 edge (anchor-neighbor) + 4 nodes x 1 circle.
    assert len(canvas._build_static_shapes()) == 5


def test_dragging_partitions_shapes_between_static_and_dynamic_layers() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)  # hits "anchor"
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    canvas._simulation_tick()  # pulls "neighbor" (edge) and "bystander" (proximity) in

    assert canvas._dynamic_slugs() == {"anchor", "neighbor", "bystander"}

    static_shapes = canvas._build_static_shapes()
    dynamic_shapes = canvas._build_dynamic_shapes()

    # "far" is the only untouched node -- 1 node x 1 circle, no edges (the
    # only edge in the fixture touches the now-dynamic anchor/neighbor pair).
    assert len(static_shapes) == 1
    # 3 dynamic nodes x 1 circle + their connecting edge.
    assert len(dynamic_shapes) == 4
    # Nothing is duplicated or dropped between the two layers.
    assert len(static_shapes) + len(dynamic_shapes) == len(canvas.build_shapes())

    static_lines = [s for s in static_shapes if isinstance(s, cv.Line)]
    dynamic_lines = [s for s in dynamic_shapes if isinstance(s, cv.Line)]
    assert len(static_lines) == 0
    assert len(dynamic_lines) == 1


def test_dynamic_edge_to_a_static_endpoint_draws_a_shadow_circle_on_top() -> None:
    """Regression: the static canvas renders *underneath* the dynamic one,
    so a cross-boundary edge (dragging a leaf whose only edge goes to
    `index`, excluded from the dynamic set by the gravity well -- the
    common case) would otherwise draw over its static endpoint's circle,
    regardless of within-canvas shape ordering.

    Post-26 fix: index's own edges only render while index is selected,
    which dragging a *different* node normally clears -- `canvas._selected`
    is forced back to index here to isolate the shape-builder's z-order
    logic under test from that unrelated selection-follows-drag behavior.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._graph = nx.DiGraph([("leaf", "index")])
    canvas._positions = {"leaf": (400.0, 300.0), "index": (400.0, 420.0)}

    _pan_start_at(canvas, 400.0, 300.0)  # hits "leaf"
    canvas._selected = "index"  # see docstring -- isolates the z-order check
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    canvas._simulation_tick()

    assert canvas._dynamic_slugs() == {"leaf"}  # "index" excluded (gravity well)

    dynamic_shapes = canvas._build_dynamic_shapes()
    edge_index = next(i for i, s in enumerate(dynamic_shapes) if isinstance(s, cv.Line))
    circle_indices = [i for i, s in enumerate(dynamic_shapes) if isinstance(s, cv.Circle)]

    # The dragged node's own circle, plus a shadow copy of "index"'s --
    # both must come after (draw on top of) the edge.
    assert len(circle_indices) == 2
    assert all(i > edge_index for i in circle_indices)


def test_simulation_tick_reports_changed_only_on_set_transitions() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)  # hits "anchor"
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)

    # First tick: the anchor becomes dynamic for the first time.
    assert canvas._simulation_tick() is True

    # Steady state: same drag, no new node enters/leaves this tick.
    assert canvas._simulation_tick() is False

    # Release: on the tick where the anchor drops out of the dynamic set
    # (and nothing else changes yet), membership changes again.
    _pan_end(canvas)
    assert canvas._simulation_tick() is True


def test_redraw_static_and_dynamic_touch_only_their_own_canvas() -> None:
    canvas = _simulation_canvas()
    canvas._redraw_all()
    static_before = canvas._static_canvas.shapes
    dynamic_before = canvas._dynamic_canvas.shapes

    canvas._redraw_static()
    assert canvas._static_canvas.shapes is not static_before  # rebuilt
    assert canvas._dynamic_canvas.shapes is dynamic_before  # untouched

    static_after_static_redraw = canvas._static_canvas.shapes
    canvas._redraw_dynamic()
    assert canvas._dynamic_canvas.shapes is not dynamic_before  # rebuilt
    assert canvas._static_canvas.shapes is static_after_static_redraw  # untouched


# --- Hover-only labels (Post-22 fix) -------------------------------------


def test_hovering_a_node_shows_its_label_and_moving_away_clears_it() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    slug, (x, y) = next(iter(canvas.node_positions.items()))
    _hover_at(canvas, x, y)

    assert canvas._hovered == slug
    assert len(canvas._hover_canvas.shapes) == 1
    assert isinstance(canvas._hover_canvas.shapes[0], cv.Text)

    _hover_at(canvas, -9999, -9999)  # empty background

    assert canvas._hovered is None
    assert canvas._hover_canvas.shapes == []


def test_exiting_the_canvas_clears_the_hovered_label() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()

    slug, (x, y) = next(iter(canvas.node_positions.items()))
    _hover_at(canvas, x, y)
    assert canvas._hovered == slug

    _exit_hover(canvas)

    assert canvas._hovered is None
    assert canvas._hover_canvas.shapes == []


def test_hovering_the_same_node_again_does_not_redraw() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    slug, (x, y) = next(iter(canvas.node_positions.items()))
    _hover_at(canvas, x, y)
    shapes_after_first_hover = canvas._hover_canvas.shapes

    _hover_at(canvas, x, y)  # same node, e.g. a tiny mouse jitter

    assert canvas._hover_canvas.shapes is shapes_after_first_hover


def test_dragging_a_node_moves_its_hover_label_with_it() -> None:
    """Regression: no PointerHoverEvent fires while a drag's button is
    down, so `self._hovered` used to freeze at whatever it was when the
    drag started -- the label then stayed put instead of tracking the
    node being moved.

    Drags without going through `_start_simulation()` (matching
    `test_pan_update_still_redraws_directly_if_the_simulation_is_not_running`'s
    own pattern) -- with a real page, the tick loop itself is what redraws
    the hover canvas every frame during a drag; under the headless
    `_page_stub()`, that loop never actually runs, so exercising the same
    `not self._sim_active` fallback path `_on_pan_update()` already has is
    what a synchronous test can actually observe.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    slug, (x, y) = next(iter(canvas.node_positions.items()))
    canvas._dragging = slug
    assert canvas._sim_active is False

    _pan_update_to(canvas, x + 40, y + 25, 40, 25)

    new_x, new_y = canvas.node_positions[slug]
    assert (new_x, new_y) != (x, y)  # sanity: the node actually moved
    expected = canvas._hover_label_shape(slug, new_x, new_y)
    label = canvas._hover_canvas.shapes[0]
    assert (label.value, label.x, label.y) == (expected.value, expected.x, expected.y)


def test_dragging_a_different_node_overrides_a_stale_hover() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    (slug_a, (xa, ya)), (slug_b, (xb, yb)) = list(canvas.node_positions.items())[:2]

    _hover_at(canvas, xa, ya)
    assert canvas._hovered == slug_a

    _pan_start_at(canvas, xb, yb)  # drags a *different* node than the hovered one

    labels = [s for s in canvas._hover_canvas.shapes if isinstance(s, cv.Text)]
    assert len(labels) == 1
    assert labels[0].value == slug_b


def test_hover_label_clears_after_drag_ends_with_no_prior_hover() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    slug, (x, y) = next(iter(canvas.node_positions.items()))
    assert canvas._hovered is None

    _pan_start_at(canvas, x, y)
    assert canvas._hover_canvas.shapes != []  # follows the dragged node

    _pan_end(canvas)

    assert canvas._hover_canvas.shapes == []  # falls back to self._hovered, still None


def test_build_shapes_never_includes_a_hover_label() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._graph = _fixture_graph()
    canvas._compute_layout()
    slug, (x, y) = next(iter(canvas.node_positions.items()))
    _hover_at(canvas, x, y)

    assert not any(isinstance(s, cv.Text) for s in canvas.build_shapes())


def test_dragging_a_node_end_to_end_runs_and_settles_the_simulation() -> None:
    """Exercises the real `page.run_task()` loop -- the direct
    `_simulation_tick()` tests above call it synchronously and bypass the
    async loop entirely.
    """
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas._graph = _simulation_fixture_graph()
        canvas._positions = {
            "anchor": (400.0, 300.0),
            "neighbor": (400.0, 420.0),
            "bystander": (430.0, 300.0),
            "far": (900.0, 900.0),
        }

        _pan_start_at(canvas, 400.0, 300.0)
        assert canvas._sim_active is True
        _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
        _wait_until(lambda: canvas._positions["neighbor"] != (400.0, 420.0))

        _pan_end(canvas)
        _wait_until(lambda: canvas._sim_active is False, timeout=10.0)

        assert canvas._positions["far"] == (900.0, 900.0)
    finally:
        fake_page.close()


# --- Constant-velocity reposition (Post-25 fix #2) -------------------------


def test_ease_in_out_endpoints_and_symmetry() -> None:
    ease = graph_canvas._ease_in_out
    assert ease(0.0) == 0.0
    assert ease(1.0) == 1.0
    assert ease(0.5) == pytest.approx(0.5)
    # Symmetric around the midpoint.
    for t in (0.1, 0.25, 0.4):
        assert ease(t) == pytest.approx(1.0 - ease(1.0 - t))
    # Monotonic.
    samples = [ease(t / 10) for t in range(11)]
    assert samples == sorted(samples)


def test_reposition_tick_moves_slower_near_the_ends_than_the_middle() -> None:
    """The actual ease-in-out signature: per-tick displacement should be
    smaller in the first/last thirds of the journey than through the
    middle third -- not a constant step throughout.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    canvas._start_reposition({"a": (1000.0, 0.0)})
    # ~100 ticks expected (1000px at the 300px/sec average speed, 1/30s
    # per tick) -- capped well above that so the full journey, including
    # the deceleration phase, is actually captured.
    deltas = []
    for _ in range(150):
        before = canvas._positions["a"][0]
        still_moving = canvas._reposition_tick()
        deltas.append(canvas._positions["a"][0] - before)
        if not still_moving:
            break

    assert len(deltas) >= 6  # enough samples for the shape to be meaningful
    third = len(deltas) // 3
    first_third = deltas[:third]
    middle_third = deltas[third : 2 * third]
    last_third = deltas[2 * third :]
    assert sum(first_third) / len(first_third) < sum(middle_third) / len(middle_third)
    assert sum(last_third) / len(last_third) < sum(middle_third) / len(middle_third)
    assert canvas._positions["a"] == (1000.0, 0.0)  # still lands exactly on target


def test_reposition_tick_snaps_exactly_once_within_the_settle_epsilon() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (99.0, 0.0)}
    canvas._start_reposition({"a": (100.0, 0.0)})  # well under the settle epsilon

    still_moving = canvas._reposition_tick()

    assert canvas._positions["a"] == (100.0, 0.0)
    assert still_moving is False
    assert canvas._reposition_targets == {}
    assert canvas._reposition_start_positions == {}
    assert canvas._reposition_progress == {}


def test_reposition_tick_completes_a_short_journey_in_one_tick() -> None:
    """A journey well under one tick's worth of average-speed distance
    still finishes in a single tick -- same graceful degradation to an
    instant snap the old constant-velocity design had.
    """
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    step = graph_canvas._REPOSITION_SPEED * graph_canvas._SIM_TICK_DT
    canvas._start_reposition({"a": (step / 4, 0.0)})

    still_moving = canvas._reposition_tick()

    assert canvas._positions["a"] == (step / 4, 0.0)
    assert still_moving is False


def test_reposition_tick_skips_and_drops_a_node_currently_being_dragged() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    canvas._start_reposition({"a": (1000.0, 0.0)})
    canvas._dragging = "a"

    still_moving = canvas._reposition_tick()

    assert canvas._positions["a"] == (0.0, 0.0)  # untouched -- the live drag wins
    assert still_moving is False
    assert canvas._reposition_targets == {}  # dropped, not resumed later
    assert canvas._reposition_start_positions == {}
    assert canvas._reposition_progress == {}


def test_start_reposition_resets_progress_for_a_changed_target() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    canvas._start_reposition({"a": (1000.0, 0.0)})
    canvas._reposition_tick()
    progress_before = canvas._reposition_progress["a"]
    assert progress_before > 0.0

    canvas._start_reposition({"a": (2000.0, 0.0)})  # a genuinely different target

    assert canvas._reposition_progress["a"] == 0.0  # fresh curve from here
    assert canvas._reposition_start_positions["a"] == canvas._positions["a"]


def test_start_reposition_leaves_progress_untouched_for_the_same_target() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    canvas._start_reposition({"a": (1000.0, 0.0)})
    canvas._reposition_tick()
    progress_before = canvas._reposition_progress["a"]
    start_before = canvas._reposition_start_positions["a"]

    canvas._start_reposition({"a": (1000.0, 0.0)})  # the same target, re-merged

    assert canvas._reposition_progress["a"] == progress_before
    assert canvas._reposition_start_positions["a"] == start_before


def test_start_reposition_merges_into_an_already_active_reposition() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0), "b": (0.0, 0.0)}

    canvas._start_reposition({"a": (100.0, 0.0)})
    assert canvas._reposition_active is True

    canvas._start_reposition({"b": (200.0, 0.0)})

    assert canvas._reposition_targets == {"a": (100.0, 0.0), "b": (200.0, 0.0)}


def test_start_reposition_places_a_brand_new_node_directly_at_its_target() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {}

    canvas._start_reposition({"new-node": (50.0, 60.0)})

    assert canvas._positions["new-node"] == (50.0, 60.0)


def test_neighbor_reposition_targets_lands_neighbors_near_the_anchor() -> None:
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    graph.add_edge("n1", "anchor")
    graph.add_edge("n2", "anchor")
    graph.add_edge("n3", "anchor")
    canvas._graph = graph
    canvas._positions = {
        "anchor": (500.0, 500.0),
        "n1": (0.0, 0.0),
        "n2": (0.0, 0.0),
        "n3": (0.0, 0.0),
    }

    targets = canvas._neighbor_reposition_targets("anchor")

    assert set(targets) == {"n1", "n2", "n3"}
    anchor_pos = canvas._positions["anchor"]
    for pos in targets.values():
        dist = math.dist(pos, anchor_pos)
        assert dist == pytest.approx(graph_canvas._SIM_NEIGHBOR_REST_LENGTH, rel=0.15)
    values = list(targets.values())
    min_pairwise = min(
        math.dist(a, b) for i, a in enumerate(values) for b in values[i + 1 :]
    )
    assert min_pairwise > 2 * graph_canvas._NODE_RADIUS


def test_neighbor_reposition_targets_excludes_the_gravity_well() -> None:
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    graph.add_edge("leaf", "index")
    graph.add_edge("n1", "leaf")  # leaf's own neighbor set: index, n1
    canvas._graph = graph
    canvas._positions = {"leaf": (0.0, 0.0), "index": (0.0, 0.0), "n1": (10.0, 10.0)}

    targets = canvas._neighbor_reposition_targets("leaf")

    assert "index" not in targets
    assert "n1" in targets


def test_neighbor_reposition_targets_ignores_non_neighbors() -> None:
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    graph.add_edge("n1", "anchor")
    graph.add_node("unrelated")
    canvas._graph = graph
    canvas._positions = {"anchor": (0.0, 0.0), "n1": (10.0, 0.0), "unrelated": (500.0, 500.0)}

    targets = canvas._neighbor_reposition_targets("anchor")

    assert set(targets) == {"n1"}


def test_neighbor_reposition_targets_stays_clear_of_overlap_at_high_neighbor_count() -> None:
    canvas = GraphCanvas(_page_stub())
    graph = nx.DiGraph()
    for i in range(60):
        graph.add_edge(f"n{i}", "anchor")
    canvas._graph = graph
    canvas._positions = {"anchor": (0.0, 0.0)} | {f"n{i}": (0.0, 0.0) for i in range(60)}

    targets = canvas._neighbor_reposition_targets("anchor")

    values = list(targets.values())
    min_pairwise = min(
        math.dist(a, b) for i, a in enumerate(values) for b in values[i + 1 :]
    )
    assert min_pairwise > 2 * graph_canvas._NODE_RADIUS


def test_pan_end_starts_a_reposition_for_direct_neighbors_and_hands_off_from_the_simulation() -> (
    None
):
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)  # hits "anchor"
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    canvas._simulation_tick()  # populates _sim_active_nodes/_sim_velocities
    assert "neighbor" in canvas._sim_active_nodes

    _pan_end(canvas)

    assert canvas._reposition_active is True
    assert "neighbor" in canvas._reposition_targets
    assert "neighbor" not in canvas._sim_active_nodes
    assert "neighbor" not in canvas._sim_velocities


def test_pan_end_leaves_a_bystander_on_the_old_spring_back_path() -> None:
    canvas = _simulation_canvas()
    _pan_start_at(canvas, 400.0, 300.0)
    _pan_update_to(canvas, 500.0, 300.0, dx=100.0, dy=0.0)
    canvas._simulation_tick()  # populates _sim_active_nodes
    assert "bystander" in canvas._sim_active_nodes

    _pan_end(canvas)

    assert "bystander" not in canvas._reposition_targets
    assert "bystander" in canvas._sim_active_nodes


def test_pan_end_does_not_reposition_when_simulation_is_disabled() -> None:
    canvas = _simulation_canvas()
    canvas._simulation_enabled = False
    canvas._dragging = "anchor"  # bypasses _start_simulation()'s own early-return

    _pan_end(canvas)

    assert canvas._reposition_active is False
    assert canvas._reposition_targets == {}


def test_set_graph_cancels_an_in_flight_reposition() -> None:
    canvas = GraphCanvas(_page_stub())
    canvas._positions = {"a": (0.0, 0.0)}
    canvas._start_reposition({"a": (1000.0, 0.0)})
    assert canvas._reposition_active is True

    canvas.set_graph(nx.DiGraph())

    assert canvas._reposition_active is False
    assert canvas._reposition_targets == {}


def test_node_spacing_change_end_animates_rather_than_snaps() -> None:
    """Exercises the real `page.run_thread()` -> `page.run_task()` ->
    reposition-loop path end to end, matching
    `test_set_graph_computes_layout_on_a_worker_thread`'s own precedent --
    confirms `_reposition_active` genuinely goes through a live, running
    phase (not an instant flip straight to settled) and that positions
    land on the freshly computed layout at the new spacing once it does.
    """
    fake_page = _FakePage()
    try:
        canvas = GraphCanvas(fake_page)
        canvas._graph = _fixture_graph()
        canvas._compute_layout()
        _wait_until(lambda: canvas.node_positions != {})
        canvas._node_spacing = 8.0  # a real, different layout to animate toward
        expected_final = canvas._layout_positions()

        canvas._on_node_spacing_change_end()

        _wait_until(lambda: canvas._reposition_active is True)
        # Reached mid-flight while genuinely active -- proof this eases in
        # via the loop rather than the target landing being instant.
        assert canvas.node_positions != expected_final

        _wait_until(lambda: canvas._reposition_active is False, timeout=10.0)

        assert canvas.node_positions == expected_final
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


# --- Dashboard panel (flet-charts) --------------------------------------


def test_dashboard_panel_defaults_to_all_zero_stats_with_no_connection() -> None:
    panel = DashboardPanel()

    assert panel.stats.concepts == 0
    assert panel.stats.total_ingested == 0
    assert panel.stats.failures == 0


def test_dashboard_panel_chart_has_a_bar_per_note_type() -> None:
    chart = DashboardPanel().build_chart()

    assert [label.label.value for label in chart.bottom_axis.labels] == [
        "Concepts",
        "Entities",
        "Sources",
        "Synthesis",
    ]
    assert [group.rods[0].to_y for group in chart.groups] == [0, 0, 0, 0]


def test_dashboard_panel_chart_max_y_never_collapses_to_zero() -> None:
    """An empty vault still needs a drawable axis."""
    assert DashboardPanel().build_chart().max_y == 1


def test_dashboard_panel_reflects_real_vault_stats(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    conn.execute(
        "INSERT INTO notes (path, title, slug, type, tags, sources, content_hash, updated_at) "
        "VALUES ('wiki/a.md', 'A', 'a', 'concept', '[]', '[]', 'hash', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO queue (title, raw_path, status, created_at, updated_at) "
        "VALUES ('doc', 'raw/doc.txt', 'completed', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"
    )
    conn.commit()

    panel = DashboardPanel()
    panel.set_connection(conn, vault_root)

    assert panel.stats.concepts == 1
    assert panel.stats.total_ingested == 1
    assert panel.build_chart().max_y >= 1


def test_dashboard_panel_distinguishes_wikilinks_from_backlinks(vault_root: Path) -> None:
    """Unlike `test_dashboard_panel_reflects_real_vault_stats`'s DB-only
    row (no file on disk, so wikilink counting sees nothing there), this
    writes a real note referencing the same target twice -- the concrete
    case the two separate stat cards exist to show.
    """
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    note_dir = vault_root / "wiki" / "concepts"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / "a.md"
    note_path.write_text(
        "---\ntitle: A\nslug: a\ntype: concept\ntags: []\nsources: []\n---\n\n"
        "See [[index]] here and [[index]] again.\n",
        encoding="utf-8",
    )
    upsert_note_from_file(conn, vault_root, note_path)
    sync_links(conn, vault_root)

    panel = DashboardPanel()
    panel.set_connection(conn, vault_root)

    assert panel.stats.total_wikilinks == 2
    assert panel.stats.total_backlinks == 1


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
