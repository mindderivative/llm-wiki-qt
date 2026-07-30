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
from pydantic import BaseModel, Field, field_validator
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

    # Phase 24 -- graph Filters. Lists, not sets: sets don't round-trip
    # through JSON cleanly; GraphCanvas converts to/from set() at its own
    # boundary. All four note types shown by default -- filtering starts
    # as a no-op, matching the pre-Phase-24 always-shown behavior.
    filter_types: list[str] = Field(
        default_factory=lambda: ["concept", "entity", "synthesis", "source"]
    )
    filter_tags: list[str] = Field(default_factory=list)
    filter_search: str = ""
    filter_date_from: str | None = None
    filter_date_to: str | None = None
    # Post-24 fix -- always a real 1-5 value now; "off" is represented by
    # filter_degrees_enabled=False, not a None/0 sentinel, since the
    # slider itself is always interactive (see graph_canvas.py).
    filter_degrees: int = 1

    @field_validator("filter_degrees", mode="before")
    @classmethod
    def _coerce_legacy_null_degrees(cls, value: object) -> object:
        """A `.llm-wiki-config` saved before this field became non-optional
        may still have `null` on disk (its old default) -- without this,
        loading that file raises a pydantic validation error and the vault
        can't be opened at all. Treat `null` the same as an omitted key.
        """
        return 1 if value is None else value

    # Post-24 fix -- one enable switch per filter dimension, plus a master.
    # All default True, matching the pre-Post-24 always-on behavior --
    # turning a filter off never clears its configured value.
    filters_enabled: bool = True
    filter_types_enabled: bool = True
    filter_tags_enabled: bool = True
    filter_search_enabled: bool = True
    filter_date_enabled: bool = True
    # Defaults False, unlike the other four: those start at no-op values
    # (all types checked, no tags/search/dates set) so "enabled" is
    # harmless before you configure anything. Degrees has no such no-op
    # state -- it activates the instant *any* node is selected, which is
    # a normal browsing action, not a deliberate filter choice, so it
    # must start off.
    filter_degrees_enabled: bool = False

    # Phase 25 -- Colors / Physics / Zoom & Pan. type_colors stores only
    # what's actually been customized (an empty dict, the default, means
    # "use the built-in defaults") -- GraphCanvas always merges this over
    # its own defaults rather than assigning it directly, so a config
    # missing a key (an older save, or a future new type) falls back
    # cleanly. Deliberately not pre-filled with hex values here, which
    # would duplicate them outside gui/theme.py and gui/graph_canvas.py
    # and go stale if either ever changes.
    type_colors: dict[str, str] = Field(default_factory=dict)
    simulation_enabled: bool = True
    simulation_strength: float = 1.0
    invert_scroll_zoom: bool = False

    # Post-25 fix -- adjustable min/max zoom + a Node Spacing control.
    # Defaults match the fixed constants they replace exactly.
    min_zoom: float = 0.5
    max_zoom: float = 2.0
    node_spacing: float = 4.0


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
