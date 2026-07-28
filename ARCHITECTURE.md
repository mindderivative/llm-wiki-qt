# LLM-Wiki — Architecture
 
## 1. Vision
 
A local-first, self-maintaining knowledge base manager. The user drops raw
source documents in; the system atomizes, summarizes, links, and lints them
into a GEO-optimized Markdown wiki, powered entirely by a local `llama.cpp`
cluster. The Markdown vault is the permanent artifact — everything else
(database, indexes, UI) is a replaceable lens on top of it.
 
## 2. Design Principles (locked decisions)
 
These came out of explicit tradeoff discussions and should not be silently
relitigated mid-build:
 
1. **Markdown + Git is the source of truth.** Every derived store (SQLite,
   vector index) must be fully rebuildable from the `wiki/` directory tree.
   Losing the database must never lose information — only rebuild time.
2. **Native only.** PySide6 desktop UI, no browser engine, no web stack,
   anywhere in the application. Qt's own `QGraphicsView` + NetworkX pattern
   is sufficient for the graph canvas; no `QWebEngineView` escape hatch.
3. **Local-first inference only.** All LLM calls go to the user's own
   `llama-server` cluster over its OpenAI-compatible endpoint. No cloud
   provider SDKs enter the dependency tree.
4. **Small footprint over framework convenience.** Prefer thin, single
   purpose libraries (`sqlite-vec`, `pygit2`, `outlines`) over heavier
   all-in-one frameworks (Pixeltable, LangChain) when the project's actual
   scale (a personal/small-team wiki, not a billion-vector production
   system) doesn't need the heavier tool's guarantees.
5. **Backend before frontend.** The `llm_wiki` engine package has zero Qt
   dependency and is fully usable/testable from a CLI before any GUI code
   exists. The GUI is the *last* thing built, and it is a thin consumer of
   a stable backend API — not the other way around, which is what caused
   rework in the previous attempt.
6. **Everything the backend does is scriptable and testable headlessly.**
   If a feature can't be exercised by a `pytest` test or a CLI command
   without opening a window, it isn't done.
## 3. Layered Architecture
 
```
┌─────────────────────────────────────────────────────────────────┐
│                          INTERFACES                              │
│  ┌───────────┐   ┌───────────┐   ┌─────────────────────────┐    │
│  │  CLI       │   │  MCP       │   │  PySide6 GUI (Phase 15) │    │
│  │ (typer)    │   │ (FastMCP)  │   │                          │    │
│  └─────┬──────┘   └─────┬──────┘   └────────────┬─────────────┘    │
└────────┼────────────────┼────────────────────────┼────────────────┘
         │                │                        │
         └────────────────┴────────────┬───────────┘
                                        │  all interfaces call the
                                        │  same engine API — no
                                        │  interface-specific logic
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                      llm_wiki ENGINE (pure Python)                │
│                                                                     │
│  vault/       VaultManager — create/load/validate vault trees      │
│  ingest/      IngestEngine, Atomizer — queue + chunk raw files     │
│  llm/         LlamaClient, structured extraction (outlines)        │
│               EmbeddingService                                     │
│  compiler/    CompilerEngine — summarize, extract, cascade-update  │
│  graph/       LinkEngine — NetworkX graph over the vault           │
│  lint/        LintEngine — schema + link + contradiction checks    │
│  vcs/         GitEngine (pygit2) — init/stage/commit/push/pull     │
│  storage/     SQLite + sqlite-vec cache layer (rebuildable)        │
│  config.py    pydantic-settings typed configuration                │
│  models.py    Pydantic domain models shared by every layer         │
└─────────────────────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                         PERSISTENCE                                │
│  wiki/              canonical Markdown notes  (Git-tracked)        │
│  raw/               immutable source archive  (Git-tracked)        │
│  .llm-wiki/db.sqlite3   derived cache: queue, chunk index,         │
│                         embeddings (sqlite-vec), link cache         │
│                         — safe to delete, rebuilds from wiki/       │
└─────────────────────────────────────────────────────────────────┘
```
 
## 4. Technology Stack
 
| Concern | Tool | Rationale |
|---|---|---|
| Language / packaging | Python 3.14, `uv` | Fast resolver, lockfile reproducibility |
| Domain models / validation | `pydantic` v2 | Single source of truth for shapes shared across CLI/MCP/GUI |
| Config | `pydantic-settings` | Typed, validated, env-var aware; replaces hand-rolled equality-check JSON manager |
| Local relational + vector store | `sqlite3` (stdlib) + `sqlite-vec` extension | Zero-server, single-file, minimal footprint; brute-force KNN is fine at wiki scale |
| LLM transport | `openai` Python SDK against `llama-server`'s OpenAI-compatible endpoint | Already the right tool; no change needed |
| Structured extraction | `outlines` | Grammar-constrained decoding against a model you control — guaranteed-valid JSON, no retry loop |
| Markdown parsing | `markdown-it-py` | Already correct in the original build |
| Frontmatter | `python-frontmatter` | Already correct |
| Graph structure | `networkx` | Already correct |
| Graph layout | `networkx.spring_layout` / `kamada_kawai_layout` | Replaces the hand-rolled (and buggy) force simulation |
| Version control | `pygit2` (libgit2 bindings) | In-process, thread-safe, no subprocess spawn per Git op |
| File watching | `watchdog` | Powers the `auto_watch_raw` config option (previously dead code) |
| MCP server | `fastmcp` | Already the right tool; previously unimplemented |
| CLI | `typer` | Thin, typed CLI as the first real interface onto the engine |
| Logging | `loguru` | Already correct; Qt signal bridge is added only in Phase 15 |
| Desktop UI (Phase 15 only) | `PySide6`, `qt-material`, `pyqtgraph` (dashboard charts only) | Native only, per Principle 2 |
| Testing | `pytest`, `pytest-qt` (Phase 15 only), `hypothesis` (optional, for chunker edge cases) | Backend must be 100% testable without Qt |
 
## 5. Vault Filesystem Layout (unchanged, canonical)
 
```
<vault_root>/
├── .llm-wiki-config              # vault identity JSON
├── .llm-wiki/
│   └── db.sqlite3                # derived cache — rebuildable, gitignored
├── raw/
│   ├── .sources/                 # untouched original uploads
│   └── {date}_{slug}.ext         # staged working copies
├── wiki/
│   ├── index.md
│   ├── log.md
│   ├── sources/                  # per-source summaries
│   ├── entities/                 # profiles of core subjects
│   ├── concepts/                 # foundational principles
│   ├── synthesis/                # cross-cutting insights
│   └── .system/
│       └── prompts/              # versioned prompt templates (new — see §8)
└── SCHEMA.md                     # human-readable operational rules
```
 
`raw/` and `wiki/` are Git-tracked. `.llm-wiki/` is gitignored — it is a
cache, not state.
 
## 6. Internal Storage Schema (SQLite + sqlite-vec)
 
All tables are derived from `wiki/` and `raw/` and can be dropped and
rebuilt at any time via `llm_wiki storage rebuild`.
 
- **`queue`** — ingestion queue items (`id`, `title`, `raw_path`,
  `archive_path`, `status`, `error`, `created_at`, `updated_at`). Replaces
  the old `queue.json` — gives us transactional updates instead of full-file
  rewrites on every status change.
- **`notes`** — one row per Markdown note (`path`, `slug`, `type`, `title`,
  `tags`, `sources`, `content_hash`, `updated_at`). `content_hash` is the
  key to incremental link/lint passes — a note is only re-parsed if its
  hash changed since the last run.
- **`chunks`** — atomic GEO chunks (`note_id` or `queue_item_id`, `ordinal`,
  `title`, `content`, `word_count`).
- **`vec_chunks`** — `sqlite-vec` virtual table, one embedding per row in
  `chunks`, joined by rowid.
- **`links`** — parsed `[[wikilink]]` edges (`source_slug`, `target_slug`),
  rebuilt only for notes whose `content_hash` changed.
- **`lint_findings`** — latest lint run's broken links, schema violations,
  isolated notes, with a `run_id` and timestamp for history.
## 7. Component Responsibilities
 
| Package | Responsibility | Notably fixes vs. previous build |
|---|---|---|
| `vault` | Create/validate/load vault directory trees; recent-vaults list | No behavior change, just decoupled from Qt |
| `storage` | Own the SQLite connection, schema migrations, `sqlite-vec` loading | New — didn't exist before |
| `ingest` | Stage raw files, archive originals, enqueue; atomize into GEO chunks | Queue is transactional DB rows, not a JSON file rewritten wholesale each time |
| `llm` | Wrap `llama-server`'s OpenAI-compatible endpoint; structured extraction via `outlines`; embedding generation | Replaces regex-JSON-scraping entity extraction with grammar-constrained, guaranteed-valid output |
| `compiler` | Orchestrate: summarize → extract entities/concepts → cascade-update existing notes → generate embeddings | Cascade updates now actually *merge* into existing notes instead of only creating stubs for missing ones |
| `graph` | Build/maintain the NetworkX link graph incrementally from `notes`/`links` tables | Incremental via `content_hash`, not a full vault re-walk every run |
| `lint` | Schema validation, broken-link detection, health scoring | Same logic, now reads from the DB cache instead of re-parsing every file |
| `vcs` | Init/stage/commit/push/pull/status via `pygit2` | Fixes the stdout-draining bug from the `QProcess` implementation by construction — no subprocess streams to manage |
| `config` | Typed settings, loaded from `.llm-wiki-config` + environment | Fixes the manual equality-check save-loop guard with `pydantic-settings`' native change detection |
 
## 8. Data Flow: The Three Pipelines
 
**`/wiki-ingest`**
`enqueue_file()` → stage in `raw/`, insert `queue` row (`QUEUED`) →
`atomize()` → chunk into `chunks` table (`PARSING`) → `compile()` → LLM
summary + `outlines`-extracted entities (`ANALYZING`) → cascade-update
target notes in `wiki/` + regenerate their embeddings (`CASCADE`) → mark
`COMPLETED`. Every status transition is a single DB row update, not a file
rewrite.
 
**`/wiki-link`**
For every note whose `content_hash` differs from its last-seen value:
re-extract `[[wikilink]]`s, diff against the `links` table, apply the
delta. Full graph is only ever fully rebuilt on explicit request
(`llm_wiki graph rebuild --full`).
 
**`/wiki-lint`**
Reads `notes` + `links` from the DB (already current thanks to the link
pipeline's incremental hashing), validates frontmatter against the Pydantic
schema, computes health score. No filesystem walk needed unless the DB is
being rebuilt from scratch.
 
## 9. Concurrency Model
 
The engine package uses **no Qt classes at all** — no `QThread`, no
`QObject`, no signals. Long-running work (LLM calls, embedding batches) is
exposed as plain Python generators/callables using `concurrent.futures` or
`asyncio` where actual parallelism helps (e.g. embedding a batch of chunks
concurrently).
 
Each interface layer adapts this to its own concurrency idiom:
- **CLI**: runs synchronously, prints progress to stdout.
- **MCP**: `FastMCP` tools call the engine directly; async where the engine
  exposes async methods.
- **GUI (Phase 15)**: a thin `QThread` wrapper subscribes to the engine's
  plain-Python progress callbacks and re-emits them as Qt signals. This
  adapter is the *only* place Qt threading concepts exist in the whole
  codebase.
## 10. Error Handling & Logging
 
- `loguru` remains the logging backbone. In Phase 0–14 it logs to stderr
  and a rotating file inside `.llm-wiki/`; the Qt `QtLogSink` bridge from
  the original build is reintroduced only in Phase 15.
- Engine functions raise typed exceptions (`VaultNotFoundError`,
  `IngestionError`, `CompilationError`, etc., defined in `models.py`) rather
  than returning `None`/`False` on failure, so every interface layer can
  handle failures explicitly instead of guessing from a falsy return.
## 11. Testing Strategy
 
- Every `llm_wiki` package gets a corresponding `tests/test_*.py` using
  `pytest`, with a fixture vault built in a `tmp_path` for each test —
  no shared mutable test state.
- LLM-dependent tests (`compiler`, `llm`) run against a **mocked** llama
  client by default (so the suite runs offline/in CI); a small marked
  subset (`@pytest.mark.live_llm`) hits the real local `llama-server` for
  manual verification.
- `pytest-qt` is added only in Phase 15, scoped to the thin Qt adapter
  layer — the engine's own tests never import PySide6.
## 12. Out of Scope (for now)
 
- Cloud LLM providers.
- Multi-user/concurrent-writer vault access.
- Mobile/web clients (MCP is the integration surface for other tools like
  Claude Desktop/Cursor instead).
- Real-time collaborative editing.
These aren't rejected forever — just explicitly deferred so they don't
creep into early phases.
 
