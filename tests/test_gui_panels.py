"""Phase 16b: Items, Git Controls, and Pipeline Log panels.

Flet controls are plain dataclasses (see test_gui_shell.py's module
docstring for why), so these are exercised the same way: build a real
panel and assert on its state and control tree, no display required.
"""

from pathlib import Path

import pytest
from loguru import logger

from llm_wiki.gui.git_panel import GitPanel
from llm_wiki.gui.items_panel import ItemsPanel
from llm_wiki.gui.log_bridge import LogLine, subscribe
from llm_wiki.gui.log_panel import LogPanel
from llm_wiki.ingest import enqueue_file, update_status
from llm_wiki.models import QueueStatus
from llm_wiki.storage import connect
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


# --- ItemsPanel ---------------------------------------------------------


def test_items_panel_empty_with_no_connection() -> None:
    panel = ItemsPanel()
    assert panel.raw_items == []
    assert panel.queue_items == []


def test_items_panel_splits_completed_from_in_progress(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    src = vault_root.parent / "doc.txt"
    src.write_text("hello", encoding="utf-8")
    item = enqueue_file(conn, vault_root, src, title="Doc One")

    panel = ItemsPanel()
    panel.set_connection(conn)
    assert len(panel.queue_items) == 1
    assert panel.raw_items == []
    assert "QUEUE · 1" in panel._queue_header.value

    update_status(conn, item.id, QueueStatus.COMPLETED)
    panel.refresh()

    assert len(panel.raw_items) == 1
    assert panel.queue_items == []
    assert "RAW ITEMS · 1" in panel._raw_header.value


def test_items_panel_refresh_picks_up_new_items(vault_root: Path) -> None:
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    panel = ItemsPanel()
    panel.set_connection(conn)
    assert panel.queue_items == []

    src = vault_root.parent / "doc.txt"
    src.write_text("hello", encoding="utf-8")
    enqueue_file(conn, vault_root, src, title="Doc One")
    panel.refresh()

    assert len(panel.queue_items) == 1


# --- LogPanel / log_bridge ----------------------------------------------


def test_log_panel_receives_loguru_records() -> None:
    panel = LogPanel()
    logger.info("hello from test_log_panel_receives_loguru_records")

    assert any(
        "hello from test_log_panel_receives_loguru_records" in line.message
        for line in panel.lines
    )


def test_log_panel_clear() -> None:
    panel = LogPanel()
    logger.info("a message for test_log_panel_clear")
    assert panel.lines

    panel.clear()

    assert panel.lines == []
    assert panel._body.controls == []


def test_multiple_log_panels_dont_duplicate_sink_registration() -> None:
    """Each LogPanel instance must not register its own loguru handler --
    otherwise a message would be duplicated per instance created across a
    test session.
    """
    first = LogPanel()
    second = LogPanel()
    logger.info("shared sink message for duplication test")

    assert sum("shared sink message for duplication test" in ln.message for ln in first.lines) == 1
    assert sum("shared sink message for duplication test" in ln.message for ln in second.lines) == 1


def test_log_bridge_reports_the_records_level() -> None:
    received: list[LogLine] = []
    subscribe(received.append)

    logger.warning("a warning for test_log_bridge_reports_the_records_level")

    assert received[-1].level == "WARNING"


# --- GitPanel -------------------------------------------------------------


def test_git_panel_reports_uninitialized_before_init(vault_root: Path) -> None:
    panel = GitPanel(on_error=lambda _msg: None)
    panel.set_vault_path(vault_root)

    assert panel.is_initialized is False
    assert panel._files.controls == []
    assert panel._init_button.visible is True


def test_git_panel_init_stage_commit_flow(vault_root: Path) -> None:
    panel = GitPanel(on_error=lambda _msg: None)
    panel.set_vault_path(vault_root)

    panel.init_repo()
    assert panel.is_initialized is True
    assert panel.clean is False
    assert panel._files.controls
    assert panel._init_button.visible is False

    panel.stage_all()
    panel._message.value = "Initial commit"
    panel.commit()

    assert panel.clean is True
    assert panel._files.controls == []


def test_git_panel_handles_many_changed_files_without_hanging(vault_root: Path) -> None:
    """Regression test for the original GitManager's QProcess stdout-draining
    bug class: a large status/diff output must not hang the UI layer.
    pygit2 (in-process, no subprocess) avoids the root cause entirely, but
    this layer gets its own coverage per the plan's explicit instruction.
    """
    concepts = vault_root / "wiki" / "concepts"
    for i in range(200):
        (concepts / f"note-{i}.md").write_text(f"note {i}", encoding="utf-8")

    panel = GitPanel(on_error=lambda _msg: None)
    panel.set_vault_path(vault_root)
    panel.init_repo()  # calls refresh() internally; must return promptly

    assert len(panel._files.controls) >= 200


def test_git_panel_commit_without_a_message_is_surfaced_not_raised(vault_root: Path) -> None:
    errors = []
    panel = GitPanel(on_error=errors.append)
    panel.set_vault_path(vault_root)
    panel.init_repo()
    panel.stage_all()

    panel.commit()  # no message set

    assert errors


def test_git_panel_commit_without_a_repo_is_surfaced_not_raised(vault_root: Path) -> None:
    errors = []
    panel = GitPanel(on_error=errors.append)
    panel.set_vault_path(vault_root)
    # Deliberately skip init_repo() -- committing with no .git yet must
    # surface via on_error, not raise pygit2.GitError into the GUI.
    panel._message.value = "no repo yet"

    panel.commit()

    assert errors
