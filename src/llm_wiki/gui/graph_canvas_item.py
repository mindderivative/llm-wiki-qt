"""Spatial network graph canvas exposed to QML (ARCHITECTURE.md's central
workspace canvas).

A `QQuickPaintedItem` port of the pre-rebuild `GraphWidget`
(`.archive/graph_widget.py`): layout computation (`networkx.spring_layout`,
off the GUI thread via `QRunnable`) and `QPainter`-based node/edge drawing
are unchanged -- only the hosting item type differs, since this now lives
inside a QML scene instead of a `QWidget`.
"""

import networkx as nx
from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtQml import QmlElement
from PySide6.QtQuick import QQuickPaintedItem

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1

_NODE_RADIUS = 8.0
_EDGE_COLOR = "#45475a"
_NODE_FILL = "#89b4fa"
_NODE_FILL_SELECTED = "#f38ba8"
_NODE_BORDER = "#11111b"
_LABEL_COLOR = "#cdd6f4"


class _GraphLayoutSignals(QObject):
    layout_computed = Signal(dict)


class _GraphLayoutTask(QRunnable):
    """Calculates a NetworkX spring layout off the main GUI thread."""

    def __init__(self, graph: nx.DiGraph) -> None:
        super().__init__()
        self.graph = graph
        self.signals = _GraphLayoutSignals()

    def run(self) -> None:
        if self.graph.number_of_nodes() == 0:
            self.signals.layout_computed.emit({})
            return

        pos = nx.spring_layout(self.graph, k=0.15, iterations=50, seed=42)
        pos_dict = {
            str(node): (float(coords[0]), float(coords[1])) for node, coords in pos.items()
        }
        self.signals.layout_computed.emit(pos_dict)


@QmlElement
class GraphCanvasItem(QQuickPaintedItem):
    """Interactive canvas visualizing the vault's `[[wikilink]]` network."""

    nodeSelected = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._graph = nx.DiGraph()
        self._node_positions: dict[str, QPointF] = {}
        self._thread_pool = QThreadPool.globalInstance()
        self._selected_node: str | None = None

    def set_graph(self, graph: nx.DiGraph) -> None:
        """Updates the displayed graph and launches async layout calculation."""
        self._graph = graph.copy()
        task = _GraphLayoutTask(self._graph)
        task.signals.layout_computed.connect(self._on_layout_computed)
        self._thread_pool.start(task)

    @property
    def node_positions(self) -> dict[str, QPointF]:
        """Current node -> canvas position mapping (populated asynchronously)."""
        return self._node_positions

    def _on_layout_computed(self, pos_dict: dict[str, tuple[float, float]]) -> None:
        """Scales the computed layout vectors to the item's current size."""
        self._node_positions.clear()
        if not pos_dict:
            self.update()
            return

        w = self.width() - 80
        h = self.height() - 80
        cx = self.width() / 2.0
        cy = self.height() / 2.0

        for node, (x, y) in pos_dict.items():
            px = cx + (x * (w / 2.0))
            py = cy + (y * (h / 2.0))
            self._node_positions[node] = QPointF(px, py)

        self.update()

    def paint(self, painter: QPainter) -> None:
        """Renders edges and nodes."""
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        pen_edge = QPen(QColor(_EDGE_COLOR), 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen_edge)
        for u, v in self._graph.edges():
            if u in self._node_positions and v in self._node_positions:
                painter.drawLine(self._node_positions[u], self._node_positions[v])

        font = QFont("SansSerif", 9)
        painter.setFont(font)

        for node, pos in self._node_positions.items():
            is_selected = node == self._selected_node
            fill = QColor(_NODE_FILL_SELECTED if is_selected else _NODE_FILL)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(QColor(_NODE_BORDER), 1))

            rect = QRectF(
                pos.x() - _NODE_RADIUS, pos.y() - _NODE_RADIUS, _NODE_RADIUS * 2, _NODE_RADIUS * 2
            )
            painter.drawEllipse(rect)

            painter.setPen(QPen(QColor(_LABEL_COLOR)))
            painter.drawText(QPointF(pos.x() + 12, pos.y() + 4), node)
