"""Shared Pydantic domain models and the typed exception hierarchy.

These are the shapes every layer of the engine (ingest, compiler, graph,
lint, storage) passes between each other and persists to/from the SQLite
cache — see ARCHITECTURE.md §6 for the table layout each model mirrors.
"""

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QueueStatus(StrEnum):
    """Lifecycle states of a queued raw document, per ARCHITECTURE.md §8."""

    QUEUED = "queued"
    PARSING = "parsing"
    ANALYZING = "analyzing"
    CASCADE = "cascade"
    COMPLETED = "completed"
    ERROR = "error"


class NoteType(StrEnum):
    """The four wiki note categories under `wiki/`."""

    SOURCE = "source"
    ENTITY = "entity"
    CONCEPT = "concept"
    SYNTHESIS = "synthesis"


class LintFindingKind(StrEnum):
    """Categories of issue the lint engine can report."""

    BROKEN_LINK = "broken_link"
    SCHEMA_VIOLATION = "schema_violation"
    ISOLATED_NOTE = "isolated_note"


class QueueItem(BaseModel):
    """Mirrors a row of the `queue` table (ARCHITECTURE.md §6)."""

    id: int | None = None
    title: str
    raw_path: Path
    archive_path: Path | None = None
    status: QueueStatus = QueueStatus.QUEUED
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class NoteFrontmatter(BaseModel):
    """The YAML frontmatter schema every `wiki/` note is validated against."""

    title: str
    slug: str
    type: NoteType
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    """An atomic GEO chunk, belonging to either a queued item or a note."""

    note_id: int | None = None
    queue_item_id: int | None = None
    ordinal: int
    title: str
    content: str
    word_count: int

    @model_validator(mode="after")
    def _exactly_one_owner(self) -> Chunk:
        if (self.note_id is None) == (self.queue_item_id is None):
            raise ValueError("Chunk must set exactly one of note_id or queue_item_id")
        return self


class LinkEdge(BaseModel):
    """A parsed `[[wikilink]]` edge between two notes, by slug."""

    source_slug: str
    target_slug: str


class LintFinding(BaseModel):
    """One issue surfaced by a `/wiki-lint` run, mirroring `lint_findings`."""

    run_id: str
    kind: LintFindingKind
    path: str
    message: str
    created_at: datetime = Field(default_factory=_utcnow)


class LLMWikiError(Exception):
    """Base class for every typed exception raised by the llm_wiki engine."""


class VaultNotFoundError(LLMWikiError):
    """Raised when a directory does not contain a valid vault."""


class IngestionError(LLMWikiError):
    """Raised when staging or enqueueing a raw document fails."""


class CompilationError(LLMWikiError):
    """Raised when the compiler pipeline (summarize/extract/cascade) fails."""


class LintError(LLMWikiError):
    """Raised when a `/wiki-lint` run cannot complete."""
