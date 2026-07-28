"""QML desktop app entry point (Phase 15 -- ARCHITECTURE.md's thin Qt shell
over the stable engine API, Design Principle 5).

No PySide6 import happens anywhere in `llm_wiki`'s engine, CLI, or MCP
packages -- this module and its siblings under `llm_wiki.gui` are the only
place Qt appears at all.
"""

import sys
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

# Importing these registers their @QmlElement types with the QML engine.
import llm_wiki.gui.app_controller  # noqa: F401
import llm_wiki.gui.graph_canvas_item  # noqa: F401
from llm_wiki.graph import get_graph_data
from llm_wiki.storage import connect

_QML_DIR = Path(__file__).parent / "qml"


def wire_graph_canvas(engine: QQmlApplicationEngine) -> None:
    """Feeds the graph canvas the active vault's link graph whenever it changes.

    Looked up by `objectName` (set in `Main.qml`) rather than assumed as a
    fixed root-object index, so this stays robust to layout changes.
    """
    root = engine.rootObjects()[0]
    controller = root.findChild(QObject, "appController")
    canvas = root.findChild(QObject, "graphCanvas")
    if controller is None or canvas is None:
        return

    def refresh_graph() -> None:
        vault_path = controller.property("vaultPath")
        if not vault_path:
            return
        conn = connect(Path(vault_path) / ".llm-wiki" / "db.sqlite3")
        canvas.set_graph(get_graph_data(conn))

    controller.vaultChanged.connect(refresh_graph)


def main() -> int:
    QQuickStyle.setStyle("Material")

    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    engine.load(str(_QML_DIR / "Main.qml"))

    if not engine.rootObjects():
        return 1

    wire_graph_canvas(engine)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
