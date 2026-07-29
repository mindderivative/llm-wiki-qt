"""In-process Git version control via pygit2 (ARCHITECTURE.md §7).

No subprocess spawn per operation, so there's no `QProcess` stdout stream
to drain -- the class of bug that hit the original `GitManager` can't
happen here by construction.
"""

from pathlib import Path

import pygit2
from loguru import logger
from pygit2.enums import FileStatus, MergeAnalysis, ResetMode

from llm_wiki.models import GitStatus

_DEFAULT_AUTHOR = ("LLM-Wiki", "llm-wiki@localhost")
_DIRTY_FLAGS = (
    FileStatus.INDEX_NEW
    | FileStatus.INDEX_MODIFIED
    | FileStatus.INDEX_DELETED
    | FileStatus.INDEX_RENAMED
    | FileStatus.INDEX_TYPECHANGE
    | FileStatus.WT_MODIFIED
    | FileStatus.WT_DELETED
    | FileStatus.WT_TYPECHANGE
    | FileStatus.WT_RENAMED
)


def init(repo_path: Path | str) -> pygit2.Repository:
    """Initializes a repository at `repo_path`, creating the directory if needed."""
    path = Path(repo_path)
    path.mkdir(parents=True, exist_ok=True)
    pygit2.init_repository(str(path))
    logger.info(f"Initialized git repository at {path}")
    return _open(path)


def _open(repo_path: Path | str) -> pygit2.Repository:
    discovered = pygit2.discover_repository(str(repo_path))
    if discovered is None:
        raise pygit2.GitError(f"No Git repository found at or above {repo_path}")
    return pygit2.Repository(discovered)


def _current_branch_name(repo: pygit2.Repository) -> str | None:
    if repo.head_is_unborn:
        target = repo.lookup_reference("HEAD").target
        return target.rsplit("/", 1)[-1] if isinstance(target, str) else None
    return repo.head.shorthand


def stage_all(repo_path: Path | str) -> None:
    """Stages all working-tree changes (new, modified, deleted)."""
    repo = _open(repo_path)
    repo.index.add_all()
    repo.index.write()
    logger.info(f"Staged all changes in {repo_path}")


def commit(
    repo_path: Path | str,
    message: str,
    *,
    author_name: str = _DEFAULT_AUTHOR[0],
    author_email: str = _DEFAULT_AUTHOR[1],
) -> str:
    """Commits the current index; returns the new commit's hex SHA."""
    repo = _open(repo_path)
    signature = pygit2.Signature(author_name, author_email)
    tree = repo.index.write_tree()
    parents = [] if repo.head_is_unborn else [repo.head.target]
    oid = repo.create_commit("HEAD", signature, signature, message, tree, parents)
    logger.info(f"Committed {str(oid)[:8]}: {message}")
    return str(oid)


def status(repo_path: Path | str) -> GitStatus:
    """Reports branch name and working-tree state as a structured `GitStatus`."""
    repo = _open(repo_path)
    modified: list[str] = []
    untracked: list[str] = []

    for path, flags in repo.status().items():
        if flags & FileStatus.WT_NEW:
            untracked.append(path)
        elif flags & _DIRTY_FLAGS:
            modified.append(path)

    return GitStatus(
        branch=_current_branch_name(repo),
        modified=sorted(modified),
        untracked=sorted(untracked),
        clean=not modified and not untracked,
    )


def set_remote(repo_path: Path | str, name: str, url: str) -> None:
    """Creates or repoints a remote."""
    repo = _open(repo_path)
    try:
        repo.remotes.create(name, url)
    except ValueError:
        repo.remotes.set_url(name, url)


def push(
    repo_path: Path | str,
    *,
    remote_name: str = "origin",
    branch: str | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> None:
    """Pushes the local branch to `remote_name`."""
    repo = _open(repo_path)
    branch_name = branch or _current_branch_name(repo)
    refspec = f"refs/heads/{branch_name}:refs/heads/{branch_name}"
    repo.remotes[remote_name].push([refspec], callbacks=callbacks)
    logger.info(f"Pushed {branch_name} to {remote_name}")


def pull(
    repo_path: Path | str,
    *,
    remote_name: str = "origin",
    branch: str | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> None:
    """Fetches from `remote_name` and fast-forwards the local branch.

    Non-fast-forward histories are left with `repo.merge()` applied and any
    conflicts unresolved in the index -- real conflict-resolution UX is out
    of scope for this phase.
    """
    repo = _open(repo_path)
    branch_name = branch or _current_branch_name(repo)
    repo.remotes[remote_name].fetch(callbacks=callbacks)

    remote_ref = repo.lookup_reference(f"refs/remotes/{remote_name}/{branch_name}")
    remote_commit_id = remote_ref.target
    merge_result, _ = repo.merge_analysis(remote_commit_id)

    if merge_result & MergeAnalysis.UP_TO_DATE:
        logger.info(f"Pulled {remote_name}/{branch_name}: already up to date")
        return

    if merge_result & MergeAnalysis.FASTFORWARD:
        repo.reset(remote_commit_id, ResetMode.HARD)
        logger.info(f"Pulled {remote_name}/{branch_name}: fast-forwarded")
        return

    repo.merge(remote_commit_id)
    logger.info(f"Pulled {remote_name}/{branch_name}: merged, resolve any conflicts")
