# LLM-Wiki

A local-first, self-maintaining knowledge base engine. Drop raw source
documents in; the engine atomizes, summarizes, links, and lints them into a
GEO-optimized Markdown wiki, powered entirely by your own `llama.cpp`
cluster. The Markdown vault is the permanent artifact — the SQLite cache,
CLI, and MCP server are all replaceable lenses on top of it.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design and
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for the phased build log.

## Requirements

- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/)
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

## Development

```bash
uv run pytest              # full suite (live-LLM tests deselected by default)
uv run pytest -m live_llm  # tests that hit a real llama-server instance
uv run ruff check .
```
