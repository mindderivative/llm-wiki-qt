"""Thin wrapper over the `openai` SDK, pointed at llama-server's
OpenAI-compatible endpoint (ARCHITECTURE.md §4).
"""

from collections.abc import Iterable

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_API_KEY = "llama-local"  # llama-server ignores the key; the SDK requires a value


class LlamaClient:
    """Chat completion client for a local llama-server instance.

    Accepts an injected `openai.OpenAI` client so tests (and
    `llm.extraction`, which needs a real `openai.OpenAI` instance for
    `outlines`) can supply a mocked or pre-configured one.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = DEFAULT_API_KEY,
        client: OpenAI | None = None,
    ) -> None:
        self.raw: OpenAI = client or OpenAI(base_url=base_url, api_key=api_key)

    def chat_completion(
        self,
        messages: Iterable[ChatCompletionMessageParam],
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 2048,
    ) -> str:
        """Sends a synchronous chat completion request; returns the message content."""
        response = self.raw.chat.completions.create(
            model=model,
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
