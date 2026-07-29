"""Phase 5: enqueue_file() staging/archiving and queue status transitions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_wiki.ingest import (
    enqueue_file,
    get_queue_item,
    list_queue,
    scan_raw_directory,
    update_status,
)
from llm_wiki.models import IngestionError, QueueStatus
from llm_wiki.storage import connect


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / ".llm-wiki" / "db.sqlite3")
    yield connection
    connection.close()


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "raw" / ".sources").mkdir(parents=True)
    return root


@pytest.fixture
def source_file(tmp_path: Path) -> Path:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "My Notes.txt"
    path.write_text("original content", encoding="utf-8")
    return path


def test_enqueue_file_produces_correct_layout_and_queued_row(conn, vault_root, source_file):
    item = enqueue_file(conn, vault_root, source_file, title="My Notes")

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    archive_path = vault_root / "raw" / ".sources" / "My Notes.txt"
    staged_path = vault_root / "raw" / f"{today}_my-notes.txt"

    assert archive_path.read_text(encoding="utf-8") == "original content"
    assert staged_path.read_text(encoding="utf-8") == "original content"

    assert item.id is not None
    assert item.status is QueueStatus.QUEUED
    assert item.raw_path == staged_path.relative_to(vault_root)
    assert item.archive_path == archive_path.relative_to(vault_root)

    row = conn.execute("SELECT * FROM queue WHERE id = ?", (item.id,)).fetchone()
    assert row["status"] == "queued"
    assert row["title"] == "My Notes"


def test_enqueue_file_missing_source_raises_ingestion_error(conn, vault_root, tmp_path):
    with pytest.raises(IngestionError):
        enqueue_file(conn, vault_root, tmp_path / "does-not-exist.txt")


def test_enqueue_file_disambiguates_name_collisions(conn, vault_root, source_file):
    first = enqueue_file(conn, vault_root, source_file, title="My Notes")
    second = enqueue_file(conn, vault_root, source_file, title="My Notes")

    assert first.raw_path != second.raw_path
    assert first.archive_path != second.archive_path
    assert (vault_root / first.raw_path).exists()
    assert (vault_root / second.raw_path).exists()


def test_status_transitions_are_persisted_and_queryable(conn, vault_root, source_file):
    item = enqueue_file(conn, vault_root, source_file, title="My Notes")

    parsing = update_status(conn, item.id, QueueStatus.PARSING)
    assert parsing.status is QueueStatus.PARSING
    assert parsing.updated_at >= item.updated_at

    errored = update_status(conn, item.id, QueueStatus.ERROR, error="atomizer boom")
    assert errored.status is QueueStatus.ERROR
    assert errored.error == "atomizer boom"

    completed = update_status(conn, item.id, QueueStatus.COMPLETED)
    assert completed.status is QueueStatus.COMPLETED
    assert completed.error is None


def test_update_status_unknown_id_raises_ingestion_error(conn):
    with pytest.raises(IngestionError):
        update_status(conn, 9999, QueueStatus.PARSING)


def test_get_queue_item_returns_matching_row(conn, vault_root, source_file):
    item = enqueue_file(conn, vault_root, source_file, title="My Notes")

    fetched = get_queue_item(conn, item.id)

    assert fetched == item


def test_get_queue_item_unknown_id_raises_ingestion_error(conn):
    with pytest.raises(IngestionError):
        get_queue_item(conn, 9999)


def test_list_queue_filters_by_status(conn, vault_root, source_file):
    item_a = enqueue_file(conn, vault_root, source_file, title="Doc A")
    item_b = enqueue_file(conn, vault_root, source_file, title="Doc B")
    update_status(conn, item_a.id, QueueStatus.COMPLETED)

    all_items = list_queue(conn)
    assert [i.id for i in all_items] == [item_a.id, item_b.id]

    queued_only = list_queue(conn, status=QueueStatus.QUEUED)
    assert [i.id for i in queued_only] == [item_b.id]

    completed_only = list_queue(conn, status=QueueStatus.COMPLETED)
    assert [i.id for i in completed_only] == [item_a.id]


# --- scan_raw_directory ---------------------------------------------------


def test_scan_raw_directory_queues_a_file_dropped_in_directly(conn, vault_root):
    (vault_root / "raw" / "dropped.txt").write_text("hi", encoding="utf-8")

    discovered = scan_raw_directory(conn, vault_root)

    assert [d.title for d in discovered] == ["dropped"]
    assert discovered[0].raw_path == Path("raw/dropped.txt")
    assert discovered[0].archive_path is None
    assert discovered[0].status is QueueStatus.QUEUED

    queued = list_queue(conn)
    assert [q.title for q in queued] == ["dropped"]


def test_scan_raw_directory_skips_files_already_tracked(conn, vault_root, source_file):
    item = enqueue_file(conn, vault_root, source_file, title="My Notes")

    discovered = scan_raw_directory(conn, vault_root)

    assert discovered == []
    assert len(list_queue(conn)) == 1
    assert list_queue(conn)[0].id == item.id


def test_scan_raw_directory_ignores_the_sources_archive(conn, vault_root, source_file):
    enqueue_file(conn, vault_root, source_file, title="My Notes")  # writes into .sources/

    discovered = scan_raw_directory(conn, vault_root)

    assert discovered == []


def test_scan_raw_directory_is_idempotent(conn, vault_root):
    (vault_root / "raw" / "dropped.txt").write_text("hi", encoding="utf-8")

    first = scan_raw_directory(conn, vault_root)
    second = scan_raw_directory(conn, vault_root)

    assert len(first) == 1
    assert second == []
    assert len(list_queue(conn)) == 1


def test_scan_raw_directory_with_no_raw_dir_returns_empty(conn, tmp_path):
    empty_vault = tmp_path / "no-raw-vault"
    empty_vault.mkdir()

    assert scan_raw_directory(conn, empty_vault) == []


def test_scan_raw_directory_only_queues_new_files(conn, vault_root):
    (vault_root / "raw" / "existing.txt").write_text("a", encoding="utf-8")
    scan_raw_directory(conn, vault_root)
    (vault_root / "raw" / "new-one.txt").write_text("b", encoding="utf-8")

    discovered = scan_raw_directory(conn, vault_root)

    assert [d.title for d in discovered] == ["new-one"]
    assert len(list_queue(conn)) == 2
