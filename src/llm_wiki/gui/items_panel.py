"""Items panel: raw (completed) documents alongside the live ingestion
queue, side by side per the mockup.

Wired to `ingest.list_queue()` (Phase 8) -- one query, split client-side
by status rather than two DB round trips.

The mockup's "Ingest" toolbar chip only ever meant "colour-code this
item's current stage," so it stays a passive legend (see toolbar.py's
docstring). But that left the app with a live "compile the queue" pipeline
and no way to ever put anything into the queue in the first place -- the
CLI's `llm-wiki ingest` command does both `enqueue_file()` and
`compile_queued_item()` in one shot, and only the compile half got wired
into the GUI in 16c. The "+ Add File..." button here is the missing half:
it only stages files (`enqueue_file()`), the same as CLI `ingest` minus
the immediate compile, so the toolbar's Run/Step/Automated controls (which
already work) are what processes them.
"""

import contextlib
import sqlite3
from collections.abc import Awaitable, Callable

import flet as ft

from llm_wiki.gui import theme
from llm_wiki.ingest import list_queue
from llm_wiki.models import QueueItem, QueueStatus

# In-progress status -> badge colour. Reuses the toolbar's pipeline-stage
# palette for visual consistency, mapped to the closest matching engine
# stage (compiler_engine.py's actual transition order).
_STAGE_COLORS = {
    QueueStatus.QUEUED: theme.STAGE_INGEST,
    QueueStatus.PARSING: theme.STAGE_ATOMIZE,
    QueueStatus.ANALYZING: theme.STAGE_LINT,
    QueueStatus.CASCADE: theme.STAGE_LINK,
    QueueStatus.ERROR: theme.ERROR,
}


def _raw_row(item: QueueItem) -> ft.Control:
    return ft.Container(
        padding=ft.Padding(10, 7, 10, 7),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.CANVAS_DOT)),
        content=ft.Text(
            item.title,
            size=11.5,
            color=theme.TEXT_LIST,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )


def _queue_row(item: QueueItem) -> ft.Control:
    colour = _STAGE_COLORS.get(item.status, theme.TEXT_MUTED)
    return ft.Container(
        padding=ft.Padding(10, 7, 10, 7),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.CANVAS_DOT)),
        content=ft.Column(
            spacing=2,
            controls=[
                ft.Text(
                    item.title,
                    size=11.5,
                    color=theme.TEXT_LIST,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    item.error if item.status is QueueStatus.ERROR else item.status.value.upper(),
                    size=9.5,
                    color=colour,
                    weight=ft.FontWeight.W_600,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
        ),
    )


class ItemsPanel(ft.Container):
    """Two side-by-side lists: completed items, and the in-progress queue."""

    def __init__(
        self,
        on_add_file: Callable[[ft.Event], Awaitable[None]],
        on_check_raw: Callable[[ft.Event], None],
    ) -> None:
        super().__init__()
        self.expand = True
        self._conn: sqlite3.Connection | None = None
        self.has_vault = False
        self.raw_items: list[QueueItem] = []
        self.queue_items: list[QueueItem] = []

        self._raw_header = ft.Text("RAW ITEMS · 0", size=10.5, color=theme.TEXT_MUTED)
        self._queue_header = ft.Text("QUEUE · 0", size=10.5, color=theme.TEXT_MUTED)
        self._raw_list = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self._queue_list = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self._add_file_button = ft.Container(
            padding=ft.Padding(9, 5, 9, 5),
            bgcolor=theme.BUTTON_BG,
            border=ft.Border.all(1, theme.BORDER_STRONG),
            border_radius=5,
            disabled=True,
            on_click=on_add_file,
            content=ft.Text("+ Add File…", size=11.5, color=theme.TEXT),
        )
        self._check_raw_button = ft.Container(
            padding=ft.Padding(9, 5, 9, 5),
            bgcolor=theme.BUTTON_BG,
            border=ft.Border.all(1, theme.BORDER_STRONG),
            border_radius=5,
            disabled=True,
            on_click=on_check_raw,
            content=ft.Text(
                "Check Raw",
                size=11.5,
                color=theme.TEXT,
                tooltip="Queue any files already sitting in raw/ that aren't tracked yet",
            ),
        )

        self.content = ft.Column(
            spacing=0,
            expand=True,
            controls=[
                ft.Container(
                    padding=ft.Padding(10, 7, 10, 7),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
                    content=ft.Row(
                        spacing=6, controls=[self._add_file_button, self._check_raw_button]
                    ),
                ),
                ft.Row(
                    spacing=0,
                    expand=True,
                    controls=[
                        ft.Container(
                            expand=True,
                            border=ft.Border.only(right=ft.BorderSide(1, theme.BORDER)),
                            content=ft.Column(
                                spacing=0,
                                expand=True,
                                controls=[
                                    ft.Container(
                                        padding=ft.Padding(10, 8, 10, 8),
                                        bgcolor=theme.PANEL_HEADER_BG,
                                        content=self._raw_header,
                                    ),
                                    self._raw_list,
                                ],
                            ),
                        ),
                        ft.Container(
                            expand=True,
                            content=ft.Column(
                                spacing=0,
                                expand=True,
                                controls=[
                                    ft.Container(
                                        padding=ft.Padding(10, 8, 10, 8),
                                        bgcolor=theme.PANEL_HEADER_BG,
                                        content=self._queue_header,
                                    ),
                                    self._queue_list,
                                ],
                            ),
                        ),
                    ],
                ),
            ],
        )

    def set_connection(self, conn: sqlite3.Connection | None) -> None:
        self._conn = conn
        self.has_vault = conn is not None
        self._add_file_button.disabled = not self.has_vault
        self._check_raw_button.disabled = not self.has_vault
        self.refresh()

    def refresh(self) -> None:
        items = list_queue(self._conn) if self._conn is not None else []
        self.raw_items = [item for item in items if item.status is QueueStatus.COMPLETED]
        self.queue_items = [item for item in items if item.status is not QueueStatus.COMPLETED]

        self._raw_header.value = f"RAW ITEMS · {len(self.raw_items)}"
        self._queue_header.value = f"QUEUE · {len(self.queue_items)}"
        self._raw_list.controls = [_raw_row(item) for item in self.raw_items]
        self._queue_list.controls = [_queue_row(item) for item in self.queue_items]

        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()
