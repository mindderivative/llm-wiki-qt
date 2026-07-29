"""Git Controls panel: status, stage/commit/push/pull, wired to
`vcs.git_engine` (Phase 4).

The mockup's demo buttons take no commit message; a real commit needs one,
so a message field is added above them -- the one place this panel departs
from the mockup's literal layout, for a functional reason rather than a
stylistic one.
"""

import contextlib
from collections.abc import Callable
from pathlib import Path

import flet as ft
import pygit2

from llm_wiki.gui import theme
from llm_wiki.vcs import git_engine

# Mirrors the mockup's M/A/D file-status badges.
_MODIFIED = ("M", theme.STAGE_LINT)
_UNTRACKED = ("A", theme.STAGE_LINK)


def _pill_button(label: str, on_click, *, accent: bool = False) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(9, 5, 9, 5),
        bgcolor=theme.ACCENT if accent else theme.BUTTON_BG,
        border=None if accent else ft.Border.all(1, theme.BORDER_STRONG),
        border_radius=5,
        on_click=on_click,
        content=ft.Text(
            label,
            size=11,
            weight=ft.FontWeight.W_600 if accent else ft.FontWeight.W_400,
            color=theme.APP_BG if accent else theme.TEXT,
        ),
    )


def _file_row(path: str, badge: str, colour: str) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(10, 7, 10, 7),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.CANVAS_DOT)),
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Container(
                    width=16,
                    height=16,
                    bgcolor=colour,
                    border_radius=3,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        badge, size=10, weight=ft.FontWeight.W_700, color=theme.APP_BG
                    ),
                ),
                ft.Text(
                    path,
                    size=11.5,
                    color=theme.TEXT_LIST,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    expand=True,
                ),
            ],
        ),
    )


class GitPanel(ft.Container):
    """Branch/status header, stage/commit/push/pull, and a changed-files list."""

    def __init__(self, on_error: Callable[[str], None]) -> None:
        super().__init__()
        self.expand = True
        self._vault_path: Path | None = None
        self._on_error = on_error
        self.branch = ""
        self.clean = True
        self.is_initialized = False

        self._status_text = ft.Text("No vault", size=11, color=theme.TEXT_MUTED)
        self._message = ft.TextField(
            hint_text="Commit message…",
            text_size=12,
            color=theme.TEXT,
            bgcolor=theme.BUTTON_BG,
            border_color=theme.BORDER_STRONG,
            focused_border_color=theme.ACCENT,
            border_radius=6,
            content_padding=ft.Padding(9, 7, 9, 7),
        )
        self._files = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self._init_button = _pill_button("Init Repo", lambda _e: self.init_repo())

        self.content = ft.Column(
            spacing=0,
            expand=True,
            controls=[
                ft.Container(
                    padding=ft.Padding(10, 8, 10, 8),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[self._status_text, self._init_button],
                    ),
                ),
                ft.Container(
                    padding=ft.Padding(10, 9, 10, 9),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
                    content=ft.Column(
                        spacing=8,
                        controls=[
                            self._message,
                            ft.Row(
                                wrap=True,
                                spacing=6,
                                run_spacing=6,
                                controls=[
                                    _pill_button("Stage All", lambda _e: self.stage_all()),
                                    _pill_button(
                                        "Commit", lambda _e: self.commit(), accent=True
                                    ),
                                    _pill_button("Push", lambda _e: self.push()),
                                    _pill_button("Pull", lambda _e: self.pull()),
                                ],
                            ),
                        ],
                    ),
                ),
                self._files,
            ],
        )
        self._sync_init_visibility()

    def set_vault_path(self, path: str | Path | None) -> None:
        self._vault_path = Path(path) if path else None
        self.refresh()

    # --- Actions --------------------------------------------------------

    def refresh(self) -> None:
        if self._vault_path is None:
            self.branch, self.clean, self.is_initialized = "", True, False
            self._files.controls = []
            self._status_text.value = "No vault"
        else:
            try:
                result = git_engine.status(self._vault_path)
            except pygit2.GitError:
                self.branch, self.clean, self.is_initialized = "", True, False
                self._files.controls = []
                self._status_text.value = "Not a git repository"
            else:
                self.is_initialized = True
                self.branch = result.branch or ""
                self.clean = result.clean
                self._files.controls = [
                    *(_file_row(p, *_MODIFIED) for p in result.modified),
                    *(_file_row(p, *_UNTRACKED) for p in result.untracked),
                ]
                state = "clean" if self.clean else "changes pending"
                self._status_text.value = f"{self.branch or '(unborn)'} · {state}"

        self._sync_init_visibility()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    def init_repo(self) -> None:
        self._run(lambda: git_engine.init(self._vault_path))

    def stage_all(self) -> None:
        self._run(lambda: git_engine.stage_all(self._vault_path))

    def commit(self) -> None:
        message = self._message.value or ""
        if not message.strip():
            self._on_error("Enter a commit message first.")
            return
        self._run(lambda: git_engine.commit(self._vault_path, message))
        self._message.value = ""

    def push(self) -> None:
        self._run(lambda: git_engine.push(self._vault_path))

    def pull(self) -> None:
        self._run(lambda: git_engine.pull(self._vault_path))

    def _run(self, action: Callable[[], object]) -> None:
        if self._vault_path is None:
            return
        try:
            action()
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not re-raised
            self._on_error(str(exc))
            return
        self.refresh()

    def _sync_init_visibility(self) -> None:
        self._init_button.visible = self._vault_path is not None and not self.is_initialized
