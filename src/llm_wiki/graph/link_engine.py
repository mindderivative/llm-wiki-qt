"""Incremental NetworkX link graph maintenance (ARCHITECTURE.md §7, §8).

Only notes whose content changed since the last sync are re-parsed for
`[[wikilinks]]` -- `notes.links_synced_hash` tracks what was last synced,
separately from `notes.content_hash` (which `upsert_note_from_file()`
keeps current on every write, for unrelated reasons). The `links` table
is kept in sync via a delta per changed note, not a full vault re-walk.
"""

import json
import re
import sqlite3
from pathlib import Path

import networkx as nx

MAX_DEGREES = 3

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")


def sync_links(conn: sqlite3.Connection, vault_root: Path | str) -> int:
    """Re-syncs outgoing edges for every note whose content changed since
    the last sync. Returns the number of notes re-processed.
    """
    vault_root = Path(vault_root)
    rows = conn.execute(
        "SELECT id, path, slug, content_hash, links_synced_hash FROM notes"
    ).fetchall()
    changed = [row for row in rows if row["links_synced_hash"] != row["content_hash"]]

    for row in changed:
        _sync_one_note(conn, vault_root, row)
    conn.commit()
    return len(changed)


def rebuild_full(conn: sqlite3.Connection, vault_root: Path | str) -> int:
    """Forces every note's outgoing edges to be re-synced, regardless of hash."""
    conn.execute("UPDATE notes SET links_synced_hash = NULL")
    return sync_links(conn, vault_root)


def get_graph_data(conn: sqlite3.Connection) -> nx.DiGraph:
    """Builds a directed graph of every note (nodes) and `[[wikilink]]` (edges).

    Nodes carry `title`/`type`/`tags` attributes -- consumed by the GUI's
    graph canvas for its node-selection info overlay (Phase 17). Existing
    callers (`degrees_of_separation`, the MCP server's `trace_network_path`)
    only check node membership and shortest paths, so this is additive.
    """
    graph = nx.DiGraph()
    for row in conn.execute("SELECT slug, title, type, tags FROM notes").fetchall():
        graph.add_node(
            row["slug"],
            title=row["title"],
            type=row["type"],
            tags=json.loads(row["tags"]),
        )
    for row in conn.execute("SELECT source_slug, target_slug FROM links").fetchall():
        graph.add_edge(row["source_slug"], row["target_slug"])
    return graph


def degrees_of_separation(
    conn: sqlite3.Connection,
    source_slug: str,
    target_slug: str,
    max_degrees: int = MAX_DEGREES,
) -> int | None:
    """Shortest path length between two notes, or `None` if unreachable within `max_degrees`."""
    graph = get_graph_data(conn)
    if source_slug not in graph or target_slug not in graph:
        return None
    try:
        length = nx.shortest_path_length(graph, source=source_slug, target=target_slug)
    except nx.NetworkXNoPath:
        return None
    return length if length <= max_degrees else None


def _sync_one_note(conn: sqlite3.Connection, vault_root: Path, row: sqlite3.Row) -> None:
    note_path = vault_root / row["path"]
    content = note_path.read_text(encoding="utf-8") if note_path.exists() else ""

    conn.execute("DELETE FROM links WHERE source_slug = ?", (row["slug"],))
    for target_slug in _extract_wikilink_targets(content):
        conn.execute(
            "INSERT OR IGNORE INTO links (source_slug, target_slug) VALUES (?, ?)",
            (row["slug"], target_slug),
        )
    conn.execute(
        "UPDATE notes SET links_synced_hash = ? WHERE id = ?",
        (row["content_hash"], row["id"]),
    )


def _extract_wikilink_targets(content: str) -> list[str]:
    targets: list[str] = []
    for match in _WIKILINK_RE.finditer(content):
        slug = match.group(1).strip().lower().replace(" ", "-")
        if slug and slug not in targets:
            targets.append(slug)
    return targets
