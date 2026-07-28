"""Session-wide test setup.

Defaults Qt to the offscreen platform plugin so the GUI test suite (Phase
15+) runs headlessly without a real display -- confirmed working in this
project's sandbox during Phase 15a. Only sets it if the environment hasn't
already picked a platform, so a real display can still be used for visual
debugging by exporting QT_QPA_PLATFORM yourself.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
