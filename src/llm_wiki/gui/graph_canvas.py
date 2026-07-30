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
import math

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


def _category_of(slug: str) -> str:
    """Assigns a node a stable colour bucket.

    The vault has no category concept -- notes are just slugs -- so this
    spreads them deterministically across the mockup's palette instead of
    inventing metadata the engine doesn't have.
    """
    categories = list(theme.CATEGORY_COLORS)
    return categories[hash(slug) % len(categories)]


class GraphCanvas(ft.Container):
    """Interactive canvas visualizing the vault's `[[wikilink]]` network."""

    def __init__(
        self, page: ft.Page, on_node_selected=None, on_settings_panel_toggled=None
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
        self._redraw_all()

    def _layout_worker(self) -> None:
        positions = self._layout_positions()
        self._page.run_task(self._apply_positions, positions)

    async def _apply_positions(self, positions: dict[str, tuple[float, float]]) -> None:
        self._positions = positions
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
        """The node whose circle contains `(x, y)`, if any."""
        hit_radius = _NODE_RADIUS * self._zoom
        for slug, (nx_, ny_) in self._positions.items():
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

    # --- Live local force simulation (Phase 22) ------------------------------

    def _start_simulation(self) -> None:
        """Called when a node-drag begins. Snapshots the pre-drag layout as
        the "home" every perturbed node eases back toward once released, and
        (re)starts the tick loop if it isn't already running.
        """
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
                    fx += -_SIM_NEIGHBOR_SPRING_K * stretch * dx / dist
                    fy += -_SIM_NEIGHBOR_SPRING_K * stretch * dy / dist
                if dist < _SIM_REPEL_RADIUS:
                    push = _SIM_REPEL_STRENGTH * (1 - dist / _SIM_REPEL_RADIUS) / dist
                    fx += push * dx
                    fy += push * dy

            hx, hy = self._home_positions[slug]
            fx += -_SIM_HOME_SPRING_K * (px - hx)
            fy += -_SIM_HOME_SPRING_K * (py - hy)

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
        self._redraw_all()

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
        # Scroll up (negative dy) zooms in, matching maps/design-tool convention.
        step = _SCROLL_ZOOM_STEP if e.scroll_delta.y < 0 else -_SCROLL_ZOOM_STEP
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

    def _node_circle(self, slug: str, x: float, y: float) -> cv.Circle:
        color = (
            theme.ACCENT if slug == self._selected else theme.CATEGORY_COLORS[_category_of(slug)]
        )
        sx = x * self._zoom + self._pan_x
        sy = y * self._zoom + self._pan_y
        return cv.Circle(sx, sy, _NODE_RADIUS * self._zoom, paint=ft.Paint(color=color))

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
            shape = self._edge_shape(u, v, edge_paint)
            if shape is not None:
                shapes.append(shape)
        for slug, (x, y) in self._positions.items():
            if slug in dynamic:
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
                shape = self._edge_shape(u, v, edge_paint)
                if shape is not None:
                    shapes.append(shape)
                if u not in dynamic:
                    static_endpoints.add(u)
                if v not in dynamic:
                    static_endpoints.add(v)
        for slug in dynamic:
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

    def _build_hover_shapes(self) -> list[cv.Shape]:
        """At most one label -- whichever node the mouse is currently
        over, if any and it still has a live position.
        """
        if self._hovered is None:
            return []
        pos = self._positions.get(self._hovered)
        if pos is None:
            return []
        return [self._hover_label_shape(self._hovered, *pos)]

    def _redraw_hover(self) -> None:
        self._hover_canvas.shapes = self._build_hover_shapes()
        with contextlib.suppress(RuntimeError):
            self._hover_canvas.update()

    # --- Settings panel (Phase 23) -------------------------------------------

    def _build_settings_panel(self) -> ft.Control:
        """Replaces the old always-on legend -- same slot/styling, now a
        collapsible shell. The legend is today's only content; Filters and
        Settings (later phases) will add more sections here.
        """
        return ft.Container(
            left=14,
            top=12,
            padding=ft.Padding(12, 9, 12, 9),
            bgcolor=theme.CHROME_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            content=self._build_settings_panel_content(),
        )

    def _build_settings_panel_content(self) -> ft.Control:
        header = ft.Row(
            spacing=6,
            controls=[
                ft.Text("⚙", size=12, color=theme.TEXT_TOGGLE_OFF),
                ft.Text(
                    "Settings", size=10.5, weight=ft.FontWeight.W_600, color=theme.TEXT
                ),
                ft.Container(width=24),  # spacer, pushes the toggle to the right
                ft.Container(
                    on_click=self._toggle_settings_panel,
                    content=ft.Text(
                        "▾" if self._settings_panel_expanded else "▸",
                        size=11,
                        color=theme.TEXT_TOGGLE_OFF,
                    ),
                ),
            ],
        )
        if not self._settings_panel_expanded:
            return header
        return ft.Column(
            spacing=8,
            controls=[
                header,
                ft.Column(
                    spacing=5,
                    controls=[
                        ft.Row(
                            spacing=7,
                            controls=[
                                ft.Container(
                                    width=8, height=8, bgcolor=color, border_radius=4
                                ),
                                ft.Text(
                                    name.title(), size=10.5, color=theme.TEXT_TOGGLE_OFF
                                ),
                            ],
                        )
                        for name, color in theme.CATEGORY_COLORS.items()
                    ],
                ),
            ],
        )

    def _rebuild_settings_panel(self) -> None:
        self._settings_panel.content = self._build_settings_panel_content()
        with contextlib.suppress(RuntimeError):
            self._settings_panel.update()

    def _toggle_settings_panel(self, e=None) -> None:
        self._settings_panel_expanded = not self._settings_panel_expanded
        self._rebuild_settings_panel()
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
        self._rebuild_settings_panel()

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
