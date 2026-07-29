"""Create/load/validate vault directory trees (ARCHITECTURE.md §5).

A de-Qt'd port of the old `vault_manager.py`: no signals, no `QObject` —
functions return a `VaultInfo` or raise `VaultNotFoundError`.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from llm_wiki.models import VaultInfo, VaultNotFoundError

CONFIG_FILENAME = ".llm-wiki-config"
_MAX_RECENT_VAULTS = 10


def default_recent_vaults_path() -> Path:
    return Path.home() / ".config" / "LLM-Wiki-Qt" / "recent_vaults.json"


def create_vault(
    vault_dir: Path | str,
    name: str,
    description: str,
    *,
    recent_vaults_path: Path | None = None,
) -> VaultInfo:
    """Instantiates the vault directory tree and baseline system files.

    Idempotent on the baseline files (`wiki/index.md`, `wiki/log.md`,
    `SCHEMA.md`) — re-running against an existing vault directory won't
    clobber content already written there, but always rewrites the
    `.llm-wiki-config` identity file.
    """
    root = Path(vault_dir).resolve()

    directories = [
        root / "raw" / ".sources",
        root / "wiki" / "sources",
        root / "wiki" / "entities",
        root / "wiki" / "concepts",
        root / "wiki" / "synthesis",
        root / "wiki" / ".system" / "prompts",
        root / ".llm-wiki",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(UTC)
    config_data = {
        "vault_name": name,
        "domain_description": description,
        "version": "1.0",
        "created_at": created_at.isoformat(),
        "vault": {"max_link_degrees": 3, "auto_watch_raw": True},
    }
    (root / CONFIG_FILENAME).write_text(
        json.dumps(config_data, indent=2), encoding="utf-8"
    )

    index_path = root / "wiki" / "index.md"
    if not index_path.exists():
        index_path.write_text(
            f"# {name} - Master Catalog\n\n"
            f"> {description}\n\n"
            "## Sources\n\n## Entities\n\n## Concepts\n\n## Synthesis\n",
            encoding="utf-8",
        )

    log_path = root / "wiki" / "log.md"
    if not log_path.exists():
        now_str = created_at.strftime("%Y-%m-%d %H:%M:%S")
        log_path.write_text(
            f"# Vault Activity Log\n\n- [{now_str}] Initialized vault '{name}'.\n",
            encoding="utf-8",
        )

    schema_path = root / "SCHEMA.md"
    if not schema_path.exists():
        schema_path.write_text(
            f"# Operational Schema for {name}\n\n"
            f"Domain Description: {description}\n\n"
            "## Rules\n"
            "- Source documents in `raw/` are immutable.\n"
            "- All section chunks must observe 200-400 word boundaries.\n"
            "- Wikilink degrees of separation limit = 3.\n",
            encoding="utf-8",
        )

    logger.info(f"Created vault '{name}' at {root}")
    return load_vault(root, recent_vaults_path=recent_vaults_path)


def load_vault(vault_dir: Path | str, *, recent_vaults_path: Path | None = None) -> VaultInfo:
    """Validates and loads an existing vault, updating the recent-vaults list."""
    root = Path(vault_dir).resolve()
    config_path = root / CONFIG_FILENAME

    if not config_path.exists():
        raise VaultNotFoundError(f"No {CONFIG_FILENAME} found at {root}")

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VaultNotFoundError(f"Failed to read vault config at {root}: {exc}") from exc

    info = VaultInfo(
        path=root,
        name=data.get("vault_name", root.name),
        description=data.get("domain_description", ""),
        version=data.get("version", "1.0"),
        created_at=(
            datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.now(UTC)
        ),
    )

    _add_to_recent(root, recent_vaults_path or default_recent_vaults_path())
    logger.info(f"Opened vault '{info.name}' at {root}")
    return info


def get_recent_vaults(recent_vaults_path: Path | None = None) -> list[Path]:
    """Returns recently loaded vault paths, most recent first."""
    path = recent_vaults_path or default_recent_vaults_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [Path(p) for p in raw]


def _add_to_recent(vault_path: Path, recent_vaults_path: Path) -> None:
    vault_path = vault_path.resolve()
    recents = [p for p in get_recent_vaults(recent_vaults_path) if p != vault_path]
    recents.insert(0, vault_path)
    recents = recents[:_MAX_RECENT_VAULTS]

    recent_vaults_path.parent.mkdir(parents=True, exist_ok=True)
    recent_vaults_path.write_text(
        json.dumps([str(p) for p in recents], indent=2), encoding="utf-8"
    )
