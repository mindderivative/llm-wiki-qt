"""Dashboard panel: vault-wide statistics, distinct from the per-run Health
panel's quality score (Phase 21 -- your own suggestion, deferred since
Phase 16).

Wired to `storage.get_vault_stats()`, which is a plain aggregation over
`notes`/`queue` -- no lint pass, no quality judgment, just counts.
"""

import contextlib
import sqlite3
from pathlib import Path

import flet as ft
import flet_charts as fc

from llm_wiki.gui import theme
from llm_wiki.storage import VaultStats, get_vault_stats

# Field -> (axis label, bar colour). Order fixes the bars' left-to-right order.
_NOTE_TYPES = (
    ("concepts", "Concepts", theme.STAGE_ATOMIZE),
    ("entities", "Entities", theme.ACCENT),
    ("sources", "Sources", theme.STAGE_LINK),
    ("synthesis", "Synthesis", theme.STAGE_LINT),
)

_EMPTY_STATS = VaultStats(
    concepts=0,
    entities=0,
    sources=0,
    synthesis=0,
    total_ingested=0,
    failures=0,
    total_wikilinks=0,
    total_backlinks=0,
)


class DashboardPanel(ft.Container):
    """Note-type counts, ingestion history, and link counts, as a bar chart
    plus stat cards.
    """

    def __init__(self) -> None:
        super().__init__()
        self.padding = 14
        self.expand = True
        self._conn: sqlite3.Connection | None = None
        self._vault_root: Path | None = None
        self.stats = _EMPTY_STATS

        self._body = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self.content = self._body
        self._render()

    def set_connection(
        self, conn: sqlite3.Connection | None, vault_root: str | Path | None = None
    ) -> None:
        self._conn = conn
        self._vault_root = Path(vault_root) if vault_root is not None else None
        self.refresh()

    def refresh(self) -> None:
        if self._conn is not None and self._vault_root is not None:
            self.stats = get_vault_stats(self._conn, self._vault_root)
        else:
            self.stats = _EMPTY_STATS
        self._render()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    # --- Rendering ------------------------------------------------------

    def build_chart(self) -> fc.BarChart:
        """A bar per note type, so an empty vault renders a flat axis."""
        counts = [getattr(self.stats, field) for field, _label, _colour in _NOTE_TYPES]
        return fc.BarChart(
            height=170,
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
            max_y=max([*counts, 1]),
            tooltip=fc.BarChartTooltip(bgcolor=theme.MENU_BG),
            bottom_axis=fc.ChartAxis(
                labels=[
                    fc.ChartAxisLabel(
                        value=i,
                        label=ft.Text(label, size=10, color=theme.TEXT_MUTED),
                    )
                    for i, (_field, label, _colour) in enumerate(_NOTE_TYPES)
                ],
                label_size=28,
            ),
            groups=[
                fc.BarChartGroup(
                    x=i,
                    rods=[
                        fc.BarChartRod(
                            from_y=0,
                            to_y=getattr(self.stats, field),
                            color=colour,
                            width=22,
                            border_radius=4,
                        )
                    ],
                )
                for i, (field, _label, colour) in enumerate(_NOTE_TYPES)
            ],
        )

    def _stat_cards(self) -> ft.Control:
        cards = [(label, str(getattr(self.stats, field))) for field, label, _colour in _NOTE_TYPES]
        cards += [
            ("Total Ingested", str(self.stats.total_ingested)),
            ("Failures", str(self.stats.failures)),
            ("Total WikiLinks", str(self.stats.total_wikilinks)),
            ("Total Backlinks", str(self.stats.total_backlinks)),
        ]
        return ft.Row(
            wrap=True,
            spacing=9,
            run_spacing=9,
            controls=[
                ft.Container(
                    width=132,
                    bgcolor=theme.CARD_BG,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=8,
                    padding=11,
                    content=ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(
                                value,
                                size=18,
                                weight=ft.FontWeight.W_700,
                                color=theme.TEXT_STAT,
                            ),
                            ft.Text(label, size=10.5, color=theme.TEXT_INACTIVE),
                        ],
                    ),
                )
                for label, value in cards
            ],
        )

    def _render(self) -> None:
        self._body.controls = [
            ft.Text(
                "VAULT DASHBOARD",
                size=11,
                color=theme.TEXT_MUTED,
                weight=ft.FontWeight.W_500,
            ),
            ft.Container(height=8),
            ft.Text("NOTES BY TYPE", size=11, color=theme.TEXT_MUTED),
            ft.Container(height=8),
            self.build_chart(),
            ft.Container(height=16),
            self._stat_cards(),
        ]
