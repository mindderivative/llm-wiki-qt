"""Spatial network graph canvas -- the central workspace of the desktop UI.

Ports the layout and drawing logic of the Phase 15 `GraphCanvasItem`
(`networkx.spring_layout` computed off the UI thread, then nodes and edges
drawn by hand) onto Flet's `Canvas`. Node categories, the legend, and the
zoom controls follow the design mockup; scroll-to-zoom, click-drag-to-pan
the whole canvas, and a "basic info" overlay on node selection (Phase 17)
extend past the mockup, which only demonstrated per-node dragging.
"""

import contextlib

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

    def __init__(self, page: ft.Page, on_node_selected=None) -> None:
        super().__init__()
        self._page = page  # NOT self.page -- ft.Container reserves that name
        self.expand = True
        self.bgcolor = theme.CANVAS_BG
        self.on_node_selected = on_node_selected

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

        self._canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        self._gestures = ft.GestureDetector(
            content=self._canvas,
            expand=True,
            on_pan_start=self._on_pan_start,
            on_pan_update=self._on_pan_update,
            on_pan_end=self._on_pan_end,
            on_scroll=self._on_scroll,
        )
        self._info_overlay = self._build_info_overlay()
        self.content = ft.Stack(
            controls=[
                self._gestures,
                self._build_legend(),
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
        pos = nx.spring_layout(self._graph, k=k, iterations=100, seed=42)
        return {str(node): self._to_canvas(float(xy[0]), float(xy[1])) for node, xy in pos.items()}

    def _compute_layout(self) -> None:
        """Synchronous entry point kept for direct test use. Production code
        goes through `set_graph()` -> `_layout_worker()` instead, which hops
        back via `page.run_task()` before the resulting `_redraw()` touches
        `self._canvas` -- `Control.update()` assumes the event-loop thread.
        """
        self._positions = self._layout_positions()
        self._redraw()

    def _layout_worker(self) -> None:
        positions = self._layout_positions()
        self._page.run_task(self._apply_positions, positions)

    async def _apply_positions(self, positions: dict[str, tuple[float, float]]) -> None:
        self._positions = positions
        self._redraw()

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
        self._redraw()

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
            self._redraw()
        elif self._panning:
            self._pan_x += e.local_delta.x
            self._pan_y += e.local_delta.y
            self._redraw()

    def _on_pan_end(self, e: ft.DragEndEvent) -> None:
        self._dragging = None
        self._panning = False

    def _notify_selection(self) -> None:
        if self.on_node_selected is not None:
            self.on_node_selected(self._selected)
        self._update_info_overlay()
        self._redraw()

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
        self._redraw()

    def _on_scroll(self, e: ft.ScrollEvent) -> None:
        # Scroll up (negative dy) zooms in, matching maps/design-tool convention.
        step = _SCROLL_ZOOM_STEP if e.scroll_delta.y < 0 else -_SCROLL_ZOOM_STEP
        self._set_zoom(self._zoom + step, focal=(e.local_position.x, e.local_position.y))

    # --- Drawing ------------------------------------------------------------

    def build_shapes(self) -> list[cv.Shape]:
        """The canvas' edge and node shapes for the current layout."""
        shapes: list[cv.Shape] = []
        edge_paint = ft.Paint(color=theme.GRAPH_EDGE, stroke_width=1.8)

        for u, v in self._graph.edges():
            if u in self._positions and v in self._positions:
                x1, y1 = self._positions[u]
                x2, y2 = self._positions[v]
                shapes.append(
                    cv.Line(
                        x1 * self._zoom + self._pan_x,
                        y1 * self._zoom + self._pan_y,
                        x2 * self._zoom + self._pan_x,
                        y2 * self._zoom + self._pan_y,
                        paint=edge_paint,
                    )
                )

        for slug, (x, y) in self._positions.items():
            color = (
                theme.ACCENT
                if slug == self._selected
                else theme.CATEGORY_COLORS[_category_of(slug)]
            )
            sx = x * self._zoom + self._pan_x
            sy = y * self._zoom + self._pan_y
            shapes.append(
                cv.Circle(sx, sy, _NODE_RADIUS * self._zoom, paint=ft.Paint(color=color))
            )
            shapes.append(
                cv.Text(
                    sx,
                    sy + _NODE_RADIUS * self._zoom + 3,
                    slug,
                    style=ft.TextStyle(size=10.5, color=theme.TEXT_NODE),
                    alignment=ft.Alignment.TOP_CENTER,
                )
            )

        return shapes

    def _redraw(self) -> None:
        self._canvas.shapes = self.build_shapes()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self._canvas.update()

    def _build_legend(self) -> ft.Control:
        return ft.Container(
            left=14,
            top=12,
            padding=ft.Padding(12, 9, 12, 9),
            bgcolor=theme.CHROME_BG,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            content=ft.Column(
                spacing=5,
                controls=[
                    ft.Row(
                        spacing=7,
                        controls=[
                            ft.Container(
                                width=8, height=8, bgcolor=color, border_radius=4
                            ),
                            ft.Text(name.title(), size=10.5, color=theme.TEXT_TOGGLE_OFF),
                        ],
                    )
                    for name, color in theme.CATEGORY_COLORS.items()
                ],
            ),
        )

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
