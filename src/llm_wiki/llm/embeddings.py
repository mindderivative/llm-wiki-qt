"""Batch embedding generation against `nomic-embed-text` (ARCHITECTURE.md §4)."""

from llm_wiki.llm.client import LlamaClient

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text-latest"


def embed_texts(
    client: LlamaClient,
    texts: list[str],
    *,
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> list[list[float]]:
    """Embeds a batch of texts in a single request; one vector per input, same order."""
    if not texts:
        return []
    response = client.raw.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]
