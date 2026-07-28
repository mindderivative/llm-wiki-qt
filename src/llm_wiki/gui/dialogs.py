"""Settings and vault-open/new dialogs.

The mockup's Settings tabs list generic fields (temperature, telemetry, a
hosted-provider API key) that this app has no equivalent for -- it runs
against a local llama-server and stores `AppSettings`. The tabbed shape is
kept; the fields are the real ones, plus an MCP tab for the settings the
mockup's toolbar controls imply.
"""

from collections.abc import Callable
from pathlib import Path

import flet as ft

from llm_wiki.gui import theme
from llm_wiki.gui.app_controller import AppController
from llm_wiki.gui.dock import DockArea


def _field(label: str, control: ft.Control) -> ft.Control:
    return ft.Column(
        spacing=6,
        controls=[ft.Text(label, size=12, color=theme.TEXT_SECONDARY), control],
    )


def _text_input(value: str, *, password: bool = False, on_change=None) -> ft.TextField:
    return ft.TextField(
        value=value,
        password=password,
        can_reveal_password=password,
        on_change=on_change,
        text_size=13,
        color=theme.TEXT,
        bgcolor=theme.BUTTON_BG,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.ACCENT,
        border_radius=6,
        content_padding=ft.Padding(10, 8, 10, 8),
    )


def _accent_button(label: str, on_click) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(16, 8, 16, 8),
        bgcolor=theme.ACCENT,
        border_radius=6,
        on_click=on_click,
        content=ft.Text(label, size=12.5, weight=ft.FontWeight.W_600, color=theme.APP_BG),
    )


def _plain_button(label: str, on_click) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(16, 8, 16, 8),
        bgcolor=theme.BUTTON_BG,
        border=ft.Border.all(1, theme.BORDER_STRONG),
        border_radius=6,
        on_click=on_click,
        content=ft.Text(label, size=12.5, color=theme.TEXT),
    )


def build_settings_dialog(
    controller: AppController, on_close: Callable[[], None]
) -> ft.AlertDialog:
    """A tabbed editor over the active vault's `.llm-wiki-config`."""
    llm = controller.settings.llm_provider
    mcp = controller.settings.mcp_server
    vault = controller.settings.vault

    provider = _text_input(llm.provider)
    host_ip = _text_input(llm.host_ip)
    host_port = _text_input(str(llm.host_port))
    api_key = _text_input(llm.api_key, password=True)
    chat_model = _text_input(llm.chat_model)
    atomizer_model = _text_input(llm.atomizer_model)
    mcp_host = _text_input(mcp.host)
    mcp_port = _text_input(str(mcp.port))
    mcp_transport = _text_input(mcp.transport)
    max_degrees = _text_input(str(vault.max_link_degrees))
    auto_watch = ft.Checkbox(
        label="Watch raw/ for new files",
        value=vault.auto_watch_raw,
        label_style=ft.TextStyle(size=12.5, color=theme.TEXT),
        active_color=theme.ACCENT,
    )

    def page(controls: list[ft.Control]) -> ft.Control:
        return ft.Container(
            padding=20,
            content=ft.Column(spacing=14, controls=controls, scroll=ft.ScrollMode.AUTO),
        )

    tabs = DockArea(
        strip_height=40,
        panels=[
            (
                "General",
                page(
                    [
                        _field(
                            "Vault Name",
                            ft.Text(controller.vault_name or "—", size=13, color=theme.TEXT),
                        ),
                        _field(
                            "Vault Path",
                            ft.Text(
                                str(controller.vault_path or "—"),
                                size=13,
                                color=theme.TEXT_LIST,
                            ),
                        ),
                        _field("Max Link Degrees", max_degrees),
                        auto_watch,
                    ]
                ),
            ),
            (
                "AI Provider",
                page(
                    [
                        _field("Provider", provider),
                        _field("Host IP", host_ip),
                        _field("Host Port", host_port),
                        _field("API Key", api_key),
                    ]
                ),
            ),
            (
                "LLM",
                page(
                    [
                        _field("Chat Model", chat_model),
                        _field("Atomizer Model", atomizer_model),
                    ]
                ),
            ),
            (
                "MCP",
                page(
                    [
                        _field("Host", mcp_host),
                        _field("Port", mcp_port),
                        _field("Transport", mcp_transport),
                    ]
                ),
            ),
        ],
    )

    def save(_e: ft.Event) -> None:
        llm.provider = provider.value
        llm.host_ip = host_ip.value
        llm.host_port = int(host_port.value)
        llm.api_key = api_key.value
        llm.chat_model = chat_model.value
        llm.atomizer_model = atomizer_model.value
        mcp.host = mcp_host.value
        mcp.port = int(mcp_port.value)
        mcp.transport = mcp_transport.value
        vault.max_link_degrees = int(max_degrees.value)
        vault.auto_watch_raw = auto_watch.value
        controller.save_settings()
        on_close()

    return ft.AlertDialog(
        modal=True,
        bgcolor=theme.CHROME_BG,
        shape=ft.RoundedRectangleBorder(radius=12),
        title=ft.Text("Settings", size=14, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Container(width=560, height=380, content=tabs),
        actions=[_plain_button("Cancel", lambda _e: on_close()), _accent_button("Save", save)],
    )


def build_vault_dialog(
    controller: AppController,
    on_close: Callable[[], None],
    on_error: Callable[[str], None],
) -> ft.AlertDialog:
    """One dialog with an Open tab (recent vaults) and a New tab (name + location)."""
    open_path = _text_input("")
    new_name = _text_input("")
    new_location = _text_input(str(Path.home() / "Vaults"))
    new_description = _text_input("")

    def recent_row(path: Path) -> ft.Control:
        return ft.Container(
            padding=ft.Padding(12, 10, 12, 10),
            border_radius=7,
            on_click=lambda _e, p=path: do_open(str(p)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(path.name, size=13, color=theme.TEXT),
                    ft.Text(str(path), size=11, color=theme.TEXT_MUTED),
                ],
            ),
        )

    def do_open(path: str) -> None:
        try:
            controller.open_vault(path)
        except Exception as exc:
            on_error(str(exc))
            return
        on_close()

    def do_create(_e: ft.Event) -> None:
        target = Path(new_location.value) / (new_name.value or "vault")
        try:
            controller.create_vault(target, new_name.value, new_description.value)
        except Exception as exc:
            on_error(str(exc))
            return
        on_close()

    recents = controller.recent_vaults()
    open_panel = ft.Container(
        padding=ft.Padding(20, 16, 20, 16),
        content=ft.Column(
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                *(recent_row(p) for p in recents),
                *(
                    []
                    if recents
                    else [ft.Text("No recent vaults", size=12.5, color=theme.TEXT_MUTED)]
                ),
                _field("Vault Path", open_path),
                _plain_button("Open", lambda _e: do_open(open_path.value)),
            ],
        ),
    )

    new_panel = ft.Container(
        padding=ft.Padding(20, 18, 20, 18),
        content=ft.Column(
            spacing=12,
            controls=[
                _field("Vault Name", new_name),
                _field("Location", new_location),
                _field("Description", new_description),
            ],
        ),
    )

    tabs = DockArea(panels=[("Open Vault", open_panel), ("New Vault", new_panel)], strip_height=44)

    return ft.AlertDialog(
        modal=True,
        bgcolor=theme.CHROME_BG,
        shape=ft.RoundedRectangleBorder(radius=12),
        content=ft.Container(width=460, height=320, content=tabs),
        actions=[
            _plain_button("Cancel", lambda _e: on_close()),
            _accent_button("Create", do_create),
        ],
    )
