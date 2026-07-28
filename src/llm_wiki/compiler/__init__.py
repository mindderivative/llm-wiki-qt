"""Orchestrates summarize -> extract -> cascade-update -> embed (the `/wiki-ingest` pipeline)."""

from llm_wiki.compiler.compiler_engine import CompileResult, compile_queued_item

__all__ = ["CompileResult", "compile_queued_item"]
