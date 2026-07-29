"""Health panel: vault score plus a findings-by-kind breakdown.

Wired to `lint.run_lint()` (Phase 11), which persists each run. The mockup
shows the score as a big number over a progress bar with a grid of stat
cards; the findings breakdown is a `flet-charts` bar chart, which is what
the Flutter build flow was adopted for.
"""

import contextlib
import sqlite3

import flet as ft
import flet_charts as fc

from llm_wiki.gui import theme
from llm_wiki.lint import run_lint
from llm_wiki.models import LintFindingKind

# Kind -> (axis label, bar colour). Order fixes the bars' left-to-right order.
_KINDS = (
    (LintFindingKind.SCHEMA_VIOLATION, "Schema", theme.STAGE_LINT),
    (LintFindingKind.BROKEN_LINK, "Links", theme.ERROR),
    (LintFindingKind.ISOLATED_NOTE, "Isolated", theme.STAGE_ATOMIZE),
)


def _score_colour(score: int) -> str:
    if score >= 80:
        return theme.STAGE_LINK
    return theme.STAGE_LINT if score >= 50 else theme.ERROR


class HealthPanel(ft.Container):
    """Score, stat cards, and a findings-by-kind bar chart."""

    def __init__(self) -> None:
        super().__init__()
        self.padding = 14
        self.expand = True
        self._conn: sqlite3.Connection | None = None
        self.score = 100
        self.counts: dict[LintFindingKind, int] = dict.fromkeys(
            (kind for kind, _label, _colour in _KINDS), 0
        )

        self._body = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        self.content = self._body
        self._render()

    def set_connection(self, conn: sqlite3.Connection | None) -> None:
        self._conn = conn
        self.refresh()

    def refresh(self) -> None:
        """Runs a fresh lint pass (persisted, per Phase 11) and redraws."""
        if self._conn is None:
            self.score = 100
            self.counts = dict.fromkeys(self.counts, 0)
        else:
            report = run_lint(self._conn)
            self.score = report.score
            self.counts = {
                kind: sum(1 for f in report.findings if f.kind is kind)
                for kind, _label, _colour in _KINDS
            }

        self._render()
        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.update()

    # --- Rendering ----------------------------------------------------------

    def build_chart(self) -> fc.BarChart:
        """A bar per finding kind, so an empty vault renders a flat axis."""
        return fc.BarChart(
            height=170,
            border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
            max_y=max([*self.counts.values(), 1]),
            tooltip=fc.BarChartTooltip(bgcolor=theme.MENU_BG),
            bottom_axis=fc.ChartAxis(
                labels=[
                    fc.ChartAxisLabel(
                        value=i,
                        label=ft.Text(label, size=10, color=theme.TEXT_MUTED),
                    )
                    for i, (_kind, label, _colour) in enumerate(_KINDS)
                ],
                label_size=28,
            ),
            groups=[
                fc.BarChartGroup(
                    x=i,
                    rods=[
                        fc.BarChartRod(
                            from_y=0,
                            to_y=self.counts[kind],
                            color=colour,
                            width=22,
                            border_radius=4,
                        )
                    ],
                )
                for i, (kind, _label, colour) in enumerate(_KINDS)
            ],
        )

    def _stat_cards(self) -> ft.Control:
        cards = [("Health Score", str(self.score))]
        cards += [(label, str(self.counts[kind])) for kind, label, _colour in _KINDS]
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
                "VAULT HEALTH SCORE",
                size=11,
                color=theme.TEXT_MUTED,
                weight=ft.FontWeight.W_500,
            ),
            ft.Container(height=8),
            ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.END,
                controls=[
                    ft.Text(
                        str(self.score),
                        size=30,
                        weight=ft.FontWeight.W_700,
                        color=_score_colour(self.score),
                    ),
                    ft.Text("/ 100", size=12, color=theme.TEXT_SUBTLE),
                ],
            ),
            ft.Container(height=8),
            ft.ProgressBar(
                value=self.score / 100,
                bgcolor=theme.INPUT_BG,
                color=_score_colour(self.score),
                border_radius=3,
                height=6,
            ),
            ft.Container(height=18),
            ft.Text("FINDINGS BY KIND", size=11, color=theme.TEXT_MUTED),
            ft.Container(height=8),
            self.build_chart(),
            ft.Container(height=16),
            self._stat_cards(),
        ]
