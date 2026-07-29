"""Batch pipeline execution over the ingestion queue (Phase 15).

Plain Python -- no Qt, no threading primitives baked in, per Design
Principle 6 and the concurrency model in ARCHITECTURE.md §9. `should_pause`
and `should_stop` are plain callables so this is trivially testable
synchronously; the GUI's `QThread` adapter is the only place this becomes
Qt-flavored, wiring those callables to a `threading.Event` the UI thread
sets from the toolbar's pause/resume/stop controls.
"""

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from llm_wiki.compiler.compiler_engine import CompileResult, compile_queued_item
from llm_wiki.ingest import list_queue
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.models import CompilationError, QueueItem, QueueStatus

_PAUSE_POLL_SECONDS = 0.01


class PipelineRunResult(BaseModel):
    """Summary of one `run_pipeline()` call."""

    processed: list[QueueItem] = Field(default_factory=list)
    results: dict[int, CompileResult] = Field(default_factory=dict)
    errors: dict[int, str] = Field(default_factory=dict)
    stopped_early: bool = False


def run_pipeline(
    conn: sqlite3.Connection,
    client: LlamaClient,
    vault_root: Path | str,
    *,
    batch_size: int = 1,
    chat_model: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    on_progress: Callable[[QueueItem, str], None] | None = None,
    should_pause: Callable[[], bool] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> PipelineRunResult:
    """Processes up to `batch_size` `QUEUED` items sequentially.

    `on_progress(item, event)` fires with `event` in `{"starting",
    "completed", "error"}` before/after each item. `should_stop()` is
    checked between items -- if it becomes true, processing halts and
    `stopped_early` is set. `should_pause()` is checked in a blocking-wait
    loop between items; the caller's thread sleeps briefly there (the GUI
    adapter runs this on a worker thread so the UI stays responsive). A
    failure on one item is recorded in `errors` and processing continues
    with the rest of the batch.
    """
    result = PipelineRunResult()
    should_stop = should_stop or (lambda: False)
    should_pause = should_pause or (lambda: False)

    queued_items = list_queue(conn, status=QueueStatus.QUEUED)[:batch_size]
    if not queued_items:
        logger.info("No queued items to process")
        return result
    logger.info(f"Processing batch of {len(queued_items)} item(s)")

    for item in queued_items:
        while should_pause() and not should_stop():
            time.sleep(_PAUSE_POLL_SECONDS)

        if should_stop():
            result.stopped_early = True
            break

        if on_progress:
            on_progress(item, "starting")

        try:
            compile_result = compile_queued_item(
                conn,
                client,
                vault_root,
                item.id,
                chat_model=chat_model,
                embedding_model=embedding_model,
                on_stage=(lambda stage, item=item: on_progress(item, stage))
                if on_progress
                else None,
            )
        except CompilationError as exc:
            result.errors[item.id] = str(exc)
            if on_progress:
                on_progress(item, "error")
            continue

        result.results[item.id] = compile_result
        result.processed.append(item)
        if on_progress:
            on_progress(item, "completed")

    if result.stopped_early:
        logger.info(f"Batch stopped early: {len(result.processed)} processed")
    elif result.errors:
        logger.info(
            f"Batch finished: {len(result.processed)} processed, {len(result.errors)} failed"
        )

    return result


def step_one(
    conn: sqlite3.Connection,
    client: LlamaClient,
    vault_root: Path | str,
    *,
    chat_model: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    on_progress: Callable[[QueueItem, str], None] | None = None,
) -> PipelineRunResult:
    """Convenience wrapper: processes exactly one `QUEUED` item (manual mode)."""
    return run_pipeline(
        conn,
        client,
        vault_root,
        batch_size=1,
        chat_model=chat_model,
        embedding_model=embedding_model,
        on_progress=on_progress,
    )
