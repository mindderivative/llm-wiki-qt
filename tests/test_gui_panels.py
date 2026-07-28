"""Phase 15b: Queue, Pipeline Log, Git Controls, and Health Dashboard panels."""

from pathlib import Path

import pytest
from loguru import logger
from PySide6.QtCore import QObject
from PySide6.QtQml import QQmlApplicationEngine

# Importing these registers their @QmlElement types with the QML engine.
import llm_wiki.gui.app_controller  # noqa: F401
import llm_wiki.gui.graph_canvas_item  # noqa: F401
from llm_wiki.gui.app import _QML_DIR
from llm_wiki.gui.git_controller import GitController
from llm_wiki.gui.health_controller import HealthController
from llm_wiki.gui.log_model import LogModel
from llm_wiki.gui.queue_model import QueueListModel
from llm_wiki.ingest import enqueue_file
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


# --- QueueListModel -----------------------------------------------------


def test_queue_model_empty_with_no_connection(qapp) -> None:
    model = QueueListModel()
    assert model.rowCount() == 0


def test_queue_model_reflects_enqueued_items(qapp, vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    src = vault_root.parent / "doc.txt"
    src.write_text("hello", encoding="utf-8")
    enqueue_file(conn, vault_root, src, title="Doc One")

    model = QueueListModel()
    model.set_connection(conn)

    assert model.rowCount() == 1
    index = model.index(0, 0)
    assert model.data(index, QueueListModel.TitleRole) == "Doc One"
    assert model.data(index, QueueListModel.StatusRole) == "queued"
    assert model.data(index, QueueListModel.ErrorRole) == ""


def test_queue_model_refresh_picks_up_new_items(qapp, vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    model = QueueListModel()
    model.set_connection(conn)
    assert model.rowCount() == 0

    src = vault_root.parent / "doc.txt"
    src.write_text("hello", encoding="utf-8")
    enqueue_file(conn, vault_root, src, title="Doc One")
    model.refresh()

    assert model.rowCount() == 1


# --- LogModel -------------------------------------------------------------


def test_log_model_receives_loguru_records(qapp) -> None:
    model = LogModel()
    logger.info("hello from test_log_model_receives_loguru_records")

    lines = [model.data(model.index(i, 0)) for i in range(model.rowCount())]
    assert any("hello from test_log_model_receives_loguru_records" in line for line in lines)


def test_log_model_clear(qapp) -> None:
    model = LogModel()
    logger.info("a message for test_log_model_clear")
    assert model.rowCount() > 0

    model.clear()

    assert model.rowCount() == 0


def test_multiple_log_models_dont_duplicate_sink_registration(qapp) -> None:
    """Each LogModel instance must not register its own loguru handler --
    otherwise a message would be duplicated per instance created across a
    test session.
    """
    first = LogModel()
    second = LogModel()
    logger.info("shared sink message for duplication test")

    first_lines = [first.data(first.index(i, 0)) for i in range(first.rowCount())]
    second_lines = [second.data(second.index(i, 0)) for i in range(second.rowCount())]

    assert sum("shared sink message for duplication test" in line for line in first_lines) == 1
    assert sum("shared sink message for duplication test" in line for line in second_lines) == 1


# --- GitController ----------------------------------------------------


def test_git_controller_reports_uninitialized_before_init(qapp, vault_root: Path) -> None:
    controller = GitController()
    controller.set_vault_path(str(vault_root))

    assert controller.isInitialized is False
    assert controller.changedFiles.rowCount() == 0


def test_git_controller_init_stage_commit_flow(qapp, vault_root: Path) -> None:
    controller = GitController()
    controller.set_vault_path(str(vault_root))

    controller.initRepo()
    assert controller.isInitialized is True
    assert controller.clean is False
    assert controller.changedFiles.rowCount() > 0

    controller.stageAll()
    controller.commit("Initial commit")

    assert controller.clean is True
    assert controller.changedFiles.rowCount() == 0


def test_git_controller_changed_files_model_roles(qapp, vault_root: Path) -> None:
    controller = GitController()
    controller.set_vault_path(str(vault_root))
    controller.initRepo()

    model = controller.changedFiles
    kinds = {model.data(model.index(i, 0), model.KindRole) for i in range(model.rowCount())}
    assert kinds <= {"modified", "untracked"}
    assert "untracked" in kinds  # a fresh vault's files are all untracked


def test_git_controller_handles_many_changed_files_without_hanging(
    qapp, vault_root: Path
) -> None:
    """Regression test for the original GitManager's QProcess stdout-draining
    bug class: a large status/diff output must not hang the UI layer.
    pygit2 (in-process, no subprocess) avoids the root cause entirely, but
    this layer gets its own coverage per the plan's explicit instruction.
    """
    concepts = vault_root / "wiki" / "concepts"
    for i in range(200):
        (concepts / f"note-{i}.md").write_text(f"note {i}", encoding="utf-8")

    controller = GitController()
    controller.set_vault_path(str(vault_root))
    controller.initRepo()  # calls refresh() internally; must return promptly

    assert controller.changedFiles.rowCount() >= 200


def test_git_controller_error_on_commit_without_a_repo_is_surfaced_not_raised(
    qapp, vault_root: Path
) -> None:
    controller = GitController()
    controller.set_vault_path(str(vault_root))
    # Deliberately skip initRepo() -- committing with no .git yet must
    # surface as errorOccurred, not raise pygit2.GitError into the GUI.

    errors = []
    controller.errorOccurred.connect(errors.append)

    controller.commit("no repo yet")

    assert errors


# --- HealthController -----------------------------------------------------


def test_health_controller_defaults_to_100_with_no_connection(qapp) -> None:
    controller = HealthController()
    assert controller.score == 100
    assert controller.schemaViolations == 0


def test_health_controller_reflects_lint_findings(qapp, vault_root: Path) -> None:
    concepts = vault_root / "wiki" / "concepts"
    (concepts / "isolated.md").write_text(
        "---\ntitle: Isolated\nslug: isolated\ntype: concept\ntags: []\nsources: []\n"
        "---\n\nNo links in or out.\n",
        encoding="utf-8",
    )
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    upsert_note_from_file(conn, vault_root, concepts / "isolated.md")

    controller = HealthController()
    controller.set_connection(conn)

    assert controller.score < 100
    assert controller.isolatedNotes == 1


# --- Full app wiring (extends the Phase 15a smoke test with real data) ----


def test_opening_a_vault_populates_all_panel_controllers(
    qapp, vault_root: Path, qtbot
) -> None:
    src = vault_root.parent / "doc.txt"
    src.write_text("hello", encoding="utf-8")
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    enqueue_file(conn, vault_root, src, title="Doc One")
    conn.close()

    engine = QQmlApplicationEngine()
    engine.load(str(_QML_DIR / "Main.qml"))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    controller = root.findChild(QObject, "appController")

    controller.openVault(str(vault_root))

    queue_model = controller.property("queueModel")
    git_controller = controller.property("gitController")
    health_controller = controller.property("healthController")

    assert queue_model.rowCount() == 1
    assert git_controller.property("isInitialized") is False
    assert health_controller.property("score") == 100
