"""Phase 11: health scoring -- a clean vault scores 100, each violation deducts."""

from pathlib import Path

import pytest

from llm_wiki.graph import sync_links
from llm_wiki.lint import STARTING_SCORE, run_lint
from llm_wiki.models import LintFindingKind
from llm_wiki.storage import connect, upsert_note_from_file


def _note(*, slug: str, title: str, note_type: str = "concept", body: str = "") -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"slug: {slug}\n"
        f"type: {note_type}\n"
        "tags: []\n"
        "sources: []\n"
        "---\n\n"
        f"{body}\n"
    )


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "db.sqlite3")
    yield connection
    connection.close()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "wiki" / "concepts").mkdir(parents=True)
    return root


def _add_note(
    vault_root: Path, conn, *, slug: str, note_type: str = "concept", body: str = ""
) -> Path:
    path = vault_root / "wiki" / "concepts" / f"{slug}.md"
    path.write_text(
        _note(slug=slug, title=slug.upper(), note_type=note_type, body=body), encoding="utf-8"
    )
    upsert_note_from_file(conn, vault_root, path)
    return path


def test_clean_vault_scores_100(conn, vault_root) -> None:
    _add_note(vault_root, conn, slug="a", body="See [[b]].")
    _add_note(vault_root, conn, slug="b", body="See [[a]].")
    sync_links(conn, vault_root)

    report = run_lint(conn)

    assert report.score == STARTING_SCORE
    assert report.findings == []


def test_schema_violation_deducts_score(conn, vault_root) -> None:
    # a -> b -> c -> a: fully connected, so nothing here is isolated or
    # broken-linked -- only c's invalid `type` should be flagged.
    _add_note(vault_root, conn, slug="a", body="See [[b]].")
    _add_note(vault_root, conn, slug="b", body="See [[c]].")
    _add_note(vault_root, conn, slug="c", note_type="not-a-real-type", body="See [[a]].")
    sync_links(conn, vault_root)

    report = run_lint(conn)

    assert len(report.findings) == 1
    assert report.findings[0].kind is LintFindingKind.SCHEMA_VIOLATION
    assert report.findings[0].path == "wiki/concepts/c.md"
    assert report.score == STARTING_SCORE - 10


def test_broken_link_deducts_score(conn, vault_root) -> None:
    _add_note(vault_root, conn, slug="a", body="See [[b]] and [[nonexistent-slug]].")
    _add_note(vault_root, conn, slug="b", body="No outgoing links.")
    sync_links(conn, vault_root)

    report = run_lint(conn)

    assert len(report.findings) == 1
    assert report.findings[0].kind is LintFindingKind.BROKEN_LINK
    assert report.findings[0].path == "wiki/concepts/a.md"
    assert "nonexistent-slug" in report.findings[0].message
    assert report.score == STARTING_SCORE - 5


def test_isolated_note_deducts_score(conn, vault_root) -> None:
    _add_note(vault_root, conn, slug="a", body="See [[b]].")
    _add_note(vault_root, conn, slug="b", body="See [[a]].")
    _add_note(vault_root, conn, slug="c", body="No links in or out.")
    sync_links(conn, vault_root)

    report = run_lint(conn)

    assert len(report.findings) == 1
    assert report.findings[0].kind is LintFindingKind.ISOLATED_NOTE
    assert report.findings[0].path == "wiki/concepts/c.md"
    assert report.score == STARTING_SCORE - 2


def test_findings_are_persisted_and_accumulate_across_runs(conn, vault_root) -> None:
    _add_note(vault_root, conn, slug="a", body="No links.")
    sync_links(conn, vault_root)

    first = run_lint(conn)
    second = run_lint(conn)

    assert first.run_id != second.run_id
    rows = conn.execute("SELECT run_id FROM lint_findings").fetchall()
    assert len(rows) == 2
    assert {row[0] for row in rows} == {first.run_id, second.run_id}


def test_score_never_goes_below_zero(conn, vault_root) -> None:
    for i in range(60):
        _add_note(vault_root, conn, slug=f"isolated-{i}", body="No links.")
    sync_links(conn, vault_root)

    report = run_lint(conn)

    assert report.score == 0
