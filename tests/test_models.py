"""Phase 1: every domain model instantiates and round-trips through JSON."""

import pytest
from pydantic import ValidationError

from llm_wiki.models import (
    Chunk,
    CompilationError,
    IngestionError,
    LinkEdge,
    LintError,
    LintFinding,
    LintFindingKind,
    LLMWikiError,
    NoteFrontmatter,
    NoteType,
    QueueItem,
    QueueStatus,
    VaultNotFoundError,
)


def _round_trip(model):
    cls = type(model)
    return cls.model_validate_json(model.model_dump_json())


def test_queue_item_round_trip() -> None:
    item = QueueItem(title="Some Doc", raw_path="raw/2026-07-27_some-doc.md")
    restored = _round_trip(item)
    assert restored == item
    assert restored.status is QueueStatus.QUEUED


def test_queue_item_status_transitions_are_typed() -> None:
    item = QueueItem(
        id=1,
        title="Doc",
        raw_path="raw/doc.md",
        archive_path="raw/.sources/doc.md",
        status=QueueStatus.COMPLETED,
        error=None,
    )
    assert _round_trip(item).status is QueueStatus.COMPLETED


def test_note_frontmatter_round_trip() -> None:
    note = NoteFrontmatter(
        title="Ada Lovelace",
        slug="ada-lovelace",
        type=NoteType.ENTITY,
        tags=["person", "mathematician"],
        sources=["1843-notes-on-the-engine"],
    )
    assert _round_trip(note) == note


def test_chunk_round_trip_with_note_id() -> None:
    chunk = Chunk(note_id=1, ordinal=0, title="Intro", content="...", word_count=250)
    assert _round_trip(chunk) == chunk


def test_chunk_round_trip_with_queue_item_id() -> None:
    chunk = Chunk(queue_item_id=1, ordinal=0, title="Intro", content="...", word_count=250)
    assert _round_trip(chunk) == chunk


def test_chunk_rejects_both_owners_set() -> None:
    with pytest.raises(ValidationError):
        Chunk(note_id=1, queue_item_id=2, ordinal=0, title="x", content="x", word_count=1)


def test_chunk_rejects_no_owner_set() -> None:
    with pytest.raises(ValidationError):
        Chunk(ordinal=0, title="x", content="x", word_count=1)


def test_link_edge_round_trip() -> None:
    edge = LinkEdge(source_slug="ada-lovelace", target_slug="analytical-engine")
    assert _round_trip(edge) == edge


def test_lint_finding_round_trip() -> None:
    finding = LintFinding(
        run_id="run-1",
        kind=LintFindingKind.BROKEN_LINK,
        path="wiki/entities/ada-lovelace.md",
        message="Link to 'babbage' has no target note.",
    )
    assert _round_trip(finding) == finding


@pytest.mark.parametrize(
    "exc_cls",
    [VaultNotFoundError, IngestionError, CompilationError, LintError],
)
def test_exception_hierarchy(exc_cls) -> None:
    assert issubclass(exc_cls, LLMWikiError)
    with pytest.raises(LLMWikiError):
        raise exc_cls("boom")
