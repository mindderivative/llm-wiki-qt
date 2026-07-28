"""FastMCP server exposing the vault to external MCP clients (ARCHITECTURE.md §3).

Every file-touching tool sandboxes its resolved path to stay within the
vault root -- a `../`-style escape attempt is rejected before the
filesystem is ever touched.
"""

import argparse
from pathlib import Path
from typing import Any

import frontmatter
import networkx as nx
from mcp.server.fastmcp import FastMCP

from llm_wiki.config import AppSettings
from llm_wiki.graph.link_engine import MAX_DEGREES, get_graph_data
from llm_wiki.llm.client import DEFAULT_API_KEY, LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts
from llm_wiki.storage import connect, similarity_search
from llm_wiki.vault import CONFIG_FILENAME


class VaultPathError(ValueError):
    """Raised when a tool argument would resolve outside the vault root."""


def _resolve_note_path(vault_root: Path, subdir: str, slug: str) -> Path:
    """Resolves `wiki/{subdir}/{slug}.md`, rejecting any path escaping `vault_root`."""
    candidate = (vault_root / "wiki" / subdir / f"{slug}.md").resolve()
    if not candidate.is_relative_to(vault_root.resolve()):
        raise VaultPathError(f"Rejected path outside the vault: {slug!r}")
    return candidate


def _read_note(vault_root: Path, subdir: str, slug: str) -> dict[str, Any]:
    """Reads one note's frontmatter/content, sandboxed to `wiki/{subdir}/`."""
    try:
        path = _resolve_note_path(vault_root, subdir, slug)
    except VaultPathError as exc:
        return {"found": False, "error": str(exc)}

    if not path.exists():
        return {"found": False, "error": f"No note at wiki/{subdir}/{slug}.md"}

    post = frontmatter.loads(path.read_text(encoding="utf-8"))
    return {
        "found": True,
        "title": str(post.get("title", slug)),
        "metadata": dict(post.metadata),
        "content": str(post.content),
    }


def create_mcp_server(vault_root: Path | str, *, client: LlamaClient | None = None) -> FastMCP:
    """Builds a FastMCP app with tools bound to one vault.

    Exposes exactly the four capabilities the plan calls for: semantic
    search (Phase 8), entity lookup, path traversal (Phase 10), and
    synthesis reads. `client` can be injected (as `LlamaClient`/`compile_queued_item`
    already allow) so tests never need a real llama-server.
    """
    vault_root = Path(vault_root).resolve()
    conn = connect(vault_root / ".llm-wiki" / "db.sqlite3")
    if client is None:
        settings = AppSettings.load(vault_root / CONFIG_FILENAME)
        client = LlamaClient(
            base_url=settings.llm_provider.base_url,
            api_key=settings.llm_provider.api_key or DEFAULT_API_KEY,
        )

    mcp = FastMCP("LLM-Wiki")

    @mcp.tool()
    def search_wiki_content(query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Semantic search over ingested/compiled wiki content."""
        (query_vector,) = embed_texts(client, [query], model=DEFAULT_EMBEDDING_MODEL)
        results = []
        for chunk_id, distance in similarity_search(conn, query_vector, top_k=top_k):
            row = conn.execute(
                """
                SELECT c.title AS chunk_title, c.content, n.path AS note_path,
                       n.title AS note_title, n.type AS note_type, q.title AS queue_title
                FROM chunks c
                LEFT JOIN notes n ON c.note_id = n.id
                LEFT JOIN queue q ON c.queue_item_id = q.id
                WHERE c.id = ?
                """,
                (chunk_id,),
            ).fetchone()
            if row is None:
                continue
            results.append(
                {
                    "chunk_id": chunk_id,
                    "title": row["note_title"] or row["queue_title"] or row["chunk_title"],
                    "path": row["note_path"],
                    "type": row["note_type"],
                    "content": row["content"],
                    "distance": distance,
                }
            )
        return results

    @mcp.tool()
    def read_entity_profile(slug: str) -> dict[str, Any]:
        """Reads an entity's profile note from wiki/entities/."""
        return _read_note(vault_root, "entities", slug)

    @mcp.tool()
    def read_synthesis_note(slug: str) -> dict[str, Any]:
        """Reads a cross-cutting synthesis note from wiki/synthesis/."""
        return _read_note(vault_root, "synthesis", slug)

    @mcp.tool()
    def trace_network_path(
        source_slug: str, target_slug: str, max_degrees: int = MAX_DEGREES
    ) -> dict[str, Any]:
        """Traces the shortest [[wikilink]] path between two notes, within `max_degrees`."""
        graph = get_graph_data(conn)
        if source_slug not in graph or target_slug not in graph:
            return {
                "path_found": False,
                "error": (
                    f"One or both subjects ('{source_slug}', '{target_slug}') "
                    "are missing from the network graph."
                ),
            }
        try:
            path = nx.shortest_path(graph, source=source_slug, target=target_slug)
        except nx.NetworkXNoPath:
            return {"path_found": False, "error": "No path exists between these subjects."}

        degrees = len(path) - 1
        if degrees > max_degrees:
            return {
                "path_found": False,
                "error": (
                    f"Shortest path is {degrees} degrees, "
                    f"exceeding the {max_degrees}-degree limit."
                ),
            }
        return {"path_found": True, "path": path, "degrees": degrees}

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM-Wiki MCP server")
    parser.add_argument("--vault", required=True, type=Path, help="Path to the vault root.")
    parser.add_argument(
        "--transport", default="stdio", choices=["stdio", "sse", "streamable-http"]
    )
    args = parser.parse_args()

    mcp = create_mcp_server(args.vault)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
