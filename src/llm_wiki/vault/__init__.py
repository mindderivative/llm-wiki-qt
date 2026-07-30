"""Vault directory tree lifecycle: create, load, validate, recent-vaults."""

from llm_wiki.vault.manager import (
    CONFIG_FILENAME,
    create_vault,
    default_recent_vaults_path,
    get_recent_vaults,
    load_vault,
)
from llm_wiki.vault.reindex import (
    append_log_entry,
    backfill_related_blocks,
    rebuild_index,
    reindex_vault,
)

__all__ = [
    "CONFIG_FILENAME",
    "append_log_entry",
    "backfill_related_blocks",
    "create_vault",
    "default_recent_vaults_path",
    "get_recent_vaults",
    "load_vault",
    "rebuild_index",
    "reindex_vault",
]
