"""Phase 15a: QML engine loads cleanly, the graph canvas renders real link
data, AppController drives vault lifecycle + settings round-trip.
"""

import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine

# Importing these registers their @QmlElement types with the QML engine --
# required before any test in this module loads Main.qml.
import llm_wiki.gui.app_controller  # noqa: E402, F401
import llm_wiki.gui.graph_canvas_item  # noqa: E402, F401
from llm_wiki.graph import sync_links
from llm_wiki.gui.app import _QML_DIR, wire_graph_canvas
from llm_wiki.storage import connect, upsert_note_from_file
from llm_wiki.vault import create_vault


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AppController's vault calls update the real ~/.config/LLM-Wiki-Qt/
    recent_vaults.json as a side effect -- redirect that to a throwaway
    home so tests never touch the real one (same pattern as Phase 12's
    CLI tests).
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)


@pytest.fixture
def engine(qapp) -> QQmlApplicationEngine:
    eng = QQmlApplicationEngine()
    eng.load(str(_QML_DIR / "Main.qml"))
    assert eng.rootObjects(), "Main.qml failed to load"
    return eng


def _find(engine: QQmlApplicationEngine, object_name: str) -> QObject:
    root = engine.rootObjects()[0]
    obj = root.findChild(QObject, object_name)
    assert obj is not None, f"no object named {object_name!r} in the scene"
    return obj


def _build_linked_vault(tmp_path: Path) -> Path:
    vault_root = tmp_path / "vault"
    create_vault(vault_root, "Test Vault", "desc")
    concepts = vault_root / "wiki" / "concepts"
    (concepts / "a.md").write_text(
        "---\ntitle: A\nslug: a\ntype: concept\ntags: []\nsources: []\n---\n\nSee [[b]].\n",
        encoding="utf-8",
    )
    (concepts / "b.md").write_text(
        "---\ntitle: B\nslug: b\ntype: concept\ntags: []\nsources: []\n---\n\nSee [[a]].\n",
        encoding="utf-8",
    )
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    upsert_note_from_file(conn, vault_root, concepts / "a.md")
    upsert_note_from_file(conn, vault_root, concepts / "b.md")
    sync_links(conn, vault_root)
    conn.close()
    return vault_root


def test_engine_loads_main_qml_with_no_warnings(qapp) -> None:
    warnings = []
    eng = QQmlApplicationEngine()
    eng.warnings.connect(warnings.extend)
    eng.load(str(_QML_DIR / "Main.qml"))

    assert len(eng.rootObjects()) == 1
    assert warnings == []


def test_expected_top_level_objects_present(engine: QQmlApplicationEngine) -> None:
    assert _find(engine, "appController") is not None
    assert _find(engine, "graphCanvas") is not None
    assert _find(engine, "bottomDockArea") is not None
    assert _find(engine, "statusIndicator") is not None


def test_app_controller_starts_with_no_active_vault(engine: QQmlApplicationEngine) -> None:
    controller = _find(engine, "appController")
    assert controller.property("hasVault") is False
    assert controller.property("vaultPath") == ""


def test_create_vault_sets_active_vault(engine: QQmlApplicationEngine, tmp_path: Path) -> None:
    controller = _find(engine, "appController")
    vault_path = tmp_path / "new-vault"

    controller.createVault(str(vault_path), "My Vault", "A description.")

    assert controller.property("hasVault") is True
    assert controller.property("vaultName") == "My Vault"
    assert Path(controller.property("vaultPath")) == vault_path.resolve()
    assert (vault_path / ".llm-wiki-config").exists()


def test_open_vault_on_invalid_directory_emits_error(
    engine: QQmlApplicationEngine, tmp_path: Path
) -> None:
    controller = _find(engine, "appController")
    errors = []
    controller.errorOccurred.connect(errors.append)

    controller.openVault(str(tmp_path / "does-not-exist"))

    assert controller.property("hasVault") is False
    assert len(errors) == 1


def test_graph_canvas_renders_fixture_graph(
    engine: QQmlApplicationEngine, tmp_path: Path, qtbot
) -> None:
    vault_root = _build_linked_vault(tmp_path)
    wire_graph_canvas(engine)
    controller = _find(engine, "appController")
    canvas = _find(engine, "graphCanvas")

    controller.openVault(str(vault_root))

    qtbot.waitUntil(lambda: bool(canvas.node_positions), timeout=5000)
    assert set(canvas.node_positions.keys()) == {"a", "b"}


def test_settings_round_trip_through_dot_llm_wiki_config(
    engine: QQmlApplicationEngine, tmp_path: Path
) -> None:
    vault_root = tmp_path / "vault"
    create_vault(vault_root, "Test Vault", "desc")
    controller = _find(engine, "appController")

    controller.openVault(str(vault_root))
    assert controller.property("chatModel") == "qwen2.5-coder-14b"  # default

    controller.setProperty("chatModel", "qwen3-8b")
    controller.setProperty("hostPort", 9999)
    controller.saveSettings()

    raw = json.loads((vault_root / ".llm-wiki-config").read_text(encoding="utf-8"))
    assert raw["llm_provider"]["chat_model"] == "qwen3-8b"
    assert raw["llm_provider"]["host_port"] == 9999
    # Vault identity fields, written by vault.manager, survive the save.
    assert raw["vault_name"] == "Test Vault"


def test_no_pyside6_import_anywhere_in_the_engine_cli_or_mcp_packages() -> None:
    """Design Principle 5: PySide6 exists only under llm_wiki.gui."""
    import ast
    from pathlib import Path as _Path

    src_root = _Path(__file__).parent.parent / "src" / "llm_wiki"
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
            if any(name.startswith("PySide6") for name in names):
                offending.append(str(py_file))

    assert offending == []
