"""Typed, env-var-aware application configuration.

Mirrors the legacy `llm_provider` / `mcp_server` / `vault` JSON config
sections, loaded from a vault's `.llm-wiki-config` file with environment
variable overrides (`LLM_WIKI_<SECTION>__<FIELD>`, see ARCHITECTURE.md §7).
Precedence, highest first: explicit constructor kwargs > environment
variables > the `.llm-wiki-config` file > the field defaults below.
"""

# `load()` returns `AppSettings` from inside the class body -- implicit on
# 3.14 via PEP 649, declared here so the package also imports on 3.13.
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    JsonConfigSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class LLMProviderConfig(BaseModel):
    """Connection details for the local llama-server cluster."""

    provider: str = "llama.cpp"
    host_ip: str = "127.0.0.1"
    host_port: int = 11434
    api_key: str = ""
    chat_model: str = "qwen2.5-coder-14b"
    atomizer_model: str = "qwen2.5-coder-7b"

    @property
    def base_url(self) -> str:
        """The OpenAI-compatible base URL, derived from `host_ip`/`host_port`.

        Always in sync with those two fields -- unlike a stored `endpoint`
        string, which the original config shape had but nothing ever read.
        """
        return f"http://{self.host_ip}:{self.host_port}/v1"


class MCPServerConfig(BaseModel):
    """Network settings for the local MCP server."""

    host: str = "127.0.0.1"
    port: int = 8001
    # No longer read by the GUI (Phase 20) -- `McpProcess` always runs
    # streamable-http, since `stdio` can't work as a GUI-toggled background
    # service. Kept in the model so an existing saved config with this key
    # still loads; `mcp/server.py`'s CLI still supports `--transport`
    # independently, for an external MCP client to spawn directly.
    transport: str = "stdio"


class VaultConfig(BaseModel):
    """Per-vault behavior settings."""

    max_link_degrees: int = 3
    auto_watch_raw: bool = True


class GraphViewConfig(BaseModel):
    """Persisted graph canvas view preferences (Phase 23)."""

    # Defaults to expanded so the category-color legend stays visible out
    # of the box, matching the pre-Phase-23 always-on behavior.
    settings_panel_expanded: bool = True


class AppSettings(BaseSettings):
    """Top-level application configuration; construct via `AppSettings.load()`."""

    # extra="ignore": `.llm-wiki-config` is shared with vault.manager, which
    # writes identity fields (vault_name, domain_description, ...) this
    # model doesn't and shouldn't know about.
    model_config = SettingsConfigDict(
        env_prefix="LLM_WIKI_", env_nested_delimiter="__", extra="ignore"
    )

    llm_provider: LLMProviderConfig = Field(default_factory=LLMProviderConfig)
    mcp_server: MCPServerConfig = Field(default_factory=MCPServerConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    graph_view: GraphViewConfig = Field(default_factory=GraphViewConfig)

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> AppSettings:
        """Builds settings, layering env vars over an optional `.llm-wiki-config` file."""
        json_file = Path(config_path) if config_path is not None else None
        if json_file is None or not json_file.exists():
            return cls()

        class _ScopedSettings(cls):  # type: ignore[misc]
            model_config = SettingsConfigDict(
                env_prefix="LLM_WIKI_",
                env_nested_delimiter="__",
                extra="ignore",
                json_file=json_file,
            )

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> tuple[PydanticBaseSettingsSource, ...]:
                return (
                    init_settings,
                    env_settings,
                    dotenv_settings,
                    JsonConfigSettingsSource(settings_cls),
                    file_secret_settings,
                )

        return _ScopedSettings()

    def save(self, config_path: Path | str) -> None:
        """Writes `llm_provider`/`mcp_server`/`vault` back to `.llm-wiki-config`.

        Preserves any other keys already in the file -- notably the vault
        identity fields (`vault_name`, `domain_description`, ...) that
        `vault.manager` writes there, which this model deliberately doesn't
        know about (see the `extra="ignore"` note above).
        """
        config_path = Path(config_path)
        existing: dict = {}
        if config_path.exists():
            existing = json.loads(config_path.read_text(encoding="utf-8"))

        existing["llm_provider"] = self.llm_provider.model_dump(mode="json")
        existing["mcp_server"] = self.mcp_server.model_dump(mode="json")
        existing["vault"] = self.vault.model_dump(mode="json")
        existing["graph_view"] = self.graph_view.model_dump(mode="json")

        config_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        logger.info(f"Settings saved to {config_path}")
