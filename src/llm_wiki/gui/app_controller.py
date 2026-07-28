"""Vault lifecycle and settings state for the desktop UI.

Plain Python -- no Flet import. Views subscribe via `subscribe()` and
rebuild themselves when the active vault changes; everything else here
delegates straight to the engine (`vault`, `config`) built in Phases 1-14.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

from llm_wiki.config import AppSettings
from llm_wiki.models import LLMWikiError
from llm_wiki.storage import connect
from llm_wiki.vault import CONFIG_FILENAME, create_vault, get_recent_vaults, load_vault


class AppController:
    """Owns the active vault, its DB connection, and its settings."""

    def __init__(self) -> None:
        self.vault_path: Path | None = None
        self.vault_name = ""
        self.settings = AppSettings.load(None)
        self.conn: sqlite3.Connection | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def has_vault(self) -> bool:
        return self.vault_path is not None

    def subscribe(self, listener: Callable[[], None]) -> None:
        """Registers a callback fired whenever the active vault changes."""
        self._listeners.append(listener)

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    # --- Vault lifecycle ----------------------------------------------------

    def create_vault(self, path: str | Path, name: str, description: str) -> None:
        """Creates a new vault directory tree and makes it the active vault.

        Raises `OSError` or `LLMWikiError` -- the caller surfaces it to the user.
        """
        info = create_vault(path, name or Path(path).name, description)
        self._set_active_vault(info.path, info.name)

    def open_vault(self, path: str | Path) -> None:
        """Validates and loads an existing vault as the active vault.

        Raises `LLMWikiError` if `path` isn't a vault.
        """
        info = load_vault(path)
        self._set_active_vault(info.path, info.name)

    def recent_vaults(self) -> list[Path]:
        return get_recent_vaults()

    def _set_active_vault(self, path: Path, name: str) -> None:
        if self.conn is not None:
            self.conn.close()
        self.vault_path = path
        self.vault_name = name
        self.settings = AppSettings.load(path / CONFIG_FILENAME)
        self.conn = connect(path / ".llm-wiki" / "db.sqlite3")
        self._notify()

    # --- Settings -------------------------------------------------------

    def save_settings(self) -> None:
        """Writes the current in-memory settings back to `.llm-wiki-config`."""
        if self.vault_path is None:
            raise LLMWikiError("No active vault -- nothing to save settings to.")
        self.settings.save(self.vault_path / CONFIG_FILENAME)
