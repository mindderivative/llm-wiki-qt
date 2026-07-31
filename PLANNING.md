# Phase 16 — Flet Desktop UI Rebuild (supersedes the Phase 15 QML shell)

## Context

Phase 15 shipped a complete PySide6 + QML desktop shell (menu/toolbar,
3-dock layout, graph canvas, Queue/Git/Health/Chat/Log panels) — already
committed and pushed to `origin/main`. After using it, the user asked for
an honest comparison against **Flet** and, after a researched back-and-forth,
explicitly decided to migrate: the deciding factor was boilerplate, not any
single missing control. `AppController` (`src/llm_wiki/gui/app_controller.py`)
is the clearest example — ~10 settings fields each need a
getter/setter/`Property(notify=...)` triplet just to round-trip a string to
QML. Flet's direct-call + `page.update()` model removes that ceremony
entirely.

Immediately after that decision, the user supplied a **Claude Design
mockup** (project `LLM-Wiki Desktop UI Prototype`,
`dc6b60d3-5584-40c3-9540-82f0e3332b50`, file `LLM-Wiki Desktop UI.dc.html`)
built with real content and interaction logic (draggable graph nodes,
tabbed panels, a running pipeline-stage simulation, a seeded chat
exchange) and confirmed: **this mockup is the target design for the Flet
rebuild** — not a separate reskin. It resolves several things left open by
the original Flet decision, and introduces a bit of new scope:

- **Dock-area interaction**: settled, and better than first assumed. The
  earlier plan was `ExpansionPanel`/fixed proportions, on the belief that
  Flet cannot do resizable panes. The user then pointed at
  `flet-contrib`'s `VerticalSplitter`, which showed the technique: a
  `GestureDetector` over a divider that resizes its neighbour — no
  compiled Flutter widget needed. The package itself was installed and
  **empirically rejected**: it constructs, but every real drag raises
  `AttributeError: 'DragUpdateEvent' object has no attribute 'delta_x'`
  (0.86 replaced `delta_x` with `local_delta`; the crash hides behind a
  short-circuit in a naive test). It is unmaintained (2024.12.3, pinned
  `flet<1,>=0`, all six controls pre-0.80). So the technique is kept and
  the dependency dropped: `gui/splitter.py`'s `ResizeHandle` (~40 lines,
  both axes, min/max clamping, unit-tested headlessly).
  **Consequence: the dock layout gets genuine drag-to-resize parity with
  the old QML `SplitView`** — no fixed-proportion compromise, no
  accordion. Tab strips still switch panels *within* each dock area, per
  the mockup.
- **Terminal panel**: the mockup includes a live pseudo-terminal tab, and
  we agreed to defer it on the grounds that no embeddable terminal existed
  for Flet. **That premise turned out to be wrong** — the user found
  [`flet-terminal`](https://github.com/Nwokike/flet-terminal), which is
  real: on PyPI at 0.2.2 (uploaded 2026-07-24), requires `flet>=0.85.3`,
  wraps `xterm.dart`, and uses a real PTY (`pty`/`winpty`).
  The catch is structural, not quality: the wheel ships a **Flutter/Dart
  package** (`flutter/flet_terminal/`, depends on `xterm: ^4.0.0` and the
  Flutter SDK), so it cannot render through the prebuilt `flet-desktop`
  client. Using it requires installing the Flutter SDK and switching the
  run/dev flow from `python -m llm_wiki.gui` to a `flet build` step. That
  cost buys every other Flutter-backed extension too (`flet-charts`,
  `flet-code-editor`).
  **Status: still deferred for now** (16a-16d ship without it), but the
  decision is now a real cost/benefit call rather than an impossibility —
  revisit once the rest of the UI is working.
- **MCP server controls**: the mockup's toolbar has Start/Stop/Restart
  buttons and a live status dot for the MCP server. Today
  `src/llm_wiki/mcp/server.py` only runs as a standalone blocking process
  (`mcp.run(transport=...)` via its own CLI entry) — nothing in the engine
  or GUI can control it from within the app. Confirmed with you: **this is
  in scope**, as a small new engine-level primitive (see 16c below).

The engine (`src/llm_wiki/{vault,ingest,llm,compiler,graph,lint,vcs,
storage}`, plus `pipeline_runner.py` and `llm/chat.py` from the original
Phase 15c/d) is unaffected — this phase only rebuilds `src/llm_wiki/gui/`.

## Design summary (from the mockup)

Dark graphite/purple theme (oklch palette — precompute hex equivalents
once, Flet has no native oklch support). Layout:

- **Menu bar**: File (New/Open/Recent/Exit), Edit (Settings…), Tools
  (Reindex Vault, Rebuild Graph, Clear Cache, Open Data Folder), View
  (Toggle Left/Right/Bottom Panel, Zoom to Fit), Help, vault name at the
  right edge.
- **Toolbar**: Automated/Manual segmented toggle, batch-size number input,
  4 stage chips (Ingest/Atomize/Link/Lint — dimmed and non-interactive
  under Automated mode), MCP Start/Stop/Restart + pulsing status dot.
- **Body grid** (`280px | 1fr | 320px` columns × `1fr | 220px` rows —
  those are now starting sizes, with `ResizeHandle` dividers between the
  columns and above the bottom dock):
  - Left column (full height): **Items** tab (two side-by-side lists —
    completed/raw items, and in-progress queue items with a colored stage
    badge) / **Git** tab (Stage All/Commit/Push/Pull + changed-files list
    with M/A/D badges).
  - Center column: graph canvas (top) with a category-color legend
    overlay (top-left) and zoom −/fit/+ controls (bottom-right), draggable
    nodes; **Pipeline Log** dock (bottom) — Terminal tab omitted per above.
  - Right column (full height): **Health** tab (score + progress bar + 2×3
    stat-card grid — replaces the original plan's `QtGraphs` charts with
    this simpler card layout, a simplification not a regression) / **AI
    Chat** tab (message bubbles, right-aligned purple for user, textarea +
    Send).
- **Status bar**: current file, current stage (color-coded), progress bar
  + %, MCP status, active-set size.
- **Settings dialog**: tabbed General / AI Provider / LLM, same
  `AppSettings` fields the current `app_controller.py` already exposes.
- **Vault dialog**: one tabbed Open/New dialog, replacing the separate
  `NewVaultDialog.qml`/`OpenVaultDialog.qml`.

## Build flow change (mid-16a, user-directed)

The user chose to install the Flutter SDK and move to a `flet build` flow to
get `flet-charts`. Confirmed necessary: `flet-charts` ships real Dart
(`bar_chart.dart`, `line_chart.dart`, a matplotlib canvas, …), so it cannot
render through the prebuilt `flet-desktop` client.

- **Flutter 3.44.8** installed to `~/flutter` from the official tarball — no
  root needed, and no system packages were missing (clang, cmake, ninja,
  pkg-config, gtk3 headers, base-devel were all already present).
  `flutter doctor` reports the Linux toolchain green; Android/Chrome are ✗
  and irrelevant here. Telemetry disabled. PATH persisted in
  `~/.config/fish/config.fish` (backup: `config.fish.bak`).
- **`[tool.flet]`** in `pyproject.toml` points at `src/main.py`, a thin
  entry module `flet build` requires (it needs a flat module at the app
  root). Dev still runs `python -m llm_wiki.gui`.
- **Python floor lowered 3.14 → 3.13.** The first build failed on
  `No matching distribution found for outlines>=1.3.2` — `outlines` declares
  `<3.14` and there is no release supporting 3.14, while `flet build` picks
  the *highest* version satisfying `requires-python`. `outlines` is load
  bearing (`llm/extraction.py`, Phase 7), so it cannot be dropped.
  Testing 3.13 in a scratch copy surfaced the only real blocker: the project
  had an undeclared dependency on **PEP 649** — two self-references inside
  class bodies (`config.py`'s `-> AppSettings`, `models.py`'s `-> Chunk`).
  Adding `from __future__ import annotations` to those two files makes all
  192 tests pass on 3.13 *and* 3.14. Net effect: the project is more
  portable, not less.
  **The build must pass `--python-version 3.13` explicitly** — flet has no
  pyproject key for it, and `requires-python = ">=3.13"` would otherwise
  resolve back up to 3.14 and reintroduce the failure.
- **Two more build blockers, both since fixed:**
  `flutter doctor` exits non-zero over the missing Android SDK / Chrome and
  `flet build` treats that as fatal → pass `--skip-flutter-doctor`.
  Then `compileall` failed over the bundled **PySide6** site-packages →
  `pyside6` moved to the dev dependency group, where it belongs now that
  the Flet shell has replaced the QML one. It stays only for the
  not-yet-ported 15b/c/d controllers and their tests; drop it at 16d.
- **Result: `Built app for Linux OK`.** `build/linux/llm-wiki`, 256 MB,
  launches clean. Verified `flet-charts`' Dart package is extracted to
  `build/flutter-packages/flet_charts` and linked in the generated
  `build/flutter/pubspec.yaml` — so the charts really are compiled into
  the client, not silently absent.
- **The Health panel is done early**, as the thing that justified all of
  this: `gui/health_panel.py` renders the mockup's score + progress bar +
  stat cards, plus a `flet-charts` `BarChart` of findings by kind over
  `lint.run_lint()`. It moves out of 16b, which keeps only Items/Git/Log.

## Staged delivery

Same one-sub-phase-at-a-time rhythm as every prior phase — each sub-phase
lands, gets verified, and only then does the next start. Old QML/PySide
files and tests are deleted **within the sub-phase that replaces them**,
not in one big-bang cleanup, so the tree stays buildable throughout.

### 16a — Flet shell, theme, graph canvas, settings/vault dialogs

- **Done.** Dependency is `flet[desktop]` — base `flet` alone has no
  desktop client; `flet-desktop` ships the prebuilt Flutter binary that
  makes `python -m llm_wiki.gui` open a window with no Flutter SDK needed.
- **Testing approach, settled empirically:** Flet's own `flet test` /
  `flet.testing` fixtures provision a Flutter test host and need the
  Flutter SDK, which is not installed here — so that route is unavailable.
  But Flet controls are plain dataclasses that compose fully headlessly,
  including subclassing, so the layer is tested by building real control
  trees and asserting on their structure. This is genuine coverage, not a
  smoke test: it caught two real bugs on first run (`e.local_x` and
  `e.delta_x` do not exist in 0.86 — both are `e.local_position.x` /
  `e.local_delta.x`), either of which would have broken dragging at
  runtime. One constraint it imposes: `control.update()` raises
  `RuntimeError` while unattached, so every refresh path wraps it in
  `contextlib.suppress(RuntimeError)`.
- New `src/llm_wiki/gui/theme.py` — single source of truth for the
  mockup's palette (background shades, accent purple, per-stage colors)
  and the oklch→hex conversions, so no panel repeats literal color values.
- `app.py` — Flet entry point (`ft.app(target=main)`), builds the root
  `Page`, applies the theme.
- `app_controller.py` rebuilt as a plain Python service object — same
  vault-lifecycle/settings responsibilities as today's file, delegating
  straight to `vault`/`config` (Phase 1/3), but with direct methods
  instead of the `Property`/`Slot`/signal triplets.
- `graph_canvas.py` — Flet `Canvas`/`GestureDetector` port of
  `graph_canvas_item.py`'s logic (off-thread `networkx.spring_layout` via
  `graph.get_graph_data()`, Phase 10), matching the mockup's node/edge
  rendering, legend overlay, zoom controls, and drag-to-reposition.
- Menu bar via Flet `MenuBar`/`SubmenuButton`/`MenuItemButton` matching
  the mockup's exact File/Edit/Tools/View/Help items.
- `SettingsDialog` and the tabbed vault-open/new dialog via Flet
  `AlertDialog` + `Tabs`, bound to the same `AppSettings` fields.
- **Manual verification (you):** launch the app, compare against the
  mockup.

### 16b — Data panels: Items/Queue, Git, Pipeline Log — done

Health shipped early (see the build-flow section above), so this landed as
three panels rather than four.

- `gui/items_panel.py` — left "Items" tab, two side-by-side lists from one
  `ingest.list_queue()` call (Phase 8), split client-side on
  `QueueItem.status`: `COMPLETED` → Raw Items (plain), everything else →
  Queue, with a stage-colour badge reusing the toolbar's palette, mapped
  to `compiler_engine.py`'s actual transition order (QUEUED → PARSING →
  ANALYZING → CASCADE → COMPLETED/ERROR) rather than the mockup's fictional
  demo labels.
- `gui/git_panel.py` — left "Git" tab, wired to `vcs.git_engine` (Phase 4,
  unchanged): Stage All/Commit/Push/Pull, an Init Repo affordance shown
  only pre-init, and a changed-files list with the mockup's M/A/D badges.
  One deliberate departure from the mockup: it added a commit-message
  `TextField` above the buttons, since the mockup's Commit button is
  static demo data and a real commit needs a message. All engine failures
  (no repo yet, empty message, ...) go through an `on_error` callback, not
  an exception into the shell — same pattern the old `GitController` used.
- `gui/log_bridge.py` + `gui/log_panel.py` — bottom "Pipeline Log" tab.
  The bridge is the same single-shared-sink pattern as the old
  `log_model.py` (one `loguru.logger.add()`, fanned out to every
  subscriber, so multiple panels never double-register), rebuilt as a
  plain `subscribe(callback)` function instead of a Qt signal. The panel
  colour-codes by loguru level (INFO/SUCCESS/WARNING/ERROR/...) rather
  than the mockup's fictional per-line stage colours, since that's the
  only thing a generic bridge actually has to key off.
- Deleted the superseded `queue_model.py`, `git_controller.py`,
  `log_model.py`, and rewrote `tests/test_gui_panels.py` against the three
  new panels (dropped the already-ported Health tests, since those moved
  to `test_gui_shell.py` when Health shipped early).
- Verification: 198 tests pass, ruff clean, app launches clean from
  source. Not yet re-verified through a `flet build` bundle — do that
  before signing off 16b, same as 16a's DoD.

### 16c — Pipeline automation toolbar + MCP process controls + status bar — done

- `gui/pipeline_adapter.py` rebuilt for Flet: `page.run_thread()` runs the
  batch on a worker thread (replacing `QThread`); progress callbacks hop
  back to the UI thread via `page.run_task()`, which schedules through
  `asyncio.run_coroutine_threadsafe()` — necessary because `Page.update()`
  ultimately does a bare `asyncio.Queue.put_nowait()` that assumes the
  event-loop thread, so touching a control directly from the worker thread
  would be unsafe. `pipeline_runner.py` itself is untouched.
- `gui/toolbar.py` — Automated/Manual pill toggle, batch size, an
  Ingest/Atomize/Link/Lint stage legend (kept non-interactive: the mockup's
  dim/bright-on-mode-switch implies per-stage manual actions, but
  `step_one()` only ever runs an item through the full compile, not one
  stage — there's no engine primitive to wire a click to), and MCP
  Start/Stop/Restart + a status dot. Pause/resume/stop were added beyond
  the mockup (it has none — its progress bar just animates on a timer,
  being a static prototype) since `pipeline_runner.py` already supports
  them and Phase 15c shipped them; dropping a working, tested capability
  during a UI port isn't a call to make silently.
- `mcp/process.py` (new, engine-level, Flet-free) — `McpProcess` wraps
  `python -m llm_wiki.mcp.server` as a managed `subprocess.Popen` with
  start/stop/restart + a `running` liveness property. Required extending
  `mcp/server.py`'s `create_mcp_server()`/`main()` with `--host`/`--port`
  (previously hardcoded), so the subprocess launcher can honor
  `AppSettings.mcp_server`.
- Status bar: progress bar/%, MCP status, and "Active set: N notes" added
  alongside the existing current-file/stage fields.
- Two real bugs, both fixed:
  - `flet build` runs its own pip resolve against `mcp>=1.28.1` (an open
    range) rather than honoring `uv.lock`, and picked `mcp==2.0.0` — which
    removed `mcp.server.fastmcp`, breaking the built bundle at import time
    (`compileall` only compiles, it doesn't execute imports, so this
    never surfaced at build time, only at launch). Fixed by adding an
    upper bound, `mcp>=1.28.1,<2.0.0`.
  - Not a code bug, but worth recording: an integration test open-vaulting
    directly from the test thread hit `sqlite3.ProgrammingError` from a
    pipeline callback running on a different thread. Root cause was the
    test, not the app — real Flet control events (`dispatch_event`) are
    always scheduled via `asyncio.create_task` on the session's own event
    loop, the same thread `page.run_task()` targets, so `AppController.conn`
    is always created and read on that one thread in the actual app. Fixed
    by driving the test's vault-open and toolbar actions through the fake
    page's loop thread too, matching real dispatch.
- Tests: `mcp/process.py` spawns the real subprocess against a fixture
  vault (6 tests); `pipeline_adapter.py` and the Shell/toolbar integration
  use a `_FakePage` double that genuinely crosses a thread boundary (real
  background thread + a dedicated event-loop thread via
  `run_coroutine_threadsafe`), not a synchronous stub, since the whole
  point is the thread-crossing being correct.
- Verified via `flet build linux`: 213 tests pass, ruff clean, bundle
  launches with no console output beyond the benign GTK/OpenGL lines
  already known from 16a/16b.
- **Manual verification (you):** run a real batch, confirm
  pause/resume/stop, and confirm MCP start/stop/restart against a real
  client.

## Post-16c fix: the missing ingest entry point

Your manual QA pass found Run/Step/Pause/Stop all appeared to do nothing,
and no llama-server activity occurred during any test. Root cause,
confirmed by staging a real item directly into a repro vault and then
Step-ing it in the built bundle successfully: **the compile-side wiring
was correct all along; there was just no way to get anything into the
queue from the GUI.** The CLI's `llm-wiki ingest` does `enqueue_file()` +
`compile_queued_item()` in one call; only the compile half (Run/Step/
Automated) got wired into the GUI in 16c. Items/Health/Git/Log's tests all
passed because they only ever exercised an *already*-populated queue.

Three pieces close the gap, all engine-first per Design Principle 5:

- **`ingest.scan_raw_directory()`** (new) — finds files sitting in `raw/`
  that aren't tracked by any queue row and registers them in place, no
  copy (unlike `enqueue_file()`, which archives+copies -- wrong for a file
  that's already exactly where `compile_queued_item()` expects it).
- **Items panel "+ Add File…"** — a real `ft.FilePicker` (added to
  `page.overlay`), wired to `enqueue_file()`. `FilePicker.pick_files()` is
  `async`; Flet's own event dispatcher (`_trigger_event`) awaits
  `on_click` directly when it's a coroutine function, so no `run_task`
  wrapping is needed for the click itself.
- **Items panel "Check Raw"** — a button calling `scan_raw_directory()`
  on demand, for files dropped in outside the app.
- **`ingest.RawWatcher`** (new, follow-up per your own suggestion) — a
  debounced `watchdog` observer over `raw/`, finally giving the Settings
  dialog's existing "Watch raw/ for new files" checkbox (`auto_watch_raw`)
  real behavior instead of doing nothing. Starts/stops with the vault and
  re-syncs when Settings is saved, so toggling it takes effect without
  reopening the vault. `watchdog` was already a transitive dep of
  `flet[cli]`; added directly since the engine layer now imports it.
  Fixed one real bug found via a test-teardown warning: `stop()` didn't
  cancel a pending debounce timer, so a change seen just before `stop()`
  could still fire the callback afterward — regression-tested.

All three follow the same thread-safety pattern `pipeline_adapter.py`
established: `RawWatcher`'s callback and the file picker's result handler
both eventually touch `AppController.conn`, so they hop through
`page.run_task()`/Flet's own coroutine-aware dispatch rather than touching
it directly.

**Follow-up fix, caught on your next test pass**: the built app rendered a
red "Unknown Control FilePicker" error pane. `FilePicker` is a `Service`
(confirmed against `flet.dev/docs/services/filepicker` and the cached Dart
package's `flet_core_extension.dart`, which does register a `"FilePicker"`
case — so the widget genuinely exists, this wasn't a missing-package
problem like `flet-charts`), and `Page` exposes a separate `page.services`
list (attached to the root view's service lifecycle) distinct from
`page.overlay` (for on-screen widgets like `SnackBar`). It had been added
to `overlay`; moved to `services`, with a regression test asserting where
it's registered. **Zenity is also required** on Linux desktop for the
native file dialog (already installed on your machine, so this build
didn't need it) — documented in the README as a prerequisite, `sudo
apt-get install zenity` on Debian/Ubuntu.

### 16d — AI Chat panel — done

- `gui/chat_panel.py` — message list matching the mockup's bubble styling
  (right-aligned purple for the user, dark for the assistant), a text
  input + Send, wired to the existing `llm.chat.ask()` (unchanged) via a
  `page.run_thread()` worker + `page.run_task()` dispatch back to the UI
  thread -- the same pattern `pipeline_adapter.py` established in 16c.
  Combined into one class rather than split into an adapter + a view: chat
  has exactly one UI consumer of its state (unlike pipeline progress,
  which fans out to the toolbar, status bar, and Items panel), so a split
  would be unjustified complexity here.
- One new wrinkle, specific to this panel: `ft.Container` (and its base
  `ft.Control`) already defines a **read-only** `page` property reflecting
  live page attachment. `ChatPanel` is the first panel that needed to
  *store* a page reference for threading, and `self.page = page` collided
  with that reserved name (`AttributeError: property 'page' ... has no
  setter`). Fixed by naming the stored reference `self._page` instead.
  No other panel (`ItemsPanel`, `GitPanel`, `HealthPanel`) had hit this,
  since none of them needed `page` before.
- Failures surface as an inline "Error: ..." assistant-style bubble rather
  than a modal dialog (`Shell._show_error`) -- a popup per failed query
  would be far more intrusive for a chat UI than an inline failed-message
  bubble, the pattern most chat apps use.
- Wired into `app.py`: `Shell` now builds `ChatPanel(page)` in place of the
  old right-dock placeholder, and calls `chat_panel.configure(vault_path,
  client, chat_model)` in `_on_vault_changed()` alongside the existing
  `pipeline_adapter.configure(...)` call.
- Deleted `chat_controller.py` -- the last remaining PySide6 GUI file.
  Also found and deleted `health_controller.py`, a PySide6 leftover from
  before `health_panel.py` shipped early (see the build-flow section
  above) that had been dead code (zero imports anywhere) this whole time.
  With both gone, `pyside6`/`pytest-qt` are removed entirely from
  `pyproject.toml`'s dev dependency group (confirmed via `uv lock`/
  `uv sync`: both packages and `shiboken6`/`pyside6-addons`/
  `pyside6-essentials` uninstall cleanly), and the now-dead
  `PySide6.QtCore.QModelIndex` entry is dropped from ruff's
  `flake8-bugbear.extend-immutable-calls`. **The project has zero PySide6
  dependency, dev or runtime, for the first time since Phase 15.**
- Tests: `test_gui_chat.py` rewritten against `ChatPanel` (was
  `ChatController`/`ChatMessageModel`), using the same real-thread-crossing
  `_FakePage` double as `test_gui_pipeline.py`/`test_gui_toolbar.py`.
  Covers: user-then-assistant append flow, empty/whitespace input ignored,
  a second send ignored while busy, a no-op before `configure()`, the
  inline-error failure path, input-field clearing on submit, and the
  typing indicator's visibility tracking `busy`.
- Verified: 250 tests pass, ruff clean, `uv sync` confirms PySide6 is gone
  from the environment. Not yet re-verified through a `flet build` bundle
  -- do that before signing off 16d, same DoD as every prior sub-phase.
- **Manual verification (you):** rebuild the bundle, open a vault with a
  running llama-server, and confirm the AI Chat panel: send a message,
  get a grounded answer back, and check what happens if the LLM call
  fails (e.g. stop llama-server mid-conversation) -- it should show an
  inline "Error: ..." bubble rather than crash or freeze the input.

## Post-16c fix: progress bar reset + a genuinely empty Pipeline Log

Your Add File → Step test surfaced two more real gaps, both fixed:

- **Progress bar stuck at 0%.** `_on_item_completed` (sets 100%) and
  `_on_run_finished` (used to reset to 0%/Idle) fire back-to-back on the
  same event-loop thread with no yield between them — `PipelineAdapter
  ._worker()` schedules both `run_task()` calls in immediate succession —
  so the completed frame was overwritten before the client ever rendered
  it. `_on_run_finished` no longer resets the status bar; it now leaves
  the last item's result showing until the next run's `on_run_started`
  legitimately zeroes the progress bar for new work. Regression-tested
  directly (call the handlers in sequence, assert the state survives).
- **Pipeline Log stayed empty on a real, successful run.** Root cause:
  `compiler_engine.py` had zero loguru calls — ARCHITECTURE.md documents
  loguru as "the logging backbone," but nothing in the actual pipeline
  ever called it, a gap flagged and left unaddressed back in the original
  Phase 15 build. Every panel's tests passed because mocked-LLM tests
  don't care whether anything got logged. Instrumented the real action
  points with `logger.info()` (and `logger.error()` on failure), all of
  which now reach the Pipeline Log for free through the existing
  process-wide sink: `compile_queued_item()` (per-stage: atomized,
  extracted, linked, embedded, completed/failed), `run_pipeline()` (batch
  size, stopped-early/failed summaries), `vault.manager` (create/open),
  `enqueue_file()`/`scan_raw_directory()`, `git_engine` (init/stage/
  commit/push/pull), `McpProcess` (start/stop), and `AppSettings.save()`.
  Removed the one-off `logger.error()` `_on_item_errored` had added in the
  ingest-entry-point fix, now redundant with the engine's own error log.

## Post-16c fix: genuine sub-item progress + lint visibility

You asked for real per-stage motion (Enqueued → Ingesting → Atomized →
Extracted → Linked → Embedded → Completed) instead of one 0%/100% jump
per item, and pointed out nothing shows Lint activity at all.

- **`models.CompileStage`** (new `StrEnum`): ATOMIZED/EXTRACTED/LINKED/
  EMBEDDED -- the checkpoints `compile_queued_item()` now reports via a new
  `on_stage` callback, one per stage transition. `run_pipeline()` forwards
  these through its existing `on_progress(item, event)` callback (already
  handled `str`; `CompileStage` is a `StrEnum`, no signature change needed),
  so `PipelineAdapter` gained one more hook, `on_item_stage(title, stage)`,
  alongside the existing `on_item_started`/`on_item_completed`.
- **Progress math**: `Shell._update_progress(stage_index)` combines
  completed-items-in-batch with the *current* item's stage position (0-6),
  so a single Step sweeps 1/6→2/6→...→6/6, and a multi-item Automated
  batch shows real motion *within* each item's band of the overall bar,
  not just at item boundaries. Status label follows: Ingesting → Atomized
  → Extracted → Linked → Embedded → Completed.
- **Lint visibility**: `lint_engine.run_lint()` now logs
  `"Lint pass: score N/100, M finding(s)"` -- it already ran on every
  Health refresh, just silently; this was the actual "no actions for
  Linting" gap, not a missing feature. (Lint itself stays vault-wide, not
  a per-item compile stage -- there's no real "lint this one file" step to
  report progress on, so it's not part of the 6-checkpoint sequence.)
- **Logging symmetry audit** (your rule: an error check that's logged
  needs a success log too, and vice versa): added the missing
  `logger.error()` at `vault.manager.load_vault()`'s two explicit
  failure checks and `ingest_engine.enqueue_file()`'s missing-source
  check (both already had success logs), and at `GitPanel._run()`'s
  catch-all (git actions had success logs but no failure path was ever
  logged anywhere). Confirmed `_add_file`/`_open_recent`'s catches don't
  need their own logging -- they only ever surface exceptions already
  logged by `enqueue_file()`/`load_vault()`.
- Tests: a direct unit test on `compile_queued_item()`'s `on_stage`
  sequence (plus the failure-path case: `atomize()` never touches the LLM,
  so a same-call failure still reports ATOMIZED before aborting), Shell
  fraction-math tests for both the single-item and multi-item-batch cases,
  and the existing full-pipeline integration test extended to assert the
  real stage sequence a genuine compile produces.
- Verified via `flet build linux`: 244 tests pass, ruff clean, bundle
  launches clean.

## Post-16c fix: dedicated vault dialogs, folder Browse, batch-size math — verified

Three more findings from your next test pass, all confirmed fixed on the
rebuilt bundle: New/Open Vault work as separate dialogs, path-field
copy/paste works too (likely fixed as a side effect of the field no longer
sharing a dialog/scroll context with the recent-vaults list), and the
progress bar completes correctly at both 1 and 3 queued files.

- **Vault dialogs, per your explicit UI guidance** ("all dialog calls
  should have their own dialog; tabs only when the entry point is
  singular; open/save file/folder functions get a native picker"):
  `build_vault_dialog()` (one tabbed dialog shared by two separate
  File-menu items — confirmed buggy: "New Vault..." could open on the
  "Open Vault" tab) is now two dedicated dialogs, `build_open_vault_dialog()`
  and `build_new_vault_dialog()`. Both gained a "Browse…" button using
  `ft.FilePicker.get_directory_path()` — the same picker `_add_file`
  already used for files, now also used for folders. Settings keeps its
  tabs: it has exactly one entry point (Edit > Settings…), so there's no
  "which tab" ambiguity to get wrong.
- The reported "can't paste into the path field" wasn't chased further —
  nothing in `_text_input()` blocks paste, and Browse is now the primary
  way to fill these fields regardless. Worth confirming on the next pass
  whether it's still an issue now that it's secondary.
- **Progress bar landing at 4% on a real Automated run** (1 item actually
  queued, batch size left at the default 25): confirmed root cause is
  `run_pipeline()` trims to `min(batch_size, items actually queued)`
  internally, but the GUI's `_batch_total` was set from the raw `batch_size`
  argument *before* that trimming happened — 1 item's 6/6 stages against a
  denominator of 25 is exactly 4%. Added `on_batch_size(count)`, a new
  `run_pipeline()` callback fired once, before any item starts, with the
  real count; `PipelineAdapter` forwards it, and `Shell._on_batch_size_known`
  is what now sets `_batch_total` (not `_on_run_started`, which resets it
  to 0/unknown until the real count arrives moments later).
- Tests: dialog-shape tests for both new dialogs plus the Browse-button
  path (mocking `get_directory_path()`, both the picked and the
  cancelled/`None` case), and `run_pipeline()` tests asserting
  `on_batch_size` reports the actual count against an oversized requested
  `batch_size`, and `0` when nothing's queued.
- Verified via `flet build linux`: 250 tests pass, ruff clean, bundle
  launches clean.

## Post-16d fix: bubbles didn't resize, chat didn't autoscroll — verified

Your manual QA on the built bundle found two real gaps, both confirmed
fixed on the rebuilt bundle:

- **Bubble width was a hardcoded 240px constant**, so dragging the right
  dock wider or narrower (`splitter.py`'s `ResizeHandle`) never changed
  bubble sizing -- dense responses stayed cramped even in a wide panel.
  `Container`/`Control` don't expose a percentage-of-parent width in Flet,
  but they do fire `on_size_change` with the control's real post-layout
  pixel size (`ft.LayoutSizeChangeEvent.width/height`) -- unrelated to
  `page.on_resized`, which only covers the OS window, not an internal
  dock resize. `ChatPanel` now listens on itself, and
  `ChatPanel._bubble_width()` computes each bubble's width as 78% of the
  panel's current width (minus the message area's own padding), floored
  at 160px so it never gets unreadably narrow. On resize, all existing
  bubbles are rebuilt from `self.messages` (the source of truth) at the
  new width, not just newly-sent ones.
- **No autoscroll on a new reply**, so a response could land below the
  fold with nothing indicating it arrived. `_message_list` was an
  `ft.Column`; swapped for `ft.ListView(auto_scroll=True)`, which is
  Flet's own built-in mechanism for this (a drop-in replacement -- same
  `.controls` list interface) rather than hand-rolling `scroll_to()` calls
  on every append.
- Tests: `_on_resized` fed a synthetic `LayoutSizeChangeEvent` and checked
  against both a normal-width and a very narrow panel (floor clamp), a
  fallback-width check for before any real layout pass has happened
  (`_panel_width` still at its 0 default -- headless tests never trigger
  `on_size_change`), and a type/`auto_scroll` check on `_message_list`.
- Verified via `flet build linux`: 254 tests pass, ruff clean, bundle
  launches clean.

## Post-16d fix: autoscroll still silent, Enter/Send dropped input focus — verified

Your next pass found the ListView-based autoscroll fix hadn't actually
fixed anything, and surfaced a second real bug (focus loss on send).

- **Autoscroll**: reading `flet`'s own Dart source
  (`~/.pub-cache/hosted/pub.dev/flet-0.86.4/lib/src/controls/
  {list_view,scrollable_control,column}.dart`) turned up the actual
  mechanism -- `auto_scroll` is a generic `ScrollableControl` behavior
  shared by `Column`/`Row`/`ListView`/etc, but `ListView` (unless
  `build_controls_on_demand=False`) renders via Flutter's
  `ListView.builder`, which only *estimates* `maxScrollExtent` from
  already-built (visible) items -- so `jumpTo(max)` right after appending
  a new item landed short, since the new item hadn't been measured yet.
  `Column` wraps its children in a `SingleChildScrollView` instead: eager
  layout, so the real extent is known the instant the append lands. Reverted
  `_message_list` from `ft.ListView(auto_scroll=True)` to
  `ft.Column(scroll=ft.ScrollMode.AUTO, auto_scroll=True, expand=True)` --
  the exact pattern `log_panel.py` already uses for the same "always show
  the newest line" requirement, and a better fit anyway: chat history here
  is always a handful of messages, not a long list that needs `ListView`'s
  lazy-building for performance.
- **Enter/Send dropped keyboard focus**, forcing a re-click into the field
  before typing the next question. Root cause: `send_message()` sets
  `_input.value = ""` and calls `.update()` to clear it, and syncing a
  `TextField`'s value from the Python side (rather than the user typing)
  resets its focus state on the client. `_on_submit` is now `async` and
  calls `await self._input.focus()` (Flet's own `TextField.focus()`
  method) right after `send_message()` -- Flet's dispatcher already awaits
  `on_click`/`on_submit` handlers directly when they're coroutines (the
  same mechanism the file picker's click handler relies on), so no
  `run_task` wrapping was needed. Suppressed under
  `contextlib.suppress(RuntimeError)`, matching `_update_if_attached()`'s
  existing unattached-control guard.
- Tests: the `_message_list` type check now asserts `Column` +
  `scroll=AUTO` + `auto_scroll=True` instead of `ListView`; a new test
  monkeypatches `_input.focus` to confirm `_on_submit` requests it
  regardless of path (Enter vs the Send button both dispatch through the
  same handler).
- Verified via `flet build linux`: 255 tests pass, ruff clean, bundle
  launches clean.

## Phase 17 — Graph canvas: scroll-zoom, canvas pan, node selection info, edge-visibility fix — implemented

### Context

Four issues raised against `gui/graph_canvas.py`'s spatial network view: no
scroll-wheel zoom (only the three on-canvas +/reset/− buttons work),
click-and-drag only ever moves an individual node — dragging empty canvas
background does nothing, so there's no way to pan the view; clicking a
node selects it (highlights its circle) but shows no data about it; and
link lines between nodes aren't visibly rendering at all, so every note
looks orphaned even when real `[[wikilink]]` edges exist.

Investigation (an Explore pass plus direct verification against the
installed Flet 0.86.4 API and a live repro through the real
`upsert_note_from_file → sync_links → get_graph_data → set_graph()` data
path) found the edge-drawing *logic* itself is not broken — a 3-note/
3-edge fixture correctly produced 3 `cv.Line` shapes with correct
coordinates, and edges are already appended before nodes in the shape
list (so they're already drawn underneath, not a z-order bug). Two real,
code-confirmed causes remain, both worth fixing regardless of which is
the dominant visible symptom:

- **Contrast**: `theme.GRAPH_EDGE = "#34383E"` (dark charcoal) at
  `stroke_width=1.2` drawn over `theme.CANVAS_BG = "#070709"`
  (near-black) — a very plausible "I don't see any lines at all" even
  though they're technically present, especially next to the fully
  saturated node circles.
- **A real threading violation**, the one exception to a pattern this
  codebase otherwise follows everywhere: `set_graph()` spins a raw
  `threading.Thread` (not `page.run_thread()`) whose target,
  `_compute_layout()`, calls `self._redraw()` → `self._canvas.update()`
  directly from that thread — off the Flet event-loop thread, guarded
  only by `contextlib.suppress(RuntimeError)`. Every other background-
  thread interaction in the GUI layer (`pipeline_adapter.py`,
  `chat_panel.py`) instead uses `page.run_thread()` for the worker +
  `page.run_task()` to hop back before touching a control, exactly
  because `Control.update()` assumes the event-loop thread. This can
  plausibly cause inconsistent/dropped redraws depending on timing,
  separate from the contrast issue.

### Implementation

**Edge visibility** (`gui/theme.py`, `gui/graph_canvas.py`):
- Bump `GRAPH_EDGE` to a visibly lighter value (e.g. `#565C64`, between
  the existing `BORDER_DASHED` `#3E434A` and `TEXT_DIM` `#5B5E62` on the
  same ramp) and the edge `Paint`'s `stroke_width` from `1.2` to `~1.8`.
- Fix the threading violation the same way `chat_panel.py` already
  established: `GraphCanvas.__init__` gains a required `page: ft.Page`
  parameter, stored as `self._page = page` (**not** `self.page` —
  `ft.Container`/`ft.Control` already expose a read-only `page` property;
  assigning over it raises `AttributeError`, the exact trap `chat_panel.py`
  hit and fixed). Split `_compute_layout()` into a pure
  `_layout_positions()` (reads `self._graph`/`self._width`/`self._height`,
  returns the positions dict, touches no Flet control — safe off-thread)
  plus the existing `_compute_layout()` kept as a synchronous wrapper
  (`self._positions = self._layout_positions(); self._redraw()`) so the
  6 existing tests that call it directly keep working unchanged. Add a new
  `_layout_worker()` (the real thread target: computes positions, then
  `self._page.run_task(self._apply_positions, positions)`) and
  `async def _apply_positions(...)` (sets `self._positions`, calls
  `self._redraw()` back on the event loop). `set_graph()` becomes
  `self._page.run_thread(self._layout_worker)`.

**Pan-offset state** (shared foundation for panning and focal-point zoom):
add `self._pan_x = self._pan_y = 0.0` and `self._panning: bool = False`.
Every place a data coordinate becomes a screen coordinate changes from
`x * zoom` to `x * zoom + pan_x` (same for `y`): `build_shapes()`'s edge/
circle/label positions, and `_node_at()`'s hit test.

**Whole-canvas panning** (item 2): extend the *existing*
`on_pan_start`/`on_pan_update`/`on_pan_end` trio rather than adding a
competing `on_tap` handler (avoids Flutter gesture-arena conflicts, and
this is what makes clicking a node already work as click-to-select today,
since pan-start fires on pointer-down before any movement threshold).
`_on_pan_start`: unchanged when a node is hit; when nothing is hit, set
`self._panning = True` and — if a node was previously selected — clear
selection (see below). `_on_pan_update`: branch on `self._dragging is not
None` (existing per-node-drag path, corrected to subtract the pan offset
before dividing by zoom, so dragging a node while panned lands it at the
right data-space point) vs. `self._panning` (new: `self._pan_x +=
e.local_delta.x`, same for `y`, then redraw). `_on_pan_end`: also clears
`self._panning`.

**Scroll-wheel zoom** (item 1), focal-point-aware — recommended over
reusing the origin-anchored `_set_zoom()` as-is, since scroll-wheel
strongly implies "zoom toward what's under my cursor" and the pan-offset
state above makes the math a small addition rather than new
infrastructure. The three zoom buttons keep their exact current
origin-anchored behavior (no natural focal point for a button click) —
`_set_zoom()` gains an optional `focal: tuple[float, float] | None = None`
parameter, defaulting to `None` so `zoom_in()`/`zoom_out()`/`zoom_reset()`
and the existing clamp test stay byte-identical. When a focal point is
given, the pan offset is adjusted so the data point under the cursor
stays fixed across the zoom change (`pan' = focal + (pan - focal) *
(new_zoom / old_zoom)`). New `_on_scroll(e: ft.ScrollEvent)` handler calls
`_set_zoom(self._zoom + step, focal=(e.local_position.x,
e.local_position.y))` where `step` is a smaller new constant than the
button's `_ZOOM_STEP` (e.g. `_SCROLL_ZOOM_STEP = 0.08`) signed by
`e.scroll_delta.y`; wired via `on_scroll=self._on_scroll` on the existing
`GestureDetector`.

**Node selection info + deselection** (item 3): keep the display entirely
inside `GraphCanvas`, as a fourth overlay `Container` in the same
`ft.Stack` already holding the legend (top-left) and zoom controls
(bottom-right) — positioned top-right, the one free corner. This follows
the exact precedent already in the file rather than adding new `app.py`
wiring; the constructor's existing (currently-unused) `on_node_selected`
callback stays available for any future external consumer but isn't
needed for the display itself. New `_build_info_overlay()` builds a
`CARD_BG`/`BORDER`-styled `Container`, `visible=False` initially. New
`_update_info_overlay()`, called from both branches of `_on_pan_start`
where `self._selected` changes (select *and* deselect): when
`self._selected is None`, hides the overlay; otherwise reads
`self._graph.nodes[self._selected]` (title/type/tags, with `.get(...,
"—")` defaults so it degrades gracefully against older graph data) plus
`self._graph.in_degree()`/`out_degree()` for a basic link count, and shows
the overlay. Deselection: `_on_pan_start`'s no-hit branch (added above for
panning) also clears `self._selected` and calls `_update_info_overlay()`
— clicking/dragging empty canvas background now deselects.

**`get_graph_data()` extension** (`graph/link_engine.py`) to actually
carry the title/type/tags data the overlay needs — currently nodes carry
zero attributes (`graph.add_node(row["slug"])` from a bare `SELECT slug
FROM notes`). Change to `SELECT slug, title, type, tags FROM notes` and
`graph.add_node(slug, title=row["title"], type=row["type"],
tags=json.loads(row["tags"]))` — `tags` follows the exact `json.loads`
convention `lint_engine.py:65` already uses for the same column (stored
via `json.dumps` in `storage/db.py:122`). Backward compatible: the other
two callers (`degrees_of_separation`, `mcp/server.py`'s
`trace_network_path`) only check node membership and shortest paths,
never attributes.

**`app.py`**: `self.graph = GraphCanvas()` → `self.graph = GraphCanvas(page)`
(matching `ChatPanel(page)` right below it) — no other call sites change,
`set_graph()`'s public signature is untouched.

**Tests** (`tests/test_gui_shell.py`, following the file's own established
pattern of hand-building Flet event objects and calling private handlers
directly, no live display needed): the 6 existing `GraphCanvas()`
construction sites need a `page` arg (a placeholder object suffices for
all but the new threading test). New coverage: pan moves `_pan_x/_pan_y`
without touching `_positions`; node drag still lands correctly once panned;
`_node_at()` accounts for pan; selecting a node shows the overlay with the
right content (fixture graph built with `add_node(slug, title=..., type=...,
tags=[...])`); a subsequent background click deselects and hides it;
focal-zoom math (the data point under the cursor maps back to the same
screen point before/after); scroll handler in both directions; the
existing button-zoom clamp test stays unchanged. One new test using the
`_FakePage` double already established in `test_gui_pipeline.py`/
`test_gui_chat.py` (real background thread + a dedicated event-loop
thread via `run_coroutine_threadsafe`) drives `set_graph()` through the
real `run_thread` → `run_task` → `_apply_positions` path end-to-end,
since the direct `_compute_layout()` tests bypass it entirely.

### Verification

Implemented per the plan above with no material deviations. `GRAPH_EDGE`
landed at `#4E535A` (oklch(44% 0.013 260), computed with the same
conversion the rest of `theme.py` uses) -- moved after `BORDER_DASHED` in
the file so the "Lines and borders" section stays a monotonic lightness
ramp, with a one-line comment noting it's deliberately lighter than a
plain border since a 1.2px line at border-lightness was invisible against
`CANVAS_BG`. Scroll direction: wheel up (`scroll_delta.y < 0`) zooms in,
matching the maps/design-tool convention -- worth confirming on your
hardware since wheel-delta sign isn't universal across platforms/devices,
trivial to flip if backwards. Tests: `tests/test_gui_shell.py`'s Graph
canvas section grew from 6 to 20 tests -- the 6 existing tests updated
for the new required `page` constructor arg (a `_page_stub()` placeholder,
since none of them touch `set_graph()`'s threaded path), plus new coverage
for pan (background-drag pans without touching node positions, node-drag
stays correct once panned, hit-testing accounts for pan), focal-point zoom
math, the zoom buttons staying origin-anchored (no pan drift), scroll in
both directions, selection showing/hiding the info overlay with real
content, deselection on an empty-canvas click, the `on_node_selected`
callback firing on both select and deselect, and one test using a real
`_FakePage` double (matching `test_gui_chat.py`'s) to drive `set_graph()`
through the actual `run_thread` -> `run_task` path end-to-end.

Verified: 266 tests pass, `uv run ruff check .` clean, `flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds and the rebuilt
bundle launches with no console output beyond the benign GTK/Atk lines
already known from every prior sub-phase.

**Manual verification (you)**: scroll-zoom in/out over a specific node and
confirm it stays under the cursor (and that scroll-up actually zooms in,
not out -- flip `_on_scroll`'s comparison in `graph_canvas.py` if it's
backwards on your setup); click-drag empty canvas to pan, drag a node
while panned to confirm it still tracks the cursor correctly; click a node
and confirm the info card (top-right) shows title/type/tags/link counts,
then click empty space and confirm it clears; confirm link lines are now
visible between connected notes.

## Phase 18 — Vault self-maintenance: wikilinks, index.md/log.md, SCHEMA.md as a live input — implemented

### Context

Testing Phase 17's graph canvas fixes exposed a much bigger gap: after
ingesting 7 files (17 entity/concept notes produced), there were **zero**
`[[wikilink]]`-style links anywhere in the vault -- not even a note back to
its own source. Investigation (two Explore passes plus direct reads of
`compiler_engine.py`, `link_engine.py`, `vault/manager.py`, `lint_engine.py`,
`storage/db.py`) found this is one root problem wearing several faces, not
several unrelated bugs:

- **No code path ever emits `[[wikilink]]` syntax.** None of the three
  content-generating prompts in `compiler/compiler_engine.py`
  (`_SUMMARY_SYSTEM_PROMPT`, `_ENTITY_EXTRACTION_PROMPT`,
  `_MERGE_SYSTEM_PROMPT`, lines 36-57) mention links at all, and nothing
  programmatically injects one either. `graph/link_engine.py`'s
  `sync_links()` (the regex-scan-and-diff that populates the `links` SQL
  table) is itself correct and tested -- it's just scanning content that
  never contains the pattern.
- **`sync_links()` is never called automatically.** It only runs via an
  explicit CLI command (`llm-wiki link sync`/`link rebuild`) or MCP action
  -- never as part of `compile_queued_item()`'s stage sequence. So even
  once wikilink text exists in note bodies, the `links` table (and
  therefore the graph canvas and MCP's `trace_network_path`) stays empty
  until someone manually re-syncs.
- **`wiki/index.md`, `wiki/log.md`, and `SCHEMA.md`** are all scaffolded
  once by `create_vault()` (`vault/manager.py:63-90`) and never touched
  again by any other code. `index.md`'s section headers never get
  populated; `log.md` gets one "Initialized vault" line and nothing since;
  `SCHEMA.md` is **write-only** -- confirmed via a full grep, nothing
  anywhere reads it back, even though its own template text says
  `"Wikilink degrees of separation limit = 3."`, i.e. it already assumes
  wikilinks are a real mechanism.
- **The GUI's "Reindex Vault" menu item is a stub** (`gui/menu.py:83`,
  `_item("Reindex Vault", None)`) -- exactly the action that should exist
  to fix an already-broken vault like the user's current one, without
  re-ingesting anything.
- **Lint partially detects the fallout but can't resolve it.**
  `lint_engine.py` already has `_check_isolated_notes()` (`ISOLATED_NOTE`)
  which should already be flagging all 17 notes today, just weakly
  (2-point deduction) -- and lint has zero visibility into `index.md`/
  `log.md`/`SCHEMA.md` since they're plain files, never `notes` table rows.

A second planning pass (a Plan-agent review of the draft design below)
surfaced two additional, previously-unnoticed bugs that this phase's fix
would otherwise trigger or worsen -- both confirmed by direct code
reads, both folded into the plan:

- **`[[index]]` would become a permanently broken link.** `index.md` is
  deliberately never passed through `upsert_note_from_file()`, so `index`
  never becomes a `notes` row. Once every note emits `[[index]]`,
  `lint_engine._check_broken_links()` (which resolves targets purely
  against `SELECT slug, path FROM notes`) would flag `BROKEN_LINK` on
  every single note, forever -- making lint scores *worse* than today.
- **A full DB rebuild would silently ingest `index.md`/`log.md` as bogus
  "source" notes.** `storage/db.py`'s `_rebuild_notes()` walks
  `wiki_dir.rglob("*.md")`, excluding only paths under `.system` --
  `index.md`/`log.md` sit directly under `wiki/` and aren't excluded.
  Today this is barely noticeable (`index.md` is nearly empty); once this
  phase makes `index.md` contain real content -- including a `[[slug]]`
  entry for every note in the vault -- a later rebuild would create real,
  spurious `("index", every-note-slug)` edges on top of the deterministic
  ones, making vault state depend on rebuild history. Untested today
  (`tests/test_storage.py`'s rebuild tests hand-build `wiki/` without
  `create_vault()`, so this path was never exercised).

### Implementation

**A. Deterministic "Related" backlinks, not prompt-dependent.** Every note
(source/entity/concept) always gets `[[index]]` plus links to its
`sources` slugs, regardless of LLM behavior -- this is the guaranteed
part the user explicitly wants ("they should all be backlinked to
Index.md"). A marker-delimited block, appended fresh on every write:

```python
_RELATED_MARKER = "<!-- llm-wiki:related -->"

def _strip_related_block(content: str) -> str:
    idx = content.rfind(_RELATED_MARKER)  # rfind, not find -- see below
    return content[:idx].rstrip() if idx != -1 else content

def _render_related_block(fm: NoteFrontmatter) -> str:
    targets = ["index"] + [s for s in fm.sources if s != fm.slug]
    lines = "\n".join(f"- [[{t}]]" for t in targets)
    return f"\n\n{_RELATED_MARKER}\n## Related\n\n{lines}\n"
```

`rfind()` (not `find()`) matters: if an LLM ever echoes the marker text in
prose (quoting it, or a source document literally containing that
string), `find()`'s first-occurrence match would truncate everything
after it -- silently destroying real content. Since the block is always
appended once, at the true end, `rfind()` is strictly safer.

`_write_note_file()` (`compiler_engine.py:238-240`, the single write
chokepoint for every note type) strips any existing related block from
`body`, then appends a freshly-rendered one from `fm.sources`. Critically,
`_cascade_update_note()` (`compiler_engine.py:199-205`) reads
`existing.content` back off disk for the merge prompt -- that must be
passed through `_strip_related_block()` first, or the merge LLM sees (and
may mangle-duplicate) the deterministic block, and a second one gets
appended on top. This keeps the block fully separate from anything
LLM-touched: regenerated from `fm.sources` on every single write, never
merged or preserved by the LLM itself.

**Source notes become bidirectional.** `fm.sources` for a source note is
`[own slug]`, so after the self-filter its Related block would otherwise
collapse to `[[index]]` only -- entity→source links, but never
source→entity. Since `entities = _extract_entities(...)`
(`compiler_engine.py:115`) is already computed before `_write_source_note()`
is called (`compiler_engine.py:121`), threading `[e.frontmatter.slug for e
in entities]` through costs nothing and makes source notes list what was
extracted from them -- a more useful, genuinely bidirectional graph rather
than a strictly one-way backlink.

Checked and confirmed clean: a brand-new note's first write has no
existing marker to strip (`_strip_related_block()`'s `idx == -1` branch
returns the string unchanged), and `python-frontmatter` only parses the
YAML block -- an HTML comment in the body is inert, no conflict.
Synthesis notes (`NoteType.SYNTHESIS`) are never actually produced by the
current pipeline, but `extract_structured()`'s `outlines` grammar is
constrained to the full 4-member `NoteType` enum, not just entity/concept
-- `_render_related_block()` only reads `fm.sources`/`fm.slug`, so it's
already type-agnostic and needs no special-casing either way.

**B. `sync_links()` runs automatically after every compile.** One call
added inside `compile_queued_item()`, after `update_status(...,
COMPLETED)`. Both `run_pipeline()` and `step_one()` funnel through this
single function (confirmed: one call site, `pipeline_runner.py:93`), so
this covers batch, step, and the CLI's `ingest` command uniformly.

**C. `index.md` becomes self-regenerating, not incrementally appended.**
New `rebuild_index(conn, vault_root)` (new module, see file list):
preserves everything on the current `index.md` before the first `##
Sources` heading (the vault name/description header -- avoids threading
`VaultInfo` through the compile call chain just for this), then
deterministically regenerates the four sections from a fresh `SELECT
slug, title, type FROM notes ORDER BY title`, rendering each row as `-
[[slug]] — Title`. Idempotent and always correct given current DB state --
no incremental-append drift (a renamed/deleted note can't leave a stale
entry, since the whole section is rebuilt from scratch every time).

**D. `log.md` gets real activity entries.** New `append_log_entry(vault_root,
message)` -- same `"- [{timestamp}] {message}\n"` format `create_vault()`
already uses for its first line. Called at the same post-compile point as
B/C with a message like `"Compiled '{title}' -> N note(s)"`.

**Failure semantics for B/C/D**: each wrapped in its own `try/except
Exception`, logged and swallowed, never re-raised -- not one shared
try/except around all three. None of them touch the note files or
embeddings that already succeeded by the time they run; a `sync_links()`
failure marking the whole item `ERROR` (via the existing top-level
`except Exception` in `compile_queued_item()`) would misreport a
maintenance side-effect as an ingestion failure, and would incorrectly
stop `rebuild_index()`/`append_log_entry()` from still running for an
unrelated reason. No new `CompileStage` checkpoint for any of this --
`CompileStage` is consumed positionally by `gui/app.py`'s progress-bar
math (`_TOTAL_STAGES = 6`), and these are invisible maintenance steps, not
user-facing compile stages.

**E. `SCHEMA.md` becomes a live prompt input.** Read once per
`compile_queued_item()` call (small file, cheap to re-read, no caching
needed), prepended to `_ENTITY_EXTRACTION_PROMPT` and `_MERGE_SYSTEM_PROMPT`
(the two prompts that write note *content*) via a small
`_build_system_prompt(base, schema_rules)` helper that no-ops gracefully
if the file is missing/empty. `create_vault()`'s default `SCHEMA.md`
template (`vault/manager.py:82-90`) gains an explicit rule: `"- Reference
related entities and concepts using [[wikilink]] syntax when meaningfully
connected."` -- the best-effort, LLM-driven cross-referencing layer,
complementing (A)'s guaranteed-but-source-scoped backlinks.

**F. "Reindex Vault" gets wired up -- and is the retroactive fix for the
user's existing 17 notes.** New `reindex_vault(conn, vault_root)`:

1. `backfill_related_blocks(conn, vault_root)` -- iterates every `notes`
   row, reads its file, strips+re-renders its Related block from its
   stored `sources`, re-runs `upsert_note_from_file()` so `content_hash`
   changes. Returns count rewritten.
2. `sync_links(conn, vault_root)` -- every note's hash just changed, so
   this picks all of them up in one pass.
3. `rebuild_index(conn, vault_root)`.

Each of the three stays independently callable/testable (mirroring
`link_engine.py`'s own shape -- `sync_links()`/`rebuild_full()`/
`get_graph_data()` are already small composable functions, `rebuild_full()`
itself just a thin wrapper over `sync_links()`) rather than one monolithic
function -- this matters specifically because `reindex_vault()` is the
user's actual fix for their already-broken vault, and if a bug surfaces
in "reindex didn't fix my vault," independently testable steps are what
make it possible to isolate which one. B/C/D inside `compile_queued_item()`
only ever need `sync_links()` + `rebuild_index()` + `append_log_entry()`
directly -- not the full `reindex_vault()` sequence, since a fresh compile
already wrote correct related blocks via `_write_note_file()`.

Wired to `gui/menu.py`'s `_item("Reindex Vault", None)` via a new
`on_reindex_vault` param threaded through `build_menu_bar()` from `Shell`
in `app.py` (same pattern as `on_zoom_reset`/`on_settings`), which calls
`reindex_vault()` then refreshes `self.graph`/`self.health_panel` (mirroring
the existing refresh block at `app.py:232-236`) so the GUI reflects the
result immediately. Also exposed as `llm-wiki vault reindex`
(`cli/main.py`'s existing `vault_app` group, alongside `vault create`/
`vault open`).

**G. Two small, necessary corrections, not new features:**
- `lint_engine._check_broken_links()` exempts the literal slug `"index"`
  as an always-valid target -- a one-line, commented exemption for this
  reserved pseudo-note, which is deliberately never a `notes` row. (Not
  making `index.md` a real tracked note instead: that would need an
  artificial `NoteType` member, `rebuild_index()`'s own listing query
  would have to filter itself out, and it'd break existing compiler tests
  for no real benefit -- keeping it untracked + exempting the slug is far
  less invasive.)
- `storage/db.py`'s `_rebuild_notes()` excludes `wiki/index.md` and
  `wiki/log.md` by filename, guarded to `note_path.parent == wiki_dir`
  (so a legitimately-slugged entity note at, say, `wiki/entities/index.md`
  about database indexing isn't accidentally skipped -- only the two
  reserved top-level files are).

**Lint otherwise stays deliberately unchanged.** `_check_isolated_notes()`
needs no changes -- once A+B land, every future note gets guaranteed
backlinks that `sync_links()` actually picks up, so it stops firing for
the common case and stays meaningful for genuinely disconnected notes. No
new lint-finding kinds for `index.md`/`log.md`/`SCHEMA.md` staleness are
added, since C/D make staleness structurally impossible (regenerated/
appended every compile) rather than something to detect after the fact.
No generic "auto-fix a lint finding" mechanism is built either --
`reindex_vault()` already **is** the concrete resolution action, just not
literally wired to individual finding rows.

### Files

- `src/llm_wiki/compiler/compiler_engine.py` — `_RELATED_MARKER`,
  `_strip_related_block()`, `_render_related_block()`; `_write_note_file()`
  strips+appends; `_cascade_update_note()` strips before merge;
  `_write_source_note()` gains entity slugs for bidirectional links;
  `compile_queued_item()` gets the three log-only post-compile calls;
  `_build_system_prompt()` folds `SCHEMA.md` into the two content prompts.
- `src/llm_wiki/vault/reindex.py` (**new**) — `backfill_related_blocks()`,
  `rebuild_index()`, `append_log_entry()`, `reindex_vault()`.
- `src/llm_wiki/storage/db.py` — `_rebuild_notes()` excludes `index.md`/
  `log.md`.
- `src/llm_wiki/lint/lint_engine.py` — `_check_broken_links()` exempts
  `"index"`.
- `src/llm_wiki/vault/manager.py` — `create_vault()`'s default `SCHEMA.md`
  template gains the wikilink-usage rule.
- `src/llm_wiki/gui/menu.py` / `src/llm_wiki/gui/app.py` — `on_reindex_vault`
  wired through `build_menu_bar()` and `Shell`.
- `src/llm_wiki/cli/main.py` — new `vault_app.command("reindex")`.

### Tests

Following this codebase's established patterns throughout (direct
function calls against a real SQLite conn + `tmp_path` vault fixture,
mocked-LLM via `SimpleNamespace`/`openai.OpenAI` monkeypatching per
`test_pipeline_runner.py`, no live LLM):

- `tests/test_compiler_engine.py`: existing exact-content-equality
  assertions become `.startswith(...)`/`in` checks that also assert the
  Related block and its `[[index]]`/`[[source]]` targets; a new assertion
  that the merge call's user message never contains `_RELATED_MARKER`
  (locks in strip-before-merge); a new test confirming `links` is
  populated after a normal `compile_queued_item()` call without a
  separate `sync_links()` call; a new test that monkeypatches `sync_links`
  to raise and confirms the item still completes as `COMPLETED` (fail-soft
  regression test).
- `tests/test_vault_reindex.py` (**new**): `rebuild_index()` regenerates
  correctly and idempotently (header preserved, stale entries never
  linger); `append_log_entry()` appends in the right format;
  `backfill_related_blocks()` rewrites and is idempotent on a second call;
  `reindex_vault()` end-to-end against a vault mimicking the user's actual
  broken state; `_strip_related_block`/`_render_related_block` unit tests,
  including the marker-appears-mid-body case that locks in the `rfind()`
  fix.
- `tests/test_storage.py`: a new/extended rebuild test built via
  `create_vault()` (not the current hand-built fixture) asserting
  `"index"`/`"log"` never appear as `notes` slugs after
  `rebuild_from_vault()` -- the regression test for the DB-rebuild bug.
- `tests/test_lint_engine.py`: a note with only `See [[index]].` in its
  body scores 100 / produces zero `BROKEN_LINK` findings.
- `tests/test_cli.py`: `llm-wiki vault reindex` against a freshly-created
  vault, exit code 0.
- `tests/test_gui_shell.py`: `on_reindex_vault` threaded into the existing
  menu-bar test fixture, asserting it's wired to a non-`None` callback.

### Verification

Implemented per the plan above, with one structural adjustment discovered
during implementation: `_strip_related_block()`/`_render_related_block()`
couldn't stay defined inside `compiler_engine.py` as originally sketched,
since `compiler_engine.py` needs `vault.reindex` (for the post-compile
maintenance calls) and `vault/reindex.py` needs the exact same rendering
to backfill old notes -- importing either package's `related_links`
submodule from the other tripped a real circular import through their
`__init__.py`s (`compiler/__init__.py` unconditionally imports
`compiler_engine`, so anything nested under `compiler/` that `vault`
needs re-triggers the cycle). Fixed by pulling the rendering functions out
into a new top-level leaf module, `src/llm_wiki/related_links.py` (sibling
to `models.py`, not nested under either package) -- verified with direct
`python -c "import ..."` checks in both import orders before moving on.

Both critical gaps the Plan-agent review surfaced were fixed as designed:
`lint_engine._check_broken_links()` exempts the literal `"index"` slug,
and `storage/db.py`'s `_rebuild_notes()` excludes `wiki/index.md`/
`wiki/log.md` by filename (guarded to the top-level `wiki/` dir only).

Tests: `tests/test_compiler_engine.py`'s two exact-content-equality
assertions became prefix/membership checks that also assert the Related
block and its targets, plus a new assertion that the merge call's message
never contains the marker text (the existing note fixture was given a
pre-existing Related block specifically so this is a real regression
test, not a vacuous one), and a new fail-soft test that monkeypatches
`sync_links` to raise and confirms the item still completes. New
`tests/test_vault_reindex.py` (20 tests) covers the rendering helpers
(including the `rfind()`-not-`find()` case with the marker appearing
mid-body), `rebuild_index()`'s idempotency/no-stale-entries behavior,
`append_log_entry()`, `backfill_related_blocks()`, and an end-to-end
`reindex_vault()` test mimicking the user's actual broken vault state.
`tests/test_storage.py`, `tests/test_lint_engine.py`, `tests/test_cli.py`,
and `tests/test_gui_shell.py` each gained one targeted regression/coverage
test per the plan's list.

Verified: 285 tests pass, `uv run ruff check .` clean, `flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds and the rebuilt
bundle launches with no console output beyond the benign GTK/Atk lines
already known from every prior phase.

**Manual verification (you)**: since your current vault already has 7
sources / 17 notes in the broken state, this is the one you'll actually
exercise the fix against --
1. Rebuild and open your existing vault, then use Tools > Reindex Vault.
   Confirm: every entity/concept note gains a `## Related` section linking
   `[[index]]` (and its source); `index.md` lists all 7 sources / 17
   entities-or-concepts under the right headings; `log.md` gains an entry;
   the graph canvas now shows real edges; the Health panel's isolated-note
   count drops to (ideally) zero.
2. Ingest one new file and confirm the same things happen automatically,
   with no manual Reindex needed.
3. Confirm `SCHEMA.md`'s new wikilink rule visibly nudges the LLM toward
   cross-referencing other entities/concepts it mentions (best-effort,
   not guaranteed -- the deterministic backlinks are the reliable floor,
   this is the enrichment on top).

## Post-18 fix: the graph canvas needed a restart to show a newly ingested note

Your test pass found the one thing Phase 18 didn't wire up: after ingesting
a new file, the graph canvas kept showing the old node/edge set until the
app was restarted, even though `compile_queued_item()` was already calling
`sync_links()` so the DB itself was current.

Root cause: `_on_item_completed()` (`gui/app.py`) already refreshed the
Items and Health panels after every compile, but never re-fetched the
graph -- only `_on_vault_changed()` (vault open) and the new Reindex Vault
action called `self.graph.set_graph(get_graph_data(conn))`. A normal
ingest never went through either path, so the canvas only ever reflected
whatever the DB looked like when the vault was first opened.

Fix: `_on_item_completed()` now calls `self.graph.set_graph(get_graph_data
(self.controller.conn))` alongside its existing panel refreshes, guarded
by the same `conn is not None` check used elsewhere in the file.

Test: extended `test_gui_toolbar.py`'s existing real-pipeline-run test
(mocked LLM, a genuine `compile_queued_item()` call through a real vault)
with an assertion that `shell.graph.node_positions` is populated after
the item completes, waited via the same `_wait_until()`/real-thread
`_FakePage` pattern already used for `shell.pipeline_adapter.running`.

Verified via `flet build linux`: 285 tests pass, ruff clean, bundle
launches clean.

## Post-18 fix: graph nodes clustered on top of each other

Once new notes showed up in the canvas without a restart, the next thing
you noticed: nodes didn't repel each other enough to be readable, forcing
you to drag them apart manually to find anything.

Root cause: `nx.spring_layout()` was called with a hardcoded `k=0.15`,
mapped into a fixed 900x560 canvas regardless of how many nodes existed.
`spring_layout`'s own default (`k=1/sqrt(n)`) already assumes bare point
nodes; ours render as a circle plus a text label, needing more room than
that, and a fixed `k` doesn't scale at all as the vault grows -- verified
directly (a synthetic 21-40 node graph, same hub-and-spoke shape the
Related-block backlinks actually produce: every note points to `[[index]]`
*and* its source) that the old code packed nodes as little as 1-2px
apart, worse than useless for a vault of any real size.

Two changes, both in `graph_canvas.py`:
- `k` now scales with node count: `k = _LAYOUT_SPACING / sqrt(n)`
  (`_LAYOUT_SPACING = 4.0`, a multiplier over `spring_layout`'s own
  default, chosen empirically against the synthetic hub graph above).
- The virtual canvas `_to_canvas()` lays nodes out into now also grows
  with node count past `_LAYOUT_BASE_NODE_COUNT` (12) notes, rather than
  always cramming everything into the same fixed box -- pan and
  scroll-zoom (both landed in Phase 17) are how you navigate a graph
  that's now bigger than one screenful, instead of the layout being
  artificially squeezed to always fit the initial view.

The exact pixel gap `spring_layout` converges to isn't fully deterministic
across seeds/iteration counts (verified directly -- it's a genuinely hard
combinatorial layout problem, not something a magic constant fully
solves), so the regression test only asserts the one thing that must
always hold regardless of convergence noise: node circles don't overlap
(`min_distance > 2 * NODE_RADIUS`), plus a direct unit test on the new
node-count -> scale relationship itself.

Verified via `flet build linux`: 287 tests pass, ruff clean, bundle
launches clean.

## Phase 19 — Terminal panel — implemented

### Context

The mockup's bottom dock has a Terminal tab alongside Pipeline Log,
deferred twice already: first on the belief no embeddable terminal
existed for Flet (wrong — `flet-terminal` does), then because it ships a
real Flutter/Dart package needing the Flutter SDK and a `flet build`
step (no longer a blocker — both have been standing since mid-16a for
`flet-charts`).

Confirmed by pulling `flet-terminal`'s actual PyPI README and its GitHub
repo's example (`examples/flet_terminal_example/src/pty_service.py`) that
the scope is bigger than "wire up a widget": `flet_terminal.Terminal` is
**only** the rendering surface (an `xterm.dart` canvas over a Flet
`DataChannel`) -- it does not spawn or manage a shell process itself. The
app is responsible for spawning a real PTY-backed shell and piping bytes
both ways: `Terminal.set_on_bytes(callback)` delivers what the user
types/pastes; the app forwards that to the PTY's stdin; the PTY's stdout
gets pushed back in via `Terminal.send_bytes(data)`. The package's own
example ships a complete, working reference for exactly this
(`PTYService`, POSIX + Windows branches) -- this plan adapts its POSIX
path (this project only ever builds `flet build linux`) rather than
reinventing PTY plumbing from scratch.

### Implementation

**`src/llm_wiki/terminal/pty_service.py`** (new package, new file) --
`PtyService`, a plain Python engine primitive with **zero Flet import**,
matching `mcp/process.py`'s `McpProcess` and `ingest/raw_watcher.py`'s
`RawWatcher` in shape: constructed with `on_output: Callable[[bytes],
None]` and `on_error: Callable[[str], None]` callbacks, exposing
`start(cwd: Path)`, `stop()`, `write(payload: bytes)`, and `resize(cols:
int, rows: int)`. Adapted from the reference `pty_service.py`'s POSIX
path, trimmed to Linux-only (drops the Windows `winpty` branch entirely
-- this project has no Windows target, confirmed by `flet build linux`
being the only build command used anywhere in this whole phase history):

- `start(cwd)`: `master_fd, slave_fd = pty.openpty()`; `shell =
  os.environ.get("SHELL", "/bin/bash")` (this is why `$SHELL` resolving
  dynamically matters for fish -- a hardcoded `/bin/bash` would silently
  ignore it); `subprocess.Popen([shell, "-l"], stdin=slave_fd,
  stdout=slave_fd, stderr=slave_fd, cwd=cwd, close_fds=True,
  start_new_session=True)` (fish accepts `-l`/`--login` the same as
  bash/zsh, confirmed against its man page -- no shell-specific branching
  needed); close the slave fd in the parent; spawn a daemon
  `threading.Thread` that loops `os.read(master_fd, 4096)` and calls
  `self._on_output(data)` until EOF/error.
- `resize(cols, rows)`: `fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
  struct.pack("HHHH", rows, cols, 0, 0))` -- keeps the shell's own
  `$COLUMNS`/`$LINES` and any TUI program honest as the panel resizes.
- `stop()`: close the master fd, terminate the child process -- mirrors
  `McpProcess.stop()`'s terminate/kill-with-timeout shape closely enough
  to reuse the same pattern, not the same code (different process
  handles).

**`src/llm_wiki/gui/terminal_panel.py`** (new) -- `TerminalPanel(ft.Container)`,
following `chat_panel.py`'s exact established shape for a panel that owns
a background thread talking to a Flet control: constructor takes
`page: ft.Page`, stored as `self._page` (not `self.page` -- the
now-familiar reserved-property trap `chat_panel.py`/`graph_canvas.py`
both hit and fixed the same way). Builds one `flet_terminal.Terminal`
(`expand=True`, background colour from `theme.TERMINAL_BG` -- a constant
that's existed unused in `theme.py` since 16a, evidently reserved for
exactly this). Owns one `PtyService`:
- `PtyService.on_output` callback hops back via `self._page.run_task(self._dispatch_output,
  data)` before calling `term.send_bytes(data)` -- the reader thread is
  a genuine background thread, so this is not optional, same reasoning
  as every other worker-thread-to-control path in this codebase.
- `term.set_on_bytes(self._pty.write)` -- user keystrokes arrive on the
  event-loop thread via Flet's own dispatch, so this is a direct,
  synchronous forward, no `run_task` needed (same as `_on_pan_start`/
  `_on_submit` elsewhere).
- `term.on_resize = self._on_resize`, parsing the event's JSON `data`
  (`{"cols": int, "rows": int}`) and calling `self._pty.resize(...)`.
- The PTY session starts lazily, once, the first time the panel is
  attached (or immediately at construction -- whichever proves simpler
  once the widget's exact mount lifecycle is in hand), with `cwd =
  self._vault_root or Path.home()`. Deliberately **not** restarted on a
  later vault switch -- killing a live shell session out from under the
  user because they opened a different vault would be more disruptive
  than a terminal that just keeps its original cwd until manually `cd`'d,
  the same tradeoff every normal terminal app already makes.
- `stop()` delegates to `self._pty.stop()` -- wired into `Shell._exit()`
  (`gui/app.py`) alongside the existing `self.mcp_process.stop()` /
  `self.raw_watcher.stop()` calls, so no orphaned shell process survives
  the app closing.

**`gui/app.py`**: `self.terminal_panel = TerminalPanel(page)`;
`self.bottom_dock = DockArea([("Pipeline Log", self.log_panel),
("Terminal", self.terminal_panel)])` (currently just the one entry).

**`pyproject.toml`**: `flet-terminal>=0.2.2` added to
`[project.dependencies]` as a real runtime dependency, alongside the
existing `flet-charts>=0.86.4` -- same reasoning: it ships compiled Dart
that has to be bundled by `flet build`, so it can't be a dev-only
dependency the way `pyside6` was during the QML-to-Flet transition.

### Tests

`PtyService` is genuinely, fully headlessly testable -- it's plain Python
with zero Flet import, same as `mcp/process.py`'s tests already spawn a
real subprocess against a fixture vault. New `tests/test_pty_service.py`:
start a session with a short, deterministic command in place of an
interactive shell (or a real shell with `-c`, matching `subprocess.run`
conventions) and assert `on_output` receives the expected bytes; `write()`
delivers input the process can echo back; `resize()` doesn't raise
against a live session; `stop()` actually terminates the child (no
zombie process left behind, checked via `Popen.poll()`).

`TerminalPanel` itself -- the two-way byte-forwarding glue -- is thinner
and harder to assert on without a live render (no meaningful "control
tree" to inspect the way `graph_canvas.py`'s shapes or `chat_panel.py`'s
message bubbles are), similar to how the graph canvas's actual on-screen
motion has always needed your manual pass regardless of what the
headless tests cover. Worth still testing what's mechanical: `set_on_bytes`'s
registered callback forwards straight to a stub `PtyService.write()`, and
`_on_resize`'s JSON parsing calls `resize()` with the right ints -- both
via a lightweight fake in place of the real `flet_terminal.Terminal` (same
spirit as `_page_stub()`/`_FakePage` elsewhere in `tests/test_gui_shell.py`).

### Verification

Implemented per the plan above, plus two real fixes the plan didn't
anticipate, both found and confirmed via direct, real subprocess testing
(spawning actual bash/fish/zsh, not mocks) before ever touching the GUI
layer:

- **`pty.fork()`, not `subprocess.Popen()` + `pty.openpty()`.** The
  plan's original sketch used the latter (matching the reference
  `pty_service.py` verbatim). Testing it directly against a real fish
  shell surfaced `tcgetpgrp failed`/`setpgid: Inappropriate ioctl for
  device` and immediate exit -- handing an already-open slave fd to
  `Popen(stdin=slave_fd, ..., start_new_session=True)` doesn't reliably
  make the PTY slave the child's controlling terminal on Linux. Fish
  (strict about job control) surfaced this immediately; bash tolerated it
  silently. Switched to `pty.fork()`, which the stdlib docs confirm
  handles session-leader + controlling-terminal setup correctly -- same
  repro then started clean. `PtyService`'s public shape stayed the same
  (`start`/`stop`/`write`/`resize`/`running`), just reimplemented over raw
  `os.kill`/`os.waitpid` instead of `subprocess.Popen`'s object, since
  `pty.fork()` only hands back a bare pid.
- **`$TERM` set explicitly to `xterm-256color`** in the child's
  environment before exec, not inherited. Two reasons, both confirmed
  directly: a GUI-launched app often has no `$TERM` at all (unlike a
  process started from a real terminal), and fish specifically probes
  terminal capabilities (DA1, OSC 11, XTGETTCAP) at interactive startup
  and waits for responses -- verified this genuinely blocks fish
  indefinitely against a raw, unresponsive PTY (bash sent no such queries
  and was unaffected). Read `xterm.dart`'s actual source (pulled into
  `~/.pub-cache` by the `flet build` below) to confirm this isn't a dead
  end in the real app: it implements a real `sendPrimaryDeviceAttributes()`
  response, and `flet_terminal`'s own Dart bridge
  (`build/flutter-packages/flet_terminal/lib/src/flet_terminal.dart`)
  wires that response back through the same DataChannel `set_on_bytes()`
  receives from -- so the DA1 query fish blocks on does get answered in
  the real widget, just not in a bare Python-only repro with no responder
  attached. `xterm-256color` (not `dumb`, which also unblocks fish but by
  making it deliberately downgrade to no colours) is what the widget
  genuinely supports.
- Custom terminal `theme` dict built from `theme.py`'s existing palette
  (`TERMINAL_BG`, unused since 16a, finally has a use) rather than one of
  `flet_terminal`'s generic built-in presets.
- Tests: `tests/test_pty_service.py` (8 tests) spawns real bash processes
  (not mocked) -- `$SHELL` forced to `/bin/bash` for determinism, since
  the capability-query hang above is real and shell-dependent, not
  something headless tests should depend on avoiding by luck.
  `tests/test_terminal_panel.py` (9 tests) covers the mechanical
  forwarding (typed-input wiring, resize JSON parsing, the `run_task`
  hop for PTY output) plus the lazy-start lifecycle (`did_mount()`
  starting exactly once, falling back to `Path.home()`, tab-switch
  remount not spawning a second session) against a real (bash-backed)
  PTY session, not a stub.

Verified: 304 tests pass, `uv run ruff check .` clean, `flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds (confirmed
`flet_terminal`'s Dart package genuinely links, not just installs --
this is the one phase where a clean build alone isn't sufficient proof,
same class of gap the "Unknown Control FilePicker" bug fell into back in
16c), rebuilt bundle launches with no console output beyond the usual
benign GTK/Atk lines, and no orphaned shell process survives a launch
where the Terminal tab was never opened (confirmed via `ps` -- lazy-start
means nothing spawns until the tab is actually selected).

**Manual verification (you)**: open the Terminal tab and confirm a real
fish prompt appears cleanly (not stuck/blank -- if it hangs, the DA1
handshake reasoning above didn't hold up in practice and is the first
place to look); run a command, confirm output streams back; resize the
bottom dock and confirm a TUI program (e.g. `htop`, or just `stty size`)
sees the new dimensions; confirm the terminal's cwd starts inside the
currently-open vault; close the app and confirm (e.g. via `ps`) the fish
child process doesn't linger.

## Phase 20 — MCP Start/Stop, fixed for real — implemented

### Context

Reported after Phase 19: clicking Start on the toolbar's MCP control
opens a second, blank/vault-less app window on top of the main one, and
closing that window flips the status dot back to Stopped. Root cause,
diagnosed by reading the built bundle: `McpProcess.start()`
(`mcp/process.py`) spawns `[sys.executable, "-m", "llm_wiki.mcp.server",
...]` via `subprocess.Popen` -- correct for a normal `uv run` dev
process, but the `flet build` bundle embeds Python via the
`serious_python` Flutter plugin rather than a standalone `python3`
executable, so `sys.executable` there resolves to the app's own compiled
binary. Spawning it just re-launches the whole packaged app as a second
process instead of running the MCP server module.

A second, independent bug rides along: `AppSettings.mcp_server.transport`
defaults to `"stdio"`, and the toolbar passes it straight through with
`stdin`/`stdout` redirected to `DEVNULL` -- `create_mcp_server()`'s own
docstring already notes `host`/`port` only apply to `sse`/
`streamable-http`, since `stdio` is meant to be spawned per-connection by
an external MCP client (e.g. Claude Desktop), not run as a standing
background service toggled from a status dot. Even without the
duplicate-window bug, a GUI-managed `stdio` process talking to `/dev/null`
was never going to do anything.

Both trace back to the same fix: stop treating the MCP server as
something to spawn as a subprocess at all. Confirmed directly (reading
`FastMCP.run_streamable_http_async()`'s actual source) that it already
does nothing more than build a `uvicorn.Config` + `uvicorn.Server` and
`await server.serve()` -- ordinary Python, runnable on a background
thread inside the same process. Also confirmed directly that this is
safe: `uvicorn.Server.capture_signals()` (the thing that would otherwise
need the main thread to install SIGINT/SIGTERM handlers) explicitly
checks `threading.current_thread() is not threading.main_thread()` and
skips signal setup entirely when it isn't -- no special handling needed,
uvicorn already anticipates being run this way.

### Implementation

**`src/llm_wiki/mcp/process.py`** -- `McpProcess`'s public shape stays
identical (`start()`/`stop()`/`restart()`/`running`), only the internals
change:

- `start(vault_root, *, host="127.0.0.1", port=8000)` -- drops the
  `transport` parameter entirely (see below for why). Builds
  `mcp = create_mcp_server(vault_root, host=host, port=port)` (unchanged
  call), `app = mcp.streamable_http_app()`, `config =
  uvicorn.Config(app, host=host, port=port, log_level="warning")`
  (`warning` keeps uvicorn's own startup banner out of the console --
  matches the project's existing preference for a quiet baseline, e.g.
  `flet-terminal`'s custom theme over a default preset), `server =
  uvicorn.Server(config)`. Spawns a daemon thread that creates its own
  `asyncio` event loop (`asyncio.new_event_loop()` +
  `loop.run_until_complete(server.serve())`, not `asyncio.run()`, since
  the loop reference needs to survive past that one call for `stop()` to
  schedule work on it) and stores `self._server`/`self._thread`/
  `self._loop`. A `threading.Event` set right before `await
  server.serve()` lets `start()` block briefly until the server thread is
  genuinely up before returning, mirroring the old code's synchronous
  "it's started" contract.
- `stop(*, timeout=5.0)` -- `self._loop.call_soon_threadsafe(setattr,
  self._server, "should_exit", True)` (the thread-safe way to touch
  another thread's event loop, not a raw unsynchronized attribute write --
  `uvicorn.Server.main_loop()` polls `should_exit` each tick and unwinds
  gracefully, closing its listening socket as part of shutdown before
  `serve()` returns), then `self._thread.join(timeout=timeout)`. Logs an
  error if the thread is still alive after the timeout (should not
  happen in practice -- uvicorn's own graceful-shutdown poll interval is
  well under a second -- but this replaces the old terminate/kill
  fallback, which has no equivalent for an in-process thread: there is no
  "kill -9" for a thread, only waiting longer or leaking it, so an error
  log is the honest outcome here instead of a silent hang).
- `restart()` -- unchanged (`stop()` then `start()`), works as-is since
  both now operate on the new internals transparently.
- `running` -- `self._thread is not None and self._thread.is_alive()`
  (was `Popen.poll()`).

Not fixed, and deliberately out of scope: the `conn = connect(...)`
SQLite handle `create_mcp_server()` opens per call isn't explicitly
closed on `stop()` -- previously killing the child process closed it for
free at the OS level; now it's left to Python's own garbage collection
once the `FastMCP`/closures referencing it are dropped. Acceptable for a
single desktop app where Start/Stop happens a handful of times per
session, not a high-churn server -- flagged here rather than silently
ignored, in case it needs revisiting if `McpProcess` ever gets used more
aggressively.

**`transport` is dropped from the GUI-managed path entirely, not
switched to a different default.** `stdio` structurally cannot work as a
"click Start, it becomes a long-running background service" model (it
requires the *client* to spawn the process, not the reverse), so there's
no real transport choice left for `McpProcess` to expose once `stdio` is
off the table -- `streamable-http` is simply hardcoded.
`src/llm_wiki/mcp/server.py`'s CLI (`main()`, `--transport` with all
three choices) stays completely untouched -- that's a genuinely
different, valid use case (an external MCP client spawning `llm-wiki mcp`
itself over stdio), independent of the GUI's toggle.

- `gui/toolbar.py`'s `mcp_start()`/`mcp_restart()` drop the
  `transport=settings.transport` kwarg from their two `self.mcp.start(...)`/
  `self.mcp.restart(...)` calls -- everything else in both methods is
  unchanged.
- `gui/dialogs.py`'s Settings dialog MCP tab drops its "Transport" field
  (`mcp_transport = _text_input(mcp.transport)`, `_field("Transport",
  mcp_transport)`, `mcp.transport = mcp_transport.value` -- three lines)
  entirely, rather than leaving a field visible that no longer does
  anything. Host/Port stay, unchanged -- still genuinely used.
- `config.py`'s `MCPServerConfig.transport` field itself is **left in the
  Pydantic model, just unused** -- removing it outright risks nothing
  functionally (Pydantic ignores unknown keys on load by default), but
  keeping it costs nothing either and avoids touching serialization
  behavior for a field an existing saved `.llm-wiki-config` might still
  carry. A one-line comment marks it as no longer read by the GUI.

### Tests

`tests/test_mcp_process.py` gets a full rewrite -- the old tests spawned
a real subprocess and checked `Popen.poll()`; the new ones start a real
`uvicorn` server (not mocked) and check it's genuinely listening, matching
the same "confirm the real thing actually launches" philosophy:

- `test_not_running_before_start` -- unchanged.
- `test_start_launches_a_live_server` -- start against a fixture vault,
  wait for `running`, then confirm a raw `socket.create_connection((host,
  port), timeout=1)` succeeds -- proves a real listener is bound, not
  just that a thread object exists.
- `test_start_while_already_running_does_not_spawn_a_second_server` --
  same `self._thread` identity check as the old subprocess-pid version.
- `test_stop_releases_the_port` -- start, stop, confirm a fresh
  `McpProcess` can bind the *same* port immediately after (proves the
  socket was actually released, not just that `running` flipped to
  `False`).
- `test_restart_serves_again` -- start, restart, confirm the port
  accepts a connection again afterward.
- `test_stop_when_never_started_is_a_no_op` -- unchanged.

`tests/test_gui_toolbar.py`'s existing `test_toolbar_mcp_start_stop_with_a_vault`
(drives `toolbar.mcp_start()`/`mcp_stop()` end-to-end through a real
vault) needs no changes -- it already only asserts on `mcp.running`, not
`transport`, and becomes a more meaningful test once `running` reflects a
real server rather than a broken subprocess. `tests/test_gui_shell.py`'s
`test_settings_dialog_exposes_a_tab_per_settings_group` (checks tab
*titles* only, not field counts) also needs no changes.

### Verification

Implemented per the plan above, with one real bug found and fixed during
implementation that the plan didn't anticipate: the first working version
signaled "started" via a `threading.Event` set immediately before
`await server.serve()` was even called -- `start()` would return with
`running` already `True`, but the actual listening socket wasn't bound
yet, so a caller acting immediately on "it's running" (exactly what the
new tests do) hit `ConnectionRefusedError`. Fixed by polling
`uvicorn.Server.started` instead -- confirmed via its source
(`Server.startup()`) that this flag is only set `True` at the very end of
socket binding, after `await loop.create_server(...)` succeeds, which is
the genuine readiness signal. Verified directly (a real `McpProcess`
against a real fixture vault, not mocked) that this fixed the race: start
now reliably means "the port is already accepting connections."

`uvicorn`, previously only a transitive dependency via `mcp`, was added
directly to `[project.dependencies]` -- this project now imports it
itself, so relying on someone else's dependency graph to keep providing
it was the wrong call.

Tests: `tests/test_mcp_process.py` fully rewritten (6 tests) -- starts a
real `uvicorn` server per test (unique ports, no mocking) and asserts on
a real `socket.create_connection()` rather than `Popen.poll()`, including
a genuine port-release proof (a second `McpProcess` immediately rebinding
the same port after the first one's `stop()`). The existing
`test_toolbar_mcp_start_stop_with_a_vault` in `test_gui_toolbar.py`
needed no changes and now exercises a real fix rather than a broken
subprocess that merely appeared to work by the old test's own (weaker)
standard.

Verified: 304 tests pass, `uv run ruff check .` clean, `flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches with no console output beyond the usual benign GTK/Atk lines,
no lingering process after a headless launch-and-kill where MCP was never
started.

**Manual verification (you)**: click Start on the toolbar's MCP control
and confirm no second window opens; confirm the status dot goes green/
"Running"; point a real MCP client (or `curl`/a quick script) at
`http://127.0.0.1:<port>` and confirm it's genuinely reachable; click
Stop and confirm the dot goes back to "Stopped" with no lingering
process; click Restart and confirm it comes back up cleanly. Open
Settings > MCP and confirm only Host/Port remain (no Transport field).

## Phase 21 — LLM-Wiki Dashboard panel — implemented

### Context

Your own suggestion, recorded on the deferred list since Phase 16:
vault-wide statistics distinct from the per-run Health panel (which
scores the *current* lint state -- schema violations, broken links,
isolated notes) -- concept/entity/source/synthesis note counts, total
items ingested to date, and failure counts. The lowest-risk item on the
deferred list: no new engine logic, purely aggregating two tables
(`notes`, `queue`) that already exist and are already fully populated by
the compile pipeline.

### Implementation

**`src/llm_wiki/storage/stats.py`** (new) -- mirrors the shape
`lint/lint_engine.py` already established for `HealthPanel` (a small
Pydantic result model next to the function that produces it, not
centralized in `models.py` -- same precedent as `LintReport`):

```python
class VaultStats(BaseModel):
    concepts: int
    entities: int
    sources: int
    synthesis: int
    total_ingested: int
    failures: int

def get_vault_stats(conn: sqlite3.Connection) -> VaultStats:
    by_type = dict(conn.execute("SELECT type, COUNT(*) FROM notes GROUP BY type").fetchall())
    by_status = dict(conn.execute("SELECT status, COUNT(*) FROM queue GROUP BY status").fetchall())
    return VaultStats(
        concepts=by_type.get("concept", 0),
        entities=by_type.get("entity", 0),
        sources=by_type.get("source", 0),
        synthesis=by_type.get("synthesis", 0),
        total_ingested=by_status.get("completed", 0),
        failures=by_status.get("error", 0),
    )
```

`total_ingested` counts `queue.status == "completed"` specifically (an
item that errored out wasn't actually ingested), keeping it a distinct,
meaningful number from `failures` (`status == "error"`) rather than both
just being sub-counts of "every item ever queued." `storage/__init__.py`
exports both, alongside the existing `db`/`vector_search` re-exports.

**`src/llm_wiki/gui/dashboard_panel.py`** (new) -- `DashboardPanel
(ft.Container)`, structurally a near-exact mirror of `health_panel.py`
(`_conn`/`set_connection()`/`refresh()` lifecycle, a `flet_charts.BarChart`
plus a stat-card grid, the same `contextlib.suppress(RuntimeError)`-guarded
`update()` for headless-test safety):

- `build_chart()` -- one bar per note type (Concepts/Entities/Sources/
  Synthesis), `max_y=max([*counts, 1])` so an empty vault still draws a
  visible axis (same reasoning `HealthPanel.build_chart()` already
  documents).
- `_stat_cards()` -- six cards: the four type counts, then Total Ingested
  and Failures. Total Ingested and Failures are deliberately *not* also
  bars on the chart -- they're a different unit (queue history, not a
  note-type breakdown), mixing them in would muddy one chart with two
  unrelated meanings.
- `refresh()` calls `storage.get_vault_stats(self._conn)`, defaulting to
  an all-zero `VaultStats` when `self._conn is None` (matching
  `HealthPanel`'s no-connection default of a clean slate rather than
  erroring).

**`gui/app.py`** wiring, following `health_panel`'s exact call sites:

- `self.dashboard_panel = DashboardPanel()`; `right_dock`'s panel list
  gains a third entry, appended at the end --
  `[("Health", self.health_panel), ("AI Chat", self.chat_panel),
  ("Dashboard", self.dashboard_panel)]` -- with `selected=1` left
  unchanged so AI Chat stays the default visible tab (Dashboard landing
  at index 2 doesn't disturb it).
- `_on_vault_changed()`: `self.dashboard_panel.set_connection(self.controller.conn)`
  alongside the existing `health_panel.set_connection(...)` /
  `items_panel.set_connection(...)` calls.
- `_on_item_completed()`: `self.dashboard_panel.refresh()` alongside the
  existing `self.health_panel.refresh()` -- a completed compile changes
  note counts, same reasoning that already refreshes Health there.
- `_reindex_vault()`: `self.dashboard_panel.set_connection(self.controller.conn)`
  alongside its existing `health_panel.set_connection(...)` call, for the
  same consistency reasons.

### Tests

- `tests/test_vault_stats.py` (new, mirroring `test_vault_reindex.py`'s
  own-file precedent for a small storage-layer addition): `get_vault_stats()`
  against an empty vault (all zeros), against a vault with a mix of note
  types and queue statuses (counts land in the right buckets), and
  confirms a `completed` item counts toward `total_ingested` while an
  `error` item counts toward `failures` and neither leaks into the other.
- `tests/test_gui_shell.py` gains a "Dashboard panel" block mirroring the
  existing "Health panel" one exactly: defaults to all-zero stats with no
  connection, `build_chart()` has a bar per note type with the right
  labels, `max_y` never collapses to zero on an empty vault, and a real
  `set_connection()` against a vault with actual `notes`/`queue` rows
  reflects real counts.

### Verification

Implemented exactly per the plan above -- no deviations, the closest any
phase in this project has come to "no surprises," matching the "lowest
risk item on the deferred list" assessment. `storage/stats.py`'s
`VaultStats`/`get_vault_stats()`, `gui/dashboard_panel.py`'s
`DashboardPanel` (a near-line-for-line mirror of `health_panel.py`), and
the `right_dock`/`_on_vault_changed()`/`_on_item_completed()`/
`_reindex_vault()` wiring all landed as designed.

Verified: 311 tests pass (7 new -- 3 for `get_vault_stats()`'s
aggregation, 4 for `DashboardPanel`'s chart/cards/real-connection
behavior), `uv run ruff check .` clean, `flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches with no console output beyond the usual benign GTK/Atk lines.

**Manual verification (you)**: open the right dock's new Dashboard tab
and confirm the four note-type counts and the ingested/failure counts
match what you'd expect from your actual vault; ingest a new file and
confirm the counts update after it completes, without needing to switch
vaults or restart.

## Post-21 addition: WikiLinks vs Backlinks counts — implemented

You asked for WikiLinks and backlinks -- not graph in/out (a vault-wide
in-count and out-count are always identical, since every `links` row is
simultaneously one outgoing edge and one incoming edge; that only differs
per-node, which the graph canvas's selection overlay already shows).
There's a real, different distinction here instead: `link_engine.py`'s
`_extract_wikilink_targets()` (used by `sync_links()`) already **dedupes
per source note** before writing to `links` -- confirmed directly reading
it -- so a note that writes `[[index]]` twice in its prose only produces
one edge in the `links` table, but the raw `[[wikilink]]` syntax still
appears twice in the file. Confirmed with you: show both --
**Total WikiLinks** (raw `[[...]]` occurrences across every note body)
and **Total Backlinks** (deduped edges -- `links` table row count).

### Implementation

**`graph/link_engine.py`** -- new `count_wikilink_occurrences(conn,
vault_root) -> int`, reading every `notes.path` off disk and summing raw
`_WIKILINK_RE.findall(content)` matches (not the deduped
`_extract_wikilink_targets()` list -- that's the whole point of this
being a different number from the edge count). Lives here, not
`storage/`, since it reuses `_WIKILINK_RE` and is squarely link-domain
logic; `storage/stats.py` importing from `graph/link_engine.py` is a
clean one-way dependency (confirmed neither `link_engine.py` nor
`graph/__init__.py` imports anything from `storage`, so no cycle).

**`storage/stats.py`** -- `VaultStats` gains `total_wikilinks: int` and
`total_backlinks: int` (the existing `COUNT(*) FROM links` query, just
named for what it now sits alongside rather than standing alone).
`get_vault_stats()`'s signature changes to `get_vault_stats(conn,
vault_root)` -- the wikilink-occurrence count needs file access, not just
the DB, so `vault_root` becomes a required second argument. This is a
breaking signature change to code that shipped in Phase 21 minutes ago;
updating its 3 existing call sites (2 in `storage/stats.py`'s own tests,
1 in `dashboard_panel.py`) is part of this addition, not separate
follow-up churn.

**`gui/dashboard_panel.py`** -- `set_connection()`'s signature gains a
second parameter, `set_connection(self, conn, vault_root=None)`, storing
`self._vault_root` alongside the existing `self._conn` (same lifecycle --
both set together, both cleared together, matching how this app always
treats "which vault is active" as one piece of state, not two). `refresh()`
calls `get_vault_stats(self._conn, self._vault_root)` when both are set,
falling back to the existing all-zero `VaultStats` otherwise.
`_stat_cards()` gains two more cards, "Total WikiLinks" and "Total
Backlinks" -- not part of the note-type bar chart, same reasoning as
Total Ingested/Failures: a link count is a different unit than a
note-type breakdown.

**`gui/app.py`** -- the two `dashboard_panel.set_connection(...)` call
sites (`_on_vault_changed()`, `_reindex_vault()`) both gain the vault-root
argument, matching the exact pattern already used one line above them for
`chat_panel.configure()`/`terminal_panel.configure()`. `_on_item_completed()`
needs no change -- it only calls `refresh()`, which already has both
pieces of state stored.

### Tests

- `tests/test_vault_stats.py`: existing 3 tests updated to the new
  `get_vault_stats(conn, vault_root)` signature; new test writes a real
  note file (not just a DB row) referencing `[[index]]` twice in its
  body, runs `sync_links()`, and asserts `total_wikilinks == 2` while
  `total_backlinks == 1` -- the concrete case the whole distinction
  exists for.
- `tests/test_gui_shell.py`'s `test_dashboard_panel_reflects_real_vault_stats`
  updated to the new `set_connection(conn, vault_root)` call; new test
  confirms the two new stat cards render with real counts from an actual
  note file + a synced `links` table, not just a bare DB row (the
  existing test's DB-only note has no file on disk, so `total_wikilinks`
  would read `0` for it -- fine as a "missing file is handled gracefully"
  case, but not what proves the real distinction).

### Verification

Implemented exactly per the plan above -- `count_wikilink_occurrences()`
landed in `graph/link_engine.py` and is re-exported from `graph/__init__.py`
(confirmed no import cycle: neither `link_engine.py` nor `graph/__init__.py`
imports anything from `storage`, so `storage/stats.py` importing from
`graph` is a clean one-way dependency). `get_vault_stats()`'s signature
change rippled to exactly the 4 call sites the plan named (2 in its own
tests, `dashboard_panel.py`, and the 2 `app.py` wiring points) -- no
surprises.

Verified: 313 tests pass (5 new -- the concrete wikilink-occurrences-vs-
backlink-edges distinction case at both the `storage/stats.py` layer and
the `DashboardPanel` layer, plus signature-update coverage), `uv run ruff
check .` clean, `flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches with no console
output beyond the usual benign GTK/Atk lines.

**Manual verification (you)**: confirm Total WikiLinks and Total
Backlinks show sensible numbers for your real vault (WikiLinks ≥
Backlinks, equal only if no note repeats the same link target within its
own body).

## Post-26 fix: index's edges hidden unless index is selected (inverted default)

### Context

Your test of Phase 26 confirmed the cap mechanism (switch + slider) works
correctly, but the base visibility was backwards: "we want there to [be]
NO lines visible when the index is [not] selected. Lines connecting to
the index should only show when index node is selected." Phase 26's
design had index's edges visible-by-default (then optionally capped once
selected) -- this restores the *original* deferred-list wording this
phase traces back to ("Link lines to index are hidden unless index node
is selected"), which got reinterpreted into "visible but capped" during
Phase 26's own planning. Confirmed with you: everything else -- the
switch, the slider, the ranking-by-recency, the caption -- stays exactly
as built; only the base-visibility polarity flips.

### Fix

One change in `src/llm_wiki/gui/graph_canvas.py`'s `_index_edge_visible()`:
was `if self._selected != _GRAVITY_WELL_SLUG or not
self._filter_index_edges_enabled: return True` (visible by default,
capped only once selected+enabled); now:

```python
def _index_edge_visible(self, neighbor, limit_visible):
    if self._selected != _GRAVITY_WELL_SLUG:
        return False  # hidden unconditionally unless index is selected
    if not self._filter_index_edges_enabled:
        return True  # selected + cap off -- show every connection
    return limit_visible is not None and neighbor in limit_visible
```

The enable switch/limit slider keep their exact prior meaning for the one
case they still govern: once index *is* selected, they decide between
"show everything" (switch off, unchanged default) and "show only the top
N by recency" (switch on). `_index_edges_caption_text()`'s not-selected
message changed from "Applies once index is selected" to "Hidden until
index is selected," matching the new behavior precisely -- no other
caption states changed.

### Tests

`tests/test_gui_shell.py`: `test_index_edge_visible_true_when_not_selecting_index`
became `test_index_edge_visible_false_when_index_is_not_selected` (now
asserts hidden regardless of the switch's state); new
`test_index_edges_are_hidden_by_default_when_index_is_not_selected`
locks in the shape-builder-level behavior (no index-touching lines with
nothing selected, or with a *different* node selected -- node circles and
the unrelated note-a/note-b edge unaffected either way).
`test_index_edges_caption_text_in_each_state` and
`test_selecting_index_updates_the_index_edges_caption_directly` updated
for the new not-selected message. Two pre-existing tests that predate
Phase 26 and happened to rely on the old always-visible default were
also fixed, not just tolerated: `test_filtered_out_nodes_are_excluded_
from_shapes_and_hit_testing` (a Type-filter test whose fixture's only
surviving edge happens to touch `index`) now correctly expects 0 lines
with nothing selected; `test_dynamic_edge_to_a_static_endpoint_draws_a_
shadow_circle_on_top` (the Post-22 z-order regression test, built around
a leaf node whose only edge goes to `index`) now forces `canvas._selected
= "index"` directly after starting the drag -- since dragging a
*different* node normally clears selection away from index, and the
z-order mechanics under test are orthogonal to that selection-follows-
drag behavior, forcing it directly isolates the thing actually being
tested rather than leaving the regression coverage silently broken by an
unrelated behavior change.

### Verification

Verified: 431 tests pass (2 new, 4 updated), re-run three times to
confirm no flakiness, `uv run ruff check .` clean, `uv run flet build
linux --python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt
bundle launches clean with no console output beyond the usual benign
GTK/Atk/OpenGL-timeout lines, no lingering process after exit.

**Manual verification (you)**: with the switch off (default) and nothing
selected, confirm zero lines touch `index` at all; select `index` and
confirm every connection appears (switch still off); turn the switch on
with a small limit and confirm only that many (most-recently-updated)
survive; deselect and confirm every index line disappears again
regardless of the switch's position.

## Post-26 fix #2: sliders were persisting (and logging) on every drag tick

### Context

Your next test pass found a real performance bug: dragging the Index
Connections slider produced "hundreds of appended log entries" in the
Pipeline Log, and afterward dragging nodes around the graph "slowed to a
stuttering crawl" -- restoring only after an app restart. You also
floated an alternative fix ("maybe the settings can be auto saved on
close vs every event").

Root-caused directly, not guessed at: `ft.Slider.on_change` fires on
*every* intermediate value during a drag gesture, not once per drag.
`_on_filter_index_edge_limit_changed()` (and every other slider handler
in the file) called `_apply_filter_change()`/`_apply_display_settings_change()`
on each of those events, which fires `on_filters_changed`/
`on_display_settings_changed` straight through to `app.py`'s
`controller.save_settings()` -- a synchronous `Path.write_text()` to
`.llm-wiki-config` *and* a `logger.info("Settings saved to ...")` call,
both on the event-loop thread, every single tick. A few seconds of
dragging plausibly produces hundreds of ticks, so hundreds of blocking
disk writes (explaining the live stutter while dragging) and hundreds of
Pipeline Log entries (explaining the lasting slowdown afterward -- a much
larger in-memory log control tree to redraw on every subsequent append,
until restart cleared it).

This wasn't unique to the Index Connections slider -- it's a systemic gap
across every continuous slider in the panel except one: Node Spacing
(Post-25 fix) already avoids it, since its relayout cost forced an
`on_change`/`on_change_end` split from the start. The same defect was
latent in Degrees, Simulation Strength, Min Zoom, and Max Zoom -- none of
them had been dragged heavily enough in prior testing to surface it, but
the mechanism is identical. Fixed all five, not just the one that got
caught, matching this project's established root-cause-not-symptom-patch
practice.

Regarding your save-on-close alternative: implemented instead as
save-on-drag-*release* (the same `on_change`/`on_change_end` split Node
Spacing already established), not save-on-app-close. This eliminates the
exact same per-tick spam with none of save-on-close's downside -- a crash
or force-quit before close would silently lose every setting changed
that session, whereas release-triggered persistence saves each real
change (checkbox, chip, switch, slider release, ...) as it happens, same
as before, just no longer hundreds of times per single slider drag.

### Fix

All in `src/llm_wiki/gui/graph_canvas.py`:

- `_apply_filter_change()` and `_apply_display_settings_change()` both
  gained a keyword-only `persist: bool = True` param -- `False` redraws
  live without firing the persistence callback. Every existing call site
  (checkboxes, chips, switches, date picks, Reset) keeps the default,
  unchanged.
- New `_persist_filter_change()` / `_persist_display_settings_change()`
  -- fire the persistence callback only, no redraw (already current from
  the slider's own `on_change` mutations). Wired as `on_change_end` on
  all five sliders: `_degrees_slider`, `_index_edges_slider`,
  `_simulation_strength_slider`, `_min_zoom_slider`, `_max_zoom_slider`.
- The five handlers (`_on_filter_degrees_changed`,
  `_on_filter_index_edge_limit_changed`, `_on_simulation_strength_changed`,
  `_on_min_zoom_changed`, `_on_max_zoom_changed`) now call their shared
  tail with `persist=False` -- still update their own value/caption and
  redraw the graph live on every tick (that part was never the problem),
  just don't touch disk until the drag ends.

### Tests

`tests/test_gui_shell.py`: five new tests (one per slider), each
confirming `on_change` alone never fires the persistence callback and
`on_change_end` (`_persist_filter_change()`/`_persist_display_settings_change()`)
fires it with the correct final value -- the same pattern already
established for Node Spacing's own `on_change`/`on_change_end` split.

### Verification

Verified: 435 tests pass (5 new), re-run twice to confirm no flakiness,
`uv run ruff check .` clean, `uv run flet build linux --python-version
3.13 --skip-flutter-doctor` succeeds, rebuilt bundle launches clean with
no console output beyond the usual benign GTK/Atk lines. One note: a
long-running `llm-wiki` process from your own manual testing was found
still alive (started well before this fix's own test launches, unrelated
to them) -- left untouched rather than killed, since it wasn't mine to
close.

**Manual verification (you)**: close your currently-running instance and
relaunch the rebuilt bundle; drag the Index Connections slider around
heavily and confirm the Pipeline Log no longer fills with entries and the
graph stays smooth; drag Degrees, Simulation Strength, and Min/Max Zoom
the same way and confirm the same; release each slider and confirm the
value still persists (close and reopen the vault, or restart, and check
it stuck).

## Post-26 fix #3: Degrees from Selected shouldn't route through index

### Context

You flagged a real flaw in the Degrees from Selected filter, confirmed by
the same Phase 18 structural guarantee that's already come up twice this
session (Post-26's index-edge cap, and the visibility inversion right
after it): every note has a direct edge to `index`. Left in the
undirected traversal `_update_degrees_from_selected()` already builds,
that guaranteed edge makes `index` a length-2 shortcut between *any* two
notes at all (`note-A -> index -> note-B`) -- so selecting a note and
setting degrees to 2 always showed the entire vault, regardless of real
topical distance, defeating the filter's purpose the moment it's used
past degree 1.

### Fix

One change in `src/llm_wiki/gui/graph_canvas.py`'s
`_update_degrees_from_selected()`: when the current selection is
anything other than `index` itself, `index` is removed from the
undirected graph before running `nx.single_source_shortest_path_length()`
-- forcing hop-distance to reflect genuine content links (source/entity
edges, any LLM-added cross-references from `SCHEMA.md`'s wikilink rule)
instead of the guaranteed backlink shortcut. Selecting `index` itself is
unaffected -- there, every note genuinely *is* one hop away, which is the
correct answer for "how far is this from the hub," not a shortcut
artifact to correct.

### Tests

`tests/test_gui_shell.py`: the existing
`test_degrees_filter_only_applies_with_a_selection` test's comment
updated to explain note-c is now *unreachable* (not "2 hops away") once
index is excluded -- its assertions were already correct at degree=1, so
no behavior change there. New
`test_degrees_filter_does_not_route_through_index_as_a_shortcut` locks in
the actual fix: raising degrees to 2 still excludes note-c (previously it
would have been included via the index shortcut), while selecting
`index` itself and setting degrees to 1 correctly includes note-c (the
unmodified-graph case).

### Verification

Verified: 436 tests pass (1 new), re-run twice to confirm no flakiness,
`uv run ruff check .` clean, `uv run flet build linux --python-version
3.13 --skip-flutter-doctor` succeeds, rebuilt bundle launches clean with
no console output beyond the usual benign GTK/Atk/OpenGL-timeout lines,
no lingering process after exit.

**Manual verification (you)**: select an ordinary note (not `index`),
enable Degrees from Selected, and confirm that raising the slider no
longer eventually shows the whole vault via the index shortcut -- only
notes genuinely connected through real content links (not just the
shared backlink to `index`) should appear as the degree count increases.
Select `index` itself and confirm degree=1 still shows every note, as
expected for the hub.

## Post-26 fix #4: settings panel switches were visually oversized

### Context

Deferred item 2, picked up 2026-07-30 with a screenshot showing the real
symptom: every `ft.Switch` in the Settings panel (Enable Filters, Type,
Tags, Search, Enable Simulation, Invert Scroll-Zoom, ...) rendered at its
full default Material size -- visually dwarfing the panel's 10.5px
labels and the reasonably-proportioned Type checkboxes right next to
them.

Checked the installed Flet API directly before guessing at a fix:
`Switch` itself has no dedicated size property, but every visual control
(via the shared `LayoutControl` base) carries a generic `scale` paint
transform. Confirmed this alone isn't sufficient -- `scale` is a
Flutter-level paint-only transform, so it shrinks what's drawn but not
the layout box the Switch reserves in its parent `Row`, which would
leave a shrunk switch rattling around inside its old-sized slot rather
than actually tightening the panel's visual rhythm.

### Fix

New `GraphCanvas._compact_switch(switch)` (`src/llm_wiki/gui/
graph_canvas.py`): sets `switch.scale = _COMPACT_SWITCH_SCALE` (0.7) and
wraps it in a fixed-size, centered `ft.Container` (`_COMPACT_SWITCH_WIDTH
= 36`, `_COMPACT_SWITCH_HEIGHT = 22`) -- the Container's own fixed
dimensions are what actually tighten the reserved layout space, not just
the visual. Applied at all four places a Switch is placed into a Row:
`_build_filter_section_box()` (covers Type/Tags/Search/Date/Degrees/
Index Connections' six switches in one shared spot), plus the three
switches built and placed directly by their own sections (`_build_
filters_section()`'s "Enable Filters" master switch, `_build_physics_
content()`'s "Enable Simulation", `_build_zoom_pan_content()`'s "Invert
Scroll-Zoom"). All nine settings-panel switches now go through the same
helper. Every stored `self._*_switch` reference is untouched -- only
scale is mutated on the existing instance and a new wrapper Container is
what actually gets placed in the tree, so every existing `.value`/
`.update()` call site (sync methods, tests) keeps working unchanged.
The three checkboxes-based Type filter and every other control are
untouched -- they weren't part of the reported inconsistency.

Both constants are framed as starting values, same as every other visual
constant in this file (`_SIM_*`, slider ranges, ...) -- expected to be
retuned against the real UI, not treated as final.

### Tests

`tests/test_gui_shell.py`: new `test_compact_switch_scales_and_wraps_
in_a_fixed_size_container` (the helper itself: scale set on the same
switch instance, returns a `Container` of the expected fixed size
wrapping that exact instance, not a copy) and `test_every_settings_
panel_switch_is_compact` (all nine real switches built during
construction carry the scale -- locks in that every call site actually
routes through the helper, not just that the helper works in isolation).

### Verification

Verified: 438 tests pass (2 new), re-run twice to confirm no flakiness,
`uv run ruff check .` clean, `uv run flet build linux --python-version
3.13 --skip-flutter-doctor` succeeds, rebuilt bundle launches clean with
no console output beyond the usual benign GTK/Atk lines, no lingering
process after exit.

**Manual verification (you)**: open the Settings panel and confirm every
switch now sits proportionally with its label and the Type checkboxes,
rather than dwarfing them; confirm every switch still toggles correctly
(click target still easy to hit, not so small it's fiddly) and that
nothing looks clipped or misaligned inside its new compact box. The
0.7 scale / 36x22 box are starting values -- if it's still too
big/small, or the click target feels too small, that's a one-line
constant tweak (`_COMPACT_SWITCH_SCALE`/`_COMPACT_SWITCH_WIDTH`/
`_COMPACT_SWITCH_HEIGHT`), not a design change.

**Confirmed on the real build (you)**: still slightly too big at 0.7x --
`_COMPACT_SWITCH_SCALE` dropped to `0.65`, with `_COMPACT_SWITCH_WIDTH`/
`_COMPACT_SWITCH_HEIGHT` scaled down proportionally (36x22 -> 33x20, the
same ~51x31 assumed full-size Switch footprint the original 36x22 box
was itself derived from at 0.7x) so the wrapper keeps tracking the
switch's actual scaled size rather than leaving extra slack around it.
Verified: 438 tests pass (constants read dynamically via
`graph_canvas._COMPACT_SWITCH_*`, no test changes needed), `uv run ruff
check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean.

## Post-26 fix #5: uniform sizing for Categories swatches and Type checkboxes

### Context

Follow-up to fix #4, with a screenshot: once switches got compact, the
Categories color swatches and Type checkboxes still looked mismatched --
the swatches tiny and tight, the checkboxes comparatively huge with wide
gaps between rows. You gave exact, numeric target ratios: swatch diameter
should match the switch's own "on"-state thumb circle; checkbox size
should be a square matching the switch's full height (track + border);
swatch row spacing should be 3/4 of checkbox row spacing, and checkbox
row spacing itself should first shrink to 3/4 of its current value.

Checked Material 3's own published Switch spec (track 52x32dp, "on"-state
thumb 24dp diameter -- documented, stable values) before picking numbers:
at `_COMPACT_SWITCH_SCALE` (0.65), those spec numbers land almost exactly
on `_COMPACT_SWITCH_WIDTH`/`_COMPACT_SWITCH_HEIGHT` (33/20) -- confirming
the earlier fix's box was already, without realizing it, tracking the
real Material spec. The same 24dp thumb figure, scaled the same way,
gives the swatch target: 24 * 0.65 ≈ 15.6, rounded to 16.

### Fix

All in `src/llm_wiki/gui/graph_canvas.py`:

- New constants: `_CATEGORY_SWATCH_DIAMETER = 16.0` (Material 3's 24dp
  "on"-thumb spec, scaled); `_COMPACT_CHECKBOX_SIZE = _COMPACT_SWITCH_
  HEIGHT` (a square matching the switch's own height exactly, per the
  literal ask); `_COMPACT_CHECKBOX_SCALE = 0.75` (Checkbox's default
  Material footprint, like Switch's, is bigger than its visible box --
  same paint-only-transform caveat, so needs the same scale + fixed-box
  pairing, not just a smaller reserved Container); `_TYPE_CHECKBOX_ROW_
  SPACING = 1.5` (2.0 * 0.75, the literal "3/4 of its current gap") and
  `_CATEGORY_SWATCH_ROW_SPACING = 1.125` (1.5 * 0.75, "3/4 the spacing
  between the checkboxes").
- New `_compact_checkbox(checkbox)`, mirroring `_compact_switch()`
  exactly: sets `.scale`, wraps in a fixed `_COMPACT_CHECKBOX_SIZE` x
  `_COMPACT_CHECKBOX_SIZE` centered Container.
- `_build_type_content()`: each checkbox now goes through `_compact_
  checkbox()` before being placed in its Row; the Column's `spacing`
  changed from `2` to `_TYPE_CHECKBOX_ROW_SPACING`.
- `_build_color_picker_row()`: `trigger_swatch`'s `width`/`height`
  changed from the old `8`/`8` to `_CATEGORY_SWATCH_DIAMETER`, `border_
  radius` to half that (keeps it a circle at any size).
- `_build_legend_section()`: Column `spacing` changed from `5` to
  `_CATEGORY_SWATCH_ROW_SPACING`.

Every stored `self._type_checkboxes[...]`/`self._color_swatch_controls
[...]` reference is untouched -- only `.scale` is mutated on the existing
Checkbox instance and a new wrapper Container is what gets placed in the
tree, so `_on_type_color_selected()`'s existing `checkbox.fill_color =
...` / `swatch.bgcolor = ...` mutations keep working unchanged.

### Tests

`tests/test_gui_shell.py`: `test_compact_checkbox_scales_and_wraps_in_a_
fixed_square_matching_switch_height` (the helper itself, including the
explicit width==height==`_COMPACT_SWITCH_HEIGHT` assertion locking in
the "matches the switch's height, as a square" requirement literally);
`test_every_type_checkbox_is_compact` (all four real checkboxes actually
route through the helper); `test_category_swatches_match_the_switch_
thumbs_diameter_and_tightened_spacing` (swatch dimensions/radius, plus
both spacing ratios asserted directly against their derivation formulas
so a future constant change that breaks the stated ratio is caught).

### Verification

Verified: 441 tests pass (3 new), re-run twice to confirm no flakiness,
`uv run ruff check .` clean (one Yoda-condition fix via `ruff check
--fix`), `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk/OpenGL-timeout lines, no
lingering process after exit.

**Manual verification (you)**: open the Settings panel and confirm the
Categories swatches, Type checkboxes, and switch thumbs all now read as
roughly the same visual scale -- no single control dominating the others;
confirm checkbox rows sit noticeably closer together than before, and
that swatch rows are tighter still; confirm every checkbox and swatch
still functions correctly (clickable, not clipped, colors still legible
at the smaller size). These are principled-but-unverified-on-a-real-
screen starting values (the Material 3 spec numbers are real; how Flet's
actual Flutter renderer maps onto them at this project's scale factor
hasn't been visually confirmed yet) -- exactly the kind of thing worth a
quick "still off, try X" round-trip if the first pass isn't quite right.

**Confirmed on the real build (you)**: Type checkboxes correct on the
first try -- unchanged. Categories still off in two ways, both in the
opposite direction from what the spec-derived numbers predicted: the
swatches read too large, and the rows too close together (a screenshot
confirmed both). `_CATEGORY_SWATCH_DIAMETER` dropped from `16.0` (the
Material 3 spec figure) to `12.0`; `_CATEGORY_SWATCH_ROW_SPACING`
decoupled entirely from the "3/4 of Type's spacing" formula (which had
produced `1.125`, now confirmed too tight once the swatches themselves
also shrank) and set independently to `6.0` -- wider than Type's own
`1.5`, on the reasoning that smaller circles need comparatively more gap
to not look bunched, the opposite relationship the original ratio
assumed. The corresponding test
(`test_category_swatches_match_the_switch_thumbs_diameter_and_tightened_
spacing`) was rewritten as `test_category_swatches_are_sized_and_spaced_
per_the_tuned_constants`, asserting the tuned values directly (and that
category spacing exceeds Type's) instead of the now-false formula
relationship.

Verified: 441 tests pass (1 rewritten, not net-new), re-run twice to
confirm no flakiness (one pre-existing, unrelated `test_terminal_panel.py`
teardown-race warning noted in the full-suite run, consistent with prior
sessions), `uv run ruff check .` clean, `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean.

**Confirmed on the real build (you)**: `12.0` still read slightly small.
`_CATEGORY_SWATCH_DIAMETER` bumped to `14.0`; row spacing (`6.0`)
confirmed correct, left unchanged. Verified: 441 tests pass (no test
changes needed -- the existing test reads the constant dynamically), `uv
run ruff check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean.

**Follow-up (you)**: with checkboxes and swatches now uniform, the
Filters/Display sliders' thumbs (Degrees, Index Connections, Simulation
Strength, Min/Max Zoom, Node Spacing) stood out as oversized by
comparison -- asked to match them to the swatch diameter too. Unlike
Switch/Checkbox, `ft.Slider` has no per-instance size property at all
(confirmed against the Flet API) -- but its Material theme does,
`SliderTheme.thumb_size` (a `Size`), reachable per-subtree via `Container
.theme` (confirmed present on `Container`, cascades to every descendant
control, not just an app-wide setting). `_build_settings_panel()`'s
outer returned `Container` gained `theme=ft.Theme(slider_theme=ft.
SliderTheme(thumb_size=ft.Size.square(_CATEGORY_SWATCH_DIAMETER)))` --
scoped to just the settings panel's own subtree (nothing in `theme.py`'s
app-wide theme touched, and no Slider elsewhere in the app affected),
reusing the swatch constant directly per the explicit ask rather than a
new hardcoded number. Every Slider in the panel inherits it for free --
no per-Slider changes needed.

Tests: new `test_settings_panel_scopes_a_slider_thumb_size_matching_the_
swatches` asserts the theme is actually attached to the settings panel
Container and its `thumb_size` matches `_CATEGORY_SWATCH_DIAMETER`
exactly (both width and height, confirming a square).

Verified: 442 tests pass (1 new), re-run twice to confirm no flakiness,
`uv run ruff check .` clean, `uv run flet build linux --python-version
3.13 --skip-flutter-doctor` succeeds, rebuilt bundle launches clean, no
lingering process after exit. This closes out Post-26 fix #5.

**Manual verification (you)**: open the Settings panel and confirm every
slider's thumb (Degrees, Index Connections, Strength, Min/Max Zoom, Node
Spacing) now reads at roughly the same size as the Categories swatches;
confirm sliders still drag smoothly and the thumb is still comfortably
clickable at the smaller size, not too fiddly.

**Confirmed on the real build (you)**: `thumb_size` alone didn't shrink
it -- an annotated screenshot showed the thumb still clearly oversized
next to the switch's own (correctly-sized) thumb. Root cause: Material
3's redesigned slider (the style Flet's `Slider.year_2023` defaults to)
uses a pill/bar-shaped "handle" thumb, not a circle -- `SliderTheme.
thumb_size` doesn't shrink that shape down to something round the way it
does the classic pre-redesign thumb. Added `year_2023=True` to the same
`SliderTheme`, reverting to the classic filled-circle thumb (the same
shape Switch/Checkbox already use) where `thumb_size` behaves as the
well-documented radius/diameter it was expected to be -- both settings
scoped together to the panel's own subtree, same as before. Note for
your next look: this also reverts the slider *track*'s visual style from
the current "gapped" M3 look to the classic continuous thin line -- a
side effect of the fix, not something separately requested; flag it if
you'd rather keep the gapped track and find another way to shrink just
the thumb.

Tests: `test_settings_panel_scopes_a_slider_thumb_size_matching_the_
swatches` extended with a `year_2023 is True` assertion.

Verified: 442 tests pass (no new tests, one extended), `uv run ruff
check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean.

**Follow-up (you)**: "I dont think we applied the slider thumb size" --
`year_2023=True` was in the code and the build was current, but a
targeted `get_api("Slider", member="year_2023")` call (per the newly
adopted "check the MCP first" habit) turned up one more documented
condition on that exact property: *"If `flet.Theme.use_material3` is
`False`, then this property is ignored."* The nested `ft.Theme(slider_
theme=...)` never set `use_material3` at all -- left at the dataclass's
own unset default rather than confirmed as genuinely inheriting `True`
from `theme.py`'s app-wide Theme (which also never sets it explicitly,
relying on Flutter's own default) -- so depending on exactly how a
nested `Container.theme` merges with its ancestor, `year_2023` was a
real candidate for being silently ignored regardless of being set.
Fixed by adding `use_material3=True` explicitly to the same nested
Theme, removing that ambiguity outright rather than leaving it to
whatever the merge behavior turns out to be.

Tests: the same test extended once more with a `panel_theme.
use_material3 is True` assertion.

Verified: 442 tests pass (one test extended, no net-new), `uv run ruff
check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean, no
lingering process after exit.

**Manual verification (you)**: confirm the slider thumb now actually
reads as a small round circle matching the swatches, not the earlier
oversized appearance.

**Follow-up (you)**: "That did not work." You tried a few things
yourself, put them back the way they were, and offered a specific
diagnosis: the theme was set on the *outer* settings-panel Container --
several controls away from any actual Slider (Container -> Column -> a
section-box Container -> Column -> Slider) -- and you suspected Flet's
build-once, call-each-`_build_*`-function style means the sliders aren't
genuinely resolving that ancestor's theme at build time, unlike a true
page-wide theme or a theme set directly on each control's own immediate
parent.

Rather than guess again, this became an empirical test of your specific
hypothesis: `_themed_slider(slider)` now wraps *each* Slider directly in
its own `ft.Container(theme=..., content=slider)` -- zero controls in
between -- replacing the single outer-panel `theme=` entirely. Applied
at all six Slider placement sites (Degrees, Index Connections, Simulation
Strength, Min Zoom, Max Zoom, Node Spacing). The wrapper keeps `expand=
True` so the Slider doesn't lose its existing full-width behavior once
it's inside an otherwise-unconfigured Container. Same `thumb_size`/
`year_2023`/`use_material3` combination as before, just relocated to sit
immediately next to the control it's meant to affect.

### Tests

`tests/test_gui_shell.py`: the old outer-panel-theme test replaced with
`test_themed_slider_wraps_directly_in_a_container_with_the_expected_
theme` (the helper itself: returns a `Container` wrapping the exact same
Slider instance, `expand=True`, and the full theme payload) and new
`test_every_settings_panel_slider_is_individually_themed`, which walks
the *actual built tree* from `_settings_panel` down (a small local
`_find_container_wrapping()` tree-search helper, since none of the six
wrapper Containers are stored as `self.*` references the way the inner
Sliders are) to confirm each of the six real sliders is genuinely wrapped
in a themed Container in the real control tree, not just that the helper
function works in isolation.

### Verification

Verified: 443 tests pass (2 new, 1 removed), re-run twice to confirm no
flakiness, `uv run ruff check .` clean, `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean, no lingering process after exit.

**Manual verification (you)**: this is the real test of your
hypothesis -- confirm the slider thumbs are now genuinely small and
round (not still oversized), and confirm every slider still visually
spans the full panel width (the `expand=True` on each wrapper Container
is what's supposed to preserve that, worth double-checking specifically
since it's new).

**Confirmed on the real build (you)**: "No change in slider thumb
size" -- the per-Slider wrap alone still didn't work. You then found and
personally tested Flet's own official docs example
(`ft.Container(theme=...)` demo with three side-by-side buttons: "Page
theme," "Inherited theme with primary color overridden," "Unique
theme") and reported the *middle* case -- a bare `Container.theme=`, the
exact pattern this whole fix chain had been using -- does not visibly
apply in the live example either, while "Page theme" (`page.theme=`) and
"Unique theme" work. The difference between the working and non-working
Container cases in that same official example: "Unique theme" pairs
`theme=` with an explicit `theme_mode=`. This lines up with `Container.
theme_mode`'s own docstring (already read once, its significance missed
the first time): it "resets" the parent theme and creates a new,
unique scheme for everything inside -- the actual mechanism that
activates a nested `theme=` override at all, not an unrelated
dark/light toggle.

`_themed_slider()` gained `theme_mode=ft.ThemeMode.DARK` alongside its
existing `theme=`. `ThemeMode.DARK` was picked deliberately, not
arbitrarily: it matches this app's own permanent `page.theme_mode`
(set once in `app.py`, this app has no light-mode path at all), so nothing
about the app's actual rendered appearance changes -- the sole purpose
of setting it here is to switch on the "unique theme" codepath your
testing identified as the real requirement.

Tests: both slider-theme tests (`test_themed_slider_wraps_directly_in_a_
container_with_the_expected_theme`, `test_every_settings_panel_slider_
is_individually_themed`) extended with a `theme_mode == ft.ThemeMode.DARK`
assertion.

Verified: 443 tests pass (no net-new, two extended), re-run twice to
confirm no flakiness, `uv run ruff check .` clean, `uv run flet build
linux --python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt
bundle launches clean, no lingering process after exit.

**Manual verification (you)**: check the slider thumb size again on this
build specifically -- this is the first version with the piece your own
docs testing identified as actually necessary (`theme_mode`, not just
`theme`), so it's the first real test of whether the underlying
mechanism works at all for this app.

**Confirmed on the real build (you)**: "Sliders are now blue, size not
changed." Genuinely useful signal, not just another failure: the color
change is direct proof `theme_mode` *did* activate the nested theme --
it fell back to Flutter's stock blue specifically because this nested
Theme had never set a `color_scheme` of its own, unlike the app's real
purple palette. That also means every *earlier* size test (`thumb_size`
alone, then `thumb_size` + `year_2023`) was run before `theme_mode`
existed -- none of those tests were clean, since the whole nested theme
was inert the entire time, not just the slider-specific fields within
it. Two changes: (1) `color_scheme=ft.ColorScheme(primary=theme.ACCENT,
surface=theme.APP_BG, error=theme.ERROR)` added to the nested Theme --
the exact same three values `theme.py`'s own `build_theme()` uses --
fixing the blue regression outright. (2) `year_2023=True` dropped,
keeping only `thumb_size`: with the theme now confirmed to genuinely
activate, `year_2023=True` still left the thumb unchanged, meaning the
classic thumb shape doesn't actually respect `thumb_size` either (the
opposite of what its docs implied) -- `thumb_size` against the *current*
default M3 "Handle" shape hadn't been cleanly tested until now.

Tests: `test_themed_slider_wraps_directly_in_a_container_with_the_
expected_theme` dropped its `year_2023` assertion, gained one for
`color_scheme` matching `theme.ACCENT`/`APP_BG`/`ERROR` exactly.
`test_every_settings_panel_slider_is_individually_themed` swapped its
`year_2023` check for `thumb_size.width` and `color_scheme.primary`.

Verified: 443 tests pass (no net-new, two extended), re-run twice to
confirm no flakiness, `uv run ruff check .` clean, `uv run flet build
linux --python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt
bundle launches clean, no lingering process after exit.

**Manual verification (you)**: confirm the sliders are back to the app's
real purple/dark palette (not blue), and check the thumb size again --
this is the first clean test of `thumb_size` against the actual default
M3 thumb shape, with everything else about the mechanism now confirmed
working.

**Confirmed on the real build (you)**: "same blue no size change" --
meaning the `color_scheme` fix from the previous round *also* didn't
apply, not just the size. That's a stronger, different signal than
before: two independent fields inside the same nested `theme=` payload
both failed to reach the widget even with `theme_mode` set, which
undercuts the working theory that `theme_mode` genuinely "activates" a
nested Container's `theme=` contents at all -- something about
`Container.theme` + `theme_mode` in this app's actual structure isn't
behaving the way the isolated docs example implied.

Switched to the one remaining approach directly proven to work in that
same official example: `page.theme` (an app-wide theme, not a nested
override). `theme.py`'s `build_theme()` gained `slider_theme=ft.
SliderTheme(thumb_size=ft.Size.square(CATEGORY_SWATCH_DIAMETER),
year_2023=True)` alongside its existing `color_scheme=`. Safe as an
app-wide change specifically because this app has no `Slider` anywhere
outside the graph canvas Settings panel -- confirmed by grep, not
assumed. `_CATEGORY_SWATCH_DIAMETER` moved out of `graph_canvas.py` into
`theme.py` as a public `CATEGORY_SWATCH_DIAMETER` (now shared by the
Categories swatches *and* the page-wide slider override, a genuine
single source of truth rather than a duplicated magic number).
`GraphCanvas._themed_slider()` and all six of its call sites are
removed entirely -- with the override living at `page.theme`, no
per-Slider wrapping is needed at all, and the nested-Container approach
is now known not to work reliably in this app regardless.

Tests: the two now-obsolete nested-theme tests (`test_themed_slider_
wraps_directly_in_a_container_with_the_expected_theme`, `test_every_
settings_panel_slider_is_individually_themed`, plus their shared
`_find_container_wrapping()` tree-search helper) are removed, replaced
by `test_build_theme_sets_a_page_wide_slider_thumb_size`, asserting
`theme.build_theme()`'s own `slider_theme.thumb_size` directly. The
existing swatch-sizing test updated for the constant's new home
(`theme.CATEGORY_SWATCH_DIAMETER`, not `graph_canvas._CATEGORY_SWATCH_
DIAMETER`).

Verified: 442 tests pass (1 net removed vs. the prior round -- two old
tests out, one new test in), re-run twice to confirm no flakiness, `uv
run ruff check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean, no
lingering process after exit.

**Manual verification (you)**: this is the last remaining approach
directly validated by your own docs testing -- confirm the sliders are
back to the app's real palette (not blue) and check the thumb size one
more time. If this still doesn't change the size, that's a strong signal
this specific cosmetic (matching the slider thumb to the swatch
diameter) may not be achievable through Flet's theme system as currently
documented/implemented, and worth deciding whether to accept the
default thumb size rather than continue guessing further.

**Confirmed on the real build (you)**: color correct, size "smaller, but
slight, hard to tell." Since the size difference was too subtle to
confirm by eye alone, ran a one-shot diagnostic: `slider_theme` gained a
temporary, unmistakable `thumb_color=ft.Colors.GREEN` override -- if
`page.theme.slider_theme` reaches the widget at all, the thumb *must*
turn green, independent of the harder-to-eyeball size question. Result:
**green**, confirmed by you. This proves the whole mechanism genuinely
works -- `thumb_size` is a real, live lever, not a coincidence -- so the
earlier "smaller, but slight" size read was accurate, not wishful
thinking. Diagnostic color reverted immediately after confirming (no
`thumb_color` override at all now, so it inherits `color_scheme.primary`
same as every other accent-colored control).

Verified: 442 tests pass, `uv run ruff check .` clean, `uv run flet
build linux --python-version 3.13 --skip-flutter-doctor` succeeds twice
(once with the green diagnostic, once with it reverted), rebuilt bundle
launches clean both times, no lingering process after exit.

This closes out the size-matching side quest that started with "the
slider thumb still reads oversized" -- the mechanism (`page.theme`'s
`slider_theme`) is confirmed genuinely working end to end. Whether
`_CATEGORY_SWATCH_DIAMETER` (14.0) is the *right* final size, now that
it's confirmed to actually apply, is open for your judgment -- it read
as only a slight reduction, so it may be worth a further decrease if you
want it to match the swatches more closely, now that we know any value
here will genuinely take effect.

**Follow-up (you)**: dropped `_CATEGORY_SWATCH_DIAMETER` from `14` to
`8` (a dedicated `SLIDER_THUMB_DIAMETER` constant, decoupled from the
swatch size) specifically to make the size effect unmistakable one way
or the other -- and got "No change." You then asked directly whether
`year_2023` was still set on each Slider control (a real, correct
question: `Slider.year_2023`'s own docs say a per-control value
overrides the theme's), which turned out not to be the actual bug
(grep confirmed no control ever sets it), but prompted rereading
`year_2023`'s docstring properly for the first time: `False` = "the
*latest* Material 3 appearance, introduced December 2023," `True` =
"*the 2023* Material 3 appearance" -- two different Material-3-era
slider redesigns, not "classic vs. redesigned." Every earlier attempt
had `year_2023=True` set, meaning `thumb_size` had never actually been
tested against Flet's real default style. Dropped it -- still no change.

At that point, direct research (Flutter's own API docs, not another
guess) settled it definitively: `SliderThemeData.thumbSize` "is used to
set the size of the `HandleThumbShape` thumb **when** `SliderThemeData.
thumbShape` is `HandleThumbShape`." Flet's `SliderTheme` exposes
`thumb_size` but has no `thumb_shape` property at all -- there is no way,
through Flet's API, to select the one thumb shape that actually reads
`thumb_size`. This is a structural gap in Flet's bindings, not a
configuration mistake: every attempt across this entire investigation
(nested `Container.theme`, `page.theme`, both `year_2023` values,
multiple diameters) was reaching for a property that cannot have any
effect without a companion property Flet doesn't expose.

You then asked about returning to the Container-level approach --
specifically applying `scale` there, the same technique already proven
working for the compact switches/checkboxes, rather than the
`theme=`/`theme_mode=` approach tried earlier at that same nesting
level. `scale` is a genuinely independent mechanism from `thumb_size` --
a raw paint transform, not dependent on `SliderThemeData` at all -- so
it isn't subject to the same dead-property problem.

### Fix

`theme.py`'s `slider_theme=` override (and the now-pointless
`SLIDER_THUMB_DIAMETER` constant) removed entirely from `build_theme()`
-- nothing left to configure there. New `GraphCanvas._compact_slider()`
in `graph_canvas.py`, mirroring `_compact_switch()`/`_compact_checkbox()`
exactly: sets `slider.scale = _COMPACT_SLIDER_SCALE` (0.7, a starting
value) and wraps in a `Container`. One deliberate difference from the
switch/checkbox wrappers: no fixed `width`/`height` -- a bare Slider
already claims the full available width on its own (confirmed
empirically: every earlier Container-wrapped Slider test in this same
investigation, going back to the very first nested-theme attempt, never
once narrowed the visible track), so the wrapper only needs `alignment=
CENTER` to keep the now-visually-smaller scaled control centered within
that unchanged reserved space, not to resize the box itself. Applied at
all six Slider placement sites (Degrees, Index Connections, Simulation
Strength, Min Zoom, Max Zoom, Node Spacing), same as every earlier
per-Slider wrapping attempt.

Unlike `_compact_switch()`/`_compact_checkbox()`, this scales the
*whole* control -- track and thumb together -- since Slider has no way
to shrink only the thumb. Flagged directly to you before implementing:
the track will visually shrink too, not just the thumb, which is a real
tradeoff worth judging on the actual build.

### Tests

`tests/test_gui_shell.py`: the now-obsolete `test_build_theme_sets_a_
page_wide_slider_thumb_size` replaced with `test_compact_slider_scales_
and_centers_within_its_unchanged_width` (the helper itself: `.scale` set
on the same Slider instance, wrapped in a `Container` with no fixed
width, `alignment=CENTER`) and `test_every_settings_panel_slider_is_
compact` (all six real sliders built during construction carry the
scale).

### Verification

Verified: 443 tests pass (2 new/replaced), re-run twice to confirm no
flakiness, `uv run ruff check .` clean, `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean, no lingering process after exit.

**Manual verification (you)**: this is the real test -- confirm the
slider thumbs are now visibly smaller, and specifically check whether
the shorter/scaled-down track is an acceptable look or feels cramped.
Also drag each slider and confirm dragging still tracks the cursor
correctly at the new scale (Flutter's `Transform` hit-testing generally
accounts for the render transform by default, but this hasn't been
verified against this app's actual `GestureDetector`-based drag handling
specifically, so it's worth deliberately testing, not assuming).
`_COMPACT_SLIDER_SCALE` (0.7) is a starting value like every other
compact-control constant in this file -- a one-line tweak either
direction if the track/thumb balance isn't right yet.

## Deferred list roadmap

A sequencing plan for the four items originally on the deferred list, per
your 2026-07-29 request and ordering. Same rhythm as every phase before
it: **one item at a time, in this order** — each gets planned in full
implementation-level detail (exact functions/files/tests, the way Phases
16-18 were) immediately before it starts, not all up front.

**Phase 19 — Terminal panel — done.** See the full write-up above.

**Phase 20 — MCP Start/Stop, fixed for real — done.** See the full
write-up above.

**Phase 21 — LLM-Wiki Dashboard panel — done.** See the full write-up
above.

**Phase 22 — Graph canvas live force simulation.** Scoped in full below.

## Phase 22 — Graph canvas: live local force simulation while dragging

### Context

Requested 2026-07-29, right after the node-spacing fix (Post-18). Three
related asks: (1) real-time dynamic repelling while dragging a node with
the mouse -- other nodes should actively push away as it moves near them,
not just sit static; (2) a dragged node's connected neighbors should
visually "trail" it (follow with some lag/elasticity) rather than staying
frozen until the next full layout recompute; (3) a general springy/
elastic feel to node movement rather than the current instant
snap-to-position. Today, `graph_canvas.py`'s layout is a single
`nx.spring_layout()` call per `set_graph()` -- a one-shot force-directed
solve, not a running simulation -- and `_on_pan_update()`'s node-drag path
(`graph_canvas.py:186-192`) just writes the dragged node directly into
`self._positions` with no effect on any other node.

**Your own hunch -- re-running `nx.spring_layout(pos=..., iterations=
small)` every drag frame, seeded from the previous frame -- was tested
directly and found not to work.** Two things were checked before writing
this plan, both with real code, not reasoning alone:

1. **Speed: not a problem.** Benchmarked `nx.spring_layout()` re-run per
   frame (`pos=` seeded from the prior call, `fixed=[dragged_node]`,
   small `iterations`) against synthetic hub-and-spoke graphs matching
   this vault's actual shape (every note links to `[[index]]` and its
   source, per Phase 18). Results: even at 200 nodes (far past this
   vault's current size) and 5 iterations/frame, each call takes ~5ms --
   comfortably under a 33ms (30fps) frame budget, with no threading
   needed at all. At realistic vault sizes (20-50 nodes) it's sub-0.3ms.
2. **Stability: a real problem, confirmed by direct repro.** Read
   networkx's actual `_fruchterman_reingold()` source
   (`networkx/drawing/layout.py`): each call computes its own "cooling"
   temperature `t = 0.1 * current_position_spread`, decaying linearly to
   0 over exactly that call's `iterations` -- it has no memory of any
   previous call's temperature. Calling it repeatedly with a *moving*
   fixed node and a small `iterations` count means every single call
   restarts at a large, barely-cooled step size, computed from whatever
   the position spread happens to be that frame. A direct repro (30 nodes,
   fixed node moved in a synthetic sweep, 2 iterations/frame, 60 frames)
   produced runaway divergence -- node coordinates blew up to the
   hundreds-of-thousands range within seconds. This isn't a tuning
   problem; it's how the algorithm's per-call temperature reset behaves
   under repeated re-seeding with a moving anchor, confirmed by reading
   the source, not guessed at.

**The fix: a small hand-rolled, locally-scoped damped spring-mass step,
not `nx.spring_layout()`, for the live phase.** `nx.spring_layout()`
stays exactly as-is for the one-shot full-graph layout in `set_graph()`
(unchanged, still runs off-thread via the existing `_layout_worker()`
path). The live-drag phase uses a small, explicit, per-tick force
calculation with a *fixed* small step size and velocity damping --
inherently stable regardless of how far/fast the anchor moves, since
every step is clamped (unlike the temperature-reset behavior above) --
validated by direct simulation (30 ticks of drag + 60 ticks of settle
against representative constants): a neighbor node visibly trails behind
the moving anchor, a non-neighbor bystander within radius gets pushed
aside, and both smoothly decay back toward their pre-drag position once
released, with no divergence.

This only touches nodes actually near or connected to the node being
dragged -- not a whole-graph ambient physics engine -- matching the
literal scope of the three asks (all phrased in terms of "while
dragging" and "connected neighbors"), and keeping the cost trivially
bounded regardless of vault size.

### Implementation

All changes confined to `src/llm_wiki/gui/graph_canvas.py`. New imports:
`asyncio`, `math`.

**New module constants** (starting values from the validated simulation
above -- expect to tune these empirically against the real UI, same as
`_LAYOUT_SPACING` was tuned in the Post-18 fix):

```python
_SIM_TICK_DT = 1 / 30            # seconds between simulation ticks
_SIM_REPEL_RADIUS = 90.0         # px; bystanders inside this get pushed away
_SIM_REPEL_STRENGTH = 4000.0
_SIM_NEIGHBOR_REST_LENGTH = 70.0  # px; a dragged node's neighbor eases toward this distance
_SIM_NEIGHBOR_SPRING_K = 18.0
_SIM_HOME_SPRING_K = 16.0         # pulls a perturbed node back toward its pre-drag spot
_SIM_DAMPING = 0.72
_SIM_MAX_SPEED = 900.0            # px/sec, hard clamp -- guarantees stability
_SIM_SETTLE_DIST_EPSILON = 2.0    # px; below this + anchor released = at rest
_SIM_SETTLE_MAX_TICKS = 90        # safety cap (~3s) so the loop always terminates
```

**Settle detection is distance-based, not velocity-based -- a second
stability finding from direct simulation, not just reasoning.** An
earlier draft of this plan used a velocity-threshold settle check
(`speed < epsilon`). Simulating it against a realistic post-drag scenario
(15 ticks of drag momentum, then release) surfaced a real bug: with
per-tick multiplicative damping, velocity can dip under any fixed
threshold while a node is still tens of pixels from home -- each tick
discards a fixed *fraction* of velocity regardless of how much force the
spring is still applying, so a heavily-damped node can cross a low
velocity threshold well before it's actually arrived, permanently
stranding it there once the loop marks it settled and stops touching it.
Switched the per-node settle check to compare its *position* against
`_home_positions[slug]` instead (`_SIM_SETTLE_DIST_EPSILON`) -- this
directly measures the thing that actually matters and has no equivalent
failure mode. Retuned `_SIM_HOME_SPRING_K`/`_SIM_DAMPING` alongside this
fix (verified by simulation against the same worst-case scenario): a
perturbed node now reliably converges to within 2px of home in roughly
50-60 ticks (~2s), comfortably inside the 90-tick safety cap, while the
repulsion force at typical interaction range still stays roughly an
order of magnitude stronger than the home-spring's pull, so it doesn't
visibly fight the repel/trail effect during an active drag.

**New `GraphCanvas` state** (added in `__init__`, alongside the existing
`_dragging`/`_panning`/`_selected` fields):
- `self._home_positions: dict[str, tuple[float, float]] = {}` -- snapshot
  of `self._positions` taken the instant a node-drag begins; the anchor
  every perturbed node eases back toward once released.
- `self._sim_velocities: dict[str, tuple[float, float]] = {}`
- `self._sim_active_nodes: set[str] = set()` -- nodes currently being
  integrated (grows as the anchor passes near new nodes, shrinks as they
  settle back to rest).
- `self._sim_active: bool = False` -- whether `_simulation_loop()` is
  currently running.
- `self._sim_settle_ticks: int = 0` -- ticks elapsed since the drag
  ended, for the safety cap.

**`_start_simulation(self) -> None`** -- called from `_on_pan_start()`'s
existing node-hit branch (`graph_canvas.py:174-178`), right after the
existing `self._notify_selection()` call: snapshots
`self._home_positions = dict(self._positions)`, resets
`self._sim_velocities`/`self._sim_active_nodes`/`self._sim_settle_ticks`,
and if `not self._sim_active`, sets it `True` and calls
`self._page.run_task(self._simulation_loop)`. No change needed to
`_on_pan_end()` at all -- it already clears `self._dragging = None`,
which `_simulation_tick()` reads fresh each tick to detect the anchor was
released and switch into settle behavior; no explicit "stop" call needed.

**`async def _simulation_loop(self) -> None`**:
```python
while self._sim_active:
    self._simulation_tick()
    self._redraw()
    await asyncio.sleep(_SIM_TICK_DT)
```

**`_simulation_tick(self) -> None`** -- the physics step, deterministic
and pure enough to unit test directly (reads/writes only
`self._positions`/`self._sim_velocities`/`self._sim_active_nodes`,
touches no Flet control):
- If `self._dragging is not None` (still dragging): compute the anchor's
  graph neighbors once (`set(self._graph.predecessors(anchor)) |
  set(self._graph.successors(anchor))`), add them to
  `self._sim_active_nodes`, and also add any node currently within
  `_SIM_REPEL_RADIUS` of the anchor's current position. Reset
  `self._sim_settle_ticks = 0`.
- If `self._dragging is None` (released): increment
  `self._sim_settle_ticks`; no new nodes are added to the active set,
  only existing ones continue easing back.
- For every node in `self._sim_active_nodes`: sum three forces --
  neighbor-spring toward `_SIM_NEIGHBOR_REST_LENGTH` from the anchor (if
  it's a direct neighbor and the anchor is still known), radial repulsion
  if within `_SIM_REPEL_RADIUS` of the anchor, and a constant home-spring
  pulling it toward `self._home_positions[slug]`. Semi-implicit Euler
  integration with `_SIM_DAMPING` velocity decay and a `_SIM_MAX_SPEED`
  clamp (this clamp is what makes it stable regardless of how far/fast
  the anchor jumps, unlike the `nx.spring_layout()` approach above). A
  node whose new position lands within `_SIM_SETTLE_DIST_EPSILON` of its
  home while the anchor is released snaps exactly to its home position
  and drops out of the active set.
- The dragged node itself is never in `self._sim_active_nodes` and is
  never touched by this method -- `_on_pan_update()`'s existing direct
  write (`graph_canvas.py:187-191`) stays completely unchanged, so the
  node under the cursor still tracks the mouse with zero added latency;
  only the *other*, reactive nodes go through the tick loop.
- When `self._dragging is None` and either `self._sim_active_nodes` is
  empty or `self._sim_settle_ticks > _SIM_SETTLE_MAX_TICKS`: set
  `self._sim_active = False`, which ends `_simulation_loop()` after this
  iteration.

**`set_graph()`** gains one line at the top: `self._sim_active = False`
-- stops any in-flight simulation before a full graph reload replaces
`self._positions` wholesale, so the tick loop never fights a fresh
`_layout_worker()` result.

No changes to `build_shapes()`, `_redraw()`, `_node_at()`, or any zoom/
pan/selection code -- the simulation only ever writes into the same
`self._positions` dict those already read.

### Tests

`tests/test_gui_shell.py`'s existing Graph canvas section (direct
construction + private-method calls, no live render, following its own
established pattern):
- `_simulation_tick()` unit tests against a small fixture graph (an
  anchor node with one direct neighbor and one non-neighbor bystander
  within repel radius, plus one far/unrelated node): dragging moves the
  anchor across several ticks and asserts the neighbor's distance to the
  anchor decreases (trailing) while the bystander's distance from its
  start position increases (repelled) and the far node never moves;
  clearing `self._dragging` and ticking further asserts a perturbed
  node's distance-to-home eventually drops under `_SIM_SETTLE_DIST_EPSILON`
  and it snaps exactly to `_home_positions`, dropping out of the active
  set; a separate test confirms `_sim_active` flips `False` once
  `_SIM_SETTLE_MAX_TICKS` is exceeded even if a node hasn't converged, so
  the loop is guaranteed to terminate regardless.
- `_on_pan_start()` on a node hit calls `_start_simulation()` (asserted
  via `self._home_positions`/`self._sim_active` state, not the async
  loop itself).
- `set_graph()` clears `self._sim_active` if a simulation was mid-flight.
- One end-to-end test using the existing `_FakePage` double (the same
  real-background-thread + dedicated-event-loop-thread pattern already
  used for `set_graph()`'s `run_thread`/`run_task` path): drives a node
  drag through `_on_pan_start`/`_on_pan_end` and confirms
  `_simulation_loop` actually runs via `run_task` and `_sim_active`
  eventually returns to `False` on its own.

### Verification

Implemented per the plan above, with one more real correctness bug found
and fixed during implementation via direct simulation (not caught by
reasoning alone -- the same "verify, don't guess" approach that found the
`nx.spring_layout()` instability above): the original per-node settle
check compared *velocity* against a threshold
(`speed < _SIM_SETTLE_EPSILON`). Simulating the actual post-drag scenario
surfaced that this is unsound -- with per-tick multiplicative damping,
velocity can dip under any fixed threshold while a node is still tens of
pixels from home (each tick discards a fixed *fraction* of velocity
regardless of remaining displacement), which would falsely mark a node
"at rest" mid-flight and strand it there once the loop stopped touching
it. Fixed by switching the settle check to compare *position* against
`_home_positions[slug]` instead (`_SIM_SETTLE_DIST_EPSILON = 2.0px`) --
this directly measures the thing that actually matters. Retuned
`_SIM_HOME_SPRING_K` (6.0 -> 16.0) and `_SIM_DAMPING` (0.80 -> 0.72)
alongside this fix; reverified by direct simulation against the original
worst-case scenario (15 ticks of sustained drag momentum, then release):
converges to within 2px of home in ~58 ticks (~1.9s), comfortably inside
the 90-tick safety cap, with repulsion at typical interaction range still
roughly 5-10x stronger than the home-spring's pull so it doesn't visibly
fight the repel/trail effect during an active drag. `_page_stub()` in
`test_gui_shell.py` (previously a bare `object()`) needed a no-op
`run_task()` method, since `_start_simulation()` now calls
`self._page.run_task(...)` on every node-drag start -- without it, all 20
existing call sites that click/drag a node would have started raising
`AttributeError`, not just the new tests.

Tests: `test_gui_shell.py`'s Graph canvas section grew by 7 tests --
`_start_simulation()`'s home-snapshot + activation, repel/trail during a
drag (numerically pre-validated against the real tuned constants before
being written, not just asserted on faith), settle-to-exact-home after
release, the safety-cap-terminates-even-when-unconverged path (forcing a
node's home artificially far away to isolate it from the convergence
path), a plain click leaving no residual motion, `set_graph()` stopping
an in-flight simulation, and one `_FakePage`-based end-to-end test
through the real `run_task()` loop. One test-only race was caught and
fixed along the way: an early version of the `set_graph()`-stops-the-sim
test closed the `_FakePage` before its background layout thread finished,
producing an intermittent "coroutine was never awaited" warning
attributed to an unrelated, later test -- fixed by waiting for the new
layout to land first, matching the existing
`test_set_graph_computes_layout_on_a_worker_thread` pattern.

Verified: 320 tests pass (7 new), `uv run ruff check .` clean (re-run
under `pytest -W error::RuntimeWarning` too, to confirm the race fix
actually holds, not just that the warning happened not to print that
run), `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches and runs with no
console output beyond the same benign GTK/Atk/OpenGL lines seen in every
prior sub-phase, and no lingering process after exit.

**Manual verification (you)**: drag a node with several connected notes
and confirm its neighbors visibly lag/trail behind it rather than
staying frozen; drag a node close to an unrelated node and confirm the
unrelated one gets pushed aside; release the drag and confirm perturbed
nodes ease back to their original spot (not an instant snap) over
roughly a second or two; confirm a plain click-to-select (no drag
movement) still works exactly as before, with no stray motion. The
tuned constants above are a real, simulation-verified starting point,
not final -- if the effect feels too subtle, too strong, too bouncy, or
too slow/fast to settle, that's a one-line constant tweak (e.g.
`_SIM_REPEL_STRENGTH`, `_SIM_DAMPING`), not a design change.

## Post-22 fix: redundant redraws during a node-drag

You reported visible lag dragging a node in a 37-note vault and asked
whether the graph runs on GPU or CPU. Investigated directly (reading
Flet's own source, not guessing): the physics is cheap CPU work
(sub-millisecond, already benchmarked in Phase 22's own research), and
painting itself is GPU-accelerated on the Flutter side (Skia/Impeller) --
neither is the bottleneck. `Canvas.update()`
(`flet/controls/core/canvas/canvas.py` -> `flet/messaging/session.py`'s
`patch_control()`) has to serialize the *entire* `shapes` list -- every
edge line, node circle, and text label -- into a patch message and push
it across the Python<->Flutter IPC boundary on every call. That's a real
per-call cost that scales with shape count and call frequency, and
Phase 22 had introduced a redundant multiplier: during an active
node-drag, `_on_pan_update()` already called `_redraw()` on every raw
pointer-move event (which can fire well above 30/sec), *and* the new
simulation tick loop independently called `_redraw()` at ~30fps -- two
uncoordinated redraw sources stacking on top of each other, each paying
the full serialization cost.

Separately noted but explicitly out of scope for this fix: "smooth with
thousands of nodes like Obsidian" is a different rendering architecture
(WebGL/physics in one JS context, no cross-process serialization per
frame), not a tuning problem -- matching that would mean a custom
Flutter rendering surface outside Flet's Canvas API, disproportionate for
a personal vault tool.

### Fix

`graph_canvas.py`'s `_on_pan_update()`, node-drag branch: still updates
`self._positions[self._dragging]` unconditionally (cheap, no Flet control
touched), but only calls `self._redraw()` directly when
`not self._sim_active`. Since `_start_simulation()` (Phase 22) always
activates the tick loop on any node hit, and the tick loop itself can
only ever stop while `self._dragging is not None` via a
`set_graph()`-triggered reset (never via convergence, which is gated
behind the anchor being released), the tick loop's own ~30fps `_redraw()`
is normally the *only* redraw source during a drag -- restoring a single,
steady redraw cadence during a drag instead of two independent, stacking
ones. The `not self._sim_active` fallback is a deliberate safety net, not
dead code: if a background vault refresh calls `set_graph()` (which sets
`_sim_active = False`) mid-drag, the drag keeps rendering directly rather
than silently freezing until release. Panning (whole-canvas drag on empty
background) is unchanged -- it has no tick loop to lean on, so it must
keep redrawing per-event.

### Tests

Two new regression tests in `test_gui_shell.py`: dragging a node while
the simulation is active leaves `canvas._canvas.shapes` as the *same
object* across a `_pan_update_to()` call (proving `_redraw()` wasn't
called again -- `_redraw()` always builds a fresh list, so object
identity surviving is the signal); dragging a node with `_sim_active`
false (bypassing `_start_simulation()`, matching the existing
`test_dragging_a_node_moves_only_that_node` pattern) still produces a
populated shape list, proving the fallback path works.

### Verification

Verified: 322 tests pass (2 new), `uv run ruff check .` clean, `uv run
flet build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches clean. One unrelated, pre-existing flake was
noticed in the full suite run (`test_terminal_panel.py`, a background
PTY-reader thread racing test teardown) -- confirmed via `git stash` that
it exists independent of this fix and doesn't reproduce when that file is
run in isolation; out of scope here since it touches Phase 19 code this
fix never goes near.

**Manual verification (you)**: drag a node in your actual vault again and
confirm whether this noticeably reduces the lag. If it's still laggy at
37 nodes, that points at something beyond the redundant-redraw
multiplier (e.g. the per-call serialization cost itself, given by roughly
70-100+ edges from the Related-block backlink structure) and is worth a
follow-up investigation rather than another constant tweak.

## Post-22 fix: split static vs. actively-simulated rendering layers

### Context

The redundant-redraw fix above helped ("much better") but not enough --
still not smooth at 37 nodes. You found and shared a comparison chart
proposing WebView + PyVis/vis.js (out-of-the-box physics, smooth pan/zoom,
handles hundreds of nodes) vs. pure Flet Canvas (hand-rolled, "laggy with
large dataset").

**The WebView route is a dead end for this project, confirmed directly,
not assumed.** `flet-webview` (the only Flet WebView control) is the sole
candidate for embedding vis.js -- its own docstring states every single
method "Works only on... iOS, Android, and macOS" (Linux and Windows
conspicuously absent everywhere), and the class docstring explicitly says
"Concerning Windows and Linux support, subscribe to this issue." Checked
that issue directly (`flet-dev/flet-webview#17`): still open, tracking
Linux support that was never implemented, on a repository **archived by
its owner in December 2025** -- no longer maintained. This project only
ever builds `flet build linux`. PyVis itself (available, but pulls in a
surprisingly heavy transitive dependency chain -- `ipython`, `jedi`,
`prompt-toolkit`, etc., for its Jupyter-notebook rendering path we'd never
use) is moot without a way to display the HTML it generates.

**Where the remaining cost actually goes, measured directly** (not
guessed): built a `GraphCanvas` against a realistic 37-note graph (Related
-block topology -- every note links to `index` and one source, 38 nodes /
74 edges / 150 shapes) and timed the real code:
- `build_shapes()` itself: ~0.57ms for all 150 shapes -- not the
  bottleneck.
- `ObjectPatch.from_diff()` (Flet's own patch/diff computation --
  `flet/controls/object_patch.py`, the thing `Canvas.update()` triggers
  via `page.update()` -> `session.patch_control()`) over the *same*
  150-shape canvas, even when **nothing actually changed** (both sides
  freshly rebuilt with identical values): ~2.1ms/call. Over a *much*
  smaller 14-shape canvas (5 nodes): ~0.25ms/call -- roughly proportional
  to total shape count, not to how many shapes actually differ.

This confirms the diagnosis: every simulation tick rebuilds and diffs
*all* ~150 shapes even though only a handful of nodes (the dragged node,
its neighbors, and any nearby bystander) are actually moving. The fix
doesn't need a new rendering technology -- it needs the hot path to stop
touching shapes that aren't moving.

Presented three options (optimize pure Flet further / investigate a
custom Flutter renderer / stop here) -- you chose optimizing pure Flet
further, on the strength of the measured ~8-10x reduction a static/
dynamic split should give at this vault's actual scale, with no new
dependency and no platform risk (unlike a custom Flutter renderer, which
remains the only way to get genuinely thousands-of-nodes-smooth but is a
different scale of project).

### Design

Split the single `cv.Canvas` into two, layered in a `ft.Stack`: a
**static** canvas (everything not currently touched by the simulation --
redrawn only on real state changes: layout reload, resize, pan, zoom,
selection) and a **dynamic** canvas (the actively-simulated node set plus
any edge touching it -- redrawn every simulation tick). During a drag,
only the small dynamic canvas gets rebuilt+diffed+sent on the 30fps hot
path; the bulk of the graph is untouched.

**Partitioning rule** (in `graph_canvas.py`): a new `_dynamic_slugs()`
returns `self._sim_active_nodes | ({self._dragging} if self._dragging is
not None else set())`. A node is dynamic iff its slug is in that set. An
edge `(u, v)` is dynamic iff `u` or `v` is in that set, static otherwise
-- this is an exhaustive, mutually-exclusive partition by construction
(every edge/node lands in exactly one canvas, no double-render or
missing-render risk), computed fresh from the same state at redraw time
by both builders.

**Refactor `build_shapes()`** into three pieces, reusing two new small
helpers (`_node_shape(slug, x, y)`, `_edge_shape(u, v, edge_paint)` --
pulled out of the current monolithic loop body, no behavior change):
`_build_static_shapes()` (edges/nodes not in `_dynamic_slugs()`),
`_build_dynamic_shapes()` (edges/nodes in it). `build_shapes()` itself
becomes `self._build_static_shapes() + self._build_dynamic_shapes()` --
kept as the *combined* view specifically so the 3 existing tests that
call it directly (`test_graph_canvas_builds_a_shape_per_edge_and_two_
per_node`, `..._edges_use_a_visible_paint`, `..._handles_an_empty_graph`)
need no changes.

**Redraw methods** replace the single `_redraw()`: `_redraw_static()`
and `_redraw_dynamic()` each rebuild+update just their own canvas;
`_redraw_all()` calls both (used everywhere redraws aren't the hot path
-- `_apply_positions()`, `_compute_layout()`, `_on_resize()`, the panning
branch and the `not self._sim_active` fallback in `_on_pan_update()`,
`_notify_selection()`, `_set_zoom()`).

**The simulation loop is the one place needing real care**, because
nodes move *between* the two canvases as they enter/leave the active set
(a bystander wandering into repel range, a node settling back to rest, or
the anchor itself the instant a drag ends) -- missing that transition
would leave a node's shape stuck rendering nowhere, or stuck in the wrong
layer, until the next unrelated full redraw. `_simulation_tick()` gains a
`bool` return: it snapshots `current_dynamic = _dynamic_slugs()` computed
from its *own* end-of-tick state (the just-updated `still_active` plus
the current `self._dragging`), compares it against a new
`self._prev_dynamic_slugs` (updated every tick), and returns whether they
differ. `_start_simulation()` resets `self._prev_dynamic_slugs = set()`
so the first tick of any drag always reports "changed" (correctly moving
the anchor into the dynamic layer immediately). `_simulation_loop()`
calls `_redraw_all()` on a "changed" tick, `_redraw_dynamic()` otherwise
-- so the expensive full redraw only happens on the (infrequent)
ticks where the active set's membership actually shifts, not on every
tick.

**Constructor**: `self._static_canvas` and `self._dynamic_canvas` (both
`cv.Canvas(shapes=[], expand=True)`), wrapped in a new `ft.Stack`
(dynamic layered *above* static, so actively-moving shapes always render
on top) that becomes the `GestureDetector`'s `content` in place of the
single canvas. `on_resize` stays attached to just one canvas (either
works -- both are `expand=True` in the same stack, so they always share
the same rendered size). Hit-testing (`_node_at()`) and all pan/zoom/
selection logic are untouched -- they already key off `self._positions`,
never the canvas/shape objects directly, so the split is purely a
rendering-layer concern.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- the entire change; no other file
  is touched (confirmed `app.py` only calls `GraphCanvas`'s public API --
  `set_graph()`, `zoom_reset()` -- never reaches into `._canvas`).

### Tests

`tests/test_gui_shell.py`'s Graph canvas section:
- New coverage for the partition itself: a static (untouched) node's
  shapes appear only via `_build_static_shapes()`; a dragged node (and,
  separately, a node placed inside `_sim_active_nodes` directly) appears
  only via `_build_dynamic_shapes()`; an edge with one static and one
  dynamic endpoint appears only in the dynamic builder's output, never
  the static one, and never in both.
- `_simulation_tick()`'s new return value: `True` on the first tick of a
  drag (anchor newly dynamic), `True` on a tick where a bystander enters
  repel range or a node settles out, `False` on a steady-state tick where
  nothing enters or leaves the active set.
- `_redraw_static()`/`_redraw_dynamic()` each touch only their own
  canvas's `.shapes` -- reuses the identity-preservation technique the
  Post-22 redundant-redraw fix's tests already established.
- The two existing redundant-redraw regression tests
  (`test_pan_update_does_not_redraw_directly_while_the_simulation_is_
  running`, `..._still_redraws_directly_if_the_simulation_is_not_
  running`) get updated to check `_static_canvas`/`_dynamic_canvas`
  instead of the now-removed `_canvas`.
- The 3 existing `build_shapes()`-based tests and the hit-testing/zoom/
  pan tests need no changes, per the design above.

### Verification

Implemented per the design above, no material deviations. `_dynamic_
slugs()`, `_node_shape()`/`_edge_shape()` helpers, `_build_static_
shapes()`/`_build_dynamic_shapes()`, `_redraw_static()`/`_redraw_
dynamic()`/`_redraw_all()`, and the `_simulation_tick()` -> `bool` /
`_prev_dynamic_slugs` changed-set tracking all landed exactly as planned.
Every `self._redraw()` call site was audited and replaced (`_apply_
positions`, `_compute_layout`, `_on_resize`, both branches of `_on_pan_
update`, `_notify_selection`, `_set_zoom` -> `_redraw_all()`;
`_simulation_loop()` -> `_redraw_all()` on a changed tick, `_redraw_
dynamic()` otherwise). `build_shapes()` kept as the static+dynamic union,
so the 3 pre-existing tests that call it directly needed no changes, as
designed.

**Remeasured after implementing, against this vault's actual topology --
the improvement is real but more modest than the isolated benchmark
suggested, and worth being honest about why.** The Phase 22 research
benchmark used a small 5-node fixture with no hub and got ~8x; measuring
the *real* code against the same 38-node/74-edge Related-block-shaped
graph used in the original diagnosis, for a **typical drag** (an ordinary
leaf note, not a hub) told a different story: this vault's topology has
every note backlinked to `index`, so `index` is *always* a direct graph
neighbor of whatever note you drag -- meaning dragging any leaf pulls the
`index` hub itself into the dynamic set, and correctly (not a bug) every
one of the hub's ~37 edges has to come along, since they'd otherwise
visually lag behind a moving hub. Measured: dynamic-canvas-only diff
~0.72ms vs. the old full-canvas diff ~2.07ms for the same drag -- a real
~2.9x cut, not the ~8x the idealized benchmark implied. Static graphs
without a dominant hub would see much closer to the original estimate;
this vault's shape is the less favorable case.

**A related, separate observation surfaced by this measurement, not
addressed here**: the Phase 22 physics itself pulls a dragged note's
direct neighbors toward it via the neighbor-spring -- for an ordinary
edge that's the intended "trailing" effect, but since `index` is *always*
one of those neighbors in this vault, dragging any single note also
visually tugs the central hub node itself. Whether that's desirable
(hub "leans toward" whatever you're focused on) or worth excluding
high-degree hubs from the neighbor-spring specifically is a real design
question, not a bug in this fix -- flagged for you to decide rather than
changed unilaterally, since it's Phase 22's physics model, not this
render-layer split.

Verified: 326 tests pass (7 new -- 4 for the static/dynamic shape
partition and the redraw-isolation guarantee, 3 for `_simulation_tick()`'s
new changed-set return value across a set-transition/steady-state/
release sequence; the 2 pre-existing redundant-redraw regression tests
updated to check `_static_canvas`/`_dynamic_canvas`), `uv run ruff check .`
clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines.

**Manual verification (you)**: drag a node in your real vault again and
judge whether it's actually smoother, keeping in mind the measured ~2.9x
(not ~8x) improvement given your vault's hub topology -- it should help
noticeably but may not feel fully "Obsidian-smooth" yet. If it's still
meaningfully laggy, the next place to look is the per-message IPC
round-trip itself (not diffing, not shape construction, both now measured
and small). Also worth deciding on the hub-neighbor-spring question above
if dragging a note visibly tugging `index` around bothers you. Re-check
pan, zoom, click-to-select, and deselect too -- none of that logic
changed, but the rendering split touches every redraw call site.

## Post-22 fix: `index` as a fixed gravity well + hover-only labels

### Context

Two ideas from you, building directly on the static/dynamic split above:

1. **Make `index` the fixed center of the graph.** Every note is already
   guaranteed to backlink to `[[index]]` (Phase 18's Related-block), so
   it's structurally the hub of this graph. Pin it at the canvas center;
   let other nodes settle loosely around it; other nodes can't drag it,
   but you still can, and everything else follows exactly like it does
   today. This directly resolves the "hub gets tugged around by every
   drag" observation flagged at the end of the static/dynamic split above
   -- if `index` can't be pulled by anything except a direct drag, it
   never enters the dynamic set on an ordinary leaf-note drag, and its
   ~37 edges stay on the untouched static layer instead of being rebuilt
   every tick. This should recover most of the gap between the measured
   ~2.9x improvement and the ~8x an idealized non-hub graph got.
2. **Only show a node's label when the mouse is hovering it.** Removes
   ~38 always-on `cv.Text` shapes from a 37-note vault's constant render
   (currently every node carries a circle *and* a label) -- fewer shapes
   to diff/serialize on every redraw, and fewer pixels to rasterize,
   independent of and complementary to the split above.

Both confirmed by direct API checks before planning, not assumed:
`nx.spring_layout()` accepts a *partial* `pos=`/`fixed=` (only the
anchored node needs a seed position; other nodes still get spring_layout's
own random init) -- verified directly, and re-verified against the exact
fixture the existing `test_graph_canvas_layout_spreads_nodes_apart_at_a_
realistic_size` regression test uses, confirming the min pairwise
distance after anchoring (~207px) stays far above that test's threshold
(18px), so anchoring doesn't reintroduce node overlap. `ft.GestureDetector`
(already wrapping the canvas stack) has `on_hover`/`on_exit` built in --
no new control needed, just two more handlers on the control already
there.

### Implementation

All changes confined to `src/llm_wiki/gui/graph_canvas.py`.

**1. Gravity well.** New constant `_GRAVITY_WELL_SLUG = "index"`.

- `_layout_positions()`: when `_GRAVITY_WELL_SLUG in self._graph`, pass
  `pos={_GRAVITY_WELL_SLUG: (0.0, 0.0)}, fixed=[_GRAVITY_WELL_SLUG]` to
  `nx.spring_layout()` (both `None` otherwise, unchanged behavior when
  the vault has no `index` node, e.g. a brand-new empty vault). `(0.0,
  0.0)` in spring_layout's data space maps to the exact canvas center via
  the existing `_to_canvas()` formula -- no other change needed for the
  "circle around the center" look; it falls out of spring_layout's own
  attraction/repulsion balance once the hub can't move, not new
  hand-rolled placement math.
- `_simulation_tick()`: the gravity well must never be *pulled* by
  anything except being directly dragged. Two small exclusions, both
  right where nodes get added to `self._sim_active_nodes`: the
  neighbor-union gains `- {_GRAVITY_WELL_SLUG}`, and the repel-radius
  bystander scan's `continue` guard gains `or slug ==
  _GRAVITY_WELL_SLUG`. Dragging `index` itself is unaffected by either
  change -- `index` is never in `_sim_active_nodes` regardless (the
  dragged node is tracked separately via `self._dragging`), and its own
  neighbor set (literally every note) is computed and used exactly as
  before, so "if it is moved, everything else moves like it already
  does" holds.

**2. Hover-only labels.** New state: `self._hovered: str | None = None`.
New third canvas layer, `self._hover_canvas = cv.Canvas(shapes=[],
expand=True)`, added to `self._canvas_layers`'s `ft.Stack` *above* both
existing layers (`[static, dynamic, hover]` -- Stack renders later
children on top, so the label is always readable). `GestureDetector`
gains `on_hover=self._on_hover, on_exit=self._on_exit`.

- `_node_shape()` (currently returns `[circle, text]`) splits into
  `_node_circle(slug, x, y) -> cv.Circle` (just the circle -- what
  `_build_static_shapes()`/`_build_dynamic_shapes()` use now, `.append()`
  instead of `.extend()`) and `_hover_label_shape(slug, x, y) -> cv.Text`
  (the exact same text styling/offset as before, just relocated).
- `_build_hover_shapes()`: `[]` when `self._hovered is None` or its
  position has disappeared (e.g. a concurrent graph reload); otherwise
  `[self._hover_label_shape(self._hovered, *pos)]` -- at most one shape,
  ever.
- `_redraw_hover()`: mirrors `_redraw_static()`/`_redraw_dynamic()`,
  rebuilds+updates just `self._hover_canvas`.
- `_on_hover(e)`: `hovered = self._node_at(e.local_position.x,
  e.local_position.y)`; if it differs from `self._hovered`, update and
  `_redraw_hover()`. `_on_exit(e)`: clears `self._hovered` (if set) and
  redraws. Both reuse the exact same `_node_at()` hit-test pan/zoom/drag
  already use -- no new coordinate math.
- `build_shapes()` (the combined static+dynamic view used by tests)
  stays graph-content-only, *not* including the hover label -- the label
  is a UI affordance layer, the same category as the legend/zoom-controls/
  info-overlay (already separate from `build_shapes()`), not graph data.
  This means the label's presence is intentionally **not** part of the
  "one shape per edge, N per node" contract those tests assert on --
  see Tests below for the one existing test whose count needs updating
  because of this.

### Tests

`tests/test_gui_shell.py`'s Graph canvas section:
- `test_graph_canvas_builds_a_shape_per_edge_and_two_per_node` ->
  becomes "...one per node" (circle only now); count updates from 8 to 5
  for the same 2-edge/3-node fixture. This is the one pre-existing test
  whose premise changes, and the change is intentional (see above), not
  a regression.
- New: `index` anchored at the canvas center when present (`node_
  positions["index"] == canvas._to_canvas(0.0, 0.0)`); layout is
  unaffected (no crash, no accidental anchor) when the graph has no
  `index` node.
- New: dragging a note with `index` as a direct neighbor, ticking the
  simulation several times, confirms `index`'s position never moves,
  while dragging `index` itself still pulls its neighbors normally
  (reusing the existing neighbor-spring test's assertions against an
  `index`-as-anchor scenario).
- New: a small `_hover_at(canvas, x, y)` / `_exit_hover(canvas)` test
  helper pair (constructing real `ft.HoverEvent` objects, matching the
  file's own `_pan_start_at`/`_pan_update_to`/`_pan_end` pattern) driving
  `_on_hover`/`_on_exit`: hovering a node populates `_hover_canvas.shapes`
  with exactly one `cv.Text`; moving off it (to empty space or another
  node) updates or clears it; `build_shapes()`'s combined output never
  includes a label, confirming the split from item 2 above.

### Verification

Implemented per the design above, no material deviations. `_GRAVITY_
WELL_SLUG = "index"`, the `pos=`/`fixed=` anchoring in `_layout_
positions()`, the two exclusion points in `_simulation_tick()`, the
`_node_shape()` -> `_node_circle()`/`_hover_label_shape()` split, the
third `_hover_canvas` layer, and `_on_hover()`/`_on_exit()` all landed
exactly as planned. One ruff finding during implementation (`SIM109`):
the three-way `slug == anchor or ... or slug == _GRAVITY_WELL_SLUG`
guard collapsed to `slug in (anchor, _GRAVITY_WELL_SLUG)`, same logic.

**Remeasured against the same 38-node/74-edge Related-block-shaped graph
and the same "drag an ordinary leaf note" scenario used to diagnose and
then measure the static/dynamic split** -- this is the number that
matters, not the isolated benchmark:
- Before any of the three fixes (original, single-canvas, always-on
  labels): ~2.07ms/diff.
- After the static/dynamic split alone: ~0.72ms/diff (the ~2.9x
  reported at the time, limited by `index` being pulled into the
  dynamic set on every drag).
- After pinning `index` as a gravity well *and* moving labels to
  hover-only: **~0.13ms/diff** -- roughly 16x faster than the original,
  ~5.6x faster than the split alone. The dynamic set for a leaf-note drag
  is now just the dragged note and its 1-2 direct (non-hub) neighbors --
  confirmed directly: dragging `note15` in the benchmark graph now
  produces a dynamic set of `{note15, note16, note14}`, with `index`
  correctly absent. Total shape count also dropped from 150 to 112 (37
  fewer labels) purely from making them hover-only, independent of the
  dynamic-set effect.
- This more than closes the gap the static/dynamic split's own
  verification flagged -- the hub-topology limitation identified there
  is directly what these two fixes target, and the result confirms it.

Verified: 334 tests pass (11 new -- 2 for anchoring `index` at the canvas
center and graceful no-`index` behavior, 2 for the gravity-well
drag-exclusion in both directions, 4 for hover show/hide/dedup/exit, plus
the shape-count assertions in 1 pre-existing test and 2 static/dynamic
tests updated for the one-shape-per-node reality), `uv run ruff check .`
clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines.

**Manual verification (you)**: confirm `index` sits at the visual center
with everything else loosely ringed around it, and that dragging any
other note no longer visibly tugs `index` out of place (while dragging
`index` itself still moves everything, as before). Hover over a few
nodes and confirm the label appears only under the cursor and disappears
when you move away, including moving the mouse off the canvas entirely.
Re-check that dragging, panning, zooming, and click-to-select still work
exactly as before -- none of that logic changed. Given the measured
numbers above, this is a good point to judge whether the graph now feels
genuinely smooth rather than just "better."

## Post-22 fix: default spacing too tight around the pinned hub, edges drawing over nodes

### Context

Two real regressions from the gravity-well fix above, both confirmed
directly, not guessed at.

**1. Default node spacing shrank -- and it's worse than a spacing-constant
issue.** Your follow-up ("zoomed all the way out, only 14 of ~38 nodes
visible with the panel maximized") ruled out my first fix attempt (an
empirical `k` scale-down) before it was ever implemented -- that fix
targeted the wrong metric (average node distance) when the real driver is
the graph's *bounding radius* (the single outermost node), which is what
actually determines how much fits in the viewport at a given zoom.
Measured directly: `nx.spring_layout()`'s default behavior (confirmed by
re-reading its own source, the same file read for the Phase 22 stability
research) auto-rescales its output so the bounding radius is always ~1.0
(its `scale=1` default) -- but **only when `fixed` is not given**. The
moment `index` became pinned, that auto-rescale silently stopped
applying, and the anchored layout's raw bounding radius came out to
~2.12 against the free layout's ~1.08 -- almost exactly **2x**, which
maps to roughly 4x the on-screen area at any zoom level. That's the
"only 14 of 38 visible" report, precisely.

The fix is a normalization, not a tuning constant: after computing the
anchored layout, rescale it so its own bounding radius is exactly 1.0 --
restoring the exact invariant the unanchored path already got for free,
rather than approximating a historical number. Verified directly against
the same 17-note/3-source fixture the existing overlap-regression test
uses: the normalized anchored bounding radius in real canvas pixels
(~661px) comes out *smaller* than the pre-gravity-well baseline
(~713px), and minimum pairwise spacing (~35px) stays comfortably clear
of that test's 18px overlap floor. No empirical constant needed, and
nothing to retune if the vault's node count changes.

**Overlap is a separate, harder constraint from "spread out enough to
see" and this fix doesn't trade one for the other.** Nodes literally
overlapping (from the original Post-18 fix, `_LAYOUT_SPACING`/`k` tuned
specifically so `min_distance > 2 * _NODE_RADIUS` always holds) must
never happen regardless of how compact the overall layout is -- that's
already a hard-tested invariant
(`test_graph_canvas_layout_spreads_nodes_apart_at_a_realistic_size`),
independent from "how much of the graph fits in the viewport," which is
what today's regression was actually about. The normalization above
doesn't touch `k` or `_LAYOUT_SPACING` at all -- it only rescales the
*outer bound* of an already-computed layout, and the measured minimum
pairwise distance after normalizing (~35px) is comfortably above the
18px overlap floor with real margin, not a close call.

**2. Edges draw over node circles at the far endpoint.** Root cause is
the static/dynamic canvas split itself: `_build_dynamic_shapes()`
correctly draws edges before circles *within* the dynamic canvas, but an
edge with only *one* dynamic endpoint (e.g. dragging a leaf note whose
edge to the still-static `index`) lives entirely in the dynamic canvas,
while the *static* endpoint's circle lives in the static canvas --
which renders **underneath** the dynamic canvas regardless of per-shape
ordering. So any cross-boundary edge's static end draws on top of that
node's own circle, because the whole static layer sits below the whole
dynamic layer, not because of a within-canvas ordering mistake.

### Implementation

Both fixes confined to `src/llm_wiki/gui/graph_canvas.py`.

**1. Spacing**: in `_layout_positions()`, right after the
`nx.spring_layout()` call, when anchoring was used (`if fixed:`),
normalize the resulting positions so their bounding radius (the single
farthest node from origin) is exactly `1.0` -- replicating the
auto-rescale `nx.spring_layout()` already performs by default, which
`fixed=` silently bypasses:

```python
pos = nx.spring_layout(self._graph, k=k, iterations=100, seed=42, pos=pos_seed, fixed=fixed)
if fixed:
    max_r = max((math.hypot(float(xy[0]), float(xy[1])) for xy in pos.values()), default=0.0)
    if max_r > 0:
        pos = {node: (xy[0] / max_r, xy[1] / max_r) for node, xy in pos.items()}
```

No new tuning constant, and nothing that needs retuning as the vault
grows -- `k`/`_LAYOUT_SPACING` (which control *relative* spacing, and
already guarantee no-overlap) are untouched; this only fixes the
*overall* graph extent, which is the thing `fixed=` broke.

**2. Z-order**: `_build_dynamic_shapes()` gains a small addition. While
building the dynamic edge list, track which *static* nodes are an
endpoint of a dynamic-canvas edge (`u not in dynamic` / `v not in
dynamic` on an edge that qualified). After the existing dynamic-node
circles are appended, append one more circle (via the existing
`_node_circle()`, no new styling) for each such static endpoint. This
draws a second copy of that node's circle, at its real (unmoving)
position, on top of the edge that would otherwise cover it -- perfectly
overlapping the real one in the static canvas below, so no visible
duplicate, just correct z-order. The static canvas itself is untouched
(still not rebuilt on the hot path); this only adds a handful of cheap
circle shapes to the already-small dynamic canvas, bounded by the degree
of whatever's currently being dragged.

### Tests

`tests/test_gui_shell.py`:
- New: with anchoring active, the resulting layout's bounding radius
  (max distance from `index`'s own position, across all nodes) is
  `pytest.approx(1.0)` in spring_layout's own units before `_to_canvas()`
  -- directly locks in the normalization, not an indirect proxy metric.
  A second assertion confirms this holds regardless of node count (e.g.
  both a 3-node and a 21-node fixture), since the fix has no node-count-
  dependent constant to get out of sync.
- The existing overlap-regression test
  (`test_graph_canvas_layout_spreads_nodes_apart_at_a_realistic_size`)
  needs no changes and must still pass unchanged -- proof the
  normalization (which only rescales the outer bound) doesn't touch the
  no-overlap guarantee `k`/`_LAYOUT_SPACING` already provide.
- New: a fixture where a dragged leaf's only edge goes to a static node
  (e.g. `index`, excluded from the dynamic set by the gravity-well fix)
  -- after a tick, `_build_dynamic_shapes()`'s shape list has the static
  endpoint's circle appearing *after* (on top of, per Canvas draw order)
  the edge that touches it. A second fixture where both edge endpoints
  are dynamic confirms no redundant circle gets added for an
  already-dynamic node (no double-draw where it isn't needed).

### Verification

Implemented per the design above -- the normalization approach replaced
the originally-proposed `_GRAVITY_WELL_K_SCALE` empirical constant mid
plan-review, after you reported a concrete symptom ("zoomed all the way
out, only 14 of ~38 nodes visible") that showed the first approach was
targeting the wrong metric. You also flagged, correctly, that "spread
out enough to see the whole graph" and "nodes must never overlap" are
two separate constraints -- this fix only touches the former (rescaling
the *outer bound* of an already-computed layout); `k`/`_LAYOUT_SPACING`,
which govern the latter, are untouched.

Tests: 6 new -- 2 for the bounding-radius normalization (exact, both a
3-node and a 21-node fixture, confirming it's not node-count-dependent),
1 confirming the pre-existing overlap-regression test still passes
unchanged, 1 for the cross-boundary z-order fix (a dragged leaf's only
edge going to the now-static `index` -- both the dragged node's circle
and a shadow copy of `index`'s circle land after the edge in the shape
list), plus the two prior static/dynamic-split tests (unaffected shape
counts, confirming no redundant circles appear when an edge's endpoints
are already both dynamic).

Verified: 337 tests pass (6 new), `uv run ruff check .` clean (one
E501 line-length fix along the way), `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean with no console output beyond the usual benign GTK/Atk
lines.

**Manual verification (you)**: confirm the default (unzoomed, freshly
opened) layout shows roughly as many nodes as it did before the
gravity-well fix, just now organized around a centered `index`, and that
nodes still never visibly overlap; drag a note whose only edge goes to a
currently-static node (most drags, given the gravity well fix) and
confirm the edge no longer visibly covers the node circle at either end,
throughout the drag, not just at rest.

## Phase 23 — Graph canvas Settings panel shell

### Context

The first of a larger wishlist (2026-07-30): a persistent, non-modal
Settings panel living on the graph canvas, replacing the legend, with
Filters (note type/tags/fuzzy search/date range/degrees-of-separation/
custom group) and Settings (colors, zoom/pan tuning, physics/animation,
custom groups) to follow inside it in later phases. Given the size of
the full list, agreed to sequence it: this phase builds only the
minimize/expand shell itself, with the existing category-color legend as
its (unchanged) content -- nothing new goes inside yet. Confirmed with
you: persisted graph-view settings (this phase's expand/collapse state,
and everything Filters/Settings will add later) live per-vault, in
`.llm-wiki-config`, joining the existing `AppSettings` mechanism
(`src/llm_wiki/config.py`) already used for `llm_provider`/`mcp_server`/
`vault`.

### Implementation

**`src/llm_wiki/config.py`**: new `GraphViewConfig(BaseModel)` sibling to
`LLMProviderConfig`/`MCPServerConfig`/`VaultConfig` -- one field for now,
`settings_panel_expanded: bool = True` (defaults to expanded so the
legend stays visible out of the box, matching today's always-on
behavior). `AppSettings` gains `graph_view: GraphViewConfig =
Field(default_factory=GraphViewConfig)`; `AppSettings.save()` gains
`existing["graph_view"] = self.graph_view.model_dump(mode="json")`,
mirroring the existing three lines exactly.

**`src/llm_wiki/gui/graph_canvas.py`**: `_build_legend()` is replaced by
a collapsible panel in the same top-left slot (`left=14, top=12`, same
`theme.CHROME_BG`/`theme.BORDER`/`border_radius=8` styling as today's
legend -- no visual style change, just a header/toggle added around the
existing content):

- New state: `self._settings_panel_expanded = True` (construction-time
  default; `set_settings_panel_expanded()` below is what actually syncs
  it from persisted settings).
- New constructor param `on_settings_panel_toggled:
  Callable[[bool], None] | None = None`, stored as `self.
  on_settings_panel_toggled` -- fired only on a genuine user click (not
  when synced from settings), so the caller can persist it.
- `_build_settings_panel_content()`: a header `ft.Row` (a small
  gear/title, a spacer, and a chevron toggle button wired to
  `_toggle_settings_panel`) plus, only when expanded, the existing
  category-color legend rows (the exact content `_build_legend()` builds
  today, unchanged) below it. Collapsed state renders just the header.
- `_toggle_settings_panel(e=None)`: flips `self._settings_panel_expanded`,
  rebuilds+updates the panel's content, and -- only here, not from
  `set_settings_panel_expanded()` -- calls `self.
  on_settings_panel_toggled(self._settings_panel_expanded)` if set.
- `set_settings_panel_expanded(expanded: bool) -> None`: syncs from
  persisted settings; a no-op if the value already matches (avoids an
  unnecessary rebuild), never fires the toggle callback. Called once
  right after construction and again on every vault switch, matching
  `chat_panel.configure()`/`dashboard_panel.set_connection()`'s existing
  call-once-per-vault-change pattern.

**`src/llm_wiki/gui/app.py`**: `self.graph = GraphCanvas(page,
on_settings_panel_toggled=self._on_graph_settings_panel_toggled)`. New
`_on_graph_settings_panel_toggled(self, expanded: bool) -> None`: no-ops
if `not self.controller.has_vault` (toggling before any vault is open
must not crash -- `save_settings()` requires a vault path, confirmed via
the existing `test_saving_settings_without_a_vault_raises` test),
otherwise sets `self.controller.settings.graph_view.
settings_panel_expanded = expanded` and calls `self.controller.
save_settings()`. `_on_vault_changed()` gains one line alongside its
existing panel-sync calls: `self.graph.set_settings_panel_expanded(self.
controller.settings.graph_view.settings_panel_expanded)`.

### Tests

- `tests/test_config.py`: `GraphViewConfig` default
  (`settings_panel_expanded is True`), and a save/load round-trip through
  a real `.llm-wiki-config` file (mirroring the file's existing
  `llm_provider`/`vault` round-trip tests).
- `tests/test_gui_shell.py`'s Graph canvas section: default state is
  expanded and shows the legend rows; `_toggle_settings_panel()` collapses
  it (content becomes just the header) and fires the callback with
  `False`; toggling again re-expands and fires with `True`;
  `set_settings_panel_expanded()` syncing the *same* value already set is
  a no-op (no callback fired, confirmed via a stub that asserts it was
  never called); syncing a *different* value updates the panel without
  firing the callback.
- `tests/test_gui_shell.py`'s Shell-level wiring: `_on_graph_settings_panel_toggled`
  persists to `controller.settings.graph_view.settings_panel_expanded`
  and calls `save_settings()` when a vault is open; a no-op (no raise)
  when none is open.

### Verification

Implemented per the design above, no material deviations, with one
placement note found during implementation: the app-level persistence
wiring test (`Shell._on_graph_settings_panel_toggled`, real vault open +
disk round-trip) landed in `tests/test_gui_toolbar.py`, not
`test_gui_shell.py` -- that file, not this one, already has the
established `Shell(page)` + real-vault-open + thread-crossing `_FakePage`
infrastructure (`test_shell_wires_vault_open_through_a_real_pipeline_run`
is the existing precedent for exactly this kind of test); `test_gui_
shell.py` only ever builds controls directly, never a full `Shell`.

Tests: 4 new in `tests/test_config.py` split into 2
(`GraphViewConfig` default `+` a save/load round-trip through a real
file), 4 new in `test_gui_shell.py`'s Graph canvas section (default
expanded-with-legend content, toggle collapses/fires the callback both
directions, `set_settings_panel_expanded()` syncs without firing the
callback, and is a no-op for an already-matching value), 2 new in
`test_gui_toolbar.py` (the toggle handler is a no-op without a vault;
persists to `controller.settings.graph_view.settings_panel_expanded`
*and* survives a real reload from disk when a vault is open).

Verified: 345 tests pass (10 new), `uv run ruff check .` clean (one
line-length wrap on the `GraphCanvas.__init__` signature), `uv run flet
build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches clean with no console output beyond the usual
benign GTK/Atk lines.

**Manual verification (you)**: confirm the panel looks the same as
today's legend when expanded; click the toggle and confirm it collapses
to just a header, and expands back; close and reopen the vault (or
restart the app) and confirm the collapsed/expanded state you left it in
is remembered.

## Phase 24 — Graph canvas Filters

### Context

Second piece of the graph-view feature list, inside the Settings panel
shell from Phase 23 (`_build_settings_panel_content()`'s docstring
already anticipated this: "Filters and Settings (later phases) will add
more sections here"). Five of the six filter types from your original
list; the sixth (filter by custom group) is deferred, per your own call,
until Custom Groups actually exist as a concept -- nothing to filter by
otherwise. Two other scope questions resolved with you before writing
this plan: the date filter uses `updated_at` only (the `notes` table has
no `created_at` at all -- confirmed directly in `storage/schema.sql`,
and `updated_at` is touched on every edit, not a stable creation date;
adding real creation tracking would be a separate schema migration, not
bundled here), and the tag filter uses ANY-selected-tag (OR) semantics.

Research before planning (not assumed): `get_graph_data()`
(`graph/link_engine.py:44-62`) already attaches `title`/`type`/`tags` to
every node -- no engine change needed for those three filters. The
existing `degrees_of_separation()` (`link_engine.py:65-79`) is *not*
reused for the degrees-of-separation filter -- it re-queries the DB per
call and, more importantly, computes over the **directed** graph, which
turns out to make it nearly useless for this purpose: every note's edges
point *into* `index` (Phase 18's Related-block), never out, so a directed
BFS from a selected leaf note can reach `index` and its own source but
essentially nothing else. This filter instead runs
`nx.single_source_shortest_path_length()` directly against the
already-in-memory `self._graph.to_undirected()`, which is what "how
topologically close is this to what I selected" actually means for
exploration. No fuzzy-matching library exists anywhere in this project
(`pyproject.toml` confirmed) -- using stdlib `difflib.SequenceMatcher`
rather than adding a new dependency for what's a short-string,
small-corpus match.

### Design

**Filtering happens at shape-build time, not by touching `self._graph`/
`self._positions`/re-running layout.** A node that fails the filter
simply never gets a shape built for it; its position is untouched, so it
snaps back exactly where it was the moment it's unfiltered again -- no
relayout, no jump. `index` (`_GRAVITY_WELL_SLUG`) is exempted from every
filter unconditionally: it's not a real note (never a `notes` row, no
`type`/`tags`/`title` of its own) and hiding it would break the visual
anchor everything else was just organized around.

**New `_passes_filters(slug) -> bool`** (`graph_canvas.py`): checks type,
tags (any-match), fuzzy search (title+slug), `updated_at` range, and --
only when a node is selected -- degrees of separation, in that order,
short-circuiting on the first failure. `_build_static_shapes()`/
`_build_dynamic_shapes()` each gain a `self._passes_filters(slug)` guard
before appending a node's circle, and `self._passes_filters(u) and
self._passes_filters(v)` before appending an edge (both endpoints must
be visible -- an edge to an invisible node would dangle). `_node_at()`
gains the same guard, so hit-testing (click/drag/hover) never targets an
invisible node.

**Degrees-of-separation cache**: `self._degrees_from_selected: dict[str,
int]`, recomputed once in `_notify_selection()` (not per filter check --
would be O(n) BFS per node otherwise) whenever `self._selected` changes,
via `nx.single_source_shortest_path_length(self._graph.to_undirected(),
self._selected)`. Cleared to `{}` on deselect.

**Selection safety**: any filter change re-checks whether `self.
_selected` still passes; if not, it's cleared (mirroring the existing
empty-canvas-click deselect path) rather than leaving an info overlay
pointing at a now-invisible node.

**State** (flat instance attributes, matching this class's existing
style -- `self._selected`/`self._zoom`/etc., not a nested config object):
`self._filter_types: set[str]` (all 4 note types by default), `self.
_filter_tags: set[str]` (empty = no tag filter), `self._filter_search:
str`, `self._filter_date_from`/`_filter_date_to: str | None` (ISO date
strings), `self._filter_degrees: int | None` (only applied when `self.
_selected is not None`, per your own spec).

**Panel UI**, appended to `_build_settings_panel_content()`'s expanded
body below the existing legend, behind a "FILTERS" section label
(`size=11, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500`, matching
`dashboard_panel.py`'s section-label convention):
- Type: 4 `ft.Checkbox` controls, reusing `dashboard_panel.py`'s exact
  `_NOTE_TYPES` color mapping (concept/`STAGE_ATOMIZE`,
  entity/`ACCENT`, source/`STAGE_LINK`, synthesis/`STAGE_LINT`) as each
  checkbox's accent color, for visual consistency with the Dashboard tab.
- Tags: toggleable chips adapted from `toolbar.py`'s `_chip()` helper
  (dot + label in a rounded bordered container -- filled/accent border
  when selected, muted otherwise), built from the full tag vocabulary
  computed live from `self._graph`'s node attributes (`{t for _, data in
  self._graph.nodes(data=True) for t in data.get("tags", [])}` -- no new
  engine query, the data's already loaded).
- Search: a compact `ft.TextField`, `on_change` updates
  `self._filter_search`.
- Date range: two small buttons showing "From: ..."/"To: ..." (or
  "Any"), each opening an `ft.DatePicker` (confirmed present in the
  installed Flet version) on click.
- Degrees: a `ft.Slider` (1-5), disabled/greyed out with a "(select a
  node)" caption when `self._selected is None`.
- A "Reset" button/link clearing all five back to defaults in one click.

The panel's expanded body gets a scrollable, height-capped wrapper
(`ft.Column(scroll=ft.ScrollMode.AUTO)` inside a fixed-height outer
`Container`) now that it holds the legend plus a genuinely tall filter
section -- it no longer just grows to fit.

**Persistence**, extending Phase 23's `GraphViewConfig`
(`src/llm_wiki/config.py`) with `filter_types: list[str]`, `filter_tags:
list[str]`, `filter_search: str`, `filter_date_from`/`filter_date_to:
str | None`, `filter_degrees: int | None` (lists, not sets -- clean JSON
round-tripping; `GraphCanvas` converts to/from `set` at its boundary).
`AppSettings.save()` needs no change -- `graph_view` is already dumped
whole. Wiring mirrors Phase 23's `on_settings_panel_toggled` pattern
exactly: a new `GraphCanvas` constructor param `on_filters_changed:
Callable[[GraphFilterState], None] | None`, a small `GraphFilterState`
NamedTuple bundling the five values (avoids five separate callbacks), a
`set_filters(state)` sync method (called once after vault open, fires no
callback) alongside `set_settings_panel_expanded()`, and each filter
control's own handler calling a shared `_apply_filter_change()` tail
(reselection-safety check, panel rebuild, `_redraw_all()`, fires `self.
on_filters_changed(...)` if set). `app.py` gains
`_on_graph_filters_changed(state)`, mirroring
`_on_graph_settings_panel_toggled` exactly (no-ops without a vault,
otherwise writes the five fields to `controller.settings.graph_view.*`
and calls `save_settings()`), and `_on_vault_changed()` gains one more
sync call alongside the existing `set_settings_panel_expanded(...)` line.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all filter logic, state, and UI.
- `src/llm_wiki/config.py` -- `GraphViewConfig` gains the five filter
  fields.
- `src/llm_wiki/gui/app.py` -- `_on_graph_filters_changed()` + one more
  `_on_vault_changed()` sync line.

### Tests

`tests/test_gui_shell.py`'s Graph canvas section: `_passes_filters()`
unit tests, one per filter dimension (type, tags-any-match, fuzzy
search including a genuinely-fuzzy not-exact-substring case, date range,
degrees-of-separation both with and without a selection) plus a
combined-filters case; `index` always passes regardless of any filter
combination; a filtered-out node is excluded from both `_build_static_
shapes()`/`_build_dynamic_shapes()` (node circle) and from any edge
touching it; `_node_at()` never hits a filtered-out node; changing a
filter that excludes the currently-selected node clears selection and
hides the info overlay; the degrees cache recomputes on selection change
and clears on deselect. `tests/test_config.py`: `GraphViewConfig`'s five
new fields default correctly and round-trip through a real file.
`tests/test_gui_toolbar.py`: `_on_graph_filters_changed` persists all
five fields when a vault is open, no-ops without one (mirroring the
existing settings-panel-toggle tests exactly).

### Verification

Implemented per the design above, with one real bug found and fixed
during implementation (not anticipated in the plan): the fuzzy-search
check originally concatenated `f"{title} {slug}"` into one string before
running `_fuzzy_match()` against it. Direct testing (matching this
session's own "verify, don't guess" habit) showed this dilutes
`difflib.SequenceMatcher`'s ratio badly for a genuinely-fuzzy (non-
substring) query -- `"alfa"` against `"alpha"` alone scores `0.667`
(comfortably over the `0.4` threshold), but against the concatenated
`"alpha note-a"` it drops to `0.27` (would incorrectly fail). Fixed by
checking title and slug independently (`_fuzzy_match(query, title) or
_fuzzy_match(query, slug)`), which also happens to be more correct
semantically -- a fuzzy hit on either field should count, not a hit on
their concatenation. `get_graph_data()` (`graph/link_engine.py`) also
needed a small, anticipated extension: `updated_at` added to its
`SELECT` and node attributes, since the date filter had nothing to read
otherwise.

Tests: 12 new in `test_gui_shell.py`'s Filters section covering each
filter dimension individually (type, tag any-match, fuzzy search --
including the genuinely-fuzzy non-substring case the bug above was
caught by, date range, degrees-of-separation both with and without a
selection) plus `index`'s unconditional exemption, shape/hit-testing
exclusion, selection-safety on a filter change, Reset, and `set_filters()`'s
sync-without-firing-callback and no-op-for-unchanged-state behavior; 4
new in `test_config.py` (`GraphViewConfig`'s five filter fields default
correctly and round-trip through a real file); 4 new in
`test_gui_toolbar.py` (`_on_graph_filters_changed` persists all five
fields to disk when a vault is open, no-ops without one -- mirroring the
existing settings-panel-toggle tests exactly).

Verified: 360 tests pass (20 new), `uv run ruff check .` clean (one
import-order fix via `ruff check --fix` after adding a new import to
`test_gui_toolbar.py`), `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines. The one unrelated,
pre-existing `test_terminal_panel.py` flake (a background PTY-reader
thread racing test teardown, first noticed several fixes ago) still
appears in the full suite run, confirmed via isolated reruns to be
untouched by anything in this phase.

**Manual verification (you)**: uncheck a note type and confirm matching
nodes (and their edges) disappear, `index` stays visible regardless;
select a couple of tag chips and confirm any-match behavior; type a
fuzzy/misspelled search term and confirm loosely-matching notes still
show; set a date range and confirm it narrows to recently-touched notes;
select a node, set degrees-of-separation to 1, and confirm only its
direct neighbors (undirected) plus itself remain; hit Reset and confirm
everything returns; close and reopen the vault and confirm your filter
choices were remembered.

## Post-24 fix: Filters redraw-coupling bug, per-filter toggles, layout/labels

### Context

Six issues from your first real usage pass on Phase 24, one flagged
**important**. Root-caused each directly against the current code before
planning a fix, not guessed at.

**#6 [IMPORTANT] Typing in Search loses focus every keystroke.**
Confirmed root cause: `_on_filter_search_changed()` calls
`_apply_filter_change()` on *every* `on_change` event (i.e. every
character), which unconditionally calls `_rebuild_settings_panel()` --
`_build_settings_panel_content()` constructs a **brand-new** `ft.TextField`
object each time and pushes it to the client via `.update()`. This is the
exact same class of bug this project already root-caused and fixed once
before (Post-16d, the chat input field): syncing a text field's `value`
from the Python side resets its focus state on the client, because the
live widget instance is being replaced/resynced, not because anything is
visually different. The general principle you're describing in #5 --
"the graph should not affect the settings, the settings should affect the
graph" -- is the same principle, generalized: continuous, high-frequency
input (typing, slider-dragging) must never trigger a full panel rebuild,
only the graph redraw it's actually supposed to cause. Fix: split
`_apply_filter_change()`'s "rebuild the panel" step behind a parameter,
default `True` for discrete interactions (checkbox/chip/date-pick/switch/
reset -- one-shot clicks, no continuous-input risk), `False` for the two
continuous ones (search typing, degrees-slider dragging). Native Flet
controls (`Checkbox`, `Slider`, `Switch`) already reflect their own
toggled/dragged state client-side without a server round-trip, so
skipping the rebuild for a handler that doesn't change any *other*
control's appearance is always safe, not just for these two.

**#2 Degrees slider always shows disabled.** Confirmed root cause:
`disabled=self._selected is None` is evaluated once, at whatever moment
`_build_settings_panel_content()` last ran -- but `_notify_selection()`
(which changes `self._selected`) never calls `_rebuild_settings_panel()`,
so the slider's disabled-state is permanently stale from construction
time. Per your explicit ask, the fix isn't to make this recompute
correctly -- it's to remove the selection-gating entirely: the slider
should always be interactive, with its own enable/disable **switch**
(item #4 below) as the only thing that actually gates it. A small caption
still notes "(select a node to apply)" when enabled but nothing's
selected, since the filter genuinely has no effect in that state --
informational, not a control-disabling condition anymore.

**#1/#3 Layout.** The settings panel `Container` has no `width`, and the
tag-chip `ft.Row(wrap=True)` has nothing to wrap *within* -- confirmed
this is why it stretches: `wrap=True` needs a bounded parent width to
actually wrap inside, which an unconstrained Container never gave it.
Per your explicit follow-up answer, tags move into a dedicated
`ft.PopupMenuButton` (confirmed present in this Flet version,
`PopupMenuItem.content` accepts an arbitrary `Control`, not just a plain
label) -- this is a genuine, not-fully-verifiable-without-a-live-build
experiment, exactly as you framed it: the popup's content is a
height-capped, scrollable container of chips, so if `PopupMenuButton`
auto-sizes to its content (the expected Flutter behavior), bounding the
content bounds the popup. The rest of the panel (type/search/date/
degrees) stays inline, each gaining a labeled, bordered sub-section
(#3) and its own enable switch (#4), and the whole panel gets a fixed
`width` so it can never stretch again regardless of content.

**#4/#5 Toggles.** New per-filter `*_enabled` booleans (defaulting
`True`, matching today's implicit always-on behavior) plus one master
`filters_enabled` -- `_passes_filters()` checks the master first (short-
circuits to "everything visible" if off), then each dimension's own
switch before applying it. Turning a filter off never clears its
configured value (types/tags/search text/dates/degrees value all
survive), addressing your stated concern directly ("hate to configure my
tags and then have to deselect them").

### Design

**`_passes_filters()`** gains the master + per-dimension gating described
above, wrapping each existing check. **`GraphFilterState`** (NamedTuple)
gains six new `bool` fields (`filters_enabled`, `types_enabled`,
`tags_enabled`, `search_enabled`, `date_enabled`, `degrees_enabled`) and
`degrees` changes from `int | None` to a plain `int` (always a real 1-5
value now that "off" is represented by `degrees_enabled=False` instead of
a `None`/`0` sentinel). **`GraphViewConfig`** (`config.py`) gains the same
six booleans plus `filter_degrees: int = 1` replacing the old
`filter_degrees: int | None = None`. `app.py`'s two sync points
(`_on_vault_changed()`'s `set_filters()` call, `_on_graph_filters_changed()`)
both need the extra fields threaded through, mechanically mirroring the
existing five.

**`_apply_filter_change(rebuild_panel: bool = True)`**: the search field's
and degrees-slider's own handlers call it with `rebuild_panel=False`;
every other handler (checkboxes, tag chips, date pickers, all six
switches, Reset) uses the default. `set_filters()` (the persisted-settings
sync path) always rebuilds, same as today -- it's never called from a
continuous-input context.

**Layout** (`_build_filters_section()` and friends): each of Type/Search/
Date/Degrees becomes its own bordered, labeled sub-`Container` (small
section label + a switch in its header row, content below, matching the
"box around each with a label" you asked for). Tags becomes a
`ft.PopupMenuButton` trigger (label shows "Tags" or "Tags (N)" when any
are selected) whose single `PopupMenuItem`'s `content` is a fixed-height
(e.g. 200px) `ft.Container` wrapping a `ft.Column(scroll=ft.ScrollMode.AUTO)`
of the same chip controls used today. The whole settings panel `Container`
gains an explicit `width` (e.g. 260px) so nothing it contains can ever
stretch it wider.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all of the above.
- `src/llm_wiki/config.py` -- `GraphViewConfig`'s six new fields + the
  `filter_degrees` type change.
- `src/llm_wiki/gui/app.py` -- both sync points extended.

### Tests

`tests/test_gui_shell.py`: existing filter tests updated for the new
`GraphFilterState` shape (six more fields, `degrees` no longer optional)
-- mechanical, no behavior change to what they already assert. New:
`_passes_filters()` respects the master switch (off -> everything
visible regardless of individual filter state) and each per-dimension
switch (off -> that dimension's configured value is ignored but not
cleared); the degrees slider's value/enabled state survive independently
of `self._selected`; a search-field change does *not* rebuild the
settings panel (assert `_settings_panel.content` identity is preserved,
the same technique the Post-22 redraw-dedup tests already established)
while still triggering a graph redraw; same for the degrees slider.
`tests/test_config.py`: the six new `GraphViewConfig` fields default
correctly and round-trip; `filter_degrees` defaults to `1`, not `None`.
`tests/test_gui_toolbar.py`: `_on_graph_filters_changed` persists the six
new fields, mirroring the existing five.

### Verification

Implemented per the design above, with one real behavioral bug found by
running the *existing* test suite (not a new test written for this fix)
after wiring the six enable switches: defaulting `filter_degrees_enabled`
to `True` like the other five meant simply *selecting a node* -- a normal
browsing action -- started silently hiding everything more than one hop
away, since degrees has no inert/no-op default value the way the other
four dimensions do (all-types-checked, empty tags/search, no dates are
all genuinely harmless; a 1-hop cutoff is not). Caught by a pre-existing
Phase 22 test (`test_dragging_partitions_shapes_between_static_and_
dynamic_layers`) suddenly losing a node it had no reason to lose. Fixed
by defaulting `filter_degrees_enabled=False` (the one exception among the
six switches) in all three places it's set: `GraphViewConfig`, `GraphCanvas
.__init__`, and `_on_filters_reset()` -- with a regression test added
specifically for this (`test_degrees_filter_is_off_by_default_even_once_
a_node_is_selected`) beyond what the plan's own test list called for.

Tests: 8 new in `test_gui_shell.py` (the redraw-decoupling regression for
both the search field and degrees slider -- asserting `_settings_panel
.content` identity survives while the graph canvas *does* redraw; master-
switch-overrides-everything; per-dimension-disable-keeps-its-value; the
degrees-default regression above), plus the existing filter tests updated
for the new `GraphFilterState`/`_passes_filters()` shape (mechanical,
same behavior asserted, just the extra six fields threaded through). 2
new in `test_config.py` (`GraphViewConfig`'s six switches default
correctly, degrees being the one exception) plus the existing round-trip
test extended to cover them. `test_gui_toolbar.py`'s two existing
`_on_graph_filters_changed` wiring tests extended with the six new
fields.

Verified: 366 tests pass (8 new, several existing ones updated), `uv run
ruff check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk/OpenGL-timeout lines.

**Manual verification (you) -- this phase has two genuinely open questions
only checkable on the real build**: (1) type continuously in the Search
field and confirm focus is never lost, and drag the degrees slider and
confirm no jank; (2) open the Tags popup and confirm its size is bounded
(not stretching across the graph) *and* -- the real unknown -- whether
clicking a chip inside it closes the whole popup or lets you pick several
before dismissing. If it auto-closes per click, that's a real UX problem
worth flagging back rather than living with, and we'd look at an
alternative (e.g. a plain bounded overlay Container instead of a true
`PopupMenuButton`) in a follow-up. Also confirm: each filter's switch
toggles its effect without clearing its configured value; the master
switch overrides everything at once; the panel never grows wider than its
fixed width regardless of tag count; labels/borders make each filter
section clearly separated at a glance; and specifically re-verify the
degrees filter stays off (no effect) until you deliberately flip its
switch, even after selecting a node.

## Post-24 fix #2: existing vaults couldn't open after the degrees-type change

### Context

Immediately after the previous fix shipped, you hit a real, blocking bug
opening your actual vault: `1 validation error for _ScopedSettings ...
graph_view.filter_degrees ... Input should be a valid integer
[type=int_type], input_value=None`. Root cause, confirmed by reproducing
it directly: your `.llm-wiki-config` was written by the original Phase 24
code, when `filter_degrees: int | None = None` was the field's real type
and `null` was its genuine on-disk default. The Post-24 fix above
narrowed that field to `int = 1` (no longer optional) -- correct for the
*new* semantics (degrees is always a real value now, gated by its own
enable switch), but it broke loading any config file that still has the
old `null` on disk, which is every vault opened before this fix. Pydantic
validates a *present* `null` value against the field's declared type
strictly; a missing key would have silently used the default, but an
explicit `null` does not.

### Fix

`src/llm_wiki/config.py`: a `field_validator("filter_degrees",
mode="before")` on `GraphViewConfig` that coerces `None` to `1` before
type validation runs -- treats an old file's `null` the same as an
omitted key. Verified directly against a reproduction of your exact
error (a config file with `"graph_view": {"filter_degrees": null}`)
before considering this fixed, not just inferred from reading the code.

### Tests

`tests/test_config.py`: new regression test loading a config file with
`filter_degrees: null` on disk, asserting it loads successfully and
falls back to `1`.

### Verification

Verified: 367 tests pass (1 new), `uv run ruff check .` clean, `uv run
flet build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches clean.

**Manual verification (you)**: open your actual vault again and confirm
it loads without error this time.

## Post-24 fix #3: settings panel must never rebuild itself, only mutate

### Context

The Post-24 fix's "discrete vs. continuous" distinction was wrong. It
only exempted the search field and degrees slider from triggering a full
panel rebuild, on the theory that one-shot clicks (checkboxes, chips,
switches, date picks, Reset) were safe to rebuild for. They aren't: every
one of those still calls `_rebuild_settings_panel()`, which reassigns
`self._settings_panel.content` to a brand-new control tree. In practice
this reset the scroll position on every click, and specifically broke
the Tags popup -- clicking a chip tore down and rebuilt the
`PopupMenuButton` it lives inside mid-interaction, leaving it "selected
but stale" (unclickable until scrolled back to and reopened), which is
exactly why whether a chip click closes the popup couldn't even be
observed.

Your instruction is unambiguous and correct: **no control interaction
should ever cause the settings panel to reset/resync/redraw.** Every
control -- chip, checkbox, switch, text field, slider, date picker --
should be self-contained. Clicking one should only ever: (1) update this
class's own filter state, (2) tell the *graph* to redraw (that's the
actual point of a filter changing), and (3) fire the persistence
callback. The panel's own control tree should be built exactly once and
never reconstructed again, matching the pattern this file already
established and proved correct elsewhere -- `_build_info_overlay()` /
`_update_info_overlay()` build the info card's `Text` controls once in
`__init__` and, on every selection change, only ever mutate their
`.value`/`.visible` and call `.update()` directly on those specific
controls, never rebuilding the overlay itself. The Filters section needs
the exact same discipline, just across more controls.

**Two smaller items from the same message**: the tags popup's padding is
asymmetric (padded top/bottom, flush left/right) -- likely because my
own `Container(padding=8)` sizes the popup's width exactly to its
content while the menu's own vertical chrome adds unrelated inset;
`PopupMenuButton` has a `menu_padding` parameter (confirmed in this
Flet version's signature) that should produce uniform spacing applied to
the *menu surface* itself instead. And the loading error is confirmed
fixed -- no action needed.

### Design

**Build every control that can change appearance exactly once, in
`__init__` (via the existing `_build_settings_panel()` construction
path), and store a reference to it.** No handler ever reassigns
`self._settings_panel.content` again. The full reference list, and what
mutates each:

- `self._panel_chevron` (`Text`), `self._panel_body` (`Container`) --
  expand/collapse becomes `self._panel_body.visible = expanded` +
  updating the chevron glyph, both existing controls mutated in place --
  not a content swap between "header only" and "header + everything"
  like today. `_toggle_settings_panel()` and `set_settings_panel_expanded()`
  both change to this.
- `self._type_checkboxes: dict[str, Checkbox]` -- their own checked-state
  is already self-reflecting client-side on click; only needed so
  `set_filters()`/Reset can sync their `.value` when loading a different
  configuration wholesale.
- `self._tag_chip_controls: dict[str, Container]` -- chips are custom
  `Container`s, not a native toggle control, so clicking one *does* need
  a direct mutation: `_on_filter_tag_toggled(tag)` restyles just that one
  container's `bgcolor`/`border`/text colour (a small `_style_tag_chip()`
  helper, reused at initial build and here) and calls `.update()` on it
  alone.
- `self._tags_trigger_label` (`Text`, the "Tags"/"Tags (N)" button label)
  -- mutated alongside the chip on every tag toggle.
- `self._tags_chip_column` (`Column`, inside the popup) -- not mutated by
  filter interactions at all; only rebuilt when the underlying *tag
  vocabulary* changes (a new tag appears after an ingest), via a new
  `_refresh_tag_popup()` called from the two layout-completion points
  (`_apply_positions()`, `_compute_layout()`) -- the only place new tags
  could actually appear, and unrelated to any filter control being
  clicked.
- `self._search_field` (`TextField`) -- already self-contained since the
  first Post-24 fix; kept as a reference only so `set_filters()`/Reset
  can sync its `.value`.
- `self._date_from_label`, `self._date_to_label` (`Text`) -- mutated
  directly when a date is picked, instead of rebuilding the button.
- `self._degrees_slider` (`Slider`) -- already self-contained for
  dragging; reference kept for `set_filters()`/Reset sync.
- `self._degrees_caption` (`Text`, "Applies once you select a node" /
  "Within N hop(s)...") -- mutated directly both when the slider changes
  *and* from `_notify_selection()` (today it doesn't touch the Filters
  panel at all, which is a second, smaller instance of the exact same
  bug -- the caption goes stale the moment you select a node, same root
  cause as bug #2 from the previous round).
- Six `Switch` controls (master + one per dimension) -- self-reflecting
  on click; kept as references purely for `set_filters()`/Reset sync.

**Dropped from the original Post-24 fix's design, not carried forward:**
mirroring each switch's on/off state into a `disabled=` cascade over its
section's sub-controls. That was the source of most of the "needs to
touch other controls" complexity, and re-reading your original ask ("I
would hate to configure my tags and then have to deselect them") argues
for the opposite anyway -- controls should stay adjustable regardless of
whether their filter is currently enabled. The switch's own on/off
position, plus the filter's actual effect on the graph, is signal
enough; nothing needs to grey out.

**`set_filters()` and `_on_filters_reset()`** both change from "rebuild
the panel" to a shared `_sync_filter_controls_to_state()`: sets every
stored control's `.value`/`.text` (checkboxes, chips' styling, search
field, date labels, slider, all six switches) from the current
`self._filter_*` state and calls `.update()` on each individually --
same "mutate, don't rebuild" discipline, just applied to a bulk sync
instead of one field. `_apply_filter_change()` loses its `rebuild_panel`
parameter entirely (dead now that nothing ever rebuilds) and simplifies
to: redraw the graph, fire the callback. `_rebuild_settings_panel()` is
deleted.

**Tags popup padding**: `ft.PopupMenuButton(menu_padding=ft.Padding(8, 8,
8, 8), ...)` replacing the inner content `Container`'s own `padding=8`,
so the uniform inset comes from the menu surface itself rather than
being asymmetrically absorbed by content-width-driven popup sizing. This
is a best-effort fix, same as the original PopupMenuButton adoption --
worth a specific look on the next real build.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all of the above.

### Tests

`tests/test_gui_shell.py`'s Filters section: every existing "does this
change the filter state / fire the callback / redraw the graph" test
stays valid: unchanged behavior, only *how* the panel updates changes.
New/changed: expand/collapse toggles `_panel_body.visible` and mutates
the chevron `Text.value` without touching `_settings_panel.content`
identity at all (a stronger version of the existing redraw-decoupling
tests -- now *nothing* should ever replace the panel's content, not just
search/slider); toggling a tag chip mutates only that chip's own
container (identity of *other* chips' containers preserved) and updates
the trigger label's `.value`; selecting a node updates
`self._degrees_caption.value` directly (locks in the fix for the second
instance of bug #2); `set_filters()`/Reset correctly sync every control
type via `_sync_filter_controls_to_state()`, still without ever touching
`_settings_panel.content`.

### Verification

Implemented per the design above, no material deviations. Every control
(chevron, panel body, 4 type checkboxes, tag chips + trigger label + chip
column, search field, 2 date labels, degrees slider + caption, 6
switches) is now built exactly once in `_build_settings_panel()`'s
construction path and stored on `self`; `_rebuild_settings_panel()` and
the `rebuild_panel` parameter are gone entirely. `_apply_filter_change()`
no longer touches the panel at all -- every handler mutates only its own
control(s) first (a chip's own container + the trigger label,
`_date_from_label`/`_date_to_label`, `_degrees_caption`) and native
controls (Checkbox/Slider/Switch/TextField) need no mutation since they
already reflect their own edited state client-side. `set_filters()`/
`_on_filters_reset()` both route through the new shared
`_sync_filter_controls_to_state()`, which mutates every stored control
from the current filter state instead of rebuilding anything.
`_refresh_tag_popup()` is wired into both layout-completion points
(`_compute_layout()`, `_apply_positions()`) -- the only place the tag
*vocabulary* itself can change -- and `_notify_selection()` now mutates
`self._degrees_caption` directly, fixing the second instance of the
selection-goes-stale bug. Tags popup padding moved to
`PopupMenuButton(menu_padding=...)` as designed.

One test-fixture gap found while writing the new tests, not a production
bug: `_filters_canvas()` (the existing Filters test fixture) assigns
`canvas._graph` *after* construction, so the tags popup -- built during
`__init__()` against the still-empty starting graph -- never picked up
the fixture's real tags. Harmless in the old rebuild-based world (the
next rebuild would have picked them up anyway) but a real gap now that
nothing rebuilds; fixed by having the fixture call the new
`_refresh_tag_popup()` itself, matching what a real layout completion
would do.

Tests: 8 new in `test_gui_shell.py`'s redraw-decoupling section -- one
per remaining control kind (type checkbox, tag chip, date pick, a
representative switch) confirming `_settings_panel.content` identity is
preserved and only the relevant control(s) mutate (the tag-chip test in
particular asserts a *different* chip's container is untouched by
toggling another one -- the exact bug this fix targets), plus a direct
test that selecting/deselecting a node mutates `_degrees_caption.value`
without going through any panel rebuild. The two existing settings-panel-
shell tests were strengthened to assert `_settings_panel.content` identity
survives collapse/expand too (previously they asserted the content
*changed shape*, which is what the old rebuild-based implementation did
by design -- now nothing about the container's content ever changes,
only `_panel_body.visible` and the chevron glyph). `set_filters()`/Reset's
existing tests were extended to also assert the bulk control-sync itself
(checkbox values, chip styling, field/label/slider values, all six
switches), not just the underlying filter state.

Verified: 372 tests pass (8 new), `uv run ruff check .` clean, `uv run
flet build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches with no console output beyond the usual benign
GTK/Atk/OpenGL-timeout lines, no lingering process after exit.

**Manual verification (you)**: this is the one that matters most --
click through every control (checkboxes, tag chips, both switches
sections, search typing, date picks, slider, master switch, Reset) and
confirm the panel's scroll position and open/closed state never jump or
reset, regardless of what you interact with. With the panel no longer
disrupted mid-interaction, you should now be able to actually tell
whether clicking a tag chip closes the Tags popup or leaves it open for
multiple picks -- let me know what you find, since that's still the
open question from before. Also check the popup's padding looks uniform
now.

**Confirmed on the real build (you)**: the settings panel never resets;
every control (checkboxes, chips, switches, search, date picks, slider,
master switch, Reset) affects the graph as intended; global disable and
each per-dimension disable both work; Reset restores defaults; the tags
popup stays open across multiple chip picks (the open question from
Phase 24 -- it's a genuine multi-select popup, not a single-pick-then-
close one); padding around the tags is uniform. Post-24 fix #3 closes out
the Filters work with no open issues.

## Post-22 fix: hover label doesn't follow a node while it's being dragged

### Context

Reported 2026-07-30: hovering shows a node's label, but if you then drag
that node, the label stays frozen at the pre-drag position instead of
tracking the node. Root cause, confirmed directly: hover
(`_on_hover`/`_on_exit`) and drag (`_on_pan_start`/`_on_pan_update`) are
different Flutter gesture channels -- once the mouse button is down for a
drag, no more `PointerHoverEvent`s fire, so `self._hovered` simply never
updates again until the drag ends and the mouse moves. Separately, even
if it did, nothing during a drag calls `_redraw_hover()` -- the
simulation tick loop (`_simulation_loop()`) only calls `_redraw_dynamic()`
(or `_redraw_all()`, which itself never touched the hover canvas) on its
~30fps cadence, so the hover canvas was frozen for the whole drag
regardless.

Of the two options you offered, following the node (rather than hiding
the label mid-drag) is the better fit here: the whole point of the label
is identifying which node you're looking at, and that's most useful
exactly while you're actively repositioning one.

### Fix

Both in `src/llm_wiki/gui/graph_canvas.py`:

- `_build_hover_shapes()`: prefers `self._dragging` over `self._hovered`
  when a drag is active (`target = self._dragging if self._dragging is
  not None else self._hovered`) -- so the label always names whatever's
  actually being dragged, "as if it were part of the node," regardless of
  whatever `self._hovered` last held from real pointer-hover events.
- `_redraw_all()` gains a `self._redraw_hover()` call alongside the
  existing static/dynamic pair -- folding hover into the same "redraw
  everything" call this file already uses everywhere outside the
  per-tick hot path (this also fixes a smaller, unreported staleness gap:
  a hovered node's label position could go stale after any full
  redraw -- selection change, filter change, resize -- since hover was
  never part of that call before). `_simulation_loop()`'s steady-state
  branch (today just `_redraw_dynamic()`) gains a matching
  `self._redraw_hover()` call, so the label updates every tick during an
  active drag, at the same ~30fps the dragged node itself moves at.
- `_on_pan_end()` gains a trailing `self._redraw_hover()` -- cheap, and
  covers the edge case where `self._hovered` was already `None` going
  into the drag (e.g. dragging started with no prior real hover event),
  so the label correctly clears rather than lingering.

No change to `_on_hover()`/`_on_exit()` themselves, hit-testing, or the
simulation physics -- this is purely which node the hover *label*
targets and when its own small canvas gets told to redraw.

### Tests

`tests/test_gui_shell.py`'s hover section: a new test drags a node
through two `_pan_update_to()` calls and asserts `_hover_canvas.shapes`
contains exactly one label, positioned at the node's *current* (not
pre-drag) coordinates after each move; a second test hovers node A, then
starts dragging a *different* node B, and confirms the label shown during
the drag is B's, not A's stale one; a third confirms the label clears
after `_on_pan_end()` if nothing was hovered going in.

### Verification

Implemented per the design above, no material deviations.
`_build_hover_shapes()` now prefers `self._dragging` over `self._hovered`;
`_redraw_all()` gained a `_redraw_hover()` call; `_simulation_loop()`'s
steady-state branch and `_on_pan_end()` both gained one too.

One test-design wrinkle, not a production bug: a test driving a drag
through the real `_on_pan_start()` (which also calls `_start_simulation()`,
setting `_sim_active = True`) can't observe the position-tracking fix via
`_pan_update_to()` afterward under the headless `_page_stub()`, since
`_on_pan_update()`'s own redundant-redraw guard
(`if not self._sim_active: self._redraw_all()`, from the earlier Post-22
fix) correctly skips the direct redraw once a simulation is "active" --
with a real page the tick loop itself picks up the slack every frame;
under the stub, that loop never actually runs. Fixed by following the
exact pattern `test_pan_update_still_redraws_directly_if_the_simulation_is_not_running`
already established: set `canvas._dragging` directly rather than going
through `_on_pan_start()`, so `_sim_active` stays `False` and the same
fallback path fires synchronously.

Tests: 3 new in `test_gui_shell.py`'s hover section -- the label tracking
a dragged node's live position across a move, a drag on a different node
overriding a stale `self._hovered` from before the drag started, and the
label correctly clearing after `_on_pan_end()` when nothing was hovered
going in.

Verified: 375 tests pass (3 new), `uv run ruff check .` clean, `uv run
flet build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches clean with no console output beyond the usual
benign GTK/Atk/OpenGL-timeout lines.

**Manual verification (you)**: drag a node around and confirm its label
now stays attached to it throughout the drag; hover a different node
after releasing and confirm the label still updates normally on plain
hover (unaffected by this fix).

## Phase 25 — Graph canvas Settings: colors, physics/animation, zoom/pan

### Context

The last piece of the original wishlist's Settings panel content
(Filters shipped in Phase 24; custom groups deferred to its own later
phase per your 2026-07-30 answer, since nothing defines what a "custom
group" even is yet). Scoped down with you to three areas, each kept to a
few meaningful knobs rather than exposing every internal constant:
**Colors** (fix a real, previously-unnoticed inconsistency), **Physics /
Animation** (an on/off switch + one strength dial over Phase 22's
simulation), and **Zoom & Pan** (one knob: invert scroll-zoom direction,
the exact thing Phase 17's own verification notes flagged as
worth-confirming-per-hardware and never got a real fix for).

**Colors -- a real bug, not just a missing feature.** Investigated
directly: node circles are colored today via `_category_of(slug)`
(`graph_canvas.py:123`), a **hash of the slug** into one of 6 arbitrary
`theme.CATEGORY_COLORS` buckets (core/scripting/rendering/physics/editor/
multiplayer -- leftover names from the original design mockup, never
connected to this vault's real data model). This has nothing to do with
a note's actual `type` (concept/entity/source/synthesis) -- the same
field the Filters panel's Type checkboxes and `_FILTER_NOTE_TYPES` already
color by by a completely different mapping
(`theme.STAGE_ATOMIZE`/`ACCENT`/`STAGE_LINK`/`STAGE_LINT`). So today, a
note's color on the graph canvas has **no relationship at all** to its
color in the Filters checkbox next to it, or to its type. Per your
answer, this phase switches node coloring to the real `type` field,
customizable per-type, unifying it with what Filters already shows.
`_category_of()`/`theme.CATEGORY_COLORS`/`theme.DEFAULT_CATEGORY` are
dead code once this lands and are removed, not left behind unused.

### Design

**Colors.** New per-instance state, `self._type_colors: dict[str, str]`,
keyed by the 4 real note types plus `"index"` (the gravity-well hub has
no `type` of its own -- never a `notes` row -- so it gets its own
customizable neutral-default entry rather than an unexplained fallback).
Built in `__init__` from a new module constant `_DEFAULT_TYPE_COLORS`,
matching today's `_FILTER_NOTE_TYPES` colors exactly for the 4 real types
(so the *default* on-screen look for those doesn't change at all -- only
the mechanism, and the fact that it's now identical between the graph and
the Filters checkboxes) plus `theme.TEXT_DIM` for `"index"` (a muted
neutral, visually distinct from the four vivid type colors and from
`theme.ACCENT`, which stays reserved for the selection highlight).

`_node_circle()` changes from `theme.CATEGORY_COLORS[_category_of(slug)]`
to a new `_node_color(slug)`: `theme.ACCENT` if selected, else
`self._type_colors.get("index")` for the gravity well, else
`self._type_colors.get(self._graph.nodes[slug].get("type"), <index
color>)` (a note with a missing/unrecognized type -- shouldn't normally
happen -- falls back to the same neutral as the hub, never an
`AttributeError`/`KeyError`).

`_FILTER_NOTE_TYPES` (currently `(key, label, color)` triples) drops its
`color` element -- becomes `_FILTER_NOTE_TYPE_LABELS = ((key, label),
...)` -- since the Type filter checkboxes' `fill_color` now reads live
from `self._type_colors[type_key]` at build time, and gets **mutated**
(not rebuilt) when that type's color changes, exactly the "build once,
mutate on change" discipline Post-24 fix #3 just established. The 3
existing unpacking call sites (`_build_type_content()`,
`_on_filters_reset()`, `__init__`'s `self._filter_types` default) update
to the 2-tuple shape.

**The old static "CATEGORIES" legend section is replaced, not
supplemented**, by an interactive color-picker version -- adding a
separate parallel "Colors" section listing the same 4-5 things a second
time would be redundant with the legend that's already sitting right
there. `_build_legend_section()` becomes 5 rows (concept/entity/source/
synthesis/index), each a small color swatch `Container` + label `Text`,
the swatch wrapped in an `ft.PopupMenuButton`. The popup itself reuses
the exact pattern the Tags popup established in Post-24 fix #3 -- **and
deliberately reuses it for the same underlying reason, not just visual
consistency**: a single `PopupMenuItem` wrapping a `Row(wrap=True)` of
small clickable preset-color `Container`s, each with its own `on_click` --
*not* one `PopupMenuItem` per preset with its own `on_click` (the more
"native" way to build a Flutter popup menu). The distinction matters
because it's exactly what this session already learned, concretely,
building the Tags popup: a click handler on the *item itself* triggers
Flutter's built-in "close menu on selection," while a click handler on a
plain `Container` *nested inside* one shared item's `content` does not --
which is what let the Tags popup stay open across multiple picks
(confirmed as the desired behavior once Post-24 fix #3 made the panel
stop resetting). The same non-auto-closing behavior is *also* right here:
it lets you preview a couple of colors against the live graph before
settling on one, without the popup dismissing after the first click.

New module constant `_GRAPH_SWATCH_PALETTE: tuple[str, ...]` -- a fixed
set of ~8 preset hex colors for the picker, replacing (not keeping
alongside) `theme.CATEGORY_COLORS` as the source of pickable colors; its
existing 6 hex values are good, still-relevant swatches and get folded
into this new tuple rather than re-invented, plus the 4 colors already
used as `_DEFAULT_TYPE_COLORS`' defaults (deduplicated).

Selecting a preset (`_on_type_color_selected(type_key, color)`): updates
`self._type_colors[type_key]`, mutates that swatch `Container`'s
`bgcolor` (+ `.update()`), mutates the matching Type-filter checkbox's
`fill_color` if `type_key` is one of the 4 real types (+ `.update()`),
then calls the shared `_apply_display_settings_change()` tail (redraw +
fire the persistence callback, mirroring `_apply_filter_change()`
exactly). No other control is touched -- same discipline as every Post-24
fix #3 handler.

**Physics / Animation.** Two new instance fields: `self._simulation_enabled
= True`, `self._simulation_strength = 1.0`. `_start_simulation()` gains a
guard at its top: `if not self._simulation_enabled: return` -- when
disabled, `self._sim_active` simply never becomes `True`, and
`_on_pan_update()`'s existing `if not self._sim_active: self._redraw_all()`
fallback (already there for the "simulation isn't running" case) takes
over automatically, so a disabled simulation means a dragged node just
moves directly with no repel/trail effect and no other code path needs to
change. `_simulation_tick()`'s three force constants (`_SIM_REPEL_STRENGTH`,
`_SIM_NEIGHBOR_SPRING_K`, `_SIM_HOME_SPRING_K`) are each multiplied by
`self._simulation_strength` at the point they're used -- scaling repel,
trail-spring, and return-to-home forces together, not the damping/speed-
clamp/radius constants (a strength dial that also changed the effective
*radius* or *damping* would stop feeling like one intuitive "how strong"
knob and start feeling like three different sliders in disguise).

UI: a new bordered `ft.Switch` ("Enable Simulation") + `ft.Slider`
(`min=0.25, max=2.5, divisions=9`, captioned "Strength: {value}x", the
same caption-mutation pattern `_degrees_caption` already established) in
a new "Physics / Animation" box.

**Zoom & Pan.** One new field, `self._invert_scroll_zoom = False`.
`_on_scroll()`'s existing sign logic (`step = _SCROLL_ZOOM_STEP if
e.scroll_delta.y < 0 else -_SCROLL_ZOOM_STEP`) gets a final sign flip
when the toggle is on. UI: a single `ft.Switch` ("Invert Scroll-Zoom") in
its own "Zoom & Pan" box. Min/max zoom stay fixed constants, not exposed
-- there's no concrete need for them the way scroll-direction's own
Phase 17 note already flagged.

Both new boxes use a new, simpler sibling to `_build_filter_section_box()`
-- `_build_settings_section_box(title, content)` -- same bordered/labeled
`Container` shape, minus the per-section enable `Switch` in the header
row (these two sections don't have an "enable this whole dimension"
concept the way each Filter does; Physics's own "Enable Simulation"
switch already lives *inside* its content). Both boxes are appended to a
new `_build_display_settings_section()`, itself added to the panel body's
Column behind a new `"DISPLAY"` `_section_label()`, after Filters --
`_build_settings_panel_content()`'s controls list grows to
`[..., section_label("FILTERS"), filters_section, divider,
section_label("DISPLAY"), display_settings_section]`.

**State bundle + persistence**, mirroring `GraphFilterState`/
`set_filters()`/`_apply_filter_change()` exactly: new `GraphDisplaySettings
(NamedTuple)` (`type_colors: dict[str, str]`, `simulation_enabled: bool`,
`simulation_strength: float`, `invert_scroll_zoom: bool`), a
`_current_display_settings()` reader, `_apply_display_settings_change()`
tail (redraw + fire `self.on_display_settings_changed` if set -- a new
`GraphCanvas.__init__` param, same "fired only on genuine user change"
contract as `on_filters_changed`), `set_display_settings(state)` (the
persisted-settings sync entry point: no-op if unchanged, otherwise
merges `state.type_colors` **over** `_DEFAULT_TYPE_COLORS` -- not a bare
assignment -- so a config missing a key (e.g. an older save, or a future
6th type) always falls back cleanly rather than `KeyError`ing on lookup
later), and a `_sync_display_controls_to_state()` mutator (swatches, the
4 checkbox fill colors, the simulation switch/slider/caption, the invert
switch) -- called from both `set_display_settings()` and nowhere else,
same "only a bulk sync rebuilds nothing, it only mutates" rule Post-24
fix #3 established for `_sync_filter_controls_to_state()`.

**`src/llm_wiki/config.py`**: `GraphViewConfig` gains `type_colors:
dict[str, str] = Field(default_factory=dict)` (stores only what's been
customized -- an empty dict, the default, means "use my built-in
defaults," matching the merge-on-load design above; deliberately *not*
pre-filled with the 5 default hex values, which would duplicate them
outside `theme.py`/`graph_canvas.py` and go stale if either ever
changes), `simulation_enabled: bool = True`, `simulation_strength: float
= 1.0`, `invert_scroll_zoom: bool = False`.

**`src/llm_wiki/gui/app.py`**: `GraphCanvas(...)` gains
`on_display_settings_changed=self._on_graph_display_settings_changed`.
New `_on_graph_display_settings_changed(state)`, mechanically identical
to `_on_graph_filters_changed()` (no-op without a vault, else writes the
four fields to `controller.settings.graph_view.*` -- `type_colors` stored
via `dict(state.type_colors)` -- and calls `save_settings()`).
`_on_vault_changed()` gains one more sync call alongside the existing
`set_filters(...)` line, threading `gv.type_colors`/`simulation_enabled`/
`simulation_strength`/`invert_scroll_zoom` into a `GraphDisplaySettings(...)`.

**Explicit non-goal**: `gui/dashboard_panel.py`'s own `_NOTE_TYPES` bar-
chart color mapping is a separate, independent module-level tuple (no
shared state with `GraphCanvas` today) and stays exactly as-is -- wiring
the Dashboard tab to live-follow a graph-canvas color customization would
add a new cross-panel dependency neither currently has, well beyond "a
few meaningful knobs." Flagged here so it's a deliberate boundary, not a
silently-dropped piece of consistency.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all state/logic/UI above.
- `src/llm_wiki/gui/theme.py` -- remove `CATEGORY_COLORS`/`DEFAULT_CATEGORY`;
  the 6 hex values fold into `graph_canvas.py`'s new `_GRAPH_SWATCH_PALETTE`
  instead of being redefined.
- `src/llm_wiki/config.py` -- `GraphViewConfig`'s four new fields.
- `src/llm_wiki/gui/app.py` -- `on_display_settings_changed` wiring +
  one more `_on_vault_changed()` sync call.

### Tests

`tests/test_gui_shell.py`:
- Delete `test_category_colours_cover_the_mockups_six_buckets` (the
  constants it checks no longer exist); add a small
  `test_graph_swatch_palette_is_a_nonempty_tuple_of_valid_hex_colours`
  in its place (the existing `test_every_palette_colour_is_a_valid_hex_string`
  only scans string constants, so a tuple constant needs its own check).
- New Colors coverage: default `_type_colors` matches
  `_FILTER_NOTE_TYPES`' old defaults for the 4 real types plus the new
  `"index"` entry; `_node_color()` (or `_node_circle()`'s paint) reflects
  a custom color immediately after `_on_type_color_selected()`, for both
  a real type and `"index"`; selecting a type's color mutates that type's
  filter checkbox `fill_color` too, without touching any other checkbox
  or rebuilding the panel (`_settings_panel.content` identity preserved,
  same guarantee every other Post-24 fix #3 test already locks in); a
  note with a missing/unrecognized `type` falls back to the same neutral
  as `"index"` rather than raising.
- New Physics coverage: disabling simulation before a drag means
  `_sim_active` never becomes `True` and the dragged node's position
  still updates directly (reusing the existing
  `test_dragging_a_node_moves_only_that_node`-style assertion); the
  strength multiplier scales `_simulation_tick()`'s actual force output
  (compare a perturbed neighbor's displacement after N ticks at
  `strength=2.0` vs. the default `1.0`, same fixture graph
  `test_simulation_tick_repels_bystanders_and_pulls_neighbors_toward_the_anchor`
  already uses).
- New Zoom/Pan coverage: `_on_scroll()` zooms the opposite direction once
  `invert_scroll_zoom` is on, reusing the existing
  `test_scroll_up_zooms_in_and_scroll_down_zooms_out` fixture/pattern.
- `set_display_settings()`/its no-op-when-unchanged path, and the
  type_colors-merges-over-defaults behavior specifically (a state with
  only `{"concept": "#123456"}` set still leaves `"entity"`/`"source"`/
  `"synthesis"`/`"index"` at their built-in defaults after syncing).

`tests/test_config.py`: `GraphViewConfig`'s four new fields default
correctly (`type_colors == {}`, the three scalars at their stated
defaults) and round-trip through a real file, including a `type_colors`
dict with a couple of entries.

`tests/test_gui_toolbar.py`: `_on_graph_display_settings_changed`
persists all four fields when a vault is open, no-ops without one --
mirroring the existing `_on_graph_filters_changed` wiring tests exactly.

### Verification

Implemented per the design above, no material deviations. `_category_of()`
and `theme.CATEGORY_COLORS`/`DEFAULT_CATEGORY` are gone; `_DEFAULT_TYPE_COLORS`
(5 keys, matching the pre-Phase-25 Filters checkbox colors exactly for the
4 real types, plus `theme.TEXT_DIM` for `index`) and `_GRAPH_SWATCH_PALETTE`
(10 preset hex colors, folding in the old category palette) both landed in
`graph_canvas.py` as planned. `_node_color()` replaced the inline color
expression in `_node_circle()`; `_FILTER_NOTE_TYPES` became
`_FILTER_NOTE_TYPE_LABELS` (2-tuples), with its 3 call sites updated
mechanically. The legend section became the interactive color-picker
version, reusing the Tags popup's exact non-auto-closing `PopupMenuButton`
pattern (one `PopupMenuItem` wrapping a `Row(wrap=True)` of plain clickable
`Container`s) for the same underlying reason -- letting you preview a
couple of colors against the live graph before dismissing. Physics's
Strength slider scales `_SIM_REPEL_STRENGTH`/`_SIM_NEIGHBOR_SPRING_K`/
`_SIM_HOME_SPRING_K` together at the point they're used in
`_simulation_tick()`; disabling simulation is a one-line guard at the top
of `_start_simulation()`, with `_on_pan_update()`'s existing
not-`_sim_active` fallback already covering the rest. Zoom & Pan's invert
switch flips `_on_scroll()`'s zoom-in/zoom-out decision before computing
the step. `GraphDisplaySettings`, `_current_display_settings()`,
`_apply_display_settings_change()`, `set_display_settings()` (merging
`state.type_colors` over `_DEFAULT_TYPE_COLORS`, never assigning directly),
and `_sync_display_controls_to_state()` all mirror the Filters equivalents
`GraphFilterState`/`_apply_filter_change()`/`set_filters()`/
`_sync_filter_controls_to_state()` exactly, per Post-24 fix #3's "build
once, mutate only" discipline -- no handler in this phase ever reassigns
`_settings_panel.content` either. `config.py`'s `GraphViewConfig` gained
the four new fields (`type_colors` defaulting to `{}`, meaning "use the
built-in defaults"); `app.py`'s `_on_graph_display_settings_changed()` and
one more `_on_vault_changed()` sync call mirror the Filters wiring
mechanically.

Tests: `test_gui_shell.py` gained 12 new tests across Colors (defaults
match the old Filters colors plus `index`'s new neutral, a color change
reflects in `_node_color()` for both a real type and `index`, a color
change mutates only that type's checkbox/swatch without touching any
other control or `_settings_panel.content`, a missing/unrecognized type
falls back to `index`'s color), Physics (disabling simulation before a
drag means `_sim_active` never activates and the node still moves
directly, the Strength multiplier produces measurably more displacement
at `2.0` than the default `1.0` over the same tick count), Zoom & Pan
(inverting flips wheel-up from zoom-in to zoom-out), and display-settings
sync (`set_display_settings()`'s no-op-when-unchanged path, and the
partial-`type_colors`-merges-over-defaults case specifically). The one
pre-existing test asserting on the old `theme.CATEGORY_COLORS`/
`DEFAULT_CATEGORY` was replaced with a check on the new
`_GRAPH_SWATCH_PALETTE` tuple (the existing generic palette-validity test
only scans string constants, so a tuple constant needs its own check).
`test_config.py` gained 2 new tests (defaults, round-trip through a real
file including a populated `type_colors` dict). `test_gui_toolbar.py`
gained 2 new tests mirroring the existing `_on_graph_filters_changed`
wiring tests exactly (no-op without a vault, persists all four fields
and survives a real reload from disk when a vault is open).

Verified: 388 tests pass (16 new), `uv run ruff check .` clean, `uv run
flet build linux --python-version 3.13 --skip-flutter-doctor` succeeds,
rebuilt bundle launches clean with no console output beyond the usual
benign GTK/Atk/OpenGL-timeout lines, no lingering process after exit.

**Manual verification (you)**: confirm node colors now match the Type
filter checkboxes' colors (previously unrelated); open a color swatch's
popup, click a couple of different presets and confirm the graph updates
live each time without the popup closing, then click elsewhere to
dismiss it; confirm the matching Type checkbox's own color updated too;
turn off "Enable Simulation" and confirm dragging a node no longer
repels/trails neighbors, just moves it directly; turn it back on, try the
Strength slider at both ends and judge whether the physics feel
noticeably weaker/stronger; toggle "Invert Scroll-Zoom" and confirm the
wheel direction actually flips; close and reopen the vault (or restart)
and confirm every one of these choices was remembered.

## Post-25 fix: adjustable min/max zoom, a Node Spacing control

### Context

Your Phase 25 follow-up: "no zoom max/min or linked min/max node
distance." Two clarifying rounds settled the concrete scope, since
neither was a 1:1 match for existing mechanics:

- **Min/max zoom**: today's `_MIN_ZOOM`/`_MAX_ZOOM` (0.5x/2.0x) are fixed
  module constants `_set_zoom()` clamps against. You confirmed: add
  adjustable Min/Max Zoom sliders to Zoom & Pan, "linked" so the pair can
  never invert (raising Min above the current Max pushes Max up to match,
  and vice versa) -- the same spirit as a dual-handle range control, just
  built as two synced sliders since Flet's `Slider` is single-handled.
- **"Min/max node distance"**: genuinely ambiguous on first read --
  `nx.spring_layout()` (the actual layout engine, `_layout_positions()`)
  has no literal min/max-distance parameter, only one "target spacing"
  input (`k`, currently a fixed `_LAYOUT_SPACING` constant divided by
  `sqrt(node_count)`). Presented three concrete interpretations (a single
  spacing slider; a literal pixel min/max pair enforced by an extra
  post-layout relaxation pass; min/max bounds on how the existing
  node-count-adaptive formula scales) -- you confirmed the first: **one
  "Node Spacing" slider**, same shape as Physics/Animation's existing
  Strength slider, replacing the fixed constant. The literal min/max-pair
  reading would need new geometry-relaxation code with no guarantee of
  always satisfying both bounds on a dense/hub-heavy graph -- not worth
  that complexity for what turned out to be, once disambiguated, a
  request for *a* spacing control, not literally two independent bounds.

### Design

All in `src/llm_wiki/gui/graph_canvas.py`, extending Phase 25's exact
`GraphDisplaySettings`/`_apply_display_settings_change()`/
`set_display_settings()`/`_sync_display_controls_to_state()` machinery
with three more fields -- no new state-bundle shape needed.

**Constants**: `_LAYOUT_SPACING`/`_MIN_ZOOM`/`_MAX_ZOOM` renamed to
`_DEFAULT_NODE_SPACING`/`_DEFAULT_MIN_ZOOM`/`_DEFAULT_MAX_ZOOM` (same
values -- default behavior is unchanged), matching the `_DEFAULT_TYPE_COLORS`
naming precedent from Phase 25. Three new slider-range constants (fixed
bounds *for the sliders themselves*, distinct from the adjustable
min/max-zoom *values* they control): `_MIN_ZOOM_SLIDER_RANGE = (0.1, 1.0)`,
`_MAX_ZOOM_SLIDER_RANGE = (1.0, 5.0)`, `_NODE_SPACING_SLIDER_RANGE =
(1.0, 8.0)`.

**New instance state** (`__init__`): `self._min_zoom`/`self._max_zoom`
(defaulting to `_DEFAULT_MIN_ZOOM`/`_DEFAULT_MAX_ZOOM`), `self._node_spacing`
(defaulting to `_DEFAULT_NODE_SPACING`). `_set_zoom()`'s clamp
(`max(_MIN_ZOOM, min(_MAX_ZOOM, value))`) switches to the instance
attributes. `_layout_positions()`'s `k = _LAYOUT_SPACING / sqrt(n)`
switches to `self._node_spacing`.

**Zoom & Pan section** gains two more sliders (Min Zoom, Max Zoom, each
with a caption showing the current value), above the existing Invert
Scroll-Zoom switch. Handlers mutate only their own caption plus, when the
pair would invert, the *sibling* slider + its caption (same "mutate a
sibling control directly" technique `_on_filter_tag_toggled()` already
uses for a chip + the tags trigger label):

```python
def _on_min_zoom_changed(self, value: float) -> None:
    self._min_zoom = value
    if self._max_zoom < value:
        self._max_zoom = value
        self._max_zoom_slider.value = value
        self._max_zoom_caption.value = f"Max Zoom: {value:.2f}x"
        with contextlib.suppress(RuntimeError):
            self._max_zoom_slider.update()
            self._max_zoom_caption.update()
    self._zoom = max(self._min_zoom, min(self._max_zoom, self._zoom))
    self._min_zoom_caption.value = f"Min Zoom: {value:.2f}x"
    with contextlib.suppress(RuntimeError):
        self._min_zoom_caption.update()
    self._apply_display_settings_change()
```

(`_on_max_zoom_changed()` mirrors this symmetrically.) Re-clamping
`self._zoom` inline here -- not via a separate `_set_zoom()` call -- avoids
a redundant redraw, since `_apply_display_settings_change()`'s own tail
already calls `_redraw_all()`. Both sliders use plain `on_change`
(continuous, cheap -- no relayout involved, just a redraw), matching the
existing Simulation Strength slider's pattern.

**New "Layout" section** (a third `_build_settings_section_box()` in
`_build_display_settings_section()`, alongside Physics/Animation and
Zoom & Pan) holds the Node Spacing slider + caption. Unlike every other
slider in this panel, a spacing change needs an actual **relayout** (only
computed once, at `set_graph()`/`_compute_layout()` time -- not a per-
frame value like zoom or physics) -- doing that on every intermediate
drag tick (`on_change` fires continuously) would spam
`nx.spring_layout()` calls. Flet's `Slider` has a distinct `on_change_end`
(fires once, when the drag gesture completes) confirmed present in this
Flet version's signature -- exactly the right hook:

```python
def _on_node_spacing_changed(self, value: float) -> None:
    self._node_spacing = value  # live caption only, no relayout yet
    self._node_spacing_caption.value = f"Node Spacing: {value:.1f}"
    with contextlib.suppress(RuntimeError):
        self._node_spacing_caption.update()

def _on_node_spacing_change_end(self, e) -> None:
    self._page.run_thread(self._layout_worker)  # the real relayout
    self._apply_display_settings_change()
```

Reuses `_layout_worker()`/`_apply_positions()` verbatim -- the exact same
off-thread-compute-then-`run_task`-back path `set_graph()` already
established, not a new mechanism. This does mean a spacing change resets
any manually-dragged node positions, same as loading a new graph would --
expected, since changing spacing is a real structural relayout request.

**`_on_vault_changed()` reordering (`app.py`)** -- a real bug this
surfaced, not a hypothetical: today, `set_graph()` (which computes the
*first* layout for a freshly-opened vault) runs *before*
`set_filters()`/`set_display_settings()` sync persisted settings in from
disk. That was harmless while display settings had no effect on layout
itself (Phase 25's colors/physics/zoom-invert are all render-time or
drag-time only) -- but `node_spacing` is a genuine layout input, so
opening a vault with a customized spacing would compute the *first*
layout at the default spacing, then immediately relayout again once
`set_display_settings()` runs and notices the persisted value differs.
Not broken, just a wasted duplicate layout pass on every vault open.
Fixed by moving the `set_settings_panel_expanded()`/`set_filters()`/
`set_display_settings()` calls to the top of `_on_vault_changed()`,
before the `if self.controller.conn is not None:` block that calls
`set_graph()` -- nothing in the reordered calls depends on `conn`, so
this is a pure reordering, not a behavior change beyond removing the
redundant pass.

**`set_display_settings()`** gets one more line: after applying the
merged state, if the *new* `node_spacing` differs from what it was
before, trigger the same `self._page.run_thread(self._layout_worker)`
relayout the live slider's `on_change_end` does -- covers both the
reordered vault-open path above and any other future caller of
`set_display_settings()` with a changed spacing.

**`config.py`**: `GraphViewConfig` gains `min_zoom: float = 0.5`,
`max_zoom: float = 2.0`, `node_spacing: float = 4.0` (all matching
today's fixed defaults exactly). `GraphDisplaySettings` (NamedTuple)
gains the same three fields; `app.py`'s existing `GraphDisplaySettings(...)`
construction and `_on_graph_display_settings_changed()` both thread them
through, mechanically identical to the existing four Phase 25 fields.

**Test infrastructure note**: `tests/test_gui_shell.py`'s `_PageStub`
(used by `_page_stub()`, the placeholder for tests that don't need
`set_graph()`'s real threaded path) has no `run_thread()` today -- its
own docstring says so explicitly, since nothing before this needed it
besides `set_graph()` itself, which most tests bypass by setting
`canvas._graph`/`canvas._positions` directly. `_on_node_spacing_change_end()`
is a second call site now, so `_PageStub` gains a no-op `run_thread()`
too (mirroring its existing no-op `run_task()`), or every existing
`_page_stub()`-based test that happens to exercise this new handler would
raise `AttributeError`.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all state/logic/UI above.
- `src/llm_wiki/config.py` -- `GraphViewConfig`'s three new fields.
- `src/llm_wiki/gui/app.py` -- `_on_vault_changed()` reordering,
  `GraphDisplaySettings(...)`/`_on_graph_display_settings_changed()`
  thread the three new fields through.

### Tests

`tests/test_gui_shell.py`:
- `_PageStub` gains a no-op `run_thread()`.
- Min/Max zoom linking, both directions: raising Min above the current
  Max pushes Max (and its slider control) up to match; lowering Max
  below the current Min pushes Min down to match; the current `self._zoom`
  re-clamps immediately if it falls outside a newly-narrowed range.
- `_layout_positions()`'s sensitivity to `self._node_spacing` directly (a
  pure function, same style as the existing node-count/scale tests): a
  small vs. large spacing value on the same graph produces measurably
  different minimum pairwise distances.
- `_on_node_spacing_changed()` updates the caption without touching
  `_page.run_thread` (assert on the caption value only); a `_FakePage`-based
  end-to-end test (matching `test_set_graph_computes_layout_on_a_worker_thread`'s
  own precedent) drives `_on_node_spacing_change_end()` and confirms a
  real relayout actually runs through `run_thread`/`run_task`.
- `set_display_settings()` triggers a relayout when `node_spacing`
  differs from the current value, and does *not* when every field
  (including spacing) is unchanged (the existing no-op-for-unchanged-state
  test extended, not replaced).

`tests/test_config.py`: `GraphViewConfig`'s three new fields default
correctly and round-trip through a real file.

`tests/test_gui_toolbar.py`: the existing
`test_graph_display_settings_changed_*` tests extended with the three
new fields, mirroring their current shape.

### Verification

Implemented per the design above, no material deviations.
`_LAYOUT_SPACING`/`_MIN_ZOOM`/`_MAX_ZOOM` renamed to `_DEFAULT_NODE_SPACING`/
`_DEFAULT_MIN_ZOOM`/`_DEFAULT_MAX_ZOOM` with identical values; three new
slider-range constants added; `self._min_zoom`/`self._max_zoom`/
`self._node_spacing` instance state wired into `_set_zoom()`'s clamp and
`_layout_positions()`'s `k` computation. Min/Max Zoom sliders landed in
Zoom & Pan with the linking logic exactly as designed (raising one past
the other pushes the sibling's value *and* its slider control, re-clamping
`self._zoom` inline rather than through a second `_set_zoom()` call to
avoid a redundant redraw). The new "Layout" section's Node Spacing slider
splits `on_change` (caption only) from `on_change_end` (the real relayout,
via `self._page.run_thread(self._layout_worker)` -- the exact existing
path, no new mechanism). `set_display_settings()` compares the incoming
`node_spacing` against the current value before applying it and triggers
the same relayout path when it changed, covering the reordered vault-open
sync. `_on_vault_changed()` in `app.py` now syncs
`set_settings_panel_expanded()`/`set_filters()`/`set_display_settings()`
*before* the `if self.controller.conn is not None:` block that calls
`set_graph()`, so a vault's first layout already reflects persisted
spacing rather than computing once at the default and immediately
relaying out again.

One thing confirmed directly, not assumed, before writing the sensitivity
test: that `self._node_spacing` actually produces a measurably different
layout. Ran `_layout_positions()` against the same 17-note hub-and-spoke
fixture the Post-18 overlap-regression test uses at `node_spacing=1.0` vs.
`8.0` -- minimum pairwise distance went from ~4.3 to ~71.6, confirming the
effect is real and substantial before locking in the assertion.

Tests: `_PageStub` gained a no-op `run_thread()` (documented as serving
both `_start_simulation()`'s existing `run_task()` need and this fix's new
`run_thread()` one). 8 new tests in `test_gui_shell.py` -- Min/Max Zoom
linking in both directions (including the slider control itself being
pushed, not just the value), the current zoom re-clamping when the range
narrows, a redraw-decoupling check that neither zoom-bound change touches
`_settings_panel.content`, the direct `_layout_positions()` spacing-
sensitivity check, `on_change` updating only the caption (no relayout
call), and a `_FakePage`-based end-to-end test for `on_change_end`
(mirroring `test_set_graph_computes_layout_on_a_worker_thread`'s own
precedent) plus one more for `set_display_settings()`'s relayout trigger.
2 new in `test_config.py` (defaults, round-trip for the three new fields).
The 3 existing `GraphDisplaySettings` construction sites needing all-new-
fields updates (one in `test_gui_shell.py`, two in `test_gui_toolbar.py`)
were mechanical.

Verified: 396 tests pass (10 new, 3 updated), `uv run ruff check .`
clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk/OpenGL-timeout lines, no
lingering process after exit.

**Manual verification (you)**: drag Min Zoom above the current Max Zoom
(and vice versa) and confirm the other slider follows rather than letting
the pair invert; confirm you can no longer scroll/button-zoom past
whatever range you've set; drag the Node Spacing slider and release, and
confirm the graph visibly relayouts (tighter/looser) once you let go --
not continuously while dragging; confirm any manually-dragged node
positions reset when spacing changes (expected, same as a fresh layout);
close and reopen the vault and confirm all three new settings, and that
spacing is applied to the very first layout you see, not a moment later.

**Confirmed on the real build (you)**: all working -- linked min/max zoom,
Node Spacing's release-triggered relayout, and persistence across a
vault reopen. No open issues.

## Post-25 fix #2: animated Node Spacing relayout + permanent neighbor repositioning on drag

### Context

Two related asks (2026-07-30), briefly parked to the deferred list and
then pulled back for immediate implementation:

1. **A Node Spacing change should ease nodes to their new positions, not
   snap.** The previous fix (adjustable min/max zoom, Node Spacing) lands
   a spacing-triggered relayout instantly via `_apply_positions()` --
   correct for `set_graph()`'s brand-new-graph load (nothing to animate
   *from*), but jarring for a Node Spacing change, which repositions the
   *same* graph from real, meaningful "before" positions.
2. **Permanently moving a node should carry its direct neighbors along.**
   Today, after any drag ends, every perturbed node -- trailing neighbors
   *and* repelled bystanders -- eases back to its exact pre-drag "home"
   (Phase 22's spring-mass simulation). So dragging `index` to a new spot
   leaves it there while every note that backlinks to it springs back to
   the old layout, immediately going stale relative to the hub they're
   now far from. Confirmed with you: any node's drag should permanently
   carry its *direct* neighbors along (not repelled bystanders, which
   keep today's spring-back-to-old-home behavior), and the neighbors'
   new resting spot should come from a small local relayout around the
   moved node's new position -- a proper constrained `spring_layout` pass
   over just the anchor + its neighbors, not a simpler same-angle
   push/pull.

You also steered the *mechanism*: rather than a time-elapsed, eased lerp,
use distance -> a normalized direction vector -> constant-velocity
movement each tick, matching the "verify, don't guess" simulation work
in Phase 22 -- both items share one new, dedicated movement system, kept
deliberately separate from Phase 22's existing spring-damped simulation
(different movement law -- a constant-velocity step toward a known,
fixed destination has no live anchor to react to, so none of
`_simulation_tick()`'s overshoot/damping tuning applies here).

### Design

All in `src/llm_wiki/gui/graph_canvas.py`.

**New constants**: `_REPOSITION_SPEED = 300.0` (px/sec, constant --
starting value, same "tune against the real UI" framing as Phase 22's
own constants), `_REPOSITION_SETTLE_DIST_EPSILON = 1.5` (px; below this,
snap exactly and stop).

**New state** (`__init__`): `self._reposition_targets: dict[str,
tuple[float, float]] = {}`, `self._reposition_active = False`.

**The shared movement primitive**, mirroring `_simulation_tick()`/
`_simulation_loop()`'s own pure-tick/async-driver split:

```python
def _reposition_tick(self) -> bool:
    still_moving: dict[str, tuple[float, float]] = {}
    step = _REPOSITION_SPEED * _SIM_TICK_DT
    for slug, target in self._reposition_targets.items():
        if slug == self._dragging:
            continue  # a live drag always wins; its target is dropped, not resumed later
        current = self._positions.get(slug)
        if current is None:
            continue
        dx, dy = target[0] - current[0], target[1] - current[1]
        distance = math.hypot(dx, dy)
        if distance <= _REPOSITION_SETTLE_DIST_EPSILON or step >= distance:
            self._positions[slug] = target
            continue
        self._positions[slug] = (current[0] + dx / distance * step, current[1] + dy / distance * step)
        still_moving[slug] = target
    self._reposition_targets = still_moving
    return bool(still_moving)

async def _reposition_loop(self) -> None:
    while self._reposition_active:
        still_moving = self._reposition_tick()
        self._redraw_all()
        if not still_moving:
            break
        await asyncio.sleep(_SIM_TICK_DT)
    self._reposition_active = False

def _start_reposition(self, targets: dict[str, tuple[float, float]]) -> None:
    for slug, target in targets.items():
        self._positions.setdefault(slug, target)  # a brand-new node starts exactly at its target
    self._reposition_targets.update(targets)  # merges into an in-flight reposition, doesn't stomp it
    if not self._reposition_active:
        self._reposition_active = True
        self._page.run_task(self._reposition_loop)
```

No static/dynamic canvas-split integration -- `_redraw_all()` every tick,
full stop. Post-22's split solved a *continuous, redundant-redraw-source*
problem specific to an active drag; here there's exactly one redraw
source per tick (already measured cheap, ~2ms even at 150 shapes), and
this is a rare, short-lived event, not a 30fps-for-seconds one. Flagged
as the first place to look if a very large vault's spacing-change
animation ever feels janky.

**Item 1 wiring** -- `_layout_worker_animated()`/`_apply_positions_animated()`
replace the direct `_apply_positions()` landing for a spacing-triggered
relayout only (`set_graph()`'s brand-new-graph load keeps the instant
`_layout_worker()`/`_apply_positions()` path unchanged -- nothing to
animate *from* there):

```python
def _layout_worker_animated(self) -> None:
    positions = self._layout_positions()
    self._page.run_task(self._apply_positions_animated, positions)

async def _apply_positions_animated(self, positions: dict[str, tuple[float, float]]) -> None:
    # Unlike _apply_positions(), no _refresh_tag_popup() call -- a
    # spacing change relayouts the *same* graph, so the tag vocabulary
    # can't have changed.
    self._start_reposition(positions)
```

`_on_node_spacing_change_end()` and `set_display_settings()`'s existing
spacing-changed branch both switch from `self._page.run_thread
(self._layout_worker)` to `self._page.run_thread(self._layout_worker_animated)`.

**Item 2 wiring** -- a new `_neighbor_reposition_targets(anchor)`, run
when a drag ends:

```python
def _neighbor_reposition_targets(self, anchor: str) -> dict[str, tuple[float, float]]:
    if anchor not in self._graph or anchor not in self._positions:
        return {}
    neighbors = (
        set(self._graph.predecessors(anchor)) | set(self._graph.successors(anchor))
    ) - {_GRAVITY_WELL_SLUG, anchor}
    neighbors &= self._positions.keys()
    if not neighbors:
        return {}

    local_graph = nx.Graph()
    local_graph.add_node(anchor)
    for slug in neighbors:
        local_graph.add_edge(anchor, slug)

    # More neighbors need more room on the ring, not a denser pack at the
    # same radius -- verified directly (a synthetic 60-neighbor case,
    # matching a heavily-backlinked node like index) that a fixed radius
    # regardless of count crowds neighbors under the no-overlap floor by
    # ~60; scaling the radius with sqrt(neighbor count) -- the same
    # principle _layout_scale() already uses for the whole graph --
    # keeps them comfortably clear (verified: min pairwise ~19px at 60
    # neighbors, ~27px at 30, both above the 18px overlap floor).
    rest = _SIM_NEIGHBOR_REST_LENGTH * max(1.0, (len(neighbors) / 6) ** 0.5)
    k = max(1.0, len(neighbors) ** 0.5)
    local_pos = nx.spring_layout(
        local_graph, k=k, iterations=50, seed=42, pos={anchor: (0.0, 0.0)}, fixed=[anchor]
    )
    # spring_layout with fixed= skips its own auto-rescale (the same
    # Post-18 finding _layout_positions() already works around) -- here
    # that means raw output distances have no reliable real-world scale
    # at all (verified directly: a fixed k=1.0 produced ~2-3 units of
    # spread regardless of k's magnitude, not "k pixels" as a first
    # guess assumed), so this rescales to the *average* neighbor distance
    # explicitly rather than trusting k's absolute value.
    distances = [math.hypot(*local_pos[slug]) for slug in neighbors]
    avg_distance = sum(distances) / len(distances) if distances else 1.0
    scale = rest / avg_distance if avg_distance > 0 else 1.0

    anchor_x, anchor_y = self._positions[anchor]
    return {
        slug: (anchor_x + local_pos[slug][0] * scale, anchor_y + local_pos[slug][1] * scale)
        for slug in neighbors
    }
```

`_on_pan_end()` (currently just clears `_dragging`/`_panning` and
redraws hover) gains the trigger, gated behind Physics/Animation's
existing "Enable Simulation" switch -- consistent, not just convenient:
`_start_simulation()` already no-ops when `self._simulation_enabled` is
`False`, so with physics off, a dragged node's neighbors never moved in
the first place and there is nothing to permanently reposition:

```python
def _on_pan_end(self, e: ft.DragEndEvent) -> None:
    moved_node = self._dragging
    self._dragging = None
    self._panning = False
    if moved_node is not None and self._simulation_enabled:
        targets = self._neighbor_reposition_targets(moved_node)
        if targets:
            # Hands control to the reposition system -- Phase 22's own
            # spring-back-to-old-home simulation must stop touching these
            # nodes, or the two would fight over where they end up.
            self._sim_active_nodes -= targets.keys()
            for slug in targets:
                self._sim_velocities.pop(slug, None)
            self._start_reposition(targets)
    self._redraw_hover()
```

Bystanders (bumped aside by repel but not direct graph neighbors) are
untouched by this -- they stay in `self._sim_active_nodes` and keep
easing back to their old home via Phase 22's existing simulation, exactly
as before. Dragging `index` itself works the same way as any other node
-- every note backlinking to it becomes a "neighbor" and gets a new local
target, which is the explicit scenario this was asked for.

**Cancellation**: `set_graph()` (a brand-new graph, replacing
`self._positions` wholesale) gains `self._reposition_active = False`
and `self._reposition_targets = {}` alongside its existing `self._sim_active
= False`, so a reposition in flight from the *previous* graph never
fights the fresh instant layout.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all of the above; no other file
  changes.

### Tests

`tests/test_gui_shell.py`:
- `_reposition_tick()` (pure, direct): moves a node a fixed step toward
  its target each call; snaps exactly and drops out of
  `self._reposition_targets` once within the settle epsilon or when a
  step would overshoot; skips (and drops) a node currently being dragged.
- `_neighbor_reposition_targets()`: a small fixture (anchor + 3
  neighbors, no edges among them) lands each neighbor at approximately
  `_SIM_NEIGHBOR_REST_LENGTH` from the anchor's real position, radiating
  outward with no overlap; the gravity well is excluded even when it's
  a graph neighbor of the dragged node; a non-neighbor node is untouched;
  a larger synthetic neighbor set (e.g. 30) still clears the no-overlap
  floor, locking in the sqrt(count) radius scaling.
- `_on_pan_end()` integration: dragging a node then releasing starts a
  reposition for its direct neighbors, removes them from
  `self._sim_active_nodes`/`self._sim_velocities`; a bystander (not a
  direct neighbor) is untouched and still eases back to its old home; a
  no-op when `self._simulation_enabled` is `False`.
- `_start_reposition()` merges into an already-active reposition rather
  than stomping it (two calls before the first settles both end up in
  `self._reposition_targets`).
- A `_FakePage`-based end-to-end test (mirroring
  `test_node_spacing_change_end_triggers_a_real_relayout`) driving
  `_on_node_spacing_change_end()` through the real `_layout_worker_animated`
  -> `_apply_positions_animated` -> `_reposition_loop` path, confirming
  positions actually reach the freshly computed layout via `_wait_until()`
  rather than snapping in one step (checked by sampling an intermediate
  tick and confirming it's neither the start nor the final position).
- `set_graph()` cancels an in-flight reposition (`_reposition_active`
  false, `_reposition_targets` empty immediately after).

### Verification

Implemented per the design above, no material deviations. The shared
`_reposition_tick()`/`_reposition_loop()`/`_start_reposition()` primitive,
`_layout_worker_animated()`/`_apply_positions_animated()` (item 1),
`_neighbor_reposition_targets()` + the `_on_pan_end()` hand-off (item 2),
and `set_graph()`'s cancellation all landed exactly as designed.

One pre-existing test needed updating, not as a workaround but because
the behavior it checked was deliberately superseded: `test_simulation_
tick_eases_perturbed_nodes_back_to_home_after_release` (a Phase 22 test)
asserted that "neighbor" -- a direct graph neighbor of the dragged
"anchor" in the shared simulation fixture -- springs back to its exact
pre-drag home after release. That's now specifically what item 2 changes
for direct neighbors; the test was updated to assert the same spring-
back behavior against "bystander" instead (pulled in by repel, not a
graph edge, so untouched by the new hand-off) -- still a real regression
test for Phase 22's original behavior, just pointed at the node that
still legitimately exhibits it.

One test-writing lesson worth recording: an early draft of the node-
spacing end-to-end test tried to prove "eases in gradually" by sampling
an intermediate position via polling and asserting it differed from both
the start and the (later-read) final position -- a real race, since
`_wait_until()`'s poll interval isn't synchronized with the reposition
loop's own tick rate, and a fast machine could plausibly observe "already
converged" on the very first poll. Replaced with a non-racy version:
confirm `_reposition_active` genuinely goes through a live `True` phase
(not an instant flip straight to settled) and that positions land on a
directly-recomputed `_layout_positions()` call once it does -- proves the
same thing (a real async animation ran, landing on the correct target)
without depending on catching a specific mid-flight frame.

Tests: 14 new in `test_gui_shell.py` -- `_reposition_tick()` (moves at
the expected constant-velocity step, snaps exactly within the settle
epsilon or on an overshooting step, skips and drops a currently-dragged
node), `_start_reposition()` (merges into an in-flight reposition,
places a brand-new node directly at its target), `_neighbor_reposition_
targets()` (lands neighbors at approximately the rest length radiating
outward with no overlap, excludes the gravity well even when it's a
graph neighbor, ignores non-neighbors, stays clear of overlap at a
synthetic 60-neighbor count), `_on_pan_end()` integration (starts a
reposition for direct neighbors and hands off from the simulation's
active-node/velocity state, leaves a bystander on the old spring-back
path untouched, no-ops when Physics/Animation's simulation switch is
off), `set_graph()`'s cancellation, and the `_FakePage`-based end-to-end
test for the Node Spacing animated path. Plus the one Phase 22 test
updated as described above.

Verified: 410 tests pass (14 new), re-run three times to confirm no
flakiness in the new `_FakePage`-based tests, `uv run ruff check .`
clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines, no lingering
process after exit.

**Manual verification (you)**: drag the Node Spacing slider and release
-- confirm nodes visibly glide to their new positions rather than
snapping; drag an ordinary note and release -- confirm its direct
neighbors settle into a new arrangement around it rather than springing
back to where they started, while unrelated bystanders still spring back
as before; drag `index` itself and release -- confirm every note
backlinked to it repositions around its new location; turn off "Enable
Simulation" and confirm dragging any node now just moves it directly,
with no neighbor repositioning afterward.

## Post-25 fix #3: ease-in-out for the constant-velocity reposition system

### Context

2026-07-30 follow-up: the reposition system from Post-25 fix #2 moves at
a literal constant speed toward its target (a straight distance ->
normalized-direction -> fixed-step-per-tick rule), so motion starts and
stops abruptly. Asked whether Flet's own animation curves (`ft.Animation`/
`animate_offset`, shown with a full `ft.AnimationCurve` demo) could
supply this. Investigated directly: that system animates properties on
real `Control`s (a `Container`'s `offset`/`rotate`/`scale`) -- Flutter's
engine interpolates client-side once you set the property and call
`.update()`. Our graph nodes are `flet.canvas` shapes (`cv.Circle`), not
Containers -- there's no `animate_offset` to set on a shape, and
switching nodes to real positioned Containers to get one is the exact
"container-per-node" architecture change already looked at and declined
for the hover-label fix a few turns ago, for the same reason: it reopens
an unmeasured performance risk the whole Post-22 canvas-split investment
was built to avoid.

What carries over cleanly is the *curve*, not the delivery mechanism:
`ft.AnimationCurve` entries are just named, well-known easing formulas
(Flutter's `Curves.easeInOut` etc.) -- reimplementing an equivalent
formula inside our own tick loop gets the same visual feel without
adopting Flet's Control-level animation system. Confirmed with you:
proceed with an `easeInOut`-equivalent curve, hand-driven the same way
the rest of the reposition system already is.

### Design

All in `src/llm_wiki/gui/graph_canvas.py`, replacing `_reposition_tick()`'s
internals and extending `_start_reposition()` -- the public shape
(`_start_reposition(targets)`, `_reposition_loop()`, `_reposition_active`)
and every caller (`_apply_positions_animated()`, `_neighbor_reposition_
targets()`'s caller in `_on_pan_end()`) are unchanged.

**Curve**: a standalone smoothstep function, not a literal port of
Flutter's cubic-bezier `easeInOut` (`Cubic(0.42, 0, 0.58, 1)`, which
needs numerically solving for y given x) -- smoothstep is the standard,
simpler "ease in and out" formula (zero slope at both ends, symmetric),
visually indistinguishable from the bezier version for this purpose:

```python
def _ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)
```

**Why constant-velocity can't just be "curved" in place**: the current
tick only ever knows *remaining* distance -- there's no notion of how
far into the journey a node is, which an ease-in curve (slow *start*)
needs. Fixing this needs two more pieces of per-node state, populated
when a node's target is newly set (not on every merge -- see below):

```python
self._reposition_start_positions: dict[str, tuple[float, float]] = {}
self._reposition_progress: dict[str, float] = {}  # linear 0..1, eased right before use
```

**`_start_reposition()`** gains the reset logic -- a *changed* target
(new, or different from what was already in flight) snapshots a fresh
start position and resets progress to 0, restarting the curve for that
node specifically; merging in an *unchanged* target (the existing
no-op-preserving case) leaves an in-progress node's curve alone:

```python
def _start_reposition(self, targets: dict[str, tuple[float, float]]) -> None:
    for slug, target in targets.items():
        self._positions.setdefault(slug, target)
        if self._reposition_targets.get(slug) != target:
            self._reposition_start_positions[slug] = self._positions[slug]
            self._reposition_progress[slug] = 0.0
    self._reposition_targets.update(targets)
    if not self._reposition_active:
        self._reposition_active = True
        self._page.run_task(self._reposition_loop)
```

**`_reposition_tick()`** -- `_REPOSITION_SPEED` becomes an *average*
speed used to derive each node's own duration from its total journey
distance (`duration = total_distance / _REPOSITION_SPEED`), rather than
a literal per-tick step; progress advances linearly in time
(`_SIM_TICK_DT / duration` per tick) and only the *eased* progress
drives position, via a straight lerp from the snapshotted start to the
target:

```python
def _reposition_tick(self) -> bool:
    still_moving: dict[str, tuple[float, float]] = {}
    for slug, target in self._reposition_targets.items():
        if slug == self._dragging:
            self._reposition_start_positions.pop(slug, None)
            self._reposition_progress.pop(slug, None)
            continue  # a live drag always wins; its target is dropped, not resumed later
        start = self._reposition_start_positions.get(slug)
        if start is None:
            continue
        total_distance = math.dist(start, target)
        if total_distance <= _REPOSITION_SETTLE_DIST_EPSILON:
            self._positions[slug] = target
            self._reposition_start_positions.pop(slug, None)
            self._reposition_progress.pop(slug, None)
            continue
        progress = self._reposition_progress.get(slug, 0.0) + _SIM_TICK_DT * _REPOSITION_SPEED / total_distance
        if progress >= 1.0:
            self._positions[slug] = target
            self._reposition_start_positions.pop(slug, None)
            self._reposition_progress.pop(slug, None)
            continue
        self._reposition_progress[slug] = progress
        eased = _ease_in_out(progress)
        self._positions[slug] = (
            start[0] + (target[0] - start[0]) * eased,
            start[1] + (target[1] - start[1]) * eased,
        )
        still_moving[slug] = target
    self._reposition_targets = still_moving
    return bool(still_moving)
```

A short journey naturally degrades to an instant snap (a small
`total_distance` gives a short `duration`, so `progress` can cross 1.0
on the very first tick) -- no special-casing needed, same as today's
`step >= distance` short-circuit.

**Cancellation**: `set_graph()`'s existing `self._reposition_targets =
{}` gains two siblings, `self._reposition_start_positions = {}` and
`self._reposition_progress = {}`, so no stale per-node curve state
lingers across a full graph reload.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all of the above; no other file
  changes, no test-facing API changes (`_start_reposition()`'s signature
  and `_reposition_tick()`'s return contract are unchanged).

### Tests

`tests/test_gui_shell.py` -- the existing Post-25 fix #2 reposition tests
need updating for the new internals, not just new coverage added
alongside:

- `test_reposition_tick_moves_a_node_toward_its_target_at_constant_speed`
  is no longer accurate (motion isn't constant-speed anymore) -- replace
  with an assertion on the *shape* of motion instead: a full journey
  sampled every tick should move *less* per-tick near the very start and
  very end than through the middle (the actual ease-in-out signature),
  and land exactly on the target once complete.
- `test_reposition_tick_snaps_exactly_once_within_the_settle_epsilon`
  and `test_reposition_tick_skips_and_drops_a_node_currently_being_dragged`
  need no behavioral change, just confirming the new drag-skip path also
  clears `_reposition_start_positions`/`_reposition_progress`.
- New: `_ease_in_out()` itself -- `0.0` at `t=0`, `1.0` at `t=1`,
  monotonic, symmetric around `t=0.5` (`ease(t) == 1 - ease(1-t)`).
- New: retargeting an already-in-flight node with a *different* target
  resets its progress to 0 (a fresh curve from wherever it currently is);
  re-merging the *same* target it's already animating toward leaves its
  progress untouched (still mid-curve, not restarted).
- New: a short journey (well under one tick's worth of distance at
  `_REPOSITION_SPEED`) still completes in one tick, same as before.
- The existing `_FakePage`-based end-to-end test
  (`test_node_spacing_change_end_animates_rather_than_snaps`) needs no
  changes -- it only asserts `_reposition_active`'s True/False transitions
  and the final landed position, both unaffected by *how* it got there.

### Verification

Implemented per the design above, no material deviations. `_ease_in_out()`
(module-level smoothstep), `self._reposition_start_positions`/
`self._reposition_progress`, the rewritten `_reposition_tick()` (duration
derived from `total_distance / _REPOSITION_SPEED`, progress advanced
linearly and only the eased value driving position), and
`_start_reposition()`'s reset-on-changed-target logic all landed exactly
as designed. `set_graph()`'s cancellation gained the two new dicts
alongside the existing `_reposition_targets` reset.

The 2 pre-existing constant-velocity tests needed rewriting, not just
adjustment -- both had set `canvas._reposition_targets` directly,
bypassing `_start_reposition()` entirely, so `_reposition_start_positions`
was never populated and every tick immediately no-op'd (`start is None`
-> `continue`). Fixed by routing setup through `_start_reposition()`, the
only way to legitimately begin a tracked journey now. The old "moves at
a constant step" assertion was replaced with the design's own intended
test: a 1000px journey sampled every tick shows measurably smaller
average per-tick displacement in its first and last thirds than its
middle third -- the actual ease-in-out signature -- while still landing
exactly on the target. One test-writing bug caught while building that
test: an initial draft only appended a tick's delta when the journey was
*still* moving afterward, silently excluding the final (most-decelerated)
tick and undercounting the loop's iteration cap relative to the ~100
ticks a 1000px journey actually needs (1000px / 300px/sec average /
(1/30)s per tick) -- both fixed before trusting the assertion.

Tests: 7 new/replaced in `test_gui_shell.py` -- `_ease_in_out()`'s
endpoints/symmetry/monotonicity, the ease-signature shape-of-motion test
above, a short-journey-completes-in-one-tick degenerate case, the
drag-skip test extended to confirm start-position/progress are also
cleared (not just the target), and `_start_reposition()`'s two branches
(a changed target resets progress to 0 from a fresh snapshot; the same
target re-merged leaves an in-progress node's curve untouched). The
existing settle-epsilon and `_FakePage`-based end-to-end tests needed
only the `_start_reposition()` routing fix, no new assertions -- both
already tested behavior unaffected by *how* the position got there.

Verified: 414 tests pass (7 new/replaced), re-run three times to confirm
no flakiness, `uv run ruff check .` clean, `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean with no console output beyond the usual benign GTK/Atk
lines, no lingering process after exit.

**Manual verification (you)**: drag the Node Spacing slider and release,
and separately drag an ordinary note and release -- confirm the motion
now visibly eases in and out (starts slow, speeds up, slows again on
approach) rather than moving at one constant speed throughout. The
`_REPOSITION_SPEED` constant is a tuned *average* now, not a literal
speed -- if the overall pace feels too slow/fast, that's still the one
constant to adjust.

**Confirmed on the real build (you)**: tested commit `7e41634` and
everything works. No open issues.

## Phase 26 — Cap index's own connections when it's selected

### Context

Pulled off the deferred list (2026-07-30). Investigated the original
report ("link lines to index are hidden unless index is selected")
directly against the rendering code first: `_passes_filters()` exempts
`index` unconditionally and edges only require both endpoints to pass,
so there's no code path that hides edges before selection -- confirmed
there's no hidden-edges bug. A screenshot of the real graph settled it:
every one of the ~30+ lines really does render, converging on `index` in
a dense starburst (every note backlinks to it, by Phase 18's design),
several running off the visible canvas entirely. At that density it
reads as a solid mass rather than distinct connections, which is what
"hidden" was actually describing.

Clarified two things with you before designing:
- "Nth degree" can't mean graph-hop-distance here -- every note has a
  *direct* edge to `index` (Phase 18's Related-block guarantees it), so
  hop-distance from `index` is always exactly 1 for literally every note;
  a hop-based filter would be a no-op. Confirmed: it should be a literal
  cap on edge *count* instead.
- Scope is `index`'s own edges specifically, only while `index` itself is
  the current selection -- not a general always-on limit, and not a
  change to the existing "Degrees from Selected" filter (which hides
  whole *nodes* by hop-distance from *any* selection, an orthogonal,
  unaffected mechanism).

### Design

All in `src/llm_wiki/gui/graph_canvas.py`, added as a 6th Filters
dimension (`_build_filters_section()`), reusing the exact
`_build_filter_section_box()` shape (title + `Switch` header + content)
every other dimension already uses -- not a bespoke "all checkbox" as
first floated, since a `Switch` is what the rest of this panel already
establishes for "does this dimension's configured value apply." Default
`False` (cap has no effect, matching today's actual behavior exactly),
the same "one exception" reasoning `filter_degrees_enabled` already
uses: turning it on would immediately and silently change what renders
the next time you happen to select `index`, so it must start opt-in.
The 30+ line count in the screenshot above is exactly that scenario.

**New per-instance state**: `self._filter_index_edges_enabled = False`,
`self._filter_index_edge_limit = 10` (a slider value, 1-30).

**Ranking**: when capped, which N of `index`'s neighbors get an edge
drawn is decided by most-recently-updated first (`updated_at`, already
on every node's graph attributes since Phase 24) -- a stable, meaningful
signal already available, unlike layout position (arbitrary, from
`spring_layout`). Only ranks neighbors that already pass every *other*
active filter, so the cap's budget is never spent on a node that
wouldn't render anyway:

```python
def _compute_index_edge_limit_visible(self) -> set[str]:
    if _GRAVITY_WELL_SLUG not in self._graph:
        return set()
    neighbors = (
        set(self._graph.predecessors(_GRAVITY_WELL_SLUG))
        | set(self._graph.successors(_GRAVITY_WELL_SLUG))
    )
    neighbors = {slug for slug in neighbors if self._passes_filters(slug)}
    ranked = sorted(
        neighbors, key=lambda slug: self._graph.nodes[slug].get("updated_at") or "", reverse=True
    )
    return set(ranked[: self._filter_index_edge_limit])
```

**Gating** -- a small helper, checked only for edges actually touching
`index`; every other edge in the graph is completely untouched by this
feature regardless of the switch:

```python
def _index_edge_visible(self, neighbor: str, limit_visible: set[str] | None) -> bool:
    if self._selected != _GRAVITY_WELL_SLUG or not self._filter_index_edges_enabled:
        return True
    return limit_visible is not None and neighbor in limit_visible
```

**No caching** -- `_compute_index_edge_limit_visible()` is called once
per `_build_static_shapes()`/`_build_dynamic_shapes()` call (not once
per edge), computed fresh every redraw rather than stored and
invalidated across the many places filter/selection state can change.
Cheap enough at this project's real vault scale (a sort over tens of
neighbors) that a cache would be premature complexity -- consistent with
how this codebase has repeatedly measured before optimizing rather than
guessing (Post-22's canvas-split work is the clearest precedent). Both
shape builders gain the same guard, right where they already loop over
`self._graph.edges()`:

```python
index_edge_limit_visible = (
    self._compute_index_edge_limit_visible()
    if self._selected == _GRAVITY_WELL_SLUG and self._filter_index_edges_enabled
    else None
)
...
for u, v in self._graph.edges():
    ...
    if not (self._passes_filters(u) and self._passes_filters(v)):
        continue
    if _GRAVITY_WELL_SLUG in (u, v):
        neighbor = v if u == _GRAVITY_WELL_SLUG else u
        if not self._index_edge_visible(neighbor, index_edge_limit_visible):
            continue
    shape = self._edge_shape(u, v, edge_paint)
    ...
```

This only ever hides the *edge line* -- the neighbor's own node circle
still renders normally (with any of its *other* edges, e.g. an
entity-to-source link, unaffected), matching the literal ask ("link
lines... showed to the Nth degree," not nodes hidden).

**UI**: `_build_index_edges_content()` -- a caption (mirroring
`_degrees_caption_text()`'s exact three-state shape) + a `Slider`
(`min=1, max=30, divisions=29` -- a tunable starting range, same framing
as every other numeric constant this project has shipped and retuned
later):

```python
def _index_edges_caption_text(self) -> str:
    if self._selected != _GRAVITY_WELL_SLUG:
        return "Applies once index is selected"
    if not self._filter_index_edges_enabled:
        return "Showing all connections"
    total = self._graph.degree(_GRAVITY_WELL_SLUG) if _GRAVITY_WELL_SLUG in self._graph else 0
    return f"Showing {min(self._filter_index_edge_limit, total)} of {total} connections"
```

Added to `_build_filters_section()` as `self._build_filter_section_box(
"Index Connections", self._index_edges_switch, self._build_index_edges_content())`,
after "Degrees from Selected." The slider's `on_change` mutates only the
caption (same pattern the Degrees slider already established); the
switch's `on_change` and the slider have no rebuild path to worry about,
per Post-24 fix #3's now-established discipline.

**Selection-time refresh**: `_notify_selection()` already mutates
`_degrees_caption` directly on every selection change (Post-24 fix #3) --
gains a matching mutation for `_index_edges_caption` (its text depends on
`self._selected`, same staleness class already fixed once for Degrees).

**State bundle + persistence**, extending the *existing* Filters
machinery exactly (not a new bundle) -- this is filter #6, sharing
`_apply_filter_change()`, `_current_filter_state()`, `set_filters()`,
`_on_filters_reset()`, `_sync_filter_controls_to_state()` with the other
five:
- `GraphFilterState` gains `index_edges_enabled: bool`,
  `index_edge_limit: int` (no `filter_` prefix, matching the NamedTuple's
  existing field-naming convention).
- `GraphViewConfig` (`config.py`) gains `filter_index_edges_enabled: bool
  = False`, `filter_index_edge_limit: int = 10` (prefixed, matching
  every other persisted filter field).
- `app.py`'s two sync points (`_on_vault_changed()`'s `set_filters(...)`
  call, `_on_graph_filters_changed()`) both thread the two new fields
  through mechanically, identical to the existing 12.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all state/logic/UI above.
- `src/llm_wiki/config.py` -- `GraphViewConfig`'s two new fields.
- `src/llm_wiki/gui/app.py` -- two sync points extended.

### Tests

`tests/test_gui_shell.py`:
- `_compute_index_edge_limit_visible()`: ranks by `updated_at` descending
  over a fixture with `index` + several neighbors at different
  timestamps; a neighbor already excluded by another active filter (e.g.
  Type) never occupies a slot or counts toward the limit; returns every
  neighbor when the count is at/under the limit; empty when `index` isn't
  in the graph.
- `_index_edge_visible()`: always `True` when `self._selected` isn't
  `index`; always `True` when the switch is off; correctly gated
  (`in`/`not in` the precomputed set) when both conditions hold.
- Static/dynamic shape-builder integration: a capped-out neighbor's edge
  to `index` is excluded from shapes, but its own node circle still
  renders; an edge *not* touching `index` is completely unaffected
  regardless of the switch/limit; dragging `index` itself still respects
  the cap in `_build_dynamic_shapes()`'s cross-boundary path.
- Redraw-decoupling (Post-24 fix #3 discipline): toggling the switch or
  dragging the slider never touches `_settings_panel.content`.
- Caption text in each of its three states.
- `_notify_selection()` updates `_index_edges_caption` directly on a
  selection change, same as the existing Degrees-caption regression test.
- `_on_filters_reset()`/`set_filters()` sync the switch/slider and restore
  the default-off state, matching the other five filters' existing tests.

`tests/test_config.py`: `GraphViewConfig`'s two new fields default
correctly and round-trip through a real file.

`tests/test_gui_toolbar.py`: the existing `_on_graph_filters_changed`
wiring tests extended with the two new fields, mirroring their current
shape exactly.

### Verification

Implemented per the design above, no material deviations.
`_compute_index_edge_limit_visible()`, `_index_edge_visible()`, the
gating added to both `_build_static_shapes()` and `_build_dynamic_shapes()`
(right where they already loop over `self._graph.edges()`, only ever
skipping the edge line itself -- node circles and non-index edges are
completely untouched), `_index_edges_caption_text()`,
`_build_index_edges_content()`, and the sixth `_build_filter_section_box()`
entry ("Index Connections", after "Degrees from Selected") all landed
exactly as designed. `GraphFilterState` gained the two new fields;
`_current_filter_state()`, `set_filters()`, `_on_filters_reset()`, and
`_sync_filter_controls_to_state()` were extended mechanically, identical
to how the existing five dimensions are threaded through each. The one
place this filter's caption differs from Degrees' own precedent: its text
depends on the *enabled* state too ("Showing all connections" vs.
"Showing N of M"), not just the selection, so
`_on_filter_index_edges_enabled_toggled()` mutates the caption directly
on toggle, not just `_apply_filter_change()`'s redraw -- `_notify_selection()`
also gained a matching mutation, the same staleness class Post-24 fix #3
already fixed once for the Degrees caption. `config.py`'s
`GraphViewConfig` gained `filter_index_edges_enabled`/`filter_index_edge_limit`
(defaulting off/10); `app.py`'s two sync points
(`_on_vault_changed()`'s `set_filters(...)` call,
`_on_graph_filters_changed()`) both thread the two new fields through,
mechanically identical to the existing twelve.

Tests: 16 new in `test_gui_shell.py`'s new "Index Connections" section --
`_compute_index_edge_limit_visible()`'s ranking-by-`updated_at`,
returning everyone under the limit, excluding a neighbor that already
fails another active filter, and returning empty without an `index` node;
`_index_edge_visible()`'s three gating branches; a capped neighbor's edge
excluded from `_build_static_shapes()` while its circle still renders
(and a non-index edge is unaffected); the switch-off case showing every
edge even with `index` selected; the cap holding in
`_build_dynamic_shapes()`'s cross-boundary path while dragging `index`
itself; redraw-decoupling for both the switch and the slider (Post-24
fix #3 discipline); the caption's three text states; and the
selection-triggered caption refresh (mirroring the existing Degrees
regression test). Plus mechanical updates to the pre-existing
`test_set_filters_syncs_without_firing_the_callback` and
`test_filters_reset_restores_defaults_and_fires_the_callback` tests (new
fields threaded through the `GraphFilterState` construction and the
reset/sync assertions) and 4 in `test_config.py`/`test_gui_toolbar.py`
(defaults, round-trip, and the two `_on_graph_filters_changed` wiring
tests extended) mirroring the existing patterns for all five prior
filter dimensions exactly.

Verified: 430 tests pass (20 new/updated), re-run three times to confirm
no flakiness, `uv run ruff check .` clean, `uv run flet build linux
--python-version 3.13 --skip-flutter-doctor` succeeds, rebuilt bundle
launches clean with no console output beyond the usual benign GTK/Atk/
OpenGL-timeout lines, no lingering process after exit.

**Manual verification (you)**: select `index` with the switch off and
confirm the full starburst still renders exactly as in your screenshot;
turn the switch on, set the slider to a small number, and confirm only
that many lines into `index` remain (picking, per the design above, its
most-recently-updated neighbors) while every neighbor's node circle
still shows; confirm any of those neighbors' *other* edges (e.g. to a
source note) are unaffected; select a different node (or nothing) and
confirm the full starburst comes back regardless of the switch's
position; close and reopen the vault and confirm the setting persisted.

## Phase 27 — Settings panel: each section as its own popup

### Context

Deferred item, picked up 2026-07-31: "the panel requires significant
scrolling to reach the bottom now that it holds Categories/Filters/
Physics/Zoom & Pan/Layout." Proposed there and confirmed now: every
related group of controls -- Filters as a whole, and individually
Physics/Animation, Zoom & Pan, and Layout -- becomes its own
`PopupMenuButton`-triggered popup, following the exact non-auto-closing
pattern the Tags filter and Colors picker already established and
verified (Post-24 fix #3, Phase 25): a click handler on a plain
`Container` nested inside one `PopupMenuItem` doesn't trigger Flutter's
built-in "close menu on selection," so several controls can be adjusted
before dismissing.

Categories was explicitly asked about and confirmed to stay **inline,
ungrouped** -- its 5 rows are already individually compact popups
(one color-picker per note type + index), and grouping them behind one
more outer popup would nest 5 popups inside 1 for no real gain, doubling
an already-present risk rather than eliminating it. That risk can't be
fully avoided regardless: Filters, once it becomes a popup, necessarily
nests its own existing Tags popup one level inside -- Tags already *is*
a `PopupMenuButton`, and there's no way to make "Filters as a whole" a
popup without that. This is the one unavoidable nested-popup instance in
this phase, and -- like the Tags auto-close question back in Post-24 fix
#3 -- it's not something verifiable without a live build; flagged
explicitly for your manual test, not assumed to work.

### Design

All in `src/llm_wiki/gui/graph_canvas.py`.

**New shared helper**, mirroring `_build_filter_section_box()`/
`_build_settings_section_box()`'s existing "shared box builder" pattern,
but producing a genuine popup trigger instead of an always-visible box:

```python
def _build_popup_section(
    self, title: str, content: ft.Control, *, width: float = 236, height: float | None = None
) -> ft.Control:
    """A card-styled PopupMenuButton trigger opening `content` in a
    popup -- the same non-auto-closing PopupMenuItem-wrapping-a-plain-
    Container pattern the Tags filter and Colors picker already
    established (Post-24 fix #3, Phase 25).
    """
    trigger = ft.Container(
        padding=ft.Padding(8, 6, 8, 6),
        bgcolor=theme.CARD_BG,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=6,
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text(title, size=10.5, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ft.Text("▾", size=10, color=theme.TEXT_TOGGLE_OFF),
            ],
        ),
    )
    return ft.PopupMenuButton(
        content=trigger,
        menu_padding=ft.Padding(8, 8, 8, 8),
        items=[ft.PopupMenuItem(content=ft.Container(width=width, height=height, content=content))],
    )
```

**Filters**: `_build_filters_section()`'s own returned `ft.Column` gains
`scroll=ft.ScrollMode.AUTO` (it's now popup content with a capped
height, no longer living inside the already-scrollable `_panel_body`).
Wired as `self._build_popup_section("Filters", self._build_filters_section(),
height=420)` -- the one section tall enough to need an internal scroll
cap; everything it contains (master switch, the 6 filter-dimension boxes
via the existing `_build_filter_section_box()`, Reset button) is
completely unchanged internally, including Tags' own nested popup.

**Physics/Animation, Zoom & Pan, Layout**: each becomes its own direct
`_build_popup_section(...)` call using their existing, unchanged content
builders (`_build_physics_content()`, `_build_zoom_pan_content()`,
`_build_layout_content()`) -- no internal popups, so no nesting risk for
these three. `_build_display_settings_section()` and `_build_settings_
section_box()` are both removed entirely: no longer needed, since each
section is now directly one `_build_popup_section()` call, and the
popup trigger itself already provides the "labeled, bordered group"
framing `_build_settings_section_box()` used to (no reason to frame it
twice -- once on the trigger, once again inside the popup).

**Categories**: `_build_legend_section()` is completely untouched,
stays directly inline in the panel body exactly as it renders today,
under its existing `_section_label("CATEGORIES")` header (kept, still
the one remaining always-visible section-label use).

**`_build_settings_panel()`**'s `_panel_body` Column is rewritten from
today's label/section/divider repetition to:

```python
controls=[
    self._section_label("CATEGORIES"),
    self._build_legend_section(),
    ft.Container(height=1, bgcolor=theme.BORDER),
    self._build_popup_section("Filters", self._build_filters_section(), height=420),
    self._build_popup_section("Physics / Animation", self._build_physics_content()),
    self._build_popup_section("Zoom & Pan", self._build_zoom_pan_content()),
    self._build_popup_section("Layout", self._build_layout_content()),
]
```

`_panel_body`'s own fixed `height=480` is dropped (the whole point of
this phase) -- Categories' 5 rows plus 4 compact trigger rows is short
enough to size naturally; `scroll=ft.ScrollMode.AUTO` stays on the outer
Column as a cheap safety net for future growth, without forcing a
reserved scrollbar area today.

**No persistence changes**: popup open/closed state is inherently
transient UI, same as Tags/Colors today -- no new `GraphViewConfig`
fields, no new `app.py` wiring. The existing `settings_panel_expanded`
field (governing the outer chevron toggle only) is untouched.

### Files

- `src/llm_wiki/gui/graph_canvas.py` -- all of the above; no other file
  changes needed.

### Tests

`tests/test_gui_shell.py`: Post-24 fix #3's "build once, mutate only"
discipline is fully preserved -- every control is still built exactly
once in `__init__` and referenced via `self.*`, so every existing test
that mutates a control directly and checks its `.value`/`.scale`/etc.
(the large majority of the Filters/Display test suite) needs **no
changes at all**. Tests that assert `canvas._settings_panel.content is
panel_content_before` (the redraw-decoupling regression tests) also need
no changes -- that identity guarantee is unaffected by what's inside the
panel. New coverage: `_build_popup_section()`'s own structural shape
(title + chevron trigger, `PopupMenuItem` wrapping the given content at
the given width/height); a test confirming Filters/Physics/Zoom & Pan/
Layout are each now reachable only via a `PopupMenuButton` (not directly
inline in `_panel_body`'s controls list), while Categories' rows remain
directly inline; the `_build_filters_section()` scroll-mode addition.

### Verification

Implemented per the design above, no material deviations. `_build_popup_
section()` landed as designed; `_panel_body`'s Column now holds
Categories' label+legend inline, a divider, then the four popup triggers
(Filters at `height=420`, Physics/Animation, Zoom & Pan, Layout, each at
the helper's default size) -- `_panel_body`'s own fixed `height=480` is
gone, and it now sizes to its much shorter natural content.
`_build_display_settings_section()` and `_build_settings_section_box()`
were both deleted outright (confirmed via grep: no remaining references
anywhere in `src/` or `tests/`) -- each display section is now directly
one `_build_popup_section()` call, and the popup trigger itself already
supplies the "labeled, bordered group" framing the deleted helper used
to. `_build_filters_section()`'s returned Column gained `scroll=ft.
ScrollMode.AUTO`. No persistence changes were needed, as designed --
popup open/closed state stays purely transient UI, matching Tags/Colors.

Confirmed the "build once, mutate only" discipline (Post-24 fix #3) is
fully intact: all 174 pre-existing tests in `test_gui_shell.py` passed
**unchanged**, with zero edits needed -- every one of them either
mutates a stored control reference directly or asserts
`_settings_panel.content` identity, neither of which the popup
restructure touches.

Tests: 5 new in `test_gui_shell.py`, added right after the Settings-panel-
shell (Phase 23) block -- `_build_popup_section()`'s own structural shape
(title + chevron trigger, a single `PopupMenuItem` wrapping the given
content at the given width/height); Filters/Physics-Animation/Zoom-&-Pan/
Layout are each reachable only via a `PopupMenuButton` in `_panel_body`'s
controls list, while Categories' label+legend rows are not; the Filters
popup's item container is capped at `height=420` and wraps the real
Filters content (checked via its "Enable Filters" row); `_build_filters_
section()`'s Column carries `scroll=ft.ScrollMode.AUTO`; `_panel_body.
height is None`.

Verified: 453 tests pass (5 new), re-run three times to confirm no
flakiness (the one pre-existing, unrelated `test_terminal_panel.py`
teardown-race warning noted in one of the three runs, consistent with
every prior session -- untouched by this phase), `uv run ruff check .`
clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines, no lingering
process after exit.

Standard rhythm (ruff, pytest re-run 2-3x, `flet build linux
--python-version 3.13 --skip-flutter-doctor`, clean launch check, no
lingering process) plus manual verification that matters more here than
most prior phases, since the one thing this plan cannot verify headlessly
is the nested Filters-popup-containing-Tags-popup interaction.

**Manual verification (you)**: open the Filters popup, then open Tags
from within it -- confirm it renders correctly (not clipped, not behind
the outer popup, not visually broken); click a tag chip and confirm
neither popup auto-closes (matching Tags' existing non-nested behavior);
click outside both and confirm they both dismiss correctly; confirm
Filters' own internal scroll (now capped at height=420) scrolls
smoothly to reach Index Connections at the bottom. Separately, confirm
Physics/Animation, Zoom & Pan, and Layout each open/close cleanly as
standalone (non-nested) popups, and that every control inside still
functions exactly as before (sliders, switches, color pickers). Confirm
Categories looks and behaves completely unchanged. Confirm the overall
panel is now visibly shorter when expanded, with no more scrolling
needed to reach the bottom.

**Follow-up (you)**: asked for 8px top/bottom padding on the container
that directly holds each popup's controls -- specifically not the
trigger (the popup's always-visible root, shown collapsed in the panel),
but the container inside `PopupMenuItem` that the passed-in `content`
(e.g. the Filters Column, the Physics Column, ...) sits directly inside.
Since that container already exists (`_build_popup_section()`'s
`ft.Container(width=width, height=height, content=content)`) and
`Container` supports `padding` natively (confirmed via `get_api` before
guessing), this was a one-line addition -- no restructuring needed:
`padding=ft.Padding(0, 8, 0, 8)` (left/right unchanged, matching the
literal ask). Applies uniformly to all four popups (Filters, Physics/
Animation, Zoom & Pan, Layout) since they all route through the same
helper.

Tests: `test_build_popup_section_wraps_content_in_a_non_auto_closing_
popup` extended with an assertion on the item container's `padding`.

Verified: 454 tests pass (no net-new, one extended), `uv run ruff
check .` clean, `uv run flet build linux --python-version 3.13
--skip-flutter-doctor` succeeds, rebuilt bundle launches clean with no
console output beyond the usual benign GTK/Atk lines, no lingering
process after exit.

## Deferred / open items

Not yet scoped for implementation -- logged here so they aren't lost
between sessions. Pick one when ready and it gets the same full
plan-before-code treatment every phase above got.

1. **Custom groups** (deferred from Phase 24's Filters and Phase 25's
   Settings) -- nothing defines what a "custom group" actually is yet
   (a user-defined tag-alias? manual per-note assignment? something
   else?), so it needs its own design pass before it's buildable. See
   Phase 25's context section for the fuller discussion.

2. **Panning deselects the currently-selected node** (raised 2026-07-30)
   -- click-drag-release on empty canvas to pan the view clears node
   selection, even though the drag never touched another node or empty
   space at release. Desired: selection is sticky -- it should only clear
   when you click a *different* node or genuinely click empty space (a
   plain click: mouse-down + mouse-up with no drag in between), not when
   a pan gesture happens to start over empty background. Root cause,
   already known from Phase 17's own design notes: `_on_pan_start()`'s
   "no hit" branch (`gui/graph_canvas.py`) clears `self._selected`
   unconditionally the instant a pointer-down lands on empty space --
   before it's known whether that pointer-down will turn into a plain
   click or a pan drag, since panning is deliberately built on the same
   `on_pan_start`/`on_pan_update`/`on_pan_end` trio as node-dragging
   (Phase 17 chose this over a competing `on_tap` handler specifically to
   avoid Flutter gesture-arena conflicts). Needs research before
   planning: whether Flet's `GestureDetector` can distinguish "pan-start
   that stays put and releases" from "pan-start that moves" cleanly
   enough to defer the deselect decision to pan-end (e.g. only clearing
   selection if total drag distance stayed near zero) without
   reintroducing the gesture-arena conflict Phase 17 avoided. Your own
   fallback if that's not feasible: move whole-canvas panning to the
   right mouse button instead, freeing the left button to mean "select/
   deselect" unambiguously.

## Verification approach

Same pattern as every prior phase: headlessly-testable logic (engine +
Python-side service/model code) gets real automated `pytest` coverage at
every sub-phase boundary; anything needing a live display or a live
llama-server is called out per sub-phase for you to check manually.
`uv run ruff check .` stays green throughout. We proceed one sub-phase at
a time — 16b doesn't start until you've confirmed 16a's DoD on your
machine.
