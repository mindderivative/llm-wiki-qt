"""The desktop UI's colour palette, ported from the design mockup.

The mockup specifies every colour in `oklch()`, which Flet has no support
for, so the values below are the exact sRGB conversions. The `oklch(...)`
comment on each line is the original authored value -- keep it, it's what
makes a future re-sync against the mockup checkable.
"""

import flet as ft

# Surfaces, darkest to lightest.
TERMINAL_BG = "#040405"  # oklch(11% 0.004 260)
CANVAS_BG = "#070709"  # oklch(13% 0.004 260)
APP_BG = "#0A0B0D"  # oklch(15% 0.005 260)
PANEL_BG = "#101214"  # oklch(18% 0.006 260)
CHROME_BG = "#121417"  # oklch(19% 0.006 260)
PANEL_HEADER_BG = "#141619"  # oklch(20% 0.007 260)
CARD_BG = "#16181C"  # oklch(21% 0.008 260)
MENU_BG = "#181B1E"  # oklch(22% 0.008 260)
ROW_HOVER = "#1B1D21"  # oklch(23% 0.008 260)
CANVAS_DOT = "#1D1F23"  # oklch(24% 0.008 260)
BUTTON_BG = "#1D1F24"  # oklch(24% 0.009 260)
BUBBLE_BG = "#1F2226"  # oklch(25% 0.009 260)
INPUT_BG = "#24272B"  # oklch(27% 0.01 260)

# Lines and borders.
BORDER = "#2A2E34"  # oklch(30% 0.012 260)
BORDER_STRONG = "#2F3339"  # oklch(32% 0.012 260)
BORDER_DASHED = "#3E434A"  # oklch(38% 0.014 260)
# Deliberately lighter than the borders above -- a 1.2px line at their
# lightness was invisible against CANVAS_BG's near-black; a graph edge
# needs more contrast than a panel divider does.
GRAPH_EDGE = "#4E535A"  # oklch(44% 0.013 260)

# Text, dimmest to brightest.
TEXT_DIM = "#5B5E62"  # oklch(48% 0.008 260)
TEXT_MUTED = "#616368"  # oklch(50% 0.008 260)
TEXT_PLACEHOLDER = "#6E7278"  # oklch(55% 0.01 260)
TEXT_INACTIVE = "#787A7F"  # oklch(58% 0.008 260)
TEXT_SUBTLE = "#7D8086"  # oklch(60% 0.01 260)
TEXT_SECONDARY = "#8C8F94"  # oklch(65% 0.008 260)
TEXT_TOGGLE_OFF = "#9B9FA3"  # oklch(70% 0.008 260)
TEXT_LOG = "#ABAEB3"  # oklch(75% 0.008 260)
TEXT_LIST = "#B5B7BB"  # oklch(78% 0.006 260)
TEXT_RECENT = "#BBBEC2"  # oklch(80% 0.006 260)
TEXT_BUBBLE = "#CBCED2"  # oklch(85% 0.006 260)
TEXT_NODE = "#D5D8DB"  # oklch(88% 0.006 260)
TEXT_STAT = "#DCDEE1"  # oklch(90% 0.005 260)
TEXT = "#E6E8EA"  # oklch(93% 0.004 260)
TEXT_BRIGHT = "#F0F2F4"  # oklch(96% 0.004 260)

# Accent -- also the Ingest stage colour and the active-tab underline.
ACCENT = "#AC77FA"  # oklch(68% 0.19 300)
ACCENT_DEEP = "#623E96"  # oklch(45% 0.14 300)
ERROR = "#F05653"  # oklch(66% 0.19 25)

# Pipeline stages.
STAGE_INGEST = ACCENT
STAGE_ATOMIZE = "#00B2DE"  # oklch(70% 0.15 220)
STAGE_LINK = "#35C177"  # oklch(72% 0.16 155)
STAGE_LINT = "#DFA11A"  # oklch(75% 0.15 80)

# Post-26 fix -- the graph canvas Settings panel's category swatches.
# Lives here (not graph_canvas.py) since build_theme() below also used
# to key the Slider thumb size off it. Material 3's own Switch spec
# (24dp "on"-thumb diameter, scaled by graph_canvas._COMPACT_SWITCH_
# SCALE) was the starting point; tuned from there against real user
# feedback.
CATEGORY_SWATCH_DIAMETER = 14.0
# Slider thumb size -- decoupled from the swatch diameter above once
# `_CATEGORY_SWATCH_DIAMETER`'s own value only produced a slight, hard-
# to-judge reduction; tuning independently now that the mechanism itself
# (page.theme's slider_theme) is confirmed genuinely reaching the widget.
SLIDER_THUMB_DIAMETER = 8.0


def build_theme() -> ft.Theme:
    return ft.Theme(
        color_scheme=ft.ColorScheme(primary=ACCENT, surface=APP_BG, error=ERROR),
        # A Slider has no per-instance thumb-size property, only a Theme
        # one -- confirmed (via direct testing, not Container.theme
        # nesting, which turned out not to reliably apply at all in this
        # app) that a page-wide Theme's own slider_theme is what actually
        # reaches the widget. Safe as an app-wide setting: this app has
        # no Slider anywhere except the graph canvas Settings panel.
        # Confirmed live via a temporary thumb_color=GREEN diagnostic --
        # the theme genuinely reaches the widget, so thumb_size is a real,
        # working lever, not a coincidence. No thumb_color override here:
        # it inherits color_scheme.primary above, matching every other
        # accent-colored control in the app.
        slider_theme=ft.SliderTheme(
            thumb_size=ft.Size.square(SLIDER_THUMB_DIAMETER),
            year_2023=True,
        ),
    )
