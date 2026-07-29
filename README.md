# LLM-Wiki

A local-first, self-maintaining knowledge base engine. Drop raw source
documents in; the engine atomizes, summarizes, links, and lints them into a
GEO-optimized Markdown wiki, powered entirely by your own `llama.cpp`
cluster. The Markdown vault is the permanent artifact — the SQLite cache,
CLI, and MCP server are all replaceable lenses on top of it.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the phased build log.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/)
- The [Flutter SDK](https://docs.flutter.dev/get-started/install/linux) —
  only to **package** the desktop app (`flet build`); the CLI, the MCP
  server, and running the app from source all work without it
- A local `llama-server` instance (or compatible OpenAI-API endpoint) for
  ingestion and search — everything else (vault management, linking,
  linting, git) works with zero LLM connectivity.

## Install

```bash
uv sync
```

## Quickstart

```bash
# Create a vault
uv run llm-wiki vault create ~/my-vault --name "My Vault"

# Ingest a document -- stages it, then summarizes/extracts/embeds it
# against your llama-server
uv run llm-wiki ingest some-document.md --vault ~/my-vault

# Re-sync the [[wikilink]] graph (incremental -- only changed notes)
uv run llm-wiki link sync --vault ~/my-vault

# Check vault health (schema violations, broken links, isolated notes)
uv run llm-wiki lint run --vault ~/my-vault

# Version control the vault
uv run llm-wiki git init --vault ~/my-vault
uv run llm-wiki git commit --vault ~/my-vault -m "Initial commit"
```

Every command above defaults `--vault` to the current directory, so once
you `cd ~/my-vault` you can drop the flag.

## CLI reference

| Command | What it does |
|---|---|
| `vault create <path> [--name] [--description]` | Scaffolds a new vault directory tree. |
| `vault open <path>` | Validates an existing vault and adds it to the recent-vaults list. |
| `ingest <file> [--title] [--chat-model] [--embedding-model]` | Stages, queues, and compiles one raw document: summary + entity/concept notes + embeddings. |
| `link sync` | Re-syncs `[[wikilinks]]` for notes whose content changed since the last sync. |
| `link rebuild` | Forces a full re-sync of every note's links, regardless of hash. |
| `lint run` | Runs schema/broken-link/isolated-note checks; reports a 0–100 health score. |
| `storage rebuild` | Wipes and reconstructs the SQLite cache purely from `wiki/` — safe at any time, the cache is fully derived. |
| `git init` | Initializes Git version control for the vault. |
| `git status` | Reports branch and working-tree status. |
| `git commit -m <message>` | Stages all changes and commits them. |
| `git push [--remote]` | Pushes the current branch to a remote. |

All commands accept `--vault <path>` (default: `.`). Run
`uv run llm-wiki --help` or `uv run llm-wiki <group> --help` for full option
details.

## Configuration

Each vault has a `.llm-wiki-config` JSON file (written by `vault create`)
holding both vault identity and the `llm_provider` / `mcp_server` / `vault`
settings sections. Any field can be overridden per-invocation with an
environment variable, prefixed `LLM_WIKI_` with `__` between the section and
field name:

```bash
LLM_WIKI_LLM_PROVIDER__HOST_PORT=8080 uv run llm-wiki ingest doc.md --vault ~/my-vault
```

Precedence (highest first): environment variables → `.llm-wiki-config` →
built-in defaults.

## MCP server

Exposes a vault's search/lookup/traversal tools to external MCP clients
(Claude Desktop, Cursor, the FastMCP dev inspector, etc.):

```bash
uv run python -m llm_wiki.mcp.server --vault ~/my-vault --transport stdio
```

Tools: `search_wiki_content` (semantic search), `read_entity_profile`,
`read_synthesis_note`, `trace_network_path` (degree-of-separation lookup).
Every file-touching tool is sandboxed to the vault root.

## Desktop app

Run it from source — fast, and all you need day to day:

```bash
uv run python -m llm_wiki.gui
```

Package it as a standalone Linux bundle (`build/linux/llm-wiki`, ~256 MB,
no Python or `uv` needed to run):

```bash
uv run flet build linux --python-version 3.13 --skip-flutter-doctor
```

Three flags' worth of explanation, because none of them are optional:

- **The Flutter SDK must be on `PATH`.** Flet extensions with Dart code
  (here `flet-charts`, which draws the Health panel's chart) are compiled
  into a custom client; the prebuilt `flet-desktop` client cannot load
  them. Install the SDK anywhere — no root required — and add its `bin/`
  to `PATH`.
- **`--python-version 3.13` is required.** `outlines` (grammar-constrained
  extraction, `llm/extraction.py`) publishes no Python 3.14 wheel, and
  `flet build` otherwise picks the highest version satisfying
  `requires-python`. Flet offers no `pyproject.toml` key for this, so it
  has to be passed on the command line.
- **`--skip-flutter-doctor` avoids a false failure.** `flutter doctor`
  exits non-zero over a missing Android SDK and Chrome, neither of which a
  Linux desktop build uses, and `flet build` treats that as fatal.

## Development

```bash
uv run pytest              # full suite (live-LLM tests deselected by default)
uv run pytest -m live_llm  # tests that hit a real llama-server instance
uv run ruff check .
```

PySide6 is a dev-only dependency: the Phase 15 QML controllers under
`gui/` still import it until they are ported to Flet. It is deliberately
kept out of `project.dependencies` so `flet build` does not bundle Qt —
which also breaks the build, since `compileall` fails on its
`site-packages`.
