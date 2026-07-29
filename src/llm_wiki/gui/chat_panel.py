"""AI Chat panel: a lightweight RAG-style test tool (Phase 15d/16d), per the
plan "for active LLM-Wiki testing" -- not a production feature. Wraps
`llm.chat.ask()` on a worker thread via `page.run_thread()`, the same
pattern as `pipeline_adapter.py`, so a live LLM call never blocks the UI.

Unlike the pipeline (where Toolbar, the status bar, and the Items panel
all need to react to the same progress stream, hence a separate
`PipelineAdapter`), chat has exactly one consumer of its state -- this
panel -- so threading and rendering are combined here rather than split
into an adapter + a view for no reason.
"""

import contextlib
from pathlib import Path

import flet as ft

from llm_wiki.gui import theme
from llm_wiki.llm import ask
from llm_wiki.llm.client import LlamaClient
from llm_wiki.llm.embeddings import DEFAULT_EMBEDDING_MODEL
from llm_wiki.storage import connect

# The right dock is user-resizable (splitter.py's ResizeHandle) and bubbles
# need to track its actual width rather than a fixed pixel value -- see
# ChatPanel._on_resized(). These bound the computed width: never narrower
# than this floor, never wider than this fraction of the available area
# (short messages still shrink to fit within that cap).
_BUBBLE_MIN_WIDTH = 160.0
_BUBBLE_MAX_FRACTION = 0.78
# Used before the panel's first layout pass (on_size_change hasn't fired
# yet) and in headless tests, where no real layout ever happens.
_BUBBLE_FALLBACK_WIDTH = 240.0


def _bubble(role: str, content: str, width: float) -> ft.Control:
    is_user = role == "user"
    return ft.Row(
        alignment=ft.MainAxisAlignment.END if is_user else ft.MainAxisAlignment.START,
        controls=[
            ft.Container(
                width=width,
                padding=ft.Padding(8, 8, 12, 8),
                bgcolor=theme.ACCENT_DEEP if is_user else theme.BUBBLE_BG,
                border_radius=10,
                content=ft.Text(
                    content,
                    width=width,
                    size=12.5,
                    color=theme.TEXT_BRIGHT if is_user else theme.TEXT_BUBBLE,
                    selectable=True,
                ),
            ),
        ],
    )


class ChatPanel(ft.Container):
    """Message history, a text input, and Send -- wired to `llm.chat.ask()`."""

    def __init__(self, page: ft.Page) -> None:
        super().__init__()
        self._page = page
        self.expand = True
        self._vault_root: Path | None = None
        self._client: LlamaClient | None = None
        self._chat_model = ""
        self._embedding_model = DEFAULT_EMBEDDING_MODEL
        self.messages: list[tuple[str, str]] = []
        self.busy = False
        self._panel_width = 0.0
        self.on_size_change = self._on_resized

        # `auto_scroll` keeps the latest message in view. A `ListView` looked
        # like the more obvious fit, but its lazy `ListView.builder` only
        # *estimates* max scroll extent from already-built items, so it did
        # not reliably reach a just-appended message. `Column` wraps its
        # children in a `SingleChildScrollView` instead -- eager layout, so
        # the real extent is known immediately. Same pattern log_panel.py
        # already uses successfully for the same "always show the newest
        # line" requirement.
        self._message_list = ft.Column(
            spacing=10, scroll=ft.ScrollMode.AUTO, auto_scroll=True, expand=True
        )
        self._typing_indicator = ft.Text(
            "assistant is typing…", size=11.5, italic=True, color=theme.TEXT_MUTED, visible=False
        )
        self._input = ft.TextField(
            hint_text="Ask about your vault…",
            expand=True,
            multiline=False,
            text_size=12.5,
            color=theme.TEXT,
            bgcolor=theme.INPUT_BG,
            border_color=theme.BORDER_STRONG,
            focused_border_color=theme.ACCENT,
            border_radius=7,
            content_padding=ft.Padding(10, 8, 10, 8),
            on_submit=self._on_submit,
        )
        self._send_button = ft.Container(
            padding=ft.Padding(14, 8, 14, 8),
            bgcolor=theme.ACCENT,
            border_radius=7,
            on_click=self._on_submit,
            content=ft.Text("Send", size=12, weight=ft.FontWeight.W_600, color=theme.APP_BG),
        )

        self.content = ft.Column(
            spacing=0,
            expand=True,
            controls=[
                ft.Container(
                    padding=12,
                    expand=True,
                    content=ft.Column(
                        spacing=10,
                        expand=True,
                        controls=[self._message_list, self._typing_indicator],
                    ),
                ),
                ft.Container(
                    padding=10,
                    border=ft.Border.only(top=ft.BorderSide(1, theme.BORDER)),
                    content=ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.END,
                        controls=[self._input, self._send_button],
                    ),
                ),
            ],
        )

    def configure(
        self,
        vault_root: str | Path,
        client: LlamaClient,
        chat_model: str,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        """Points this panel at a vault + LLM client (called on vault load)."""
        self._vault_root = Path(vault_root)
        self._client = client
        self._chat_model = chat_model
        self._embedding_model = embedding_model

    # --- Sending ----------------------------------------------------------

    async def _on_submit(self, _e: ft.Event) -> None:
        self.send_message(self._input.value or "")
        # send_message() clears `_input.value` and updates the control,
        # which drops keyboard focus -- request it back so typing the next
        # question doesn't require re-clicking into the field. Suppressed
        # while unattached, same as `_update_if_attached()` below.
        with contextlib.suppress(RuntimeError):
            await self._input.focus()

    def send_message(self, text: str) -> None:
        if self.busy or not text.strip() or self._vault_root is None or self._client is None:
            return

        self._append("user", text)
        self._input.value = ""
        self.busy = True
        self._typing_indicator.visible = True
        self._update_if_attached()

        self._page.run_thread(self._worker, text)

    # --- Worker thread ------------------------------------------------------

    def _worker(self, text: str) -> None:
        assert self._vault_root is not None
        assert self._client is not None
        conn = connect(self._vault_root / ".llm-wiki" / "db.sqlite3")
        try:
            answer = ask(
                conn,
                self._client,
                text,
                chat_model=self._chat_model,
                embedding_model=self._embedding_model,
            )
            self._page.run_task(self._dispatch_answered, answer)
        except Exception as exc:  # noqa: BLE001 -- surfaced to the panel, not re-raised
            self._page.run_task(self._dispatch_failed, str(exc))
        finally:
            conn.close()

    # --- Back on the UI thread, via page.run_task() --------------------------

    async def _dispatch_answered(self, answer: str) -> None:
        self._append("assistant", answer)
        self._finish()

    async def _dispatch_failed(self, error: str) -> None:
        self._append("assistant", f"Error: {error}")
        self._finish()

    def _finish(self) -> None:
        self.busy = False
        self._typing_indicator.visible = False
        self._update_if_attached()

    # --- Rendering ------------------------------------------------------

    def _bubble_width(self) -> float:
        # 24 == the message area's own left+right padding (see `content=`
        # in __init__) -- the actual space a bubble has to work with.
        available = self._panel_width - 24
        if available <= 0:
            return _BUBBLE_FALLBACK_WIDTH
        return max(_BUBBLE_MIN_WIDTH, min(available * _BUBBLE_MAX_FRACTION, available))

    def _on_resized(self, e: ft.LayoutSizeChangeEvent) -> None:
        self._panel_width = e.width
        width = self._bubble_width()
        self._message_list.controls = [
            _bubble(role, content, width) for role, content in self.messages
        ]
        self._update_if_attached()

    def _append(self, role: str, content: str) -> None:
        self.messages.append((role, content))
        self._message_list.controls.append(_bubble(role, content, self._bubble_width()))
        self._update_if_attached()

    def _update_if_attached(self) -> None:
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()
