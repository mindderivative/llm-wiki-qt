-- LLM-Wiki storage schema (ARCHITECTURE.md §6).
--
-- Every table here is a derived cache, fully rebuildable from wiki/ + raw/
-- via `storage.db.rebuild_from_vault()` (Design Principle 1: losing this
-- file must only cost rebuild time, never information).
--
-- Migration approach: a single `schema_version` row. At this project's
-- scale a full migration framework (e.g. Alembic) is unwarranted --
-- bump SCHEMA_VERSION in db.py and add an explicit migration step there
-- when the shape below changes.

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    archive_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    sources TEXT NOT NULL DEFAULT '[]',
    content_hash TEXT NOT NULL,
    -- content_hash as of the last successful link sync; NULL means never
    -- synced. Distinct from content_hash itself, which upsert_note_from_file()
    -- keeps current on every write -- link_engine.sync_links() is what's
    -- allowed to advance this one, once it's re-processed the note's edges.
    links_synced_hash TEXT,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_slug ON notes (slug);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER REFERENCES notes (id) ON DELETE CASCADE,
    queue_item_id INTEGER REFERENCES queue (id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    CHECK ((note_id IS NULL) != (queue_item_id IS NULL))
);

-- 768 dims matches nomic-embed-text-v1.5's default output size (see
-- ARCHITECTURE.md §4). Wired up for real in Phase 8.
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    chunk_id INTEGER PRIMARY KEY,
    embedding FLOAT[768]
);

CREATE TABLE IF NOT EXISTS links (
    source_slug TEXT NOT NULL,
    target_slug TEXT NOT NULL,
    PRIMARY KEY (source_slug, target_slug)
);

CREATE TABLE IF NOT EXISTS lint_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lint_findings_run_id ON lint_findings (run_id);
