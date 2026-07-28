"""Phase 7: batch embedding generation against a mocked client."""

from types import SimpleNamespace

import pytest

from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL, embed_texts


class _FakeEmbeddings:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        vectors = [[float(i)] * 3 for i in range(len(kwargs["input"]))]
        return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vectors])


class _FakeOpenAI:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddings()


def test_embed_texts_returns_one_vector_per_input() -> None:
    client = LlamaClient(client=_FakeOpenAI())

    vectors = embed_texts(client, ["hello", "world"])

    assert vectors == [[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]


def test_embed_texts_uses_default_model_and_passes_input() -> None:
    fake = _FakeOpenAI()
    client = LlamaClient(client=fake)

    embed_texts(client, ["a", "b", "c"])

    assert fake.embeddings.last_kwargs["model"] == DEFAULT_EMBEDDING_MODEL
    assert fake.embeddings.last_kwargs["input"] == ["a", "b", "c"]


def test_embed_texts_with_empty_list_returns_empty_without_calling_client() -> None:
    fake = _FakeOpenAI()
    client = LlamaClient(client=fake)

    assert embed_texts(client, []) == []
    assert fake.embeddings.last_kwargs is None


@pytest.mark.live_llm
def test_embed_texts_against_real_llama_server() -> None:
    """Manual verification only -- run with `pytest -m live_llm` against a real server."""
    client = LlamaClient()
    vectors = embed_texts(client, ["hello world"])
    assert len(vectors) == 1
    assert len(vectors[0]) > 0
