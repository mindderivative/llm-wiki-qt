"""The application menu bar (File / Edit / Tools / View / Help).

Actions are passed in rather than reached for, so this module stays a
layout concern and the shell owns what the items actually do.
"""

from collections.abc import Callable

import flet as ft

from llm_wiki.gui import theme

MENU_HEIGHT = 34

_MENU_STYLE = ft.MenuStyle(
    bgcolor=theme.MENU_BG,
    shape=ft.RoundedRectangleBorder(radius=8),
    side=ft.BorderSide(1, theme.BORDER_STRONG),
)


def _item(label: str, on_click: Callable[[ft.Event], None] | None) -> ft.MenuItemButton:
    return ft.MenuItemButton(
        content=ft.Text(label, size=13, color=theme.TEXT),
        on_click=on_click,
        style=ft.ButtonStyle(bgcolor={ft.ControlState.HOVERED: theme.INPUT_BG}),
    )


def _submenu(label: str, items: list[ft.Control]) -> ft.SubmenuButton:
    return ft.SubmenuButton(
        content=ft.Text(label, size=13, color=theme.TEXT),
        menu_style=_MENU_STYLE,
        controls=items,
        style=ft.ButtonStyle(bgcolor={ft.ControlState.HOVERED: theme.INPUT_BG}),
    )


def build_menu_bar(
    *,
    vault_label: str,
    on_new_vault: Callable[[ft.Event], None],
    on_open_vault: Callable[[ft.Event], None],
    on_open_recent: Callable[[str], None],
    recent_vaults: list[str],
    on_settings: Callable[[ft.Event], None],
    on_exit: Callable[[ft.Event], None],
    on_reindex_vault: Callable[[ft.Event], None],
    on_zoom_reset: Callable[[ft.Event], None],
    on_toggle_left: Callable[[ft.Event], None],
    on_toggle_right: Callable[[ft.Event], None],
    on_toggle_bottom: Callable[[ft.Event], None],
) -> ft.Control:
    def recent_item(path: str) -> ft.MenuItemButton:
        return ft.MenuItemButton(
            content=ft.Text(path, size=12.5, color=theme.TEXT_RECENT),
            on_click=lambda _e, p=path: on_open_recent(p),
            style=ft.ButtonStyle(bgcolor={ft.ControlState.HOVERED: theme.INPUT_BG}),
        )

    file_items: list[ft.Control] = [
        _item("New Vault…", on_new_vault),
        _item("Open Vault…", on_open_vault),
    ]
    if recent_vaults:
        file_items.append(
            ft.Container(
                padding=ft.Padding(12, 6, 12, 6),
                content=ft.Text("Recent", size=11, color=theme.TEXT_MUTED),
            )
        )
        file_items.extend(recent_item(p) for p in recent_vaults)
    file_items.append(_item("Exit", on_exit))

    menu_bar = ft.MenuBar(
        expand=True,
        style=ft.MenuStyle(bgcolor=theme.CHROME_BG, elevation=0),
        controls=[
            _submenu("File", file_items),
            _submenu("Edit", [_item("Settings…", on_settings)]),
            _submenu(
                "Tools",
                [
                    _item("Reindex Vault", on_reindex_vault),
                    _item("Rebuild Graph", None),
                    _item("Clear Cache", None),
                    _item("Open Data Folder", None),
                ],
            ),
            _submenu(
                "View",
                [
                    _item("Toggle Left Panel", on_toggle_left),
                    _item("Toggle Right Panel", on_toggle_right),
                    _item("Toggle Bottom Panel", on_toggle_bottom),
                    _item("Zoom to Fit", on_zoom_reset),
                ],
            ),
            _submenu("Help", []),
        ],
    )

    return ft.Container(
        height=MENU_HEIGHT,
        bgcolor=theme.CHROME_BG,
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
        padding=ft.Padding(6, 0, 12, 0),
        content=ft.Row(
            spacing=0,
            controls=[
                menu_bar,
                ft.Text(vault_label, size=12, color=theme.TEXT_SUBTLE),
            ],
        ),
    )
