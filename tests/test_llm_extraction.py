"""Phase 7: outlines-based structured extraction, using NoteFrontmatter as schema."""

from types import SimpleNamespace

import openai
import pytest

from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.extraction import extract_structured
from llm_wiki.models import NoteFrontmatter, NoteType


def _fake_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))]
    )


@pytest.fixture
def mocked_llama_client(monkeypatch: pytest.MonkeyPatch) -> LlamaClient:
    """A LlamaClient wrapping a real `openai.OpenAI` instance whose HTTP call is mocked.

    `outlines.from_openai()` isinstance-checks its argument, so a bare
    duck-typed fake won't do -- instead we monkeypatch the one method
    that would otherwise hit the network.
    """
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    fake_json = (
        '{"title": "Ada Lovelace", "slug": "ada-lovelace", "type": "entity", '
        '"tags": ["person"], "sources": ["1843-notes"]}'
    )
    monkeypatch.setattr(
        real_client.chat.completions, "create", lambda **kwargs: _fake_response(fake_json)
    )
    return LlamaClient(client=real_client)


def test_extract_structured_returns_validated_model(mocked_llama_client: LlamaClient) -> None:
    result = extract_structured(
        mocked_llama_client,
        "Extract entity frontmatter for Ada Lovelace.",
        NoteFrontmatter,
        model="qwen2.5-coder-14b",
    )

    assert isinstance(result, NoteFrontmatter)
    assert result.slug == "ada-lovelace"
    assert result.type is NoteType.ENTITY
    assert result.tags == ["person"]


def test_extract_structured_requests_json_schema_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = openai.OpenAI(base_url="http://127.0.0.1:1", api_key="test")
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response(
            '{"title": "T", "slug": "t", "type": "concept", "tags": [], "sources": []}'
        )

    monkeypatch.setattr(real_client.chat.completions, "create", fake_create)
    client = LlamaClient(client=real_client)

    extract_structured(client, "prompt", NoteFrontmatter, model="qwen3-8b")

    assert captured["model"] == "qwen3-8b"
    assert captured["response_format"]["type"] == "json_schema"


@pytest.mark.live_llm
def test_extract_structured_against_real_llama_server() -> None:
    """Manual verification only -- run with `pytest -m live_llm` against a real server."""
    client = LlamaClient()
    result = extract_structured(
        client,
        "Generate frontmatter for a note titled 'Test Note' of type 'source'.",
        NoteFrontmatter,
        model="qwen2.5-coder-14b",
    )
    assert isinstance(result, NoteFrontmatter)
