"""Phase 3: vault tree creation/loading and the recent-vaults list."""

from pathlib import Path

import pytest

from llm_wiki.models import VaultNotFoundError
from llm_wiki.vault import create_vault, get_recent_vaults, load_vault

EXPECTED_DIRS = [
    "raw/.sources",
    "wiki/sources",
    "wiki/entities",
    "wiki/concepts",
    "wiki/synthesis",
    "wiki/.system/prompts",
    ".llm-wiki",
]
EXPECTED_FILES = [".llm-wiki-config", "wiki/index.md", "wiki/log.md", "SCHEMA.md"]


def test_create_vault_produces_correct_tree(tmp_path: Path) -> None:
    root = tmp_path / "my-vault"
    recent_path = tmp_path / "recent.json"

    info = create_vault(root, "My Vault", "A test knowledge base.", recent_vaults_path=recent_path)

    for rel_dir in EXPECTED_DIRS:
        assert (root / rel_dir).is_dir(), f"missing directory: {rel_dir}"
    for rel_file in EXPECTED_FILES:
        assert (root / rel_file).is_file(), f"missing file: {rel_file}"

    assert info.path == root
    assert info.name == "My Vault"
    assert info.description == "A test knowledge base."
    assert info.version == "1.0"


def test_load_vault_raises_on_non_vault_directory(tmp_path: Path) -> None:
    empty_dir = tmp_path / "not-a-vault"
    empty_dir.mkdir()

    with pytest.raises(VaultNotFoundError):
        load_vault(empty_dir, recent_vaults_path=tmp_path / "recent.json")


def test_load_vault_raises_on_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(VaultNotFoundError):
        load_vault(tmp_path / "does-not-exist", recent_vaults_path=tmp_path / "recent.json")


def test_load_vault_returns_info_matching_created(tmp_path: Path) -> None:
    root = tmp_path / "vault"
    recent_path = tmp_path / "recent.json"
    created = create_vault(root, "Vault Name", "Description text", recent_vaults_path=recent_path)

    loaded = load_vault(root, recent_vaults_path=recent_path)

    assert loaded.path == created.path
    assert loaded.name == created.name
    assert loaded.description == created.description
    assert loaded.created_at == created.created_at


def test_recent_vaults_persist_and_dedupe(tmp_path: Path) -> None:
    recent_path = tmp_path / "recent.json"
    vault_a = tmp_path / "vault-a"
    vault_b = tmp_path / "vault-b"

    create_vault(vault_a, "A", "", recent_vaults_path=recent_path)
    create_vault(vault_b, "B", "", recent_vaults_path=recent_path)
    assert get_recent_vaults(recent_path) == [vault_b.resolve(), vault_a.resolve()]

    # Re-loading vault A should move it to the front, not duplicate it.
    load_vault(vault_a, recent_vaults_path=recent_path)
    assert get_recent_vaults(recent_path) == [vault_a.resolve(), vault_b.resolve()]


def test_recent_vaults_capped_at_ten(tmp_path: Path) -> None:
    recent_path = tmp_path / "recent.json"
    for i in range(12):
        create_vault(tmp_path / f"vault-{i}", f"V{i}", "", recent_vaults_path=recent_path)

    recents = get_recent_vaults(recent_path)
    assert len(recents) == 10
    assert recents[0] == (tmp_path / "vault-11").resolve()


def test_get_recent_vaults_with_no_file_returns_empty_list(tmp_path: Path) -> None:
    assert get_recent_vaults(tmp_path / "does-not-exist.json") == []
