"""Phase 1: config loads from a `.llm-wiki-config` fixture and env vars win."""

import json
from pathlib import Path

import pytest

from llm_wiki.config import AppSettings


def test_load_with_no_file_returns_defaults() -> None:
    settings = AppSettings.load(None)
    assert settings.llm_provider.chat_model == "qwen2.5-coder-14b"
    assert settings.mcp_server.port == 8001
    assert settings.vault.max_link_degrees == 3


def test_load_with_missing_file_returns_defaults(tmp_path: Path) -> None:
    settings = AppSettings.load(tmp_path / "does-not-exist.json")
    assert settings.llm_provider.provider == "llama.cpp"


def test_load_from_fixture_file(tmp_path: Path) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    config_path.write_text(
        json.dumps(
            {
                "llm_provider": {"chat_model": "qwen3-8b", "host_port": 9999},
                "vault": {"max_link_degrees": 2},
            }
        ),
        encoding="utf-8",
    )

    settings = AppSettings.load(config_path)

    assert settings.llm_provider.chat_model == "qwen3-8b"
    assert settings.llm_provider.host_port == 9999
    # Fields not present in the file fall back to their defaults.
    assert settings.llm_provider.provider == "llama.cpp"
    assert settings.vault.max_link_degrees == 2
    assert settings.mcp_server.port == 8001


def test_env_var_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    config_path.write_text(
        json.dumps({"llm_provider": {"chat_model": "qwen3-8b"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_WIKI_LLM_PROVIDER__CHAT_MODEL", "env-override-model")

    settings = AppSettings.load(config_path)

    assert settings.llm_provider.chat_model == "env-override-model"


def test_env_var_overrides_default_with_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_WIKI_MCP_SERVER__PORT", "9001")

    settings = AppSettings.load(None)

    assert settings.mcp_server.port == 9001
