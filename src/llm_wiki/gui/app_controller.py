"""Python<->QML bridge for vault lifecycle and application settings.

Thin wiring only -- every method here delegates straight to the engine
(`vault`, `config`) built in Phases 1-14. No new logic lives here beyond
translating between QML-friendly types and the engine's own.
"""

import sqlite3
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtQml import QmlElement

from llm_wiki.config import AppSettings
from llm_wiki.gui.git_controller import GitController
from llm_wiki.gui.health_controller import HealthController
from llm_wiki.gui.queue_model import QueueListModel
from llm_wiki.models import LLMWikiError
from llm_wiki.storage import connect
from llm_wiki.vault import CONFIG_FILENAME, create_vault, get_recent_vaults, load_vault

QML_IMPORT_NAME = "LLMWiki"
QML_IMPORT_MAJOR_VERSION = 1


@QmlElement
class AppController(QObject):
    """Exposes vault open/create/recent and `.llm-wiki-config` settings to QML."""

    vaultChanged = Signal()
    settingsChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._vault_path = ""
        self._vault_name = ""
        self._settings = AppSettings.load(None)
        self._conn: sqlite3.Connection | None = None
        self._queue_model = QueueListModel(self)
        self._git_controller = GitController(self)
        self._health_controller = HealthController(self)

    # --- Vault lifecycle ----------------------------------------------------

    @Slot(str, str, str)
    def createVault(self, path: str, name: str, description: str) -> None:
        """Creates a new vault directory tree and makes it the active vault."""
        try:
            info = create_vault(path, name or Path(path).name, description)
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._set_active_vault(info.path, info.name)

    @Slot(str)
    def openVault(self, path: str) -> None:
        """Validates and loads an existing vault as the active vault."""
        try:
            info = load_vault(path)
        except LLMWikiError as exc:
            self.errorOccurred.emit(str(exc))
            return
        self._set_active_vault(info.path, info.name)

    @Slot(result=list)
    def recentVaults(self) -> list[str]:
        return [str(p) for p in get_recent_vaults()]

    def _set_active_vault(self, path: Path, name: str) -> None:
        self._vault_path = str(path)
        self._vault_name = name
        self._settings = AppSettings.load(path / CONFIG_FILENAME)
        self._conn = connect(path / ".llm-wiki" / "db.sqlite3")

        self._queue_model.set_connection(self._conn)
        self._git_controller.set_vault_path(self._vault_path)
        self._health_controller.set_connection(self._conn)

        self.vaultChanged.emit()
        self.settingsChanged.emit()

    @Property(str, notify=vaultChanged)
    def vaultPath(self) -> str:
        return self._vault_path

    @Property(str, notify=vaultChanged)
    def vaultName(self) -> str:
        return self._vault_name

    @Property(bool, notify=vaultChanged)
    def hasVault(self) -> bool:
        return bool(self._vault_path)

    @Property(QObject, constant=True)
    def queueModel(self) -> QueueListModel:
        return self._queue_model

    @Property(QObject, constant=True)
    def gitController(self) -> GitController:
        return self._git_controller

    @Property(QObject, constant=True)
    def healthController(self) -> HealthController:
        return self._health_controller

    # --- Settings -------------------------------------------------------

    @Slot()
    def saveSettings(self) -> None:
        """Writes the current in-memory settings back to `.llm-wiki-config`."""
        if not self._vault_path:
            self.errorOccurred.emit("No active vault -- nothing to save settings to.")
            return
        self._settings.save(Path(self._vault_path) / CONFIG_FILENAME)

    def _get_provider(self) -> str:
        return self._settings.llm_provider.provider

    def _set_provider(self, value: str) -> None:
        self._settings.llm_provider.provider = value
        self.settingsChanged.emit()

    provider = Property(str, _get_provider, _set_provider, notify=settingsChanged)

    def _get_host_ip(self) -> str:
        return self._settings.llm_provider.host_ip

    def _set_host_ip(self, value: str) -> None:
        self._settings.llm_provider.host_ip = value
        self.settingsChanged.emit()

    hostIp = Property(str, _get_host_ip, _set_host_ip, notify=settingsChanged)

    def _get_host_port(self) -> int:
        return self._settings.llm_provider.host_port

    def _set_host_port(self, value: int) -> None:
        self._settings.llm_provider.host_port = value
        self.settingsChanged.emit()

    hostPort = Property(int, _get_host_port, _set_host_port, notify=settingsChanged)

    def _get_api_key(self) -> str:
        return self._settings.llm_provider.api_key

    def _set_api_key(self, value: str) -> None:
        self._settings.llm_provider.api_key = value
        self.settingsChanged.emit()

    apiKey = Property(str, _get_api_key, _set_api_key, notify=settingsChanged)

    def _get_chat_model(self) -> str:
        return self._settings.llm_provider.chat_model

    def _set_chat_model(self, value: str) -> None:
        self._settings.llm_provider.chat_model = value
        self.settingsChanged.emit()

    chatModel = Property(str, _get_chat_model, _set_chat_model, notify=settingsChanged)

    def _get_atomizer_model(self) -> str:
        return self._settings.llm_provider.atomizer_model

    def _set_atomizer_model(self, value: str) -> None:
        self._settings.llm_provider.atomizer_model = value
        self.settingsChanged.emit()

    atomizerModel = Property(
        str, _get_atomizer_model, _set_atomizer_model, notify=settingsChanged
    )

    def _get_mcp_host(self) -> str:
        return self._settings.mcp_server.host

    def _set_mcp_host(self, value: str) -> None:
        self._settings.mcp_server.host = value
        self.settingsChanged.emit()

    mcpHost = Property(str, _get_mcp_host, _set_mcp_host, notify=settingsChanged)

    def _get_mcp_port(self) -> int:
        return self._settings.mcp_server.port

    def _set_mcp_port(self, value: int) -> None:
        self._settings.mcp_server.port = value
        self.settingsChanged.emit()

    mcpPort = Property(int, _get_mcp_port, _set_mcp_port, notify=settingsChanged)

    def _get_mcp_transport(self) -> str:
        return self._settings.mcp_server.transport

    def _set_mcp_transport(self, value: str) -> None:
        self._settings.mcp_server.transport = value
        self.settingsChanged.emit()

    mcpTransport = Property(
        str, _get_mcp_transport, _set_mcp_transport, notify=settingsChanged
    )
