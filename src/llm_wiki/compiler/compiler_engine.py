"""Orchestrates the `/wiki-ingest` pipeline (ARCHITECTURE.md §8):
summarize -> extract entities/concepts -> cascade-update -> embed.

The cascade-update step is the gap flagged in the original build's
review: if an extracted entity/concept already has a note, the LLM is
asked to *merge* the new information into the existing content rather
than overwriting it or leaving a duplicate stub.
"""

import sqlite3
from pathlib import Path

import frontmatter
from loguru import logger
from pydantic import BaseModel

from llm_wiki.ingest.atomizer import atomize
from llm_wiki.ingest.ingest_engine import get_queue_item, update_status
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts
from llm_wiki.llm.extraction import extract_structured
from llm_wiki.models import (
    Chunk,
    CompilationError,
    ExtractedNote,
    NoteFrontmatter,
    NoteType,
    QueueItem,
    QueueStatus,
)
from llm_wiki.storage.db import insert_chunk, upsert_note_from_file
from llm_wiki.storage.vector_search import upsert_chunk_embedding

_SUMMARY_SYSTEM_PROMPT = (
    "You are an expert technical knowledge synthesizer for an authoritative LLM Wiki. "
    "Analyze the provided source content and write a high-density, structured summary. "
    "Use Markdown headers (##), bold key terms, and bullet points. Keep sections "
    "concise (200-400 words each)."
)

_ENTITY_EXTRACTION_PROMPT = (
    "Identify the core entities (people, systems, tools) and concepts (foundational "
    "principles, themes) discussed in the following summary. For each one, produce "
    "its frontmatter (title, a URL-safe slug, type -- 'entity' for a concrete subject "
    "or 'concept' for an abstract principle, relevant tags) and a self-contained "
    "Markdown content body describing it."
)

_MERGE_SYSTEM_PROMPT = (
    "You maintain a single authoritative note in a knowledge wiki. You will be given "
    "the note's EXISTING content and NEW information gathered from another source. "
    "Rewrite the note so it incorporates the new information, without duplicating "
    "anything already covered. Preserve accurate existing content. Respond with only "
    "the merged Markdown body -- no frontmatter, no commentary."
)

_NOTE_TYPE_DIRS: dict[NoteType, str] = {
    NoteType.SOURCE: "sources",
    NoteType.ENTITY: "entities",
    NoteType.CONCEPT: "concepts",
    NoteType.SYNTHESIS: "synthesis",
}


class ExtractedEntities(BaseModel):
    """Structured-extraction schema: every entity/concept found in a summary."""

    entities: list[ExtractedNote]


class CompileResult(BaseModel):
    """What one `compile_queued_item()` run produced."""

    source_path: Path
    entity_paths: list[Path]
    chunk_ids: list[int]


def compile_queued_item(
    conn: sqlite3.Connection,
    client: LlamaClient,
    vault_root: Path | str,
    queue_item_id: int,
    *,
    chat_model: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> CompileResult:
    """Runs the full ingest pipeline for one queued item: atomize, summarize,
    extract, cascade-update, embed. On failure, marks the item ERROR and
    re-raises as `CompilationError`.
    """
    vault_root = Path(vault_root)
    queue_item = get_queue_item(conn, queue_item_id)
    logger.info(f"Ingest stage started for {queue_item.title}")

    try:
        raw_text = (vault_root / queue_item.raw_path).read_text(encoding="utf-8")

        update_status(conn, queue_item_id, QueueStatus.PARSING)
        chunks = atomize(raw_text, queue_item_id=queue_item_id)
        logger.info(f"Atomized into {len(chunks)} chunk(s)")

        update_status(conn, queue_item_id, QueueStatus.ANALYZING)
        summary_text = _generate_summary(client, chat_model, queue_item.title, raw_text)
        entities = _extract_entities(client, chat_model, summary_text)
        logger.info(f"Extracted {len(entities)} entity/concept note(s)")

        update_status(conn, queue_item_id, QueueStatus.CASCADE)
        source_path, source_slug = _write_source_note(vault_root, queue_item, summary_text)
        upsert_note_from_file(conn, vault_root, source_path)

        entity_paths = []
        for extracted in entities:
            note_path = _cascade_update_note(vault_root, client, chat_model, extracted, source_slug)
            upsert_note_from_file(conn, vault_root, note_path)
            entity_paths.append(note_path)
        logger.info(f"Linked {len(entity_paths)} note(s) to {source_slug}")

        chunk_ids = _persist_and_embed_chunks(conn, client, embedding_model, chunks)
        logger.info(f"Embedded {len(chunk_ids)} chunk(s)")

        update_status(conn, queue_item_id, QueueStatus.COMPLETED)
        logger.info(f"Compilation completed for {queue_item.title}")
        return CompileResult(
            source_path=source_path, entity_paths=entity_paths, chunk_ids=chunk_ids
        )

    except Exception as exc:
        update_status(conn, queue_item_id, QueueStatus.ERROR, error=str(exc))
        logger.error(f"Compilation failed for {queue_item.title}: {exc}")
        raise CompilationError(f"Compilation failed for queue item {queue_item_id}: {exc}") from exc


def _generate_summary(client: LlamaClient, model: str, title: str, raw_text: str) -> str:
    user_content = f"Source Document Title: {title}\n\nContent:\n{raw_text[:12000]}"
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    summary = client.chat_completion(messages, model=model, temperature=0.2)
    return summary or f"## Overview\n\nAutomated ingestion for `{title}` completed."


def _extract_entities(client: LlamaClient, model: str, summary_text: str) -> list[ExtractedNote]:
    prompt = f"{_ENTITY_EXTRACTION_PROMPT}\n\nSUMMARY:\n{summary_text}"
    result = extract_structured(client, prompt, ExtractedEntities, model=model)
    return result.entities


def _write_source_note(
    vault_root: Path, queue_item: QueueItem, summary_text: str
) -> tuple[Path, str]:
    slug = Path(queue_item.raw_path).stem
    note_dir = vault_root / "wiki" / "sources"
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{slug}.md"

    fm = NoteFrontmatter(
        title=f"{queue_item.title} - Summary",
        slug=slug,
        type=NoteType.SOURCE,
        tags=["source", "ingested"],
        sources=[slug],
    )
    _write_note_file(note_path, fm, summary_text)
    return note_path, slug


def _cascade_update_note(
    vault_root: Path,
    client: LlamaClient,
    model: str,
    extracted: ExtractedNote,
    source_slug: str,
) -> Path:
    """Writes a new note, or merges into an existing one, for one extracted entity/concept."""
    subdir = _NOTE_TYPE_DIRS[extracted.frontmatter.type]
    note_dir = vault_root / "wiki" / subdir
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{extracted.frontmatter.slug}.md"

    if note_path.exists():
        existing = frontmatter.loads(note_path.read_text(encoding="utf-8"))
        body = _merge_note_content(
            client,
            model,
            existing_content=str(existing.content),
            new_content=extracted.content,
        )
        tags = sorted(set(existing.get("tags", [])) | set(extracted.frontmatter.tags))
        sources = sorted(set(existing.get("sources", [])) | {source_slug})
    else:
        body = extracted.content
        tags = extracted.frontmatter.tags
        sources = [source_slug]

    fm = NoteFrontmatter(
        title=extracted.frontmatter.title,
        slug=extracted.frontmatter.slug,
        type=extracted.frontmatter.type,
        tags=tags,
        sources=sources,
    )
    _write_note_file(note_path, fm, body)
    return note_path


def _merge_note_content(
    client: LlamaClient, model: str, *, existing_content: str, new_content: str
) -> str:
    messages = [
        {"role": "system", "content": _MERGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"EXISTING NOTE:\n{existing_content}\n\nNEW INFORMATION:\n{new_content}",
        },
    ]
    merged = client.chat_completion(messages, model=model, temperature=0.1)
    return merged or existing_content


def _write_note_file(path: Path, fm: NoteFrontmatter, body: str) -> None:
    post = frontmatter.Post(body.strip() + "\n", **fm.model_dump(mode="json"))
    path.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")


def _persist_and_embed_chunks(
    conn: sqlite3.Connection,
    client: LlamaClient,
    embedding_model: str,
    chunks: list[Chunk],
) -> list[int]:
    if not chunks:
        return []
    chunk_ids = [insert_chunk(conn, chunk) for chunk in chunks]
    vectors = embed_texts(client, [chunk.content for chunk in chunks], model=embedding_model)
    for chunk_id, vector in zip(chunk_ids, vectors, strict=True):
        upsert_chunk_embedding(conn, chunk_id, vector)
    return chunk_ids
