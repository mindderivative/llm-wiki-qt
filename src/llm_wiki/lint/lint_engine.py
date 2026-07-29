"""Health scoring against the DB cache (ARCHITECTURE.md §7, §8).

Reads exclusively from `notes` + `links` -- already current thanks to
the link pipeline's incremental hashing (Phase 10). No filesystem walk
here; the only way stale data would show up is if the DB cache itself
needs rebuilding from `wiki/` (`storage.rebuild_from_vault()`, Phase 2).
"""

import json
import sqlite3
import uuid

from loguru import logger
from pydantic import BaseModel, ValidationError

from llm_wiki.models import LintFinding, LintFindingKind, NoteFrontmatter

STARTING_SCORE = 100

# Point deductions per finding kind -- arbitrary but fixed, so scores are
# reproducible across runs of the same vault state.
_DEDUCTIONS: dict[LintFindingKind, int] = {
    LintFindingKind.SCHEMA_VIOLATION: 10,
    LintFindingKind.BROKEN_LINK: 5,
    LintFindingKind.ISOLATED_NOTE: 2,
}


class LintReport(BaseModel):
    """Result of one `run_lint()` pass."""

    run_id: str
    findings: list[LintFinding]
    score: int


def run_lint(conn: sqlite3.Connection) -> LintReport:
    """Runs schema validation, broken-link detection, and isolated-note
    detection, persists the findings (tagged with a fresh `run_id`, kept
    for history alongside prior runs), and returns a health-scored report.
    """
    run_id = uuid.uuid4().hex
    findings: list[LintFinding] = [
        *_check_schema(conn, run_id),
        *_check_broken_links(conn, run_id),
        *_check_isolated_notes(conn, run_id),
    ]

    _persist_findings(conn, findings)
    score = _compute_score(findings)
    logger.info(f"Lint pass: score {score}/100, {len(findings)} finding(s)")

    return LintReport(run_id=run_id, findings=findings, score=score)


def _check_schema(conn: sqlite3.Connection, run_id: str) -> list[LintFinding]:
    findings = []
    rows = conn.execute("SELECT path, title, slug, type, tags, sources FROM notes").fetchall()
    for row in rows:
        try:
            NoteFrontmatter(
                title=row["title"],
                slug=row["slug"],
                type=row["type"],
                tags=json.loads(row["tags"]),
                sources=json.loads(row["sources"]),
            )
        except ValidationError as exc:
            findings.append(
                LintFinding(
                    run_id=run_id,
                    kind=LintFindingKind.SCHEMA_VIOLATION,
                    path=row["path"],
                    message=str(exc),
                )
            )
    return findings


def _check_broken_links(conn: sqlite3.Connection, run_id: str) -> list[LintFinding]:
    slug_to_path = {
        row["slug"]: row["path"] for row in conn.execute("SELECT slug, path FROM notes")
    }
    findings = []
    for row in conn.execute("SELECT source_slug, target_slug FROM links").fetchall():
        if row["target_slug"] in slug_to_path:
            continue
        findings.append(
            LintFinding(
                run_id=run_id,
                kind=LintFindingKind.BROKEN_LINK,
                path=slug_to_path.get(row["source_slug"], row["source_slug"]),
                message=f"Link to '{row['target_slug']}' has no target note.",
            )
        )
    return findings


def _check_isolated_notes(conn: sqlite3.Connection, run_id: str) -> list[LintFinding]:
    connected_slugs: set[str] = set()
    for row in conn.execute("SELECT source_slug, target_slug FROM links").fetchall():
        connected_slugs.add(row["source_slug"])
        connected_slugs.add(row["target_slug"])

    findings = []
    for row in conn.execute("SELECT slug, path FROM notes").fetchall():
        if row["slug"] in connected_slugs:
            continue
        findings.append(
            LintFinding(
                run_id=run_id,
                kind=LintFindingKind.ISOLATED_NOTE,
                path=row["path"],
                message="Note has no incoming or outgoing links.",
            )
        )
    return findings


def _persist_findings(conn: sqlite3.Connection, findings: list[LintFinding]) -> None:
    for finding in findings:
        conn.execute(
            """
            INSERT INTO lint_findings (run_id, kind, path, message, created_at)
            VALUES (:run_id, :kind, :path, :message, :created_at)
            """,
            {
                "run_id": finding.run_id,
                "kind": finding.kind.value,
                "path": finding.path,
                "message": finding.message,
                "created_at": finding.created_at.isoformat(),
            },
        )
    conn.commit()


def _compute_score(findings: list[LintFinding]) -> int:
    deduction = sum(_DEDUCTIONS[finding.kind] for finding in findings)
    return max(0, STARTING_SCORE - deduction)
