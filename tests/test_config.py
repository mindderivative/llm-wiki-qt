"""Phase 1: config loads from a `.llm-wiki-config` fixture and env vars win."""

import json
from pathlib import Path

import pytest

from llm_wiki.config import AppSettings
from llm_wiki.vault import create_vault


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


def test_llm_provider_base_url_derives_from_host_ip_and_port() -> None:
    settings = AppSettings.load(None)
    assert settings.llm_provider.base_url == "http://127.0.0.1:11434/v1"

    settings.llm_provider.host_ip = "192.168.1.50"
    settings.llm_provider.host_port = 9999
    # Always derived, never stale -- there's no separate stored field to drift.
    assert settings.llm_provider.base_url == "http://192.168.1.50:9999/v1"


def test_fresh_install_defaults_are_sane() -> None:
    """Phase 14: what a machine with no prior `.llm-wiki-config` gets, unmodified."""
    settings = AppSettings.load(None)

    assert settings.llm_provider.base_url.startswith("http://")
    assert settings.llm_provider.chat_model
    assert settings.llm_provider.atomizer_model
    assert 0 < settings.mcp_server.port < 65536
    assert settings.mcp_server.transport in {"stdio", "sse"}
    assert settings.vault.max_link_degrees > 0
    assert isinstance(settings.vault.auto_watch_raw, bool)


def test_loads_cleanly_from_a_real_vault_created_config(tmp_path: Path) -> None:
    """Regression test: `.llm-wiki-config` is shared with vault.manager, which writes
    identity fields (vault_name, domain_description, ...) this model doesn't know
    about -- loading a config file from an actual `create_vault()` call must not
    raise on those extra keys.
    """
    vault_root = tmp_path / "vault"
    create_vault(
        vault_root, "My Vault", "desc", recent_vaults_path=tmp_path / "recent.json"
    )

    settings = AppSettings.load(vault_root / ".llm-wiki-config")

    assert settings.vault.max_link_degrees == 3
    assert settings.llm_provider.chat_model == "qwen2.5-coder-14b"


def test_save_round_trips_through_load(tmp_path: Path) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    settings = AppSettings.load(None)
    settings.llm_provider.chat_model = "qwen3-8b"
    settings.vault.max_link_degrees = 5

    settings.save(config_path)
    reloaded = AppSettings.load(config_path)

    assert reloaded.llm_provider.chat_model == "qwen3-8b"
    assert reloaded.vault.max_link_degrees == 5


def test_save_preserves_vault_identity_fields(tmp_path: Path) -> None:
    vault_root = tmp_path / "vault"
    create_vault(
        vault_root, "My Vault", "A description.", recent_vaults_path=tmp_path / "recent.json"
    )
    config_path = vault_root / ".llm-wiki-config"

    settings = AppSettings.load(config_path)
    settings.llm_provider.chat_model = "qwen3-8b"
    settings.save(config_path)

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    assert raw["vault_name"] == "My Vault"
    assert raw["domain_description"] == "A description."
    assert raw["llm_provider"]["chat_model"] == "qwen3-8b"


def test_graph_view_defaults_to_settings_panel_expanded() -> None:
    settings = AppSettings.load(None)
    assert settings.graph_view.settings_panel_expanded is True


def test_graph_view_round_trips_through_save(tmp_path: Path) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    settings = AppSettings.load(None)
    settings.graph_view.settings_panel_expanded = False

    settings.save(config_path)
    reloaded = AppSettings.load(config_path)

    assert reloaded.graph_view.settings_panel_expanded is False


def test_graph_view_filters_default_to_all_types_and_nothing_else() -> None:
    settings = AppSettings.load(None)
    assert settings.graph_view.filter_types == ["concept", "entity", "synthesis", "source"]
    assert settings.graph_view.filter_tags == []
    assert settings.graph_view.filter_search == ""
    assert settings.graph_view.filter_date_from is None
    assert settings.graph_view.filter_date_to is None
    assert settings.graph_view.filter_degrees == 1


def test_graph_view_filter_enable_switches_default_true_except_degrees() -> None:
    """Degrees is the one exception: it has no no-op state (it activates
    the instant any node is selected, a normal browsing action), so it
    must default off while the other four -- which start at genuinely
    inert values -- default on.
    """
    settings = AppSettings.load(None)
    gv = settings.graph_view
    assert gv.filters_enabled is True
    assert gv.filter_types_enabled is True
    assert gv.filter_tags_enabled is True
    assert gv.filter_search_enabled is True
    assert gv.filter_date_enabled is True
    assert gv.filter_degrees_enabled is False


def test_graph_view_filters_round_trip_through_save(tmp_path: Path) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    settings = AppSettings.load(None)
    settings.graph_view.filter_types = ["concept"]
    settings.graph_view.filter_tags = ["core", "physics"]
    settings.graph_view.filter_search = "alpha"
    settings.graph_view.filter_date_from = "2026-01-01"
    settings.graph_view.filter_date_to = "2026-12-31"
    settings.graph_view.filter_degrees = 2
    settings.graph_view.filters_enabled = False
    settings.graph_view.filter_degrees_enabled = True

    settings.save(config_path)
    reloaded = AppSettings.load(config_path)

    assert reloaded.graph_view.filter_types == ["concept"]
    assert reloaded.graph_view.filter_tags == ["core", "physics"]
    assert reloaded.graph_view.filter_search == "alpha"
    assert reloaded.graph_view.filter_date_from == "2026-01-01"
    assert reloaded.graph_view.filter_date_to == "2026-12-31"
    assert reloaded.graph_view.filter_degrees == 2
    assert reloaded.graph_view.filters_enabled is False
    assert reloaded.graph_view.filter_degrees_enabled is True


def test_graph_view_loads_a_legacy_null_filter_degrees(tmp_path: Path) -> None:
    """Regression: a `.llm-wiki-config` saved before filter_degrees became
    a non-optional int (Post-24 fix) still has `null` on disk, its old
    default -- that used to raise a pydantic validation error and block
    opening the vault entirely.
    """
    config_path = tmp_path / ".llm-wiki-config"
    config_path.write_text(
        json.dumps({"graph_view": {"filter_degrees": None, "filter_types": ["concept"]}}),
        encoding="utf-8",
    )

    settings = AppSettings.load(config_path)

    assert settings.graph_view.filter_degrees == 1
    assert settings.graph_view.filter_types == ["concept"]


def test_graph_view_display_settings_default_to_the_built_in_look() -> None:
    settings = AppSettings.load(None)
    gv = settings.graph_view
    assert gv.type_colors == {}
    assert gv.simulation_enabled is True
    assert gv.simulation_strength == 1.0
    assert gv.invert_scroll_zoom is False


def test_graph_view_display_settings_round_trip_through_save(tmp_path: Path) -> None:
    config_path = tmp_path / ".llm-wiki-config"
    settings = AppSettings.load(None)
    settings.graph_view.type_colors = {"concept": "#123456", "index": "#654321"}
    settings.graph_view.simulation_enabled = False
    settings.graph_view.simulation_strength = 1.75
    settings.graph_view.invert_scroll_zoom = True

    settings.save(config_path)
    reloaded = AppSettings.load(config_path)

    assert reloaded.graph_view.type_colors == {"concept": "#123456", "index": "#654321"}
    assert reloaded.graph_view.simulation_enabled is False
    assert reloaded.graph_view.simulation_strength == 1.75
    assert reloaded.graph_view.invert_scroll_zoom is True
