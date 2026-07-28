# LLM-Wiki — Implementation Plan
 
Backend-first, one phase at a time. Each phase has a single deliverable,
concrete files, and a definition of done you can verify yourself before
moving on. Phases 0–11 have **zero PySide6 dependency** — everything is
testable via `pytest` and/or the CLI as it comes online in Phase 12.
 
Do not start a phase until the previous one's "Definition of Done" is
actually true, not just "mostly written."
 
---
 
### Phase 0 — Environment & Repo Scaffold
**Goal:** A clean, reproducible project skeleton.
**Deliverables:**
- `uv init`, `pyproject.toml` with project metadata, Python 3.14 pin
- `src/llm_wiki/` package layout (empty `__init__.py`s per module from the
  architecture doc)
- `ruff` + `pre-commit` config
- `pytest` configured, one placeholder test passing
- `.gitignore` (must exclude `.llm-wiki/`, `raw/.sources/` originals if you
  decide those shouldn't be double-tracked, `__pycache__`, etc.)
**Definition of done:** `uv run pytest` passes with one trivial test;
`ruff check .` is clean; repo pushed to Git with an initial commit.
---
 
### Phase 1 — Domain Models & Config
**Goal:** Every shared data shape and the settings system exist before
anything uses them.
**Deliverables:**
- `models.py`: `QueueItem`, `QueueStatus` (enum), `NoteFrontmatter`,
  `NoteType` (enum), `Chunk`, `LinkEdge`, `LintFinding`, and the typed
  exception hierarchy (`LLMWikiError` base, `VaultNotFoundError`,
  `IngestionError`, `CompilationError`, `LintError`)
- `config.py`: `pydantic-settings` classes mirroring the old
  `llm_provider` / `mcp_server` / `vault` config sections, loaded from
  `.llm-wiki-config` with env-var override support
**Definition of done:** Unit tests instantiate and (de)serialize every
model to/from JSON; a config loads correctly from a fixture file and from
env vars overriding it.
---
 
### Phase 2 — Storage Layer
**Goal:** SQLite + `sqlite-vec` cache layer, fully rebuildable.
**Deliverables:**
- `storage/schema.sql` — DDL for `queue`, `notes`, `chunks`, `vec_chunks`,
  `links`, `lint_findings`
- `storage/db.py` — connection management, extension loading, a
  `rebuild_from_vault()` function that wipes and reconstructs the DB purely
  from `wiki/` + `raw/` contents
- Migration approach decided (simple versioned schema table is enough at
  this scale — no need for Alembic)
**Definition of done:** A test creates a fixture vault with a couple of
Markdown notes, runs `rebuild_from_vault()`, and asserts the `notes` table
matches. Deleting `.llm-wiki/db.sqlite3` and rebuilding is a one-line CLI
command by the end of Phase 12, but the underlying function must work now.
---
 
### Phase 3 — Vault Manager
**Goal:** Create/load/validate vault directory trees — the direct,
de-Qt'd port of the old `vault_manager.py`.
**Deliverables:**
- `vault/manager.py`: `create_vault()`, `load_vault()`,
  `get_recent_vaults()` — same directory scaffolding as before
  (`raw/`, `wiki/{sources,entities,concepts,synthesis}/`, `.system/`),
  plus `.llm-wiki/` for the DB cache
- No Qt signals — returns a `VaultInfo` model or raises
  `VaultNotFoundError`
**Definition of done:** Tests cover: creating a new vault produces the
correct tree; loading a non-vault directory raises the typed exception;
recent-vaults list persists and dedupes correctly.
---
 
### Phase 4 — Git Engine
**Goal:** `pygit2`-based version control, in-process.
**Deliverables:**
- `vcs/git_engine.py`: `init()`, `stage_all()`, `commit()`,
  `set_remote()`, `push()`, `pull()`, `status()` returning a structured
  `GitStatus` model (branch, modified, untracked, clean)
**Definition of done:** Tests run against a real temp Git repo (via
`pygit2`, no shelling out) covering init → stage → commit → status-clean,
and a modified-file → status-dirty case.
---
 
### Phase 5 — Ingestion Engine
**Goal:** Stage raw files and manage the queue as DB rows.
**Deliverables:**
- `ingest/ingest_engine.py`: `enqueue_file()` (archive + stage + insert
  `queue` row), `update_status()`, `list_queue()`
**Definition of done:** Enqueueing a fixture file produces the correct
`raw/` + `raw/.sources/` layout and a `QUEUED` row; status transitions are
correctly persisted and queryable.
---
 
### Phase 6 — Atomizer
**Goal:** Header-boundary GEO chunking, ported and hardened.
**Deliverables:**
- `ingest/atomizer.py`: asset shielding (code/tables), 200–400 word
  bounding, GEO frontmatter generation — same logic as before, now with a
  focused test suite (this module had zero tests previously)
**Definition of done:** Tests cover: a document with code fences and
tables round-trips unshielded correctly; an oversized section gets split;
undersized sections get merged; word counting excludes Markdown syntax
correctly.
---
 
### Phase 7 — LLM Client Layer
**Goal:** Talk to `llama-server`, with structured, guaranteed-valid
extraction.
**Deliverables:**
- `llm/client.py`: thin wrapper over the `openai` SDK pointed at
  `llama-server`
- `llm/extraction.py`: `outlines`-based structured generation using the
  `NoteFrontmatter`/entity Pydantic models from Phase 1 as the schema
- `llm/embeddings.py`: batch embedding generation against
  `nomic-embed-text`
**Definition of done:** Tests run against a mocked client by default
(`@pytest.fixture` fake completions); a `@pytest.mark.live_llm` test
exists for manual verification against your real llama-server instance.
---
 
### Phase 8 — Embeddings + Vector Search
**Goal:** Wire `llm/embeddings.py` output into `storage`'s `vec_chunks`
table and expose similarity search.
**Deliverables:**
- `storage/vector_search.py`: `upsert_chunk_embedding()`,
  `similarity_search(query, top_k)`
**Definition of done:** Test embeds a handful of fixture chunks (mocked
embedding vectors are fine here), runs a similarity query, and asserts
sane nearest-neighbor ordering.
---
 
### Phase 9 — Compiler Engine
**Goal:** The real orchestration — summarize, extract, cascade-update,
embed. This is the most complex phase; do not rush it.
**Deliverables:**
- `compiler/compiler_engine.py`: `compile_queued_item()` running the full
  pipeline described in Architecture §8
- **Real cascade-update logic** (the gap flagged in the original review):
  fetch existing entity/concept note if present, ask the LLM to *merge*
  new information without duplication, write back — not just stub creation
- Concept/synthesis note generation (previously entirely missing)
**Definition of done:** An end-to-end test (mocked LLM) takes a fixture
raw document through the full pipeline and asserts: a source summary
exists, entity notes exist (both newly-created and merge-updated cases
are tested separately), embeddings were generated for new chunks.
---
 
### Phase 10 — Link Engine
**Goal:** Incremental graph maintenance.
**Deliverables:**
- `graph/link_engine.py`: `sync_links()` (hash-diff incremental),
  `rebuild_full()`, `get_graph_data()` for eventual UI consumption,
  degree-of-separation reporting
**Definition of done:** Test asserts that re-running `sync_links()` with
no file changes touches zero rows; changing one note's content only
re-processes that note's outgoing edges.
---
 
### Phase 11 — Lint Engine
**Goal:** Health scoring against the DB cache.
**Deliverables:**
- `lint/lint_engine.py`: schema validation, broken-link detection,
  isolated-note detection, health score computation
**Definition of done:** Tests cover a clean vault (100 score), and fixture
vaults with each violation type individually to confirm correct scoring
deductions.
---
 
### Phase 12 — CLI Front-End
**Goal:** The first real interface. Proves the backend is genuinely
usable headlessly before any GUI work starts.
**Deliverables:**
- `cli/main.py` (`typer`): `vault create/open`, `ingest <file>`,
  `link sync/rebuild`, `lint run`, `storage rebuild`, `git status/commit/push`
**Definition of done:** You can, from a terminal, create a vault, ingest a
real document against your real llama-server, watch it produce summary +
entity notes, run link/lint, and commit — with zero PySide6 imported
anywhere in the process.
---
 
### Phase 13 — MCP Server
**Goal:** Expose the engine to external MCP clients (Claude Desktop,
Cursor, etc.) — the piece that was a dead stub in the original build.
**Deliverables:**
- `mcp/server.py`: real `FastMCP` app with tools for semantic search
  (Phase 8), entity lookup, path traversal (Phase 10), synthesis reads —
  with vault-root path sandboxing on every file-touching tool
**Definition of done:** Connecting an external MCP client (or the FastMCP
dev inspector) and calling each tool returns correct data from a fixture
vault; a path-traversal attempt (`../../etc/passwd`-style input) is
rejected.
---
 
### Phase 14 — Backend Hardening
**Goal:** Everything above, but robust.
**Deliverables:**
- Full test coverage review — every module from Phases 1–13 has tests
- `README.md` documenting CLI usage
- Config validated against a truly fresh install (no existing
  `.llm-wiki-config`) to confirm defaults are sane
**Definition of done:** `uv run pytest` green end-to-end; you can `uv run
llm-wiki vault create` on a machine with nothing but `raw`
llama-server running, and go from zero to a linted, committed vault
using only the CLI.
---
 
### Phase 15 — PySide6 Desktop UI (future, not started until 0–14 are solid)
**Goal:** Thin Qt shell over the now-stable engine.
**High-level deliverables (detailed plan written when we get here):**
- `Ui_MainWindow`/`Ui_SettingsDialog` from Qt Designer, ported from the
  original `.ui` files where still applicable
- One `QThread`-based adapter translating engine progress callbacks into
  Qt signals (Architecture §9) — the *only* place threading is Qt-flavored
- Graph canvas: `QGraphicsView` + `networkx` layout algorithms
  (Architecture §4 — this part was validated as correct in the original
  design, just needs the physics bug fixed and hand-rolled simulation
  replaced with a real layout algorithm)
- Health dashboard charts via `pyqtgraph`
- `pytest-qt` coverage for dock wiring, signal/slot chains, and the Git
  status display (specifically re-testing the class of bug found in the
  original `GitManager` stdout-draining issue — `pygit2` avoids the root
  cause, but the UI layer should still be tested)
---
 
## Working Agreement
 
We go phase by phase. I won't start writing Phase *N+1* code until you've
confirmed Phase *N*'s definition of done is actually met on your machine —
not just that I produced the files. Each phase should end with you running
the tests yourself and telling me the result before we continue.
 
