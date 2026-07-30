"""Spatial network graph canvas -- the central workspace of the desktop UI.

Ports the layout and drawing logic of the Phase 15 `GraphCanvasItem`
(`networkx.spring_layout` computed off the UI thread, then nodes and edges
drawn by hand) onto Flet's `Canvas`. Node categories, the legend, and the
zoom controls follow the design mockup; scroll-to-zoom, click-drag-to-pan
the whole canvas, and a "basic info" overlay on node selection (Phase 17)
extend past the mockup, which only demonstrated per-node dragging.
"""

import asyncio
import contextlib
import difflib
import math
from typing import NamedTuple

import flet as ft
import flet.canvas as cv
import networkx as nx

from llm_wiki.gui import theme

_NODE_RADIUS = 9.0
_LAYOUT_MARGIN = 60.0
# Multiplier over spring_layout's own k=1/sqrt(n) default -- see
# GraphCanvas._layout_positions() for why nodes need more room than that.
_LAYOUT_SPACING = 4.0
# Node count the base canvas size (_BASE_WIDTH/_BASE_HEIGHT) was tuned
# for -- see GraphCanvas._layout_scale().
_LAYOUT_BASE_NODE_COUNT = 12
_ZOOM_STEP = 0.15
_SCROLL_ZOOM_STEP = 0.08
_MIN_ZOOM = 0.5
_MAX_ZOOM = 2.0
# Nominal layout space; the canvas scales to its real size on first resize.
_BASE_WIDTH = 900.0
_BASE_HEIGHT = 560.0

# Phase 22 -- live local force simulation while dragging a node. Re-running
# nx.spring_layout() per frame (the original hunch) was benchmarked as fast
# enough but proved numerically unstable: its internal cooling temperature
# resets on every call based on current position spread, so a moving fixed
# node makes it diverge. This is a small, explicitly damped/clamped
# spring-mass step instead -- inherently stable regardless of how far/fast
# the dragged node moves. Starting values, tuned empirically against a
# direct simulation; expect to retune against the real UI.
_SIM_TICK_DT = 1 / 30  # seconds between simulation ticks
_SIM_REPEL_RADIUS = 90.0  # px; bystanders inside this get pushed away
_SIM_REPEL_STRENGTH = 4000.0
_SIM_NEIGHBOR_REST_LENGTH = 70.0  # px; a dragged node's neighbor eases toward this
_SIM_NEIGHBOR_SPRING_K = 18.0
_SIM_HOME_SPRING_K = 16.0  # pulls a perturbed node back toward its pre-drag spot
_SIM_DAMPING = 0.72
_SIM_MAX_SPEED = 900.0  # px/sec, hard clamp -- guarantees stability
# Settle is distance-based, not velocity-based: with heavy damping, speed can
# dip below any velocity threshold while a node is still meaningfully far
# from home (each tick discards a fixed fraction of velocity regardless of
# remaining displacement), which would falsely mark it "at rest" mid-flight
# and strand it there. Confirmed by direct simulation before landing on this.
_SIM_SETTLE_DIST_EPSILON = 2.0  # px; below this + anchor released = at rest
_SIM_SETTLE_MAX_TICKS = 90  # safety cap (~3s) so the loop always terminates

# Post-22 fix -- every note is guaranteed to backlink to [[index]] (Phase
# 18's Related-block), so it's structurally the hub of this graph. Pinning
# it at the canvas center keeps the graph organized and stops it from
# being dragged around by every other node's movement -- see
# _layout_positions() and _simulation_tick().
_GRAVITY_WELL_SLUG = "index"

# Phase 24 -- Filters. Just the 4 real note types' keys/labels; their
# *colors* moved to _DEFAULT_TYPE_COLORS below (Phase 25) once they
# became user-customizable rather than fixed constants.
_FILTER_NOTE_TYPE_LABELS = (
    ("concept", "Concept"),
    ("entity", "Entity"),
    ("source", "Source"),
    ("synthesis", "Synthesis"),
)
# difflib.SequenceMatcher ratio below which a search term doesn't count as
# a loose match -- an exact substring always matches regardless (see
# _fuzzy_match()); this only governs the genuinely-fuzzy fallback.
_FUZZY_MATCH_THRESHOLD = 0.4

# Phase 25 -- node colors, by real note `type` (replacing the old
# hash-bucket "category" system, which had no relationship at all to a
# note's actual type or to the Filters panel's own type colors -- see
# GraphCanvas._node_color()). Defaults match the pre-Phase-25 Filters
# checkbox colors exactly, so the *look* of the 4 real types doesn't
# change, only the mechanism -- and now the graph and Filters agree.
_DEFAULT_TYPE_COLORS: dict[str, str] = {
    "concept": theme.STAGE_ATOMIZE,
    "entity": theme.ACCENT,
    "source": theme.STAGE_LINK,
    "synthesis": theme.STAGE_LINT,
    # The gravity well is never a real `notes` row -- no `type` of its
    # own -- so it gets its own default: a muted neutral, distinct from
    # the four vivid type colors and from theme.ACCENT (reserved for the
    # current selection highlight).
    _GRAVITY_WELL_SLUG: theme.TEXT_DIM,
}
# Preset choices offered by the Colors picker (Phase 25) -- a curated,
# still-relevant set inherited from the old CATEGORY_COLORS hash-bucket
# palette (now removed from theme.py, since nothing else used it) plus
# the four colors already used as _DEFAULT_TYPE_COLORS' own defaults.
_GRAPH_SWATCH_PALETTE: tuple[str, ...] = (
    theme.STAGE_ATOMIZE,
    theme.ACCENT,
    theme.STAGE_LINK,
    theme.STAGE_LINT,
    theme.ERROR,
    "#AD87ED",  # oklch(70% 0.15 300) -- was CATEGORY_COLORS["core"]
    "#DA76BB",  # oklch(70% 0.15 340) -- was CATEGORY_COLORS["rendering"]
    "#CE9200",  # oklch(70% 0.15 80) -- was CATEGORY_COLORS["physics"]
    "#3BB974",  # oklch(70% 0.15 155) -- was CATEGORY_COLORS["editor"]
    "#ED7665",  # oklch(70% 0.15 30) -- was CATEGORY_COLORS["multiplayer"]
    theme.TEXT_DIM,
)


class GraphFilterState(NamedTuple):
    """Bundles every Filters value in one payload -- `on_filters_changed`
    fires this whole; `set_filters()` accepts it whole -- rather than
    eleven separate callbacks/setters for eleven independent fields.
    """

    types: frozenset[str]
    tags: frozenset[str]
    search: str
    date_from: str | None
    date_to: str | None
    # Post-24 fix -- always a real 1-5 value; "off" is degrees_enabled,
    # not a None/0 sentinel (the slider is always interactive now).
    degrees: int
    # Post-24 fix -- one enable switch per dimension, plus a master.
    filters_enabled: bool
    types_enabled: bool
    tags_enabled: bool
    search_enabled: bool
    date_enabled: bool
    degrees_enabled: bool


class GraphDisplaySettings(NamedTuple):
    """Bundles Colors/Physics/Zoom-Pan (Phase 25) in one payload -- the
    same "on_*_changed fires this whole, set_*() accepts it whole" shape
    `GraphFilterState` already established.
    """

    # Only the types the caller has actually customized -- GraphCanvas
    # always merges this over _DEFAULT_TYPE_COLORS, never assigns it
    # directly, so a config missing a key (an older save, or a future new
    # type) falls back cleanly instead of a lookup ever raising.
    type_colors: dict[str, str]
    simulation_enabled: bool
    simulation_strength: float
    invert_scroll_zoom: bool


def _fuzzy_match(query: str, text: str) -> bool:
    """A "loosely matching" search: always true for an empty query or a
    real substring hit; falls back to a similarity ratio for genuine
    typos/fuzziness. No new dependency -- stdlib difflib is plenty for
    short strings (titles/slugs) over a small corpus (one vault).
    """
    query = query.strip().lower()
    if not query:
        return True
    text = text.lower()
    if query in text:
        return True
    return difflib.SequenceMatcher(None, query, text).ratio() >= _FUZZY_MATCH_THRESHOLD


class GraphCanvas(ft.Container):
    """Interactive canvas visualizing the vault's `[[wikilink]]` network."""

    def __init__(
        self,
        page: ft.Page,
        on_node_selected=None,
        on_settings_panel_toggled=None,
        on_filters_changed=None,
        on_display_settings_changed=None,
    ) -> None:
        super().__init__()
        self._page = page  # NOT self.page -- ft.Container reserves that name
        self.expand = True
        self.bgcolor = theme.CANVAS_BG
        self.on_node_selected = on_node_selected
        # Phase 23 -- fired only on a genuine user click of the settings
        # panel's toggle (never from set_settings_panel_expanded(), which
        # syncs from persisted settings), so the caller can persist it.
        self.on_settings_panel_toggled = on_settings_panel_toggled
        self._settings_panel_expanded = True

        # Phase 24 -- Filters. Fired only on a genuine user change (never
        # from set_filters(), which syncs from persisted settings), same
        # split as on_settings_panel_toggled above.
        self.on_filters_changed = on_filters_changed
        self._filter_types: set[str] = {t for t, _label in _FILTER_NOTE_TYPE_LABELS}
        self._filter_tags: set[str] = set()
        self._filter_search = ""
        self._filter_date_from: str | None = None
        self._filter_date_to: str | None = None
        # Post-24 fix -- always a real 1-5 value; whether it's applied is
        # controlled by self._filter_degrees_enabled, not this value.
        self._filter_degrees = 1
        # Post-24 fix -- one enable switch per dimension, plus a master.
        # Four default True (matching the pre-Post-24 always-on behavior --
        # each starts at a no-op value, so "enabled" is harmless). Degrees
        # is the exception: it has no no-op state, activating the instant
        # any node is selected -- a normal browsing action, not a
        # deliberate filter choice -- so it must start off.
        self._filters_enabled = True
        self._filter_types_enabled = True
        self._filter_tags_enabled = True
        self._filter_search_enabled = True
        self._filter_date_enabled = True
        self._filter_degrees_enabled = False
        # Recomputed in _notify_selection() whenever self._selected changes
        # -- see _passes_filters() and _update_degrees_from_selected().
        self._degrees_from_selected: dict[str, int] = {}
        # Shown via self._page.show_dialog(...) -- the same mechanism
        # app.py's own AlertDialogs use, confirmed as this Flet version's
        # established pattern (no page.overlay/page.services registration
        # needed first).
        self._date_picker_from = ft.DatePicker(
            on_change=lambda e: self._on_filter_date_from_changed(e.control.value)
        )
        self._date_picker_to = ft.DatePicker(
            on_change=lambda e: self._on_filter_date_to_changed(e.control.value)
        )

        # Phase 25 -- Colors / Physics / Zoom & Pan. Fired only on a
        # genuine user change (never from set_display_settings(), which
        # syncs from persisted settings), same split as on_filters_changed.
        self.on_display_settings_changed = on_display_settings_changed
        # Always the full 5-key map (merged over _DEFAULT_TYPE_COLORS at
        # every sync point -- see set_display_settings()) so a lookup
        # never has to guard a missing key.
        self._type_colors: dict[str, str] = dict(_DEFAULT_TYPE_COLORS)
        self._simulation_enabled = True
        self._simulation_strength = 1.0
        self._invert_scroll_zoom = False

        self._graph = nx.DiGraph()
        self._positions: dict[str, tuple[float, float]] = {}
        self._width = _BASE_WIDTH
        self._height = _BASE_HEIGHT
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._dragging: str | None = None
        self._panning = False
        self._selected: str | None = None
        # Post-22 fix -- a node's label only renders while the mouse is
        # over it; see _on_hover()/_on_exit().
        self._hovered: str | None = None

        # Phase 22 -- live local force simulation state.
        self._home_positions: dict[str, tuple[float, float]] = {}
        self._sim_velocities: dict[str, tuple[float, float]] = {}
        self._sim_active_nodes: set[str] = set()
        self._sim_active = False
        self._sim_settle_ticks = 0
        # Post-22 fix -- tracks the dynamic-canvas node set as of the last
        # tick, so _simulation_tick() can report whether it changed.
        self._prev_dynamic_slugs: set[str] = set()

        # Two layered canvases (Post-22 fix), not one: during a drag, only
        # the small _dynamic_canvas needs to be rebuilt+diffed+sent at
        # ~30fps -- the bulk of the graph on _static_canvas stays untouched.
        # on_resize only needs to live on one of them; both are expand=True
        # in the same Stack, so they always share the same rendered size.
        self._static_canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        self._dynamic_canvas = cv.Canvas(shapes=[], expand=True)
        # Topmost layer (Post-22 fix): holds at most one label, for
        # whichever node the mouse is currently over -- see
        # _build_hover_shapes(). Kept separate from static/dynamic so
        # hover never has to rebuild the graph's actual node/edge shapes.
        self._hover_canvas = cv.Canvas(shapes=[], expand=True)
        self._canvas_layers = ft.Stack(
            controls=[self._static_canvas, self._dynamic_canvas, self._hover_canvas],
            expand=True,
        )
        self._gestures = ft.GestureDetector(
            content=self._canvas_layers,
            expand=True,
            on_pan_start=self._on_pan_start,
            on_pan_update=self._on_pan_update,
            on_pan_end=self._on_pan_end,
            on_scroll=self._on_scroll,
            on_hover=self._on_hover,
            on_exit=self._on_exit,
        )
        self._info_overlay = self._build_info_overlay()
        self._settings_panel = self._build_settings_panel()
        self.content = ft.Stack(
            controls=[
                self._gestures,
                self._settings_panel,
                self._build_zoom_controls(),
                self._info_overlay,
            ],
            expand=True,
        )

    @property
    def node_positions(self) -> dict[str, tuple[float, float]]:
        """Current node -> canvas position mapping (populated asynchronously)."""
        return self._positions

    @property
    def zoom(self) -> float:
        return self._zoom

    # --- Graph data ---------------------------------------------------------

    def set_graph(self, graph: nx.DiGraph) -> None:
        """Displays `graph`, computing its layout on a worker thread."""
        # A full reload replaces `self._positions` wholesale -- stop any
        # in-flight simulation so it doesn't fight the fresh layout.
        self._sim_active = False
        self._graph = graph.copy()
        self._page.run_thread(self._layout_worker)

    def _layout_positions(self) -> dict[str, tuple[float, float]]:
        """Pure layout computation -- reads only plain attributes, touches no
        Flet control, so it's safe to run off the event-loop thread.
        """
        node_count = self._graph.number_of_nodes()
        if node_count == 0:
            return {}
        # `spring_layout`'s own default (k=1/sqrt(n)) assumes point-nodes;
        # ours render as a circle plus a text label underneath, which needs
        # more breathing room to stay legible -- a fixed k=0.15 was tighter
        # than even that default once the vault had more than ~44 notes,
        # and always too tight for a small vault, leaving everything
        # clustered on top of each other regardless of graph size.
        k = _LAYOUT_SPACING / (node_count**0.5)
        pos_seed = None
        fixed = None
        if _GRAVITY_WELL_SLUG in self._graph:
            # Anchors the hub every note backlinks to at the canvas center
            # -- the rest of the graph settles into a rough ring around it
            # from spring_layout's own attraction/repulsion balance, no
            # explicit circular-placement math needed. A partial pos= (just
            # the fixed node) is enough -- every other node still gets
            # spring_layout's own random init.
            pos_seed = {_GRAVITY_WELL_SLUG: (0.0, 0.0)}
            fixed = [_GRAVITY_WELL_SLUG]
        pos = nx.spring_layout(self._graph, k=k, iterations=100, seed=42, pos=pos_seed, fixed=fixed)
        if fixed:
            # spring_layout auto-rescales its output to a ~1.0 bounding
            # radius by default (its own scale=1), but silently skips that
            # rescale whenever fixed= is given -- confirmed directly by
            # reading its source. Left alone, the anchored layout's raw
            # bounding radius comes out ~2x the unanchored one (measured on
            # this file's own benchmark graph), which is what "only half
            # the graph fits on screen at the same zoom" turned out to be.
            # Restoring the same ~1.0-bounding-radius invariant the
            # unanchored path already gets for free -- not a tuning
            # constant, an exact rescale to match spring_layout's own
            # normal behavior.
            radii = (math.hypot(float(xy[0]), float(xy[1])) for xy in pos.values())
            max_r = max(radii, default=0.0)
            if max_r > 0:
                pos = {node: (xy[0] / max_r, xy[1] / max_r) for node, xy in pos.items()}
        return {str(node): self._to_canvas(float(xy[0]), float(xy[1])) for node, xy in pos.items()}

    def _compute_layout(self) -> None:
        """Synchronous entry point kept for direct test use. Production code
        goes through `set_graph()` -> `_layout_worker()` instead, which hops
        back via `page.run_task()` before the resulting `_redraw_all()`
        touches the canvases -- `Control.update()` assumes the event-loop
        thread.
        """
        self._positions = self._layout_positions()
        self._refresh_tag_popup()
        self._redraw_all()

    def _layout_worker(self) -> None:
        positions = self._layout_positions()
        self._page.run_task(self._apply_positions, positions)

    async def _apply_positions(self, positions: dict[str, tuple[float, float]]) -> None:
        self._positions = positions
        # A reload is the only moment the tag vocabulary can actually
        # change -- unrelated to any filter control being clicked, so this
        # is the one place the popup's chip set gets rebuilt.
        self._refresh_tag_popup()
        self._redraw_all()

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """Maps a spring-layout vector (roughly -1..1) into canvas pixels."""
        scale = self._layout_scale()
        half_w = (self._width - _LAYOUT_MARGIN * 2) / 2.0 * scale
        half_h = (self._height - _LAYOUT_MARGIN * 2) / 2.0 * scale
        return (self._width / 2.0 + x * half_w, self._height / 2.0 + y * half_h)

    def _layout_scale(self) -> float:
        """A larger vault needs proportionally more virtual canvas space for
        its nodes not to crowd together -- fitting every note into the same
        fixed on-screen box regardless of how many there are is what caused
        them to overlap. Pan and scroll-zoom (added alongside this) are how
        the user navigates a graph that's now bigger than the visible frame;
        1.0 (today's fixed size) for small vaults, growing linearly past
        `_LAYOUT_BASE_NODE_COUNT` notes.
        """
        node_count = self._graph.number_of_nodes()
        return max(1.0, node_count / _LAYOUT_BASE_NODE_COUNT)

    def _on_resize(self, e: cv.CanvasResizeEvent) -> None:
        self._width, self._height = e.width, e.height
        self._redraw_all()

    # --- Interaction --------------------------------------------------------

    def _node_at(self, x: float, y: float) -> str | None:
        """The node whose circle contains `(x, y)`, if any -- a filtered-out
        node is never drawn, so it must never be hit-testable either
        (click/drag/hover).
        """
        hit_radius = _NODE_RADIUS * self._zoom
        for slug, (nx_, ny_) in self._positions.items():
            if not self._passes_filters(slug):
                continue
            sx = nx_ * self._zoom + self._pan_x
            sy = ny_ * self._zoom + self._pan_y
            if (sx - x) ** 2 + (sy - y) ** 2 <= hit_radius**2:
                return slug
        return None

    def _on_pan_start(self, e: ft.DragStartEvent) -> None:
        self._dragging = self._node_at(e.local_position.x, e.local_position.y)
        if self._dragging is not None:
            self._panning = False
            self._selected = self._dragging
            self._notify_selection()
            self._start_simulation()
        else:
            # Empty background: pan the whole canvas instead of a node.
            self._panning = True
            if self._selected is not None:
                self._selected = None
                self._notify_selection()

    def _on_pan_update(self, e: ft.DragUpdateEvent) -> None:
        if self._dragging is not None:
            self._positions[self._dragging] = (
                (e.local_position.x - self._pan_x) / self._zoom,
                (e.local_position.y - self._pan_y) / self._zoom,
            )
            # The simulation tick loop (started alongside any node-drag,
            # see _start_simulation()) already redraws at a steady ~30fps.
            # Redrawing again here on every raw pointer-move event roughly
            # doubles how often the full shape list gets serialized across
            # the Python<->Flutter boundary during a drag -- that
            # serialization, not the physics math, is the real cost.
            # Skip only when the loop is actually running; if it isn't
            # (e.g. a graph reload mid-drag stopped it), fall back to
            # redrawing directly so the drag never silently stops rendering.
            if not self._sim_active:
                self._redraw_all()
        elif self._panning:
            self._pan_x += e.local_delta.x
            self._pan_y += e.local_delta.y
            self._redraw_all()

    def _on_pan_end(self, e: ft.DragEndEvent) -> None:
        self._dragging = None
        self._panning = False
        # The hover label may have been following the just-released node
        # (see _build_hover_shapes()) -- re-evaluate now that dragging has
        # stopped, so it correctly falls back to self._hovered (or clears,
        # if that was never set).
        self._redraw_hover()

    # --- Live local force simulation (Phase 22) ------------------------------

    def _start_simulation(self) -> None:
        """Called when a node-drag begins. Snapshots the pre-drag layout as
        the "home" every perturbed node eases back toward once released, and
        (re)starts the tick loop if it isn't already running.

        Phase 25: a no-op when Physics/Animation's "Enable Simulation"
        switch is off -- `self._sim_active` simply never becomes `True`,
        so `_on_pan_update()`'s existing `if not self._sim_active:
        self._redraw_all()` fallback (already there for "the tick loop
        isn't running") takes over automatically: a disabled simulation
        means a dragged node just moves directly, no other code path
        needs to change.
        """
        if not self._simulation_enabled:
            return
        self._home_positions = dict(self._positions)
        self._sim_velocities = {}
        self._sim_active_nodes = set()
        self._sim_settle_ticks = 0
        # Forces the first tick to report "changed" (see _simulation_tick())
        # so the anchor is moved into the dynamic layer immediately.
        self._prev_dynamic_slugs = set()
        if not self._sim_active:
            self._sim_active = True
            self._page.run_task(self._simulation_loop)

    async def _simulation_loop(self) -> None:
        while self._sim_active:
            changed = self._simulation_tick()
            # A full redraw is only needed when a node actually crossed
            # between the static and dynamic canvases this tick (Post-22
            # fix) -- the common case is a steady-state tick where only
            # the (already dynamic) shapes moved, so only that small
            # canvas needs rebuilding+diffing+sending.
            if changed:
                self._redraw_all()
            else:
                self._redraw_dynamic()
                # The dragged node's label (see _build_hover_shapes())
                # needs to track it every tick too, at the same cadence.
                self._redraw_hover()
            await asyncio.sleep(_SIM_TICK_DT)

    def _simulation_tick(self) -> bool:
        """One physics step. Reads/writes only `self._positions` /
        `self._sim_velocities` / `self._sim_active_nodes` -- touches no
        Flet control, so it's safe to call directly (e.g. from tests) and
        cheap enough to run every tick on the event-loop thread.

        Returns whether the dynamic node set's membership changed this
        tick (a node entered or left) -- the caller uses this to decide
        whether the static canvas needs rebuilding too.
        """
        anchor = self._dragging
        neighbors: set[str] = set()
        if anchor is not None and anchor in self._graph:
            neighbors = set(self._graph.predecessors(anchor)) | set(
                self._graph.successors(anchor)
            )

        anchor_pos = self._positions.get(anchor) if anchor is not None else None
        if anchor is not None:
            self._sim_settle_ticks = 0
            # The gravity well never gets pulled by anything except being
            # directly dragged -- excluded here, not from `neighbors`
            # itself, so dragging it still moves its (unfiltered) neighbor
            # set exactly like any other node would.
            self._sim_active_nodes |= (
                neighbors & self._home_positions.keys()
            ) - {_GRAVITY_WELL_SLUG}
            if anchor_pos is not None:
                for slug, pos in self._positions.items():
                    if slug in (anchor, _GRAVITY_WELL_SLUG) or slug not in self._home_positions:
                        continue
                    if math.dist(pos, anchor_pos) < _SIM_REPEL_RADIUS:
                        self._sim_active_nodes.add(slug)
        else:
            self._sim_settle_ticks += 1

        # Phase 25 -- Physics/Animation's Strength slider scales repel,
        # trail-spring, and return-to-home forces together (not damping,
        # the speed clamp, or the repel radius -- those would each turn
        # this into a different-feeling knob rather than one intuitive
        # "how strong" dial).
        strength = self._simulation_strength
        still_active: set[str] = set()
        for slug in self._sim_active_nodes:
            if slug not in self._positions or slug not in self._home_positions:
                continue
            px, py = self._positions[slug]
            vx, vy = self._sim_velocities.get(slug, (0.0, 0.0))
            fx = fy = 0.0

            if anchor_pos is not None:
                dx, dy = px - anchor_pos[0], py - anchor_pos[1]
                dist = max(math.hypot(dx, dy), 1e-3)
                if slug in neighbors:
                    stretch = dist - _SIM_NEIGHBOR_REST_LENGTH
                    fx += -_SIM_NEIGHBOR_SPRING_K * strength * stretch * dx / dist
                    fy += -_SIM_NEIGHBOR_SPRING_K * strength * stretch * dy / dist
                if dist < _SIM_REPEL_RADIUS:
                    push = _SIM_REPEL_STRENGTH * strength * (1 - dist / _SIM_REPEL_RADIUS) / dist
                    fx += push * dx
                    fy += push * dy

            hx, hy = self._home_positions[slug]
            fx += -_SIM_HOME_SPRING_K * strength * (px - hx)
            fy += -_SIM_HOME_SPRING_K * strength * (py - hy)

            vx = (vx + fx * _SIM_TICK_DT) * _SIM_DAMPING
            vy = (vy + fy * _SIM_TICK_DT) * _SIM_DAMPING
            speed = math.hypot(vx, vy)
            if speed > _SIM_MAX_SPEED:
                scale = _SIM_MAX_SPEED / speed
                vx, vy = vx * scale, vy * scale

            new_pos = (px + vx * _SIM_TICK_DT, py + vy * _SIM_TICK_DT)

            if anchor is None and math.dist(new_pos, (hx, hy)) < _SIM_SETTLE_DIST_EPSILON:
                self._positions[slug] = self._home_positions[slug]
                self._sim_velocities.pop(slug, None)
                continue

            self._positions[slug] = new_pos
            self._sim_velocities[slug] = (vx, vy)
            still_active.add(slug)

        self._sim_active_nodes = still_active

        if anchor is None and (
            not self._sim_active_nodes or self._sim_settle_ticks > _SIM_SETTLE_MAX_TICKS
        ):
            self._sim_active = False

        current_dynamic = self._dynamic_slugs()
        changed = current_dynamic != self._prev_dynamic_slugs
        self._prev_dynamic_slugs = current_dynamic
        return changed

    def _notify_selection(self) -> None:
        if self.on_node_selected is not None:
            self.on_node_selected(self._selected)
        self._update_info_overlay()
        self._update_degrees_from_selected()
        # Post-24 fix #3: the degrees caption reads self._selected, so it
        # goes stale the instant a node is (de)selected unless mutated
        # directly here -- the same bug class as the Filters panel at
        # large, just a second instance of it.
        self._degrees_caption.value = self._degrees_caption_text()
        with contextlib.suppress(RuntimeError):
            self._degrees_caption.update()
        self._redraw_all()

    def _update_degrees_from_selected(self) -> None:
        """Caches every node's hop-distance from the current selection, for
        the degrees-of-separation filter -- recomputed here (on selection
        change), not per filter check, since that would be an O(n) BFS on
        every single _passes_filters() call otherwise.

        Undirected, deliberately: every note's edges point *into* `index`
        (Phase 18's Related-block), never out, so a directed BFS from a
        selected leaf note could reach `index` and its own source and
        essentially nothing else -- useless for "how topologically close is
        this" exploration, which is what this filter is actually for.
        """
        if self._selected is None or self._selected not in self._graph:
            self._degrees_from_selected = {}
            return
        self._degrees_from_selected = nx.single_source_shortest_path_length(
            self._graph.to_undirected(), self._selected
        )

    def _passes_filters(self, slug: str) -> bool:
        """Whether `slug` should be visible under the current Filters state.

        `index` is unconditionally exempt: it's never a real `notes` row
        (no type/tags/title of its own), and hiding it would break the
        visual anchor everything else was just organized around (the
        gravity well, Post-22).

        The master switch and each dimension's own switch (Post-24 fix)
        only ever gate whether a dimension's *configured* value is
        applied -- turning a filter off never clears it, so re-enabling
        restores exactly what was set before.
        """
        if slug == _GRAVITY_WELL_SLUG:
            return True
        if not self._filters_enabled:
            return True
        if slug not in self._graph:
            return False
        data = self._graph.nodes[slug]

        if self._filter_types_enabled:
            node_type = data.get("type")
            if node_type is not None and node_type not in self._filter_types:
                return False

        if self._filter_tags_enabled and self._filter_tags:
            node_tags = set(data.get("tags") or [])
            if not node_tags & self._filter_tags:
                return False

        if self._filter_search_enabled and self._filter_search:
            # Checked independently, not concatenated -- a combined
            # "title slug" string dilutes the similarity ratio for both,
            # weakening genuinely-fuzzy (non-substring) matches.
            title = data.get("title") or slug
            if not (
                _fuzzy_match(self._filter_search, title)
                or _fuzzy_match(self._filter_search, slug)
            ):
                return False

        if self._filter_date_enabled and (self._filter_date_from or self._filter_date_to):
            updated = data.get("updated_at")
            if not updated:
                return False
            if self._filter_date_from and updated < f"{self._filter_date_from}T00:00:00":
                return False
            if self._filter_date_to and updated > f"{self._filter_date_to}T23:59:59":
                return False

        if (
            self._filter_degrees_enabled
            and self._selected is not None
            and slug != self._selected
        ):
            distance = self._degrees_from_selected.get(slug)
            if distance is None or distance > self._filter_degrees:
                return False

        return True

    def zoom_in(self, e=None) -> None:
        self._set_zoom(self._zoom + _ZOOM_STEP)

    def zoom_out(self, e=None) -> None:
        self._set_zoom(self._zoom - _ZOOM_STEP)

    def zoom_reset(self, e=None) -> None:
        self._set_zoom(1.0)

    def _set_zoom(self, value: float, focal: tuple[float, float] | None = None) -> None:
        new_zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, value))
        if focal is not None and new_zoom != self._zoom:
            # Keeps the data point under `focal` fixed on screen across the
            # zoom change -- the buttons have no such point, so they don't
            # pass one, and stay origin-anchored exactly as before.
            fx, fy = focal
            ratio = new_zoom / self._zoom
            self._pan_x = fx + (self._pan_x - fx) * ratio
            self._pan_y = fy + (self._pan_y - fy) * ratio
        self._zoom = new_zoom
        self._redraw_all()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        # Scroll up (negative dy) zooms in, matching maps/design-tool
        # convention -- flipped when Zoom & Pan's Invert Scroll-Zoom
        # switch (Phase 25) is on, for hardware/platforms where that
        # convention feels backwards (flagged as worth confirming back in
        # Phase 17, never had a real fix until now).
        zoom_in = e.scroll_delta.y < 0
        if self._invert_scroll_zoom:
            zoom_in = not zoom_in
        step = _SCROLL_ZOOM_STEP if zoom_in else -_SCROLL_ZOOM_STEP
        self._set_zoom(self._zoom + step, focal=(e.local_position.x, e.local_position.y))

    def _on_hover(self, e: ft.HoverEvent) -> None:
        hovered = self._node_at(e.local_position.x, e.local_position.y)
        if hovered != self._hovered:
            self._hovered = hovered
            self._redraw_hover()

    def _on_exit(self, e: ft.HoverEvent) -> None:
        if self._hovered is not None:
            self._hovered = None
            self._redraw_hover()

    # --- Drawing ------------------------------------------------------------

    def _dynamic_slugs(self) -> set[str]:
        """Nodes rendered on the dynamic canvas: the actively-simulated set
        plus the node currently being dragged, if any -- see the Post-22
        static/dynamic split for why this needs to stay a live
        computation, not a cached one.
        """
        slugs = set(self._sim_active_nodes)
        if self._dragging is not None:
            slugs.add(self._dragging)
        return slugs

    def _node_color(self, slug: str) -> str:
        """Colored by the note's real `type` (Phase 25) -- customizable
        via `self._type_colors`, which the Colors picker mutates. Falls
        back to the gravity well's own neutral default for the hub itself
        (never a real `notes` row, so it has no `type`) and for any note
        with a missing/unrecognized type, rather than ever raising.
        """
        if slug == self._selected:
            return theme.ACCENT
        fallback = self._type_colors[_GRAVITY_WELL_SLUG]
        if slug == _GRAVITY_WELL_SLUG:
            return fallback
        node_type = self._graph.nodes[slug].get("type") if slug in self._graph else None
        return self._type_colors.get(node_type, fallback)

    def _node_circle(self, slug: str, x: float, y: float) -> cv.Circle:
        sx = x * self._zoom + self._pan_x
        sy = y * self._zoom + self._pan_y
        return cv.Circle(
            sx, sy, _NODE_RADIUS * self._zoom, paint=ft.Paint(color=self._node_color(slug))
        )

    def _hover_label_shape(self, slug: str, x: float, y: float) -> cv.Text:
        sx = x * self._zoom + self._pan_x
        sy = y * self._zoom + self._pan_y
        return cv.Text(
            sx,
            sy + _NODE_RADIUS * self._zoom + 3,
            slug,
            style=ft.TextStyle(size=10.5, color=theme.TEXT_NODE),
            alignment=ft.Alignment.TOP_CENTER,
        )

    def _edge_shape(self, u: str, v: str, edge_paint: ft.Paint) -> cv.Shape | None:
        if u not in self._positions or v not in self._positions:
            return None
        x1, y1 = self._positions[u]
        x2, y2 = self._positions[v]
        return cv.Line(
            x1 * self._zoom + self._pan_x,
            y1 * self._zoom + self._pan_y,
            x2 * self._zoom + self._pan_x,
            y2 * self._zoom + self._pan_y,
            paint=edge_paint,
        )

    def _build_static_shapes(self) -> list[cv.Shape]:
        """Everything not currently touched by the simulation -- redrawn
        only on real state changes (layout, resize, pan, zoom, selection),
        never on the per-tick simulation hot path.
        """
        dynamic = self._dynamic_slugs()
        edge_paint = ft.Paint(color=theme.GRAPH_EDGE, stroke_width=1.8)
        shapes: list[cv.Shape] = []
        for u, v in self._graph.edges():
            if u in dynamic or v in dynamic:
                continue
            if not (self._passes_filters(u) and self._passes_filters(v)):
                continue
            shape = self._edge_shape(u, v, edge_paint)
            if shape is not None:
                shapes.append(shape)
        for slug, (x, y) in self._positions.items():
            if slug in dynamic or not self._passes_filters(slug):
                continue
            shapes.append(self._node_circle(slug, x, y))
        return shapes

    def _build_dynamic_shapes(self) -> list[cv.Shape]:
        """The actively-simulated node set plus any edge touching it --
        redrawn every simulation tick. Kept small on purpose: this is the
        one canvas that pays the redraw cost at ~30fps during a drag.
        """
        dynamic = self._dynamic_slugs()
        edge_paint = ft.Paint(color=theme.GRAPH_EDGE, stroke_width=1.8)
        shapes: list[cv.Shape] = []
        static_endpoints: set[str] = set()
        for u, v in self._graph.edges():
            if u in dynamic or v in dynamic:
                if not (self._passes_filters(u) and self._passes_filters(v)):
                    continue
                shape = self._edge_shape(u, v, edge_paint)
                if shape is not None:
                    shapes.append(shape)
                if u not in dynamic:
                    static_endpoints.add(u)
                if v not in dynamic:
                    static_endpoints.add(v)
        for slug in dynamic:
            if not self._passes_filters(slug):
                continue
            pos = self._positions.get(slug)
            if pos is not None:
                shapes.append(self._node_circle(slug, *pos))
        # The static canvas sits *underneath* this one, so a cross-boundary
        # edge (one dynamic endpoint, one static) would otherwise draw over
        # its static endpoint's circle regardless of within-canvas
        # ordering. A redundant circle here, on top of the edge, exactly
        # overlaps the real one below -- no visible duplicate, correct
        # z-order. The static canvas itself is never touched by this.
        for slug in static_endpoints:
            pos = self._positions.get(slug)
            if pos is not None:
                shapes.append(self._node_circle(slug, *pos))
        return shapes

    def build_shapes(self) -> list[cv.Shape]:
        """All shapes for the current state, regardless of the static/
        dynamic render split -- the combined view tests and any other
        caller wanting the full picture should use.
        """
        return self._build_static_shapes() + self._build_dynamic_shapes()

    def _redraw_static(self) -> None:
        self._static_canvas.shapes = self._build_static_shapes()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self._static_canvas.update()

    def _redraw_dynamic(self) -> None:
        self._dynamic_canvas.shapes = self._build_dynamic_shapes()
        with contextlib.suppress(RuntimeError):
            self._dynamic_canvas.update()

    def _redraw_all(self) -> None:
        self._redraw_static()
        self._redraw_dynamic()
        self._redraw_hover()

    def _build_hover_shapes(self) -> list[cv.Shape]:
        """At most one label. While a node is being dragged, its label
        follows it -- as if part of the node -- regardless of the mouse's
        real hover target: no PointerHoverEvent fires once a drag's button
        is down, so `self._hovered` would otherwise stay frozen at
        whatever it was the instant the drag started.
        """
        target = self._dragging if self._dragging is not None else self._hovered
        if target is None:
            return []
        pos = self._positions.get(target)
        if pos is None:
            return []
        return [self._hover_label_shape(target, *pos)]

    def _redraw_hover(self) -> None:
        self._hover_canvas.shapes = self._build_hover_shapes()
        with contextlib.suppress(RuntimeError):
            self._hover_canvas.update()

    # --- Settings panel (Phase 23) -------------------------------------------

    def _build_settings_panel(self) -> ft.Control:
        """Replaces the old always-on legend -- same slot/styling, a
        collapsible shell. Fixed width (Post-24 fix): nothing inside --
        the tag list in particular -- can stretch this across the graph
        regardless of how much content it holds.

        Post-24 fix #3: every control that can change appearance is built
        exactly once, here, and stored on `self`. No handler ever
        reassigns `self._settings_panel.content` (or any other control's
        `.content`/`.controls`) again -- every interaction only mutates
        the specific control(s) it affects and calls `.update()` on them
        directly, the same discipline `_build_info_overlay()`/
        `_update_info_overlay()` already established for the selection
        card. This is what makes every control genuinely self-contained:
        the settings panel itself never cares what's inside it.
        """
        self._panel_chevron = ft.Text(
            "▾" if self._settings_panel_expanded else "▸", size=11, color=theme.TEXT_TOGGLE_OFF
        )
        header = ft.Row(
            spacing=6,
            controls=[
                ft.Text("⚙", size=12, color=theme.TEXT_TOGGLE_OFF),
                ft.Text(
                    "Settings", size=10.5, weight=ft.FontWeight.W_600, color=theme.TEXT
                ),
                ft.Container(width=24),  # spacer, pushes the toggle to the right
                ft.Container(on_click=self._toggle_settings_panel, content=self._panel_chevron),
            ],
        )
        self._panel_body = ft.Container(
            # Phase 24 (Filters) made this section genuinely tall -- capped
            # height + scroll instead of growing to cover the graph.
            height=480,
            visible=self._settings_panel_expanded,
            content=ft.Column(
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    self._section_label("CATEGORIES"),
                    self._build_legend_section(),
                    ft.Container(height=1, bgcolor=theme.BORDER),
                    self._section_label("FILTERS"),
                    self._build_filters_section(),
                    ft.Container(height=1, bgcolor=theme.BORDER),
                    self._section_label("DISPLAY"),
                    self._build_display_settings_section(),
                ],
            ),
        )
        return ft.Container(
            left=14,
            top=12,
            width=260,
            padding=ft.Padding(12, 9, 12, 9),
            bgcolor=theme.CHROME_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            content=ft.Column(spacing=8, controls=[header, self._panel_body]),
        )

    def _section_label(self, text: str) -> ft.Control:
        return ft.Text(text, size=11, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500)

    def _build_legend_section(self) -> ft.Control:
        """Phase 25: an interactive color picker per real note type (plus
        `index`, the gravity-well hub) -- replacing, not supplementing,
        the old static legend. A separate parallel "Colors" section
        listing the same 4-5 things a second time would be redundant with
        the legend already sitting right here.
        """
        self._color_swatch_controls: dict[str, ft.Container] = {}
        rows = [
            self._build_color_picker_row(type_key, label)
            for type_key, label in (*_FILTER_NOTE_TYPE_LABELS, (_GRAVITY_WELL_SLUG, "Index"))
        ]
        return ft.Column(spacing=5, controls=rows)

    def _build_color_picker_row(self, type_key: str, label: str) -> ft.Control:
        """A `PopupMenuButton` wrapping a `Row(wrap=True)` of clickable
        preset swatches inside a single `PopupMenuItem` -- deliberately
        *not* one `PopupMenuItem` per preset. The former doesn't
        auto-close on a click into a plain nested `Container` (proven by
        the Tags popup, Post-24 fix #3); the latter would, via Flutter's
        own built-in "close menu on item selection." Not auto-closing is
        the point here too: it lets you preview a couple of colors
        against the live graph before settling on one.
        """
        trigger_swatch = ft.Container(
            width=8, height=8, border_radius=4, bgcolor=self._type_colors[type_key]
        )
        self._color_swatch_controls[type_key] = trigger_swatch
        palette = ft.Row(
            wrap=True,
            spacing=6,
            run_spacing=6,
            controls=[
                ft.Container(
                    width=18,
                    height=18,
                    border_radius=4,
                    bgcolor=color,
                    border=ft.Border.all(1, theme.BORDER_STRONG),
                    on_click=lambda e, t=type_key, c=color: self._on_type_color_selected(t, c),
                )
                for color in _GRAPH_SWATCH_PALETTE
            ],
        )
        return ft.PopupMenuButton(
            menu_padding=ft.Padding(8, 8, 8, 8),
            content=ft.Row(
                spacing=7,
                controls=[trigger_swatch, ft.Text(label, size=10.5, color=theme.TEXT_TOGGLE_OFF)],
            ),
            items=[ft.PopupMenuItem(content=ft.Container(width=190, content=palette))],
        )

    def _on_type_color_selected(self, type_key: str, color: str) -> None:
        self._type_colors[type_key] = color
        swatch = self._color_swatch_controls.get(type_key)
        if swatch is not None:
            swatch.bgcolor = color
            with contextlib.suppress(RuntimeError):
                swatch.update()
        # The Type filter checkbox for this type (if it's one of the 4
        # real types, not "index") shares the same color -- kept in sync
        # the same way a tag chip's own container is (mutate just this
        # one control, never rebuild anything around it).
        checkbox = self._type_checkboxes.get(type_key)
        if checkbox is not None:
            checkbox.fill_color = color
            with contextlib.suppress(RuntimeError):
                checkbox.update()
        self._apply_display_settings_change()

    # --- Filters (Phase 24, redesigned Post-24 fix #3) -------------------------
    #
    # Every control below is built exactly once (from _build_filters_section(),
    # itself only ever called from _build_settings_panel() in __init__) and
    # its reference stored on self. Interaction handlers mutate only the
    # specific control(s) they affect -- never rebuild any container's
    # content/controls list. The one exception is the tag-chip column,
    # which is rebuilt, but only when the underlying tag *vocabulary*
    # changes (a graph reload), never from a filter interaction -- see
    # _refresh_tag_popup().

    def _all_tags(self) -> list[str]:
        tags = {t for _, data in self._graph.nodes(data=True) for t in (data.get("tags") or [])}
        return sorted(tags)

    def _tags_trigger_text(self) -> str:
        count = len(self._filter_tags)
        return f"Tags ({count})" if count else "Tags"

    def _degrees_caption_text(self) -> str:
        if self._selected is None:
            return "Applies once you select a node"
        return f"Within {self._filter_degrees} hop(s) of the selection"

    def _build_tag_chip(self, tag: str) -> ft.Container:
        selected = tag in self._filter_tags
        chip = ft.Container(
            padding=ft.Padding(8, 4, 8, 4),
            bgcolor=theme.ACCENT if selected else theme.CARD_BG,
            border=ft.Border.all(1, theme.ACCENT if selected else theme.BORDER_STRONG),
            border_radius=10,
            on_click=lambda e, t=tag: self._on_filter_tag_toggled(t),
            content=ft.Text(
                tag, size=10, color=theme.TEXT if selected else theme.TEXT_TOGGLE_OFF
            ),
        )
        self._tag_chip_controls[tag] = chip
        return chip

    def _style_tag_chip(self, tag: str) -> None:
        """Mutates one chip's own container in place -- the only control a
        tag toggle touches. Other chips, and the panel around them, are
        never rebuilt.
        """
        chip = self._tag_chip_controls.get(tag)
        if chip is None:
            return
        selected = tag in self._filter_tags
        chip.bgcolor = theme.ACCENT if selected else theme.CARD_BG
        chip.border = ft.Border.all(1, theme.ACCENT if selected else theme.BORDER_STRONG)
        chip.content.color = theme.TEXT if selected else theme.TEXT_TOGGLE_OFF
        with contextlib.suppress(RuntimeError):
            chip.update()

    def _build_tag_chip_list_controls(self) -> list[ft.Control]:
        self._tag_chip_controls = {}
        tags = self._all_tags()
        if not tags:
            return [ft.Text("No tags yet", size=10, color=theme.TEXT_MUTED)]
        return [
            ft.Row(
                wrap=True,
                spacing=4,
                run_spacing=4,
                controls=[self._build_tag_chip(tag) for tag in tags],
            )
        ]

    def _refresh_tag_popup(self) -> None:
        """Rebuilds just the tag-chip column -- called only when the tag
        *vocabulary* could have changed (a graph reload), never from a
        filter interaction. Everything else in the panel is untouched.
        """
        self._tags_chip_column.controls = self._build_tag_chip_list_controls()
        with contextlib.suppress(RuntimeError):
            self._tags_chip_column.update()

    def _build_tags_popup(self) -> ft.Control:
        """A PopupMenuButton (Post-24 fix #1) rather than an inline wrapping
        row -- the tag list has no natural bound on length, and an inline
        row had nothing to wrap *within*, which was the actual cause of the
        panel stretching across the graph. The popup's own content is a
        fixed-size scrollable box, so however many tags exist, the popup
        itself never grows past it. `menu_padding` (Post-24 fix #3), not an
        inner Container padding, so the uniform inset comes from the menu
        surface itself rather than being asymmetrically absorbed by
        content-width-driven popup sizing.
        """
        self._tags_chip_column = ft.Column(
            scroll=ft.ScrollMode.AUTO, controls=self._build_tag_chip_list_controls()
        )
        self._tags_trigger_label = ft.Text(
            self._tags_trigger_text(), size=10.5, color=theme.TEXT_TOGGLE_OFF
        )
        return ft.PopupMenuButton(
            menu_padding=ft.Padding(8, 8, 8, 8),
            content=ft.Container(
                padding=ft.Padding(8, 5, 8, 5),
                bgcolor=theme.CARD_BG,
                border=ft.Border.all(1, theme.BORDER_STRONG),
                border_radius=6,
                content=self._tags_trigger_label,
            ),
            items=[
                ft.PopupMenuItem(
                    content=ft.Container(width=220, height=200, content=self._tags_chip_column)
                )
            ],
        )

    def _build_type_content(self) -> ft.Control:
        self._type_checkboxes = {}
        rows = []
        for type_key, label in _FILTER_NOTE_TYPE_LABELS:
            checkbox = ft.Checkbox(
                value=type_key in self._filter_types,
                fill_color=self._type_colors[type_key],
                on_change=(
                    lambda e, t=type_key: self._on_filter_type_changed(t, e.control.value)
                ),
            )
            self._type_checkboxes[type_key] = checkbox
            rows.append(
                ft.Row(
                    spacing=4,
                    controls=[checkbox, ft.Text(label, size=10.5, color=theme.TEXT_TOGGLE_OFF)],
                )
            )
        return ft.Column(spacing=2, controls=rows)

    def _build_search_content(self) -> ft.Control:
        self._search_field = ft.TextField(
            value=self._filter_search,
            hint_text="Search notes…",
            height=34,
            text_size=11,
            content_padding=ft.Padding(8, 4, 8, 4),
            on_change=lambda e: self._on_filter_search_changed(e.control.value),
        )
        return self._search_field

    def _build_date_content(self) -> ft.Control:
        self._date_from_label = ft.Text(
            f"From: {self._filter_date_from or 'Any'}", size=10, color=theme.TEXT_TOGGLE_OFF
        )
        self._date_to_label = ft.Text(
            f"To: {self._filter_date_to or 'Any'}", size=10, color=theme.TEXT_TOGGLE_OFF
        )
        return ft.Row(
            spacing=6,
            controls=[
                ft.Container(
                    padding=ft.Padding(8, 5, 8, 5),
                    bgcolor=theme.CARD_BG,
                    border=ft.Border.all(1, theme.BORDER_STRONG),
                    border_radius=6,
                    on_click=lambda e: self._page.show_dialog(self._date_picker_from),
                    content=self._date_from_label,
                ),
                ft.Container(
                    padding=ft.Padding(8, 5, 8, 5),
                    bgcolor=theme.CARD_BG,
                    border=ft.Border.all(1, theme.BORDER_STRONG),
                    border_radius=6,
                    on_click=lambda e: self._page.show_dialog(self._date_picker_to),
                    content=self._date_to_label,
                ),
            ],
        )

    def _build_degrees_content(self) -> ft.Control:
        self._degrees_caption = ft.Text(
            self._degrees_caption_text(), size=10, color=theme.TEXT_MUTED
        )
        self._degrees_slider = ft.Slider(
            min=1,
            max=5,
            divisions=4,
            value=self._filter_degrees,
            label="{value}",
            on_change=lambda e: self._on_filter_degrees_changed(int(e.control.value)),
        )
        return ft.Column(spacing=2, controls=[self._degrees_caption, self._degrees_slider])

    def _build_filter_section_box(
        self, title: str, switch: ft.Switch, content: ft.Control
    ) -> ft.Control:
        """A bordered, labeled sub-section wrapping a pre-built enable
        switch -- one per filter dimension (Post-24 fix #3/#4: visually
        separated, individually toggleable without losing its configured
        value). The switch is built by the caller, not here, so its
        reference can be stored for `_sync_filter_controls_to_state()`.
        """
        return ft.Container(
            padding=ft.Padding(8, 6, 8, 6),
            bgcolor=theme.CARD_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=6,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text(
                                title, size=10.5, weight=ft.FontWeight.W_600, color=theme.TEXT
                            ),
                            switch,
                        ],
                    ),
                    content,
                ],
            ),
        )

    def _build_filters_section(self) -> ft.Control:
        self._master_switch = ft.Switch(
            value=self._filters_enabled,
            on_change=lambda e: self._on_filter_master_toggled(e.control.value),
        )
        self._types_switch = ft.Switch(
            value=self._filter_types_enabled,
            on_change=lambda e: self._on_filter_types_enabled_toggled(e.control.value),
        )
        self._tags_switch = ft.Switch(
            value=self._filter_tags_enabled,
            on_change=lambda e: self._on_filter_tags_enabled_toggled(e.control.value),
        )
        self._search_switch = ft.Switch(
            value=self._filter_search_enabled,
            on_change=lambda e: self._on_filter_search_enabled_toggled(e.control.value),
        )
        self._date_switch = ft.Switch(
            value=self._filter_date_enabled,
            on_change=lambda e: self._on_filter_date_enabled_toggled(e.control.value),
        )
        self._degrees_switch = ft.Switch(
            value=self._filter_degrees_enabled,
            on_change=lambda e: self._on_filter_degrees_enabled_toggled(e.control.value),
        )

        return ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text(
                            "Enable Filters",
                            size=10.5,
                            weight=ft.FontWeight.W_600,
                            color=theme.TEXT,
                        ),
                        self._master_switch,
                    ],
                ),
                self._build_filter_section_box(
                    "Type", self._types_switch, self._build_type_content()
                ),
                self._build_filter_section_box(
                    "Tags", self._tags_switch, self._build_tags_popup()
                ),
                self._build_filter_section_box(
                    "Search", self._search_switch, self._build_search_content()
                ),
                self._build_filter_section_box(
                    "Date", self._date_switch, self._build_date_content()
                ),
                self._build_filter_section_box(
                    "Degrees from Selected", self._degrees_switch, self._build_degrees_content()
                ),
                ft.Container(
                    padding=ft.Padding(9, 5, 9, 5),
                    bgcolor=theme.BUTTON_BG,
                    border=ft.Border.all(1, theme.BORDER_STRONG),
                    border_radius=5,
                    on_click=self._on_filters_reset,
                    content=ft.Text("Reset Filters", size=10.5, color=theme.TEXT),
                ),
            ],
        )

    def _sync_filter_controls_to_state(self) -> None:
        """Mutates every stored control from `self._filter_*`/`self._filters_*`
        -- used by `set_filters()` (persisted-settings sync) and
        `_on_filters_reset()`. Same "mutate, don't rebuild" discipline as
        every single-control handler, just applied in bulk.
        """
        for type_key, checkbox in self._type_checkboxes.items():
            checkbox.value = type_key in self._filter_types
            with contextlib.suppress(RuntimeError):
                checkbox.update()
        for tag in self._tag_chip_controls:
            self._style_tag_chip(tag)
        self._tags_trigger_label.value = self._tags_trigger_text()
        with contextlib.suppress(RuntimeError):
            self._tags_trigger_label.update()
        self._search_field.value = self._filter_search
        with contextlib.suppress(RuntimeError):
            self._search_field.update()
        self._date_from_label.value = f"From: {self._filter_date_from or 'Any'}"
        self._date_to_label.value = f"To: {self._filter_date_to or 'Any'}"
        with contextlib.suppress(RuntimeError):
            self._date_from_label.update()
            self._date_to_label.update()
        self._degrees_slider.value = self._filter_degrees
        self._degrees_caption.value = self._degrees_caption_text()
        with contextlib.suppress(RuntimeError):
            self._degrees_slider.update()
            self._degrees_caption.update()
        self._master_switch.value = self._filters_enabled
        self._types_switch.value = self._filter_types_enabled
        self._tags_switch.value = self._filter_tags_enabled
        self._search_switch.value = self._filter_search_enabled
        self._date_switch.value = self._filter_date_enabled
        self._degrees_switch.value = self._filter_degrees_enabled
        with contextlib.suppress(RuntimeError):
            self._master_switch.update()
            self._types_switch.update()
            self._tags_switch.update()
            self._search_switch.update()
            self._date_switch.update()
            self._degrees_switch.update()

    # --- Display settings (Phase 25: Physics/Animation, Zoom & Pan) ------------

    def _build_settings_section_box(self, title: str, content: ft.Control) -> ft.Control:
        """A simpler sibling to `_build_filter_section_box()` -- same
        bordered/labeled shape, minus the per-section enable `Switch`:
        neither Physics/Animation nor Zoom & Pan has an "enable this
        whole dimension" concept the way a Filter does (Physics's own
        "Enable Simulation" switch already lives *inside* its content).
        """
        return ft.Container(
            padding=ft.Padding(8, 6, 8, 6),
            bgcolor=theme.CARD_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=6,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Text(title, size=10.5, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    content,
                ],
            ),
        )

    def _simulation_strength_caption_text(self) -> str:
        return f"Strength: {self._simulation_strength:.2f}x"

    def _build_physics_content(self) -> ft.Control:
        self._simulation_switch = ft.Switch(
            value=self._simulation_enabled,
            on_change=lambda e: self._on_simulation_enabled_toggled(e.control.value),
        )
        self._simulation_strength_caption = ft.Text(
            self._simulation_strength_caption_text(), size=10, color=theme.TEXT_MUTED
        )
        self._simulation_strength_slider = ft.Slider(
            min=0.25,
            max=2.5,
            divisions=9,
            value=self._simulation_strength,
            label="{value}",
            on_change=(
                lambda e: self._on_simulation_strength_changed(round(e.control.value, 2))
            ),
        )
        return ft.Column(
            spacing=6,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text("Enable Simulation", size=10.5, color=theme.TEXT_TOGGLE_OFF),
                        self._simulation_switch,
                    ],
                ),
                self._simulation_strength_caption,
                self._simulation_strength_slider,
            ],
        )

    def _build_zoom_pan_content(self) -> ft.Control:
        self._invert_scroll_switch = ft.Switch(
            value=self._invert_scroll_zoom,
            on_change=lambda e: self._on_invert_scroll_toggled(e.control.value),
        )
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text("Invert Scroll-Zoom", size=10.5, color=theme.TEXT_TOGGLE_OFF),
                self._invert_scroll_switch,
            ],
        )

    def _build_display_settings_section(self) -> ft.Control:
        return ft.Column(
            spacing=8,
            controls=[
                self._build_settings_section_box(
                    "Physics / Animation", self._build_physics_content()
                ),
                self._build_settings_section_box("Zoom & Pan", self._build_zoom_pan_content()),
            ],
        )

    def _current_display_settings(self) -> GraphDisplaySettings:
        return GraphDisplaySettings(
            type_colors=dict(self._type_colors),
            simulation_enabled=self._simulation_enabled,
            simulation_strength=self._simulation_strength,
            invert_scroll_zoom=self._invert_scroll_zoom,
        )

    def _apply_display_settings_change(self) -> None:
        """Common tail for every Colors/Physics/Zoom-Pan control's own
        handler -- mirrors `_apply_filter_change()` exactly.
        """
        self._redraw_all()
        if self.on_display_settings_changed is not None:
            self.on_display_settings_changed(self._current_display_settings())

    def _on_simulation_enabled_toggled(self, enabled: bool) -> None:
        self._simulation_enabled = enabled
        self._apply_display_settings_change()

    def _on_simulation_strength_changed(self, value: float) -> None:
        self._simulation_strength = value
        self._simulation_strength_caption.value = self._simulation_strength_caption_text()
        with contextlib.suppress(RuntimeError):
            self._simulation_strength_caption.update()
        self._apply_display_settings_change()

    def _on_invert_scroll_toggled(self, enabled: bool) -> None:
        self._invert_scroll_zoom = enabled
        self._apply_display_settings_change()

    def _sync_display_controls_to_state(self) -> None:
        """Mutates every stored Colors/Physics/Zoom-Pan control from
        current state -- used only by `set_display_settings()`, same
        "bulk sync mutates, never rebuilds" rule
        `_sync_filter_controls_to_state()` established.
        """
        for type_key, swatch in self._color_swatch_controls.items():
            swatch.bgcolor = self._type_colors[type_key]
            with contextlib.suppress(RuntimeError):
                swatch.update()
        for type_key, checkbox in self._type_checkboxes.items():
            checkbox.fill_color = self._type_colors[type_key]
            with contextlib.suppress(RuntimeError):
                checkbox.update()
        self._simulation_switch.value = self._simulation_enabled
        self._simulation_strength_slider.value = self._simulation_strength
        self._simulation_strength_caption.value = self._simulation_strength_caption_text()
        with contextlib.suppress(RuntimeError):
            self._simulation_switch.update()
            self._simulation_strength_slider.update()
            self._simulation_strength_caption.update()
        self._invert_scroll_switch.value = self._invert_scroll_zoom
        with contextlib.suppress(RuntimeError):
            self._invert_scroll_switch.update()

    def set_display_settings(self, state: GraphDisplaySettings) -> None:
        """Syncs Colors/Physics/Zoom-Pan from persisted settings -- called
        once after construction and again on every vault switch. Never
        fires `on_display_settings_changed`; that's only for genuine user
        changes.
        """
        if state == self._current_display_settings():
            return
        # Merges over the built-in defaults rather than assigning
        # directly -- a persisted config missing a key (an older save, or
        # a future new type) must never leave a lookup elsewhere without
        # an entry to fall back on.
        self._type_colors = {**_DEFAULT_TYPE_COLORS, **state.type_colors}
        self._simulation_enabled = state.simulation_enabled
        self._simulation_strength = state.simulation_strength
        self._invert_scroll_zoom = state.invert_scroll_zoom
        self._sync_display_controls_to_state()
        self._redraw_all()

    def _toggle_settings_panel(self, e=None) -> None:
        self._settings_panel_expanded = not self._settings_panel_expanded
        self._apply_panel_expanded_state()
        if self.on_settings_panel_toggled is not None:
            self.on_settings_panel_toggled(self._settings_panel_expanded)

    def set_settings_panel_expanded(self, expanded: bool) -> None:
        """Syncs the panel from persisted settings -- called once after
        construction and again on every vault switch (a different vault
        may have a different saved preference). Never fires
        `on_settings_panel_toggled`; that's only for genuine user clicks.
        """
        if expanded == self._settings_panel_expanded:
            return
        self._settings_panel_expanded = expanded
        self._apply_panel_expanded_state()

    def _apply_panel_expanded_state(self) -> None:
        """Mutates the body's visibility and the chevron glyph in place --
        Post-24 fix #3: expand/collapse never reassigns
        `self._settings_panel.content`, only these two controls.
        """
        self._panel_body.visible = self._settings_panel_expanded
        self._panel_chevron.value = "▾" if self._settings_panel_expanded else "▸"
        with contextlib.suppress(RuntimeError):
            self._panel_body.update()
            self._panel_chevron.update()

    # --- Filters (Phase 24) --------------------------------------------------

    def _current_filter_state(self) -> GraphFilterState:
        return GraphFilterState(
            types=frozenset(self._filter_types),
            tags=frozenset(self._filter_tags),
            search=self._filter_search,
            date_from=self._filter_date_from,
            date_to=self._filter_date_to,
            degrees=self._filter_degrees,
            filters_enabled=self._filters_enabled,
            types_enabled=self._filter_types_enabled,
            tags_enabled=self._filter_tags_enabled,
            search_enabled=self._filter_search_enabled,
            date_enabled=self._filter_date_enabled,
            degrees_enabled=self._filter_degrees_enabled,
        )

    def _apply_filter_change(self) -> None:
        """Common tail for every filter control's own handler: clears
        selection if it's no longer visible under the new filters, redraws
        the graph, and -- only here, not from `set_filters()` -- fires
        `on_filters_changed` so the caller can persist it.

        Post-24 fix #3: never touches the settings panel itself. Every
        handler that calls this has already mutated its own control(s)
        directly (a chip's own container, a date label, ...) or needs no
        mutation at all (native Flet controls like Checkbox/Slider/Switch/
        TextField already reflect their own edited state client-side) --
        so there is nothing left for this shared tail to rebuild.
        """
        if self._selected is not None and not self._passes_filters(self._selected):
            self._selected = None
            self._update_info_overlay()
            self._degrees_from_selected = {}
        self._redraw_all()
        if self.on_filters_changed is not None:
            self.on_filters_changed(self._current_filter_state())

    def _on_filter_type_changed(self, note_type: str, checked: bool) -> None:
        if checked:
            self._filter_types.add(note_type)
        else:
            self._filter_types.discard(note_type)
        self._apply_filter_change()

    def _on_filter_tag_toggled(self, tag: str) -> None:
        if tag in self._filter_tags:
            self._filter_tags.discard(tag)
        else:
            self._filter_tags.add(tag)
        self._style_tag_chip(tag)
        self._tags_trigger_label.value = self._tags_trigger_text()
        with contextlib.suppress(RuntimeError):
            self._tags_trigger_label.update()
        self._apply_filter_change()

    def _on_filter_search_changed(self, value: str) -> None:
        self._filter_search = value
        self._apply_filter_change()

    @staticmethod
    def _to_date_string(value) -> str | None:
        """Normalizes a DatePicker's value (`datetime.datetime` *or*
        `datetime.date`, per its own type signature) to a plain ISO date
        string (`YYYY-MM-DD`), or `None`.
        """
        if value is None:
            return None
        if hasattr(value, "date") and callable(value.date):
            value = value.date()
        return value.isoformat()

    def _on_filter_date_from_changed(self, value) -> None:
        self._filter_date_from = self._to_date_string(value)
        self._date_from_label.value = f"From: {self._filter_date_from or 'Any'}"
        with contextlib.suppress(RuntimeError):
            self._date_from_label.update()
        self._apply_filter_change()

    def _on_filter_date_to_changed(self, value) -> None:
        self._filter_date_to = self._to_date_string(value)
        self._date_to_label.value = f"To: {self._filter_date_to or 'Any'}"
        with contextlib.suppress(RuntimeError):
            self._date_to_label.update()
        self._apply_filter_change()

    def _on_filter_degrees_changed(self, value: int) -> None:
        self._filter_degrees = value
        self._degrees_caption.value = self._degrees_caption_text()
        with contextlib.suppress(RuntimeError):
            self._degrees_caption.update()
        self._apply_filter_change()

    def _on_filter_master_toggled(self, enabled: bool) -> None:
        self._filters_enabled = enabled
        self._apply_filter_change()

    def _on_filter_types_enabled_toggled(self, enabled: bool) -> None:
        self._filter_types_enabled = enabled
        self._apply_filter_change()

    def _on_filter_tags_enabled_toggled(self, enabled: bool) -> None:
        self._filter_tags_enabled = enabled
        self._apply_filter_change()

    def _on_filter_search_enabled_toggled(self, enabled: bool) -> None:
        self._filter_search_enabled = enabled
        self._apply_filter_change()

    def _on_filter_date_enabled_toggled(self, enabled: bool) -> None:
        self._filter_date_enabled = enabled
        self._apply_filter_change()

    def _on_filter_degrees_enabled_toggled(self, enabled: bool) -> None:
        self._filter_degrees_enabled = enabled
        self._apply_filter_change()

    def _on_filters_reset(self, e=None) -> None:
        self._filter_types = {t for t, _label in _FILTER_NOTE_TYPE_LABELS}
        self._filter_tags = set()
        self._filter_search = ""
        self._filter_date_from = None
        self._filter_date_to = None
        self._filter_degrees = 1
        self._filters_enabled = True
        self._filter_types_enabled = True
        self._filter_tags_enabled = True
        self._filter_search_enabled = True
        self._filter_date_enabled = True
        self._filter_degrees_enabled = False  # see __init__'s comment on why
        self._sync_filter_controls_to_state()
        self._apply_filter_change()

    def set_filters(self, state: GraphFilterState) -> None:
        """Syncs Filters from persisted settings -- called once after
        construction and again on every vault switch. Never fires
        `on_filters_changed`; that's only for genuine user changes.
        """
        if state == self._current_filter_state():
            return
        self._filter_types = set(state.types)
        self._filter_tags = set(state.tags)
        self._filter_search = state.search
        self._filter_date_from = state.date_from
        self._filter_date_to = state.date_to
        self._filter_degrees = state.degrees
        self._filters_enabled = state.filters_enabled
        self._filter_types_enabled = state.types_enabled
        self._filter_tags_enabled = state.tags_enabled
        self._filter_search_enabled = state.search_enabled
        self._filter_date_enabled = state.date_enabled
        self._filter_degrees_enabled = state.degrees_enabled
        if self._selected is not None and not self._passes_filters(self._selected):
            self._selected = None
            self._update_info_overlay()
            self._degrees_from_selected = {}
        self._sync_filter_controls_to_state()
        self._redraw_all()

    def _build_zoom_controls(self) -> ft.Control:
        def button(label: str, on_click, size: float) -> ft.Control:
            return ft.Container(
                width=26,
                height=26,
                bgcolor=theme.MENU_BG,
                border=ft.Border.all(1, theme.BORDER_STRONG),
                border_radius=6,
                alignment=ft.Alignment.CENTER,
                on_click=on_click,
                content=ft.Text(label, size=size, color=theme.TEXT),
            )

        return ft.Container(
            right=14,
            bottom=12,
            content=ft.Row(
                spacing=4,
                controls=[
                    button("–", self.zoom_out, 14),
                    button("⤢", self.zoom_reset, 11),
                    button("+", self.zoom_in, 14),
                ],
            ),
        )

    # --- Selected-node info overlay ------------------------------------------

    def _build_info_overlay(self) -> ft.Control:
        self._info_title = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT)
        self._info_type = ft.Text("", size=10.5, color=theme.TEXT_SUBTLE)
        self._info_tags = ft.Text("", size=10.5, color=theme.TEXT_SUBTLE)
        self._info_links = ft.Text("", size=10.5, color=theme.TEXT_SUBTLE)
        return ft.Container(
            right=14,
            top=12,
            visible=False,
            width=200,
            padding=ft.Padding(12, 9, 12, 9),
            bgcolor=theme.CARD_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            content=ft.Column(
                spacing=4,
                controls=[self._info_title, self._info_type, self._info_tags, self._info_links],
            ),
        )

    def _update_info_overlay(self) -> None:
        if self._selected is None:
            self._info_overlay.visible = False
        else:
            data = self._graph.nodes[self._selected]
            tags = data.get("tags") or []
            self._info_title.value = data.get("title") or self._selected
            self._info_type.value = f"Type: {data.get('type', '—')}"
            self._info_tags.value = f"Tags: {', '.join(tags) or '—'}"
            in_deg = self._graph.in_degree(self._selected)
            out_deg = self._graph.out_degree(self._selected)
            self._info_links.value = f"Links: {in_deg} in / {out_deg} out"
            self._info_overlay.visible = True
        with contextlib.suppress(RuntimeError):
            self._info_overlay.update()
