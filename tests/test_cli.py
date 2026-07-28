"""Phase 12: CLI commands, via typer's CliRunner -- no PySide6, no real network."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import llm_wiki.cli.main as cli
from llm_wiki.cli.main import app
from llm_wiki.compiler import CompileResult
from llm_wiki.vcs import git_engine

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every vault-scoped command calls load_vault(), which updates the
    real ~/.config/LLM-Wiki-Qt/recent_vaults.json as a side effect --
    redirect that to a throwaway home so tests never touch the real one.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)


def test_vault_create(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"

    result = runner.invoke(
        app, ["vault", "create", str(vault_path), "--name", "My Vault", "--description", "desc"]
    )

    assert result.exit_code == 0
    assert "Created vault 'My Vault'" in result.output
    assert (vault_path / ".llm-wiki-config").exists()


def test_vault_create_defaults_name_to_directory_name(tmp_path: Path) -> None:
    vault_path = tmp_path / "default-named-vault"

    result = runner.invoke(app, ["vault", "create", str(vault_path)])

    assert result.exit_code == 0
    assert "default-named-vault" in result.output


def test_vault_open_on_valid_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    result = runner.invoke(app, ["vault", "open", str(vault_path)])

    assert result.exit_code == 0
    assert "Opened vault" in result.output


def test_vault_open_on_invalid_directory_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["vault", "open", str(tmp_path / "not-a-vault")])

    assert result.exit_code == 1


def test_link_sync_and_rebuild_on_empty_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    sync_result = runner.invoke(app, ["link", "sync", "--vault", str(vault_path)])
    rebuild_result = runner.invoke(app, ["link", "rebuild", "--vault", str(vault_path)])

    assert sync_result.exit_code == 0
    assert "Synced 0 note(s)." in sync_result.output
    assert rebuild_result.exit_code == 0
    assert "Rebuilt links for 0 note(s)." in rebuild_result.output


def test_lint_run_on_clean_vault(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    result = runner.invoke(app, ["lint", "run", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Health score: 100/100. No issues found." in result.output


def test_storage_rebuild(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    result = runner.invoke(app, ["storage", "rebuild", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Storage cache rebuilt from wiki/." in result.output
    assert (vault_path / ".llm-wiki" / "db.sqlite3").exists()


def test_git_init_status_commit_flow(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    init_result = runner.invoke(app, ["git", "init", "--vault", str(vault_path)])
    assert init_result.exit_code == 0

    dirty_status = runner.invoke(app, ["git", "status", "--vault", str(vault_path)])
    assert dirty_status.exit_code == 0
    assert "(dirty)" in dirty_status.output
    assert "untracked: SCHEMA.md" in dirty_status.output

    commit_result = runner.invoke(
        app, ["git", "commit", "--vault", str(vault_path), "-m", "Initial commit"]
    )
    assert commit_result.exit_code == 0
    assert "Committed" in commit_result.output

    clean_status = runner.invoke(app, ["git", "status", "--vault", str(vault_path)])
    assert "(clean)" in clean_status.output


def test_git_push_reports_failure_without_a_remote(tmp_path: Path) -> None:
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])
    runner.invoke(app, ["git", "init", "--vault", str(vault_path)])

    result = runner.invoke(app, ["git", "push", "--vault", str(vault_path)])

    assert result.exit_code == 1
    assert "Push failed" in result.output


def test_commands_on_nonexistent_vault_fail_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    for args in (
        ["link", "sync", "--vault", str(missing)],
        ["lint", "run", "--vault", str(missing)],
        ["storage", "rebuild", "--vault", str(missing)],
        ["git", "status", "--vault", str(missing)],
    ):
        result = runner.invoke(app, args)
        assert result.exit_code == 1, f"expected failure for {args}"


def test_ingest_enqueues_and_calls_compile_queued_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the CLI's own orchestration (enqueue -> compile -> report) by
    stubbing compile_queued_item() -- its internals are already covered by
    Phase 9's tests; this test is about the CLI wiring, not the pipeline.
    """
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    source_file = tmp_path / "note.txt"
    source_file.write_text("Some raw content to ingest.", encoding="utf-8")

    captured: dict = {}

    def fake_compile_queued_item(conn, client, vault, item_id, *, chat_model, embedding_model):
        captured["item_id"] = item_id
        captured["chat_model"] = chat_model
        captured["embedding_model"] = embedding_model
        return CompileResult(
            source_path=vault / "wiki" / "sources" / "note.md",
            entity_paths=[vault / "wiki" / "entities" / "thing.md"],
            chunk_ids=[1, 2],
        )

    monkeypatch.setattr(cli, "compile_queued_item", fake_compile_queued_item)

    result = runner.invoke(
        app,
        [
            "ingest",
            str(source_file),
            "--vault",
            str(vault_path),
            "--title",
            "My Note",
            "--chat-model",
            "test-model",
        ],
    )

    assert result.exit_code == 0
    assert "Queued 'My Note' as item #" in result.output
    assert "Wrote source summary" in result.output
    assert "Wrote/updated entity note" in result.output
    assert "Embedded 2 chunk(s)." in result.output
    assert captured["chat_model"] == "test-model"
    assert captured["item_id"] is not None


def test_ingest_on_nonexistent_vault_fails_cleanly(tmp_path: Path) -> None:
    source_file = tmp_path / "note.txt"
    source_file.write_text("content", encoding="utf-8")

    result = runner.invoke(
        app, ["ingest", str(source_file), "--vault", str(tmp_path / "does-not-exist")]
    )

    assert result.exit_code == 1


def test_ingest_with_missing_source_file_fails_cleanly_not_a_traceback(tmp_path: Path) -> None:
    """Regression test: enqueue_file()'s IngestionError must be caught and
    reported as a clean exit, not leak an unhandled-exception traceback.
    """
    vault_path = tmp_path / "my-vault"
    runner.invoke(app, ["vault", "create", str(vault_path)])

    result = runner.invoke(
        app, ["ingest", str(tmp_path / "does-not-exist.md"), "--vault", str(vault_path)]
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Source file not found" in result.output


def test_no_pyside6_imported_anywhere_in_cli_module() -> None:
    """Design Principle 5: the CLI stays a thin, Qt-free interface onto the engine.

    Checks cli/main.py's own source (via AST) rather than sys.modules --
    once Phase 15 introduces a real PySide6 GUI, other tests in the same
    session legitimately import PySide6, so a process-wide sys.modules
    check is no longer a valid proxy for "this module doesn't import it".
    """
    import ast
    from pathlib import Path

    source_path = Path(cli.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)

    assert not any(name.startswith("PySide6") for name in imported_names)
    # git_engine import used above should not have pulled in a Qt binding either.
    assert git_engine is not None
