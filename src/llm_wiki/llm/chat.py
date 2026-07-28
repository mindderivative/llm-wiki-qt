"""Simple RAG-style chat over a vault's compiled content (Phase 15).

Deliberately simple, not a production feature -- per the plan, this
exists "for active LLM-Wiki testing": embed the query, pull the top-K
nearest chunks via `storage.vector_search`, stuff them as context into a
single `chat_completion()` call.
"""

import sqlite3

from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts
from llm_wiki.storage import similarity_search

_SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions about the user's LLM-Wiki "
    "knowledge base. Use only the provided context to answer; if the context "
    "doesn't contain the answer, say so plainly rather than guessing."
)


def ask(
    conn: sqlite3.Connection,
    client: LlamaClient,
    query: str,
    *,
    chat_model: str,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    top_k: int = 5,
) -> str:
    """Answers `query`, grounded in the vault's compiled/ingested chunks."""
    (query_vector,) = embed_texts(client, [query], model=embedding_model)
    hits = similarity_search(conn, query_vector, top_k=top_k)

    context_parts = []
    for chunk_id, _distance in hits:
        row = conn.execute("SELECT content FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        if row is not None:
            context_parts.append(row["content"])

    context = (
        "\n\n---\n\n".join(context_parts) if context_parts else "(no relevant context found)"
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION:\n{query}"},
    ]
    return client.chat_completion(messages, model=chat_model)
