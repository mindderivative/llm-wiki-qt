"""Phase 4: pygit2-based git engine, against real temp repos (no shelling out)."""

from pathlib import Path

import pygit2

from llm_wiki.vcs import git_engine


def test_init_creates_repo_with_unborn_head(tmp_path: Path) -> None:
    repo = git_engine.init(tmp_path)
    assert repo.head_is_unborn
    assert (tmp_path / ".git").is_dir()


def test_init_stage_commit_status_clean(tmp_path: Path) -> None:
    git_engine.init(tmp_path)
    (tmp_path / "note.md").write_text("# Hello\n", encoding="utf-8")

    git_engine.stage_all(tmp_path)
    sha = git_engine.commit(tmp_path, "Initial commit")

    assert len(sha) == 40  # hex SHA-1

    result = git_engine.status(tmp_path)
    assert result.clean is True
    assert result.modified == []
    assert result.untracked == []
    assert result.branch is not None


def test_modified_file_produces_dirty_status(tmp_path: Path) -> None:
    git_engine.init(tmp_path)
    note_path = tmp_path / "note.md"
    note_path.write_text("# Hello\n", encoding="utf-8")
    git_engine.stage_all(tmp_path)
    git_engine.commit(tmp_path, "Initial commit")

    note_path.write_text("# Hello, again\n", encoding="utf-8")

    result = git_engine.status(tmp_path)
    assert result.clean is False
    assert result.modified == ["note.md"]
    assert result.untracked == []


def test_untracked_file_is_reported_separately_from_modified(tmp_path: Path) -> None:
    git_engine.init(tmp_path)
    (tmp_path / "tracked.md").write_text("tracked\n", encoding="utf-8")
    git_engine.stage_all(tmp_path)
    git_engine.commit(tmp_path, "Initial commit")

    (tmp_path / "new.md").write_text("new\n", encoding="utf-8")

    result = git_engine.status(tmp_path)
    assert result.untracked == ["new.md"]
    assert result.modified == []
    assert result.clean is False


def test_set_remote_creates_then_repoints(tmp_path: Path) -> None:
    git_engine.init(tmp_path)
    git_engine.set_remote(tmp_path, "origin", "https://example.com/a.git")
    git_engine.set_remote(tmp_path, "origin", "https://example.com/b.git")

    repo = pygit2.Repository(str(tmp_path))
    assert [r.name for r in repo.remotes] == ["origin"]
    assert repo.remotes["origin"].url == "https://example.com/b.git"


def test_pull_on_diverged_history_merges_without_fast_forward(tmp_path: Path) -> None:
    bare_path = tmp_path / "remote.git"
    pygit2.init_repository(str(bare_path), bare=True)

    work_a = tmp_path / "work-a"
    git_engine.init(work_a)
    (work_a / "note.md").write_text("from a\n", encoding="utf-8")
    git_engine.stage_all(work_a)
    git_engine.commit(work_a, "First commit")
    git_engine.set_remote(work_a, "origin", str(bare_path))
    git_engine.push(work_a)

    work_b = tmp_path / "work-b"
    git_engine.init(work_b)
    git_engine.set_remote(work_b, "origin", str(bare_path))
    # work_b has its own unrelated commit, so pulling can't fast-forward.
    (work_b / ".gitkeep").write_text("", encoding="utf-8")
    git_engine.stage_all(work_b)
    git_engine.commit(work_b, "placeholder")

    git_engine.pull(work_b)

    # Non-conflicting merge: both files present, left mid-merge for the
    # caller to finalize with a commit (see pull()'s docstring).
    repo_b = pygit2.Repository(str(work_b))
    assert repo_b.state() == pygit2.enums.RepositoryState.MERGE
    assert not repo_b.index.conflicts
    assert (work_b / "note.md").exists()
    assert (work_b / ".gitkeep").exists()


def test_pull_fast_forwards_a_clone(tmp_path: Path) -> None:
    bare_path = tmp_path / "remote.git"
    pygit2.init_repository(str(bare_path), bare=True)

    work_a = tmp_path / "work-a"
    git_engine.init(work_a)
    (work_a / "note.md").write_text("v1\n", encoding="utf-8")
    git_engine.stage_all(work_a)
    git_engine.commit(work_a, "v1")
    git_engine.set_remote(work_a, "origin", str(bare_path))
    git_engine.push(work_a)

    work_b_repo = pygit2.clone_repository(str(bare_path), str(tmp_path / "work-b"))
    work_b = Path(work_b_repo.workdir)

    (work_a / "note.md").write_text("v2\n", encoding="utf-8")
    git_engine.stage_all(work_a)
    git_engine.commit(work_a, "v2")
    git_engine.push(work_a)

    git_engine.pull(work_b)

    assert (work_b / "note.md").read_text(encoding="utf-8") == "v2\n"
    assert git_engine.status(work_b).clean is True
