"""Spatial network graph canvas -- the central workspace of the desktop UI.

Ports the layout and drawing logic of the Phase 15 `GraphCanvasItem`
(`networkx.spring_layout` computed off the UI thread, then nodes and edges
drawn by hand) onto Flet's `Canvas`. Node categories, the legend, and the
zoom controls follow the design mockup.
"""

import contextlib
import threading

import flet as ft
import flet.canvas as cv
import networkx as nx

from llm_wiki.gui import theme

_NODE_RADIUS = 9.0
_LAYOUT_MARGIN = 60.0
_ZOOM_STEP = 0.15
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

    def __init__(self, on_node_selected=None) -> None:
        super().__init__()
        self.expand = True
        self.bgcolor = theme.CANVAS_BG
        self.on_node_selected = on_node_selected

        self._graph = nx.DiGraph()
        self._positions: dict[str, tuple[float, float]] = {}
        self._width = _BASE_WIDTH
        self._height = _BASE_HEIGHT
        self._zoom = 1.0
        self._dragging: str | None = None
        self._selected: str | None = None

        self._canvas = cv.Canvas(shapes=[], expand=True, on_resize=self._on_resize)
        self._gestures = ft.GestureDetector(
            content=self._canvas,
            expand=True,
            on_pan_start=self._on_pan_start,
            on_pan_update=self._on_pan_update,
            on_pan_end=self._on_pan_end,
        )
        self.content = ft.Stack(
            controls=[self._gestures, self._build_legend(), self._build_zoom_controls()],
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
        threading.Thread(target=self._compute_layout, daemon=True).start()

    def _compute_layout(self) -> None:
        if self._graph.number_of_nodes() == 0:
            self._positions = {}
        else:
            pos = nx.spring_layout(self._graph, k=0.15, iterations=50, seed=42)
            self._positions = {
                str(node): self._to_canvas(float(xy[0]), float(xy[1])) for node, xy in pos.items()
            }
        self._redraw()

    def _to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """Maps a spring-layout vector (roughly -1..1) into canvas pixels."""
        half_w = (self._width - _LAYOUT_MARGIN * 2) / 2.0
        half_h = (self._height - _LAYOUT_MARGIN * 2) / 2.0
        return (self._width / 2.0 + x * half_w, self._height / 2.0 + y * half_h)

    def _on_resize(self, e: cv.CanvasResizeEvent) -> None:
        self._width, self._height = e.width, e.height
        self._redraw()

    # --- Interaction --------------------------------------------------------

    def _node_at(self, x: float, y: float) -> str | None:
        """The node whose circle contains `(x, y)`, if any."""
        hit_radius = _NODE_RADIUS * self._zoom
        for slug, (nx_, ny_) in self._positions.items():
            if (nx_ * self._zoom - x) ** 2 + (ny_ * self._zoom - y) ** 2 <= hit_radius**2:
                return slug
        return None

    def _on_pan_start(self, e: ft.DragStartEvent) -> None:
        self._dragging = self._node_at(e.local_position.x, e.local_position.y)
        if self._dragging is not None:
            self._selected = self._dragging
            if self.on_node_selected is not None:
                self.on_node_selected(self._dragging)
            self._redraw()

    def _on_pan_update(self, e: ft.DragUpdateEvent) -> None:
        if self._dragging is None:
            return
        self._positions[self._dragging] = (
            e.local_position.x / self._zoom,
            e.local_position.y / self._zoom,
        )
        self._redraw()

    def _on_pan_end(self, e: ft.DragEndEvent) -> None:
        self._dragging = None

    def zoom_in(self, e=None) -> None:
        self._set_zoom(self._zoom + _ZOOM_STEP)

    def zoom_out(self, e=None) -> None:
        self._set_zoom(self._zoom - _ZOOM_STEP)

    def zoom_reset(self, e=None) -> None:
        self._set_zoom(1.0)

    def _set_zoom(self, value: float) -> None:
        self._zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, value))
        self._redraw()

    # --- Drawing ------------------------------------------------------------

    def build_shapes(self) -> list[cv.Shape]:
        """The canvas' edge and node shapes for the current layout."""
        shapes: list[cv.Shape] = []
        edge_paint = ft.Paint(color=theme.GRAPH_EDGE, stroke_width=1.2)

        for u, v in self._graph.edges():
            if u in self._positions and v in self._positions:
                x1, y1 = self._positions[u]
                x2, y2 = self._positions[v]
                shapes.append(
                    cv.Line(
                        x1 * self._zoom,
                        y1 * self._zoom,
                        x2 * self._zoom,
                        y2 * self._zoom,
                        paint=edge_paint,
                    )
                )

        for slug, (x, y) in self._positions.items():
            color = (
                theme.ACCENT
                if slug == self._selected
                else theme.CATEGORY_COLORS[_category_of(slug)]
            )
            shapes.append(
                cv.Circle(
                    x * self._zoom,
                    y * self._zoom,
                    _NODE_RADIUS * self._zoom,
                    paint=ft.Paint(color=color),
                )
            )
            shapes.append(
                cv.Text(
                    x * self._zoom,
                    y * self._zoom + _NODE_RADIUS * self._zoom + 3,
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
