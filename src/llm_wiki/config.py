"""Typed, env-var-aware application configuration.

Mirrors the legacy `llm_provider` / `mcp_server` / `vault` JSON config
sections, loaded from a vault's `.llm-wiki-config` file with environment
variable overrides (`LLM_WIKI_<SECTION>__<FIELD>`, see ARCHITECTURE.md §7).
Precedence, highest first: explicit constructor kwargs > environment
variables > the `.llm-wiki-config` file > the field defaults below.
"""

from pathlib import Path

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
    endpoint: str = "http://127.0.0.1:11434/v1/chat/completions"
    api_key: str = ""
    chat_model: str = "qwen2.5-coder-14b"
    atomizer_model: str = "qwen2.5-coder-7b"


class MCPServerConfig(BaseModel):
    """Network settings for the local MCP server."""

    host: str = "127.0.0.1"
    port: int = 8001
    transport: str = "stdio"


class VaultConfig(BaseModel):
    """Per-vault behavior settings."""

    max_link_degrees: int = 3
    auto_watch_raw: bool = True


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
