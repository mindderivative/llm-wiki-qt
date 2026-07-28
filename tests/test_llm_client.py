"""Phase 7: LlamaClient against a mocked client fixture; live_llm test is manual-only."""

from types import SimpleNamespace

import pytest

from llm_wiki.llm.client import LlamaClient


class _FakeCompletions:
    def __init__(self, content: str | None) -> None:
        self._content = content
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        message = SimpleNamespace(content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeOpenAI:
    def __init__(self, content: str | None) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


@pytest.fixture
def fake_completion_client() -> _FakeOpenAI:
    """A fake `openai.OpenAI`-shaped client returning a canned chat completion."""
    return _FakeOpenAI("  Hello from llama-server.  ")


def test_chat_completion_returns_stripped_content(fake_completion_client) -> None:
    client = LlamaClient(client=fake_completion_client)

    result = client.chat_completion([{"role": "user", "content": "hi"}], model="qwen2.5-coder-14b")

    assert result == "Hello from llama-server."


def test_chat_completion_passes_model_and_params(fake_completion_client) -> None:
    client = LlamaClient(client=fake_completion_client)

    client.chat_completion(
        [{"role": "user", "content": "hi"}],
        model="qwen3-8b",
        temperature=0.7,
        max_tokens=128,
    )

    sent = fake_completion_client.chat.completions.last_kwargs
    assert sent["model"] == "qwen3-8b"
    assert sent["temperature"] == 0.7
    assert sent["max_tokens"] == 128


@pytest.mark.parametrize("content", ["", None])
def test_chat_completion_handles_empty_or_missing_content(content) -> None:
    client = LlamaClient(client=_FakeOpenAI(content))

    result = client.chat_completion([{"role": "user", "content": "hi"}], model="m")

    assert result == ""


@pytest.mark.live_llm
def test_chat_completion_against_real_llama_server() -> None:
    """Manual verification only -- run with `pytest -m live_llm` against a real server."""
    client = LlamaClient()
    result = client.chat_completion(
        [{"role": "user", "content": "Say 'ok' and nothing else."}],
        model="qwen2.5-coder-14b",
    )
    assert result
