"""Entry point for `flet build`.

`flet build` requires a flat module at the root of the packaged app
directory (`tool.flet.app.path`), so this hands straight off to the real
shell in `llm_wiki.gui.app`. Running the app in development still goes
through `python -m llm_wiki.gui`.
"""

import flet as ft

from llm_wiki.gui.app import main

ft.run(main)
