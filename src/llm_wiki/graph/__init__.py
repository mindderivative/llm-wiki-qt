"""Incremental NetworkX link graph over the vault's `[[wikilinks]]`."""

from llm_wiki.graph.link_engine import (
    MAX_DEGREES,
    degrees_of_separation,
    get_graph_data,
    rebuild_full,
    sync_links,
)

__all__ = [
    "MAX_DEGREES",
    "degrees_of_separation",
    "get_graph_data",
    "rebuild_full",
    "sync_links",
]
