"""Orchestrates the `/wiki-ingest` pipeline (ARCHITECTURE.md §8):
summarize -> extract entities/concepts -> cascade-update -> embed.

The cascade-update step is the gap flagged in the original build's
review: if an extracted entity/concept already has a note, the LLM is
asked to *merge* the new information into the existing content rather
than overwriting it or leaving a duplicate stub.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import frontmatter
from loguru import logger
from pydantic import BaseModel

from llm_wiki.graph.link_engine import sync_links
from llm_wiki.ingest.atomizer import atomize
from llm_wiki.ingest.ingest_engine import get_queue_item, update_status
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts
from llm_wiki.llm.extraction import extract_structured
from llm_wiki.models import (
    Chunk,
    CompilationError,
    CompileStage,
    ExtractedNote,
    NoteFrontmatter,
    NoteType,
    QueueItem,
    QueueStatus,
)
from llm_wiki.related_links import render_related_block, strip_related_block
from llm_wiki.storage.db import insert_chunk, upsert_note_from_file
from llm_wiki.storage.vector_search import upsert_chunk_embedding
from llm_wiki.vault.reindex import append_log_entry, rebuild_index

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

def _build_system_prompt(base: str, schema_rules: str) -> str:
    """Folds a vault's SCHEMA.md into a base system prompt, if present."""
    if not schema_rules.strip():
        return base
    return f"{base}\n\nVault-specific operational rules (from SCHEMA.md):\n{schema_rules}"


def _load_schema_rules(vault_root: Path) -> str:
    schema_path = vault_root / "SCHEMA.md"
    if not schema_path.exists():
        return ""
    return schema_path.read_text(encoding="utf-8")


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
    on_stage: Callable[[CompileStage], None] | None = None,
) -> CompileResult:
    """Runs the full ingest pipeline for one queued item: atomize, summarize,
    extract, cascade-update, embed. On failure, marks the item ERROR and
    re-raises as `CompilationError`.

    `on_stage(stage)` fires after each `CompileStage` checkpoint completes --
    `pipeline_runner.run_pipeline()` forwards these through its own
    `on_progress(item, event)` callback so the GUI can show sub-item
    progress rather than a single 0%/100% jump per item.
    """
    vault_root = Path(vault_root)
    queue_item = get_queue_item(conn, queue_item_id)
    logger.info(f"Ingest stage started for {queue_item.title}")

    try:
        raw_text = (vault_root / queue_item.raw_path).read_text(encoding="utf-8")
        schema_rules = _load_schema_rules(vault_root)

        update_status(conn, queue_item_id, QueueStatus.PARSING)
        chunks = atomize(raw_text, queue_item_id=queue_item_id)
        logger.info(f"Atomized into {len(chunks)} chunk(s)")
        if on_stage:
            on_stage(CompileStage.ATOMIZED)

        update_status(conn, queue_item_id, QueueStatus.ANALYZING)
        summary_text = _generate_summary(client, chat_model, queue_item.title, raw_text)
        entities = _extract_entities(client, chat_model, summary_text, schema_rules)
        logger.info(f"Extracted {len(entities)} entity/concept note(s)")
        if on_stage:
            on_stage(CompileStage.EXTRACTED)

        update_status(conn, queue_item_id, QueueStatus.CASCADE)
        entity_slugs = [extracted.frontmatter.slug for extracted in entities]
        source_path, source_slug = _write_source_note(
            vault_root, queue_item, summary_text, entity_slugs
        )
        upsert_note_from_file(conn, vault_root, source_path)

        entity_paths = []
        for extracted in entities:
            note_path = _cascade_update_note(
                vault_root, client, chat_model, extracted, source_slug, schema_rules
            )
            upsert_note_from_file(conn, vault_root, note_path)
            entity_paths.append(note_path)
        logger.info(f"Linked {len(entity_paths)} note(s) to {source_slug}")
        if on_stage:
            on_stage(CompileStage.LINKED)

        chunk_ids = _persist_and_embed_chunks(conn, client, embedding_model, chunks)
        logger.info(f"Embedded {len(chunk_ids)} chunk(s)")
        if on_stage:
            on_stage(CompileStage.EMBEDDED)

        update_status(conn, queue_item_id, QueueStatus.COMPLETED)
        logger.info(f"Compilation completed for {queue_item.title}")

        _run_post_compile_maintenance(conn, vault_root, queue_item.title, len(entity_paths))

        return CompileResult(
            source_path=source_path, entity_paths=entity_paths, chunk_ids=chunk_ids
        )

    except Exception as exc:
        update_status(conn, queue_item_id, QueueStatus.ERROR, error=str(exc))
        logger.error(f"Compilation failed for {queue_item.title}: {exc}")
        raise CompilationError(f"Compilation failed for queue item {queue_item_id}: {exc}") from exc


def _run_post_compile_maintenance(
    conn: sqlite3.Connection, vault_root: Path, title: str, note_count: int
) -> None:
    """Keeps the link graph, `index.md`, and `log.md` live after every
    compile. Each step is independent and log-only on failure -- none of
    them touch the note files or embeddings the compile already wrote
    successfully, so a maintenance hiccup here must never flip an
    otherwise-successful item to ERROR (see Phase 18 of the plan file).
    """
    try:
        synced = sync_links(conn, vault_root)
        logger.info(f"Synced {synced} note(s) into the link graph")
    except Exception as exc:  # noqa: BLE001 -- logged, not fatal to the compile
        logger.error(f"Post-compile link sync failed for {title}: {exc}")

    try:
        rebuild_index(conn, vault_root)
        logger.info("Rebuilt wiki/index.md")
    except Exception as exc:  # noqa: BLE001 -- logged, not fatal to the compile
        logger.error(f"Post-compile index rebuild failed for {title}: {exc}")

    try:
        append_log_entry(vault_root, f"Compiled '{title}' -> {note_count} note(s)")
    except Exception as exc:  # noqa: BLE001 -- logged, not fatal to the compile
        logger.error(f"Post-compile log append failed for {title}: {exc}")


def _generate_summary(client: LlamaClient, model: str, title: str, raw_text: str) -> str:
    user_content = f"Source Document Title: {title}\n\nContent:\n{raw_text[:12000]}"
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    summary = client.chat_completion(messages, model=model, temperature=0.2)
    return summary or f"## Overview\n\nAutomated ingestion for `{title}` completed."


def _extract_entities(
    client: LlamaClient, model: str, summary_text: str, schema_rules: str = ""
) -> list[ExtractedNote]:
    system_prompt = _build_system_prompt(_ENTITY_EXTRACTION_PROMPT, schema_rules)
    prompt = f"{system_prompt}\n\nSUMMARY:\n{summary_text}"
    result = extract_structured(client, prompt, ExtractedEntities, model=model)
    return result.entities


def _write_source_note(
    vault_root: Path,
    queue_item: QueueItem,
    summary_text: str,
    entity_slugs: list[str] | None = None,
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
    # `sources` is self-referential for a source note (it's a summary *of*
    # itself), so its Related block would otherwise collapse to just
    # `[[index]]` -- entity_slugs makes it also link forward to whatever
    # was extracted from it, a genuinely bidirectional graph rather than a
    # one-way entity->source backlink.
    _write_note_file(note_path, fm, summary_text, extra_related=entity_slugs)
    return note_path, slug


def _cascade_update_note(
    vault_root: Path,
    client: LlamaClient,
    model: str,
    extracted: ExtractedNote,
    source_slug: str,
    schema_rules: str = "",
) -> Path:
    """Writes a new note, or merges into an existing one, for one extracted entity/concept."""
    subdir = _NOTE_TYPE_DIRS[extracted.frontmatter.type]
    note_dir = vault_root / "wiki" / subdir
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / f"{extracted.frontmatter.slug}.md"

    if note_path.exists():
        existing = frontmatter.loads(note_path.read_text(encoding="utf-8"))
        # Strip the deterministic Related block before it ever reaches the
        # LLM -- otherwise the merge prompt sees it as "existing content"
        # and may mangle/duplicate it; `_write_note_file()` regenerates a
        # fresh one from `fm.sources` unconditionally below regardless.
        existing_content = strip_related_block(str(existing.content))
        body = _merge_note_content(
            client,
            model,
            existing_content=existing_content,
            new_content=extracted.content,
            schema_rules=schema_rules,
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
    client: LlamaClient,
    model: str,
    *,
    existing_content: str,
    new_content: str,
    schema_rules: str = "",
) -> str:
    system_prompt = _build_system_prompt(_MERGE_SYSTEM_PROMPT, schema_rules)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"EXISTING NOTE:\n{existing_content}\n\nNEW INFORMATION:\n{new_content}",
        },
    ]
    merged = client.chat_completion(messages, model=model, temperature=0.1)
    return merged or existing_content


def _write_note_file(
    path: Path, fm: NoteFrontmatter, body: str, *, extra_related: list[str] | None = None
) -> None:
    clean_body = strip_related_block(body.strip())
    full_body = clean_body + render_related_block(fm, extra_related)
    post = frontmatter.Post(full_body.strip() + "\n", **fm.model_dump(mode="json"))
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
