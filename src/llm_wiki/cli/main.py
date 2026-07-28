"""Typer CLI -- the first real interface onto the `llm_wiki` engine (ARCHITECTURE.md §3).

Every command here is a thin wrapper: no new engine logic lives in this
module, only argument parsing, connection wiring, and printing. Runs
synchronously and prints progress to stdout (ARCHITECTURE.md §9). Zero
PySide6 anywhere in this process, per Design Principle 5.
"""

from pathlib import Path

import typer

from llm_wiki.compiler import compile_queued_item
from llm_wiki.config import AppSettings
from llm_wiki.graph import rebuild_full, sync_links
from llm_wiki.ingest import enqueue_file
from llm_wiki.lint import run_lint
from llm_wiki.llm.client import DEFAULT_API_KEY, LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.models import LLMWikiError
from llm_wiki.storage import connect, rebuild_from_vault
from llm_wiki.vault import CONFIG_FILENAME, create_vault, load_vault
from llm_wiki.vcs import git_engine

app = typer.Typer(name="llm-wiki", help="Local-first, self-maintaining knowledge base engine.")
vault_app = typer.Typer(help="Create, open, and inspect vaults.")
link_app = typer.Typer(help="Maintain the [[wikilink]] graph.")
lint_app = typer.Typer(help="Run health checks against the DB cache.")
storage_app = typer.Typer(help="Manage the SQLite + sqlite-vec cache.")
git_app = typer.Typer(help="Version control the vault.")

app.add_typer(vault_app, name="vault")
app.add_typer(link_app, name="link")
app.add_typer(lint_app, name="lint")
app.add_typer(storage_app, name="storage")
app.add_typer(git_app, name="git")

_VaultOption = typer.Option(Path("."), "--vault", "-v", help="Path to the vault directory.")


def _db_path(vault_path: Path) -> Path:
    return vault_path / ".llm-wiki" / "db.sqlite3"


def _fail(message: str) -> None:
    typer.echo(message, err=True)
    raise typer.Exit(code=1)


def _require_vault(vault_path: Path) -> None:
    """Validates `vault_path` is a real vault before any DB/git operation touches it."""
    try:
        load_vault(vault_path)
    except LLMWikiError as exc:
        _fail(str(exc))


@vault_app.command("create")
def vault_create(
    path: Path = typer.Argument(..., help="Directory to create the vault in."),
    name: str | None = typer.Option(None, help="Vault name (defaults to the directory name)."),
    description: str = typer.Option("", help="Short description of the vault's domain."),
) -> None:
    """Creates a new vault directory tree."""
    info = create_vault(path, name or path.name, description)
    typer.echo(f"Created vault '{info.name}' at {info.path}")


@vault_app.command("open")
def vault_open(path: Path = typer.Argument(..., help="Path to an existing vault.")) -> None:
    """Validates an existing vault and adds it to the recent-vaults list."""
    try:
        info = load_vault(path)
    except LLMWikiError as exc:
        _fail(str(exc))
    typer.echo(f"Opened vault '{info.name}' at {info.path}")


@app.command()
def ingest(
    file: Path = typer.Argument(..., help="Raw document to stage and compile."),
    vault: Path = _VaultOption,
    title: str | None = typer.Option(None, help="Title (defaults to the file name)."),
    chat_model: str | None = typer.Option(
        None, help="Overrides the vault's configured chat model."
    ),
    embedding_model: str = typer.Option(DEFAULT_EMBEDDING_MODEL, help="Embedding model name."),
) -> None:
    """Stages, queues, and compiles one raw document against the vault's llama-server."""
    _require_vault(vault)

    settings = AppSettings.load(vault / CONFIG_FILENAME)
    conn = connect(_db_path(vault))
    client = LlamaClient(
        base_url=settings.llm_provider.base_url,
        api_key=settings.llm_provider.api_key or DEFAULT_API_KEY,
    )

    try:
        item = enqueue_file(conn, vault, file, title=title)
    except LLMWikiError as exc:
        _fail(str(exc))
    typer.echo(f"Queued '{item.title}' as item #{item.id} ({item.raw_path})")

    try:
        result = compile_queued_item(
            conn,
            client,
            vault,
            item.id,
            chat_model=chat_model or settings.llm_provider.chat_model,
            embedding_model=embedding_model,
        )
    except LLMWikiError as exc:
        _fail(str(exc))

    typer.echo(f"Wrote source summary: {result.source_path}")
    for entity_path in result.entity_paths:
        typer.echo(f"Wrote/updated entity note: {entity_path}")
    typer.echo(f"Embedded {len(result.chunk_ids)} chunk(s).")


@link_app.command("sync")
def link_sync_cmd(vault: Path = _VaultOption) -> None:
    """Incrementally re-syncs [[wikilinks]] for changed notes only."""
    _require_vault(vault)
    conn = connect(_db_path(vault))
    count = sync_links(conn, vault)
    typer.echo(f"Synced {count} note(s).")


@link_app.command("rebuild")
def link_rebuild_cmd(vault: Path = _VaultOption) -> None:
    """Forces a full re-sync of every note's [[wikilinks]]."""
    _require_vault(vault)
    conn = connect(_db_path(vault))
    count = rebuild_full(conn, vault)
    typer.echo(f"Rebuilt links for {count} note(s).")


@lint_app.command("run")
def lint_run_cmd(vault: Path = _VaultOption) -> None:
    """Runs schema/broken-link/isolated-note checks and reports a health score."""
    _require_vault(vault)
    conn = connect(_db_path(vault))
    report = run_lint(conn)
    if not report.findings:
        typer.echo(f"Health score: {report.score}/100. No issues found.")
        return
    typer.echo(f"Health score: {report.score}/100. {len(report.findings)} issue(s):")
    for finding in report.findings:
        typer.echo(f"  [{finding.kind.value}] {finding.path}: {finding.message}")


@storage_app.command("rebuild")
def storage_rebuild_cmd(vault: Path = _VaultOption) -> None:
    """Wipes and reconstructs the DB cache purely from wiki/."""
    _require_vault(vault)
    rebuild_from_vault(vault, _db_path(vault))
    typer.echo("Storage cache rebuilt from wiki/.")


@git_app.command("init")
def git_init_cmd(vault: Path = _VaultOption) -> None:
    """Initializes Git version control for the vault."""
    _require_vault(vault)
    git_engine.init(vault)
    typer.echo(f"Initialized Git repository at {vault}")


@git_app.command("status")
def git_status_cmd(vault: Path = _VaultOption) -> None:
    """Reports branch and working-tree status."""
    _require_vault(vault)
    result = git_engine.status(vault)
    state = "clean" if result.clean else "dirty"
    typer.echo(f"Branch: {result.branch} ({state})")
    for path in result.modified:
        typer.echo(f"  modified: {path}")
    for path in result.untracked:
        typer.echo(f"  untracked: {path}")


@git_app.command("commit")
def git_commit_cmd(
    message: str = typer.Option(..., "--message", "-m", help="Commit message."),
    vault: Path = _VaultOption,
) -> None:
    """Stages all changes and commits them."""
    _require_vault(vault)
    git_engine.stage_all(vault)
    sha = git_engine.commit(vault, message)
    typer.echo(f"Committed {sha[:12]}")


@git_app.command("push")
def git_push_cmd(
    vault: Path = _VaultOption,
    remote: str = typer.Option("origin", help="Remote name."),
) -> None:
    """Pushes the current branch to a remote."""
    _require_vault(vault)
    try:
        git_engine.push(vault, remote_name=remote)
    except Exception as exc:  # pygit2.GitError, network failures, etc.
        _fail(f"Push failed: {exc}")
    typer.echo(f"Pushed to '{remote}'.")


if __name__ == "__main__":
    app()
