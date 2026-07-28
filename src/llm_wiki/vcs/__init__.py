"""In-process Git version control (pygit2) -- init/stage/commit/push/pull/status."""

from llm_wiki.vcs.git_engine import (
    commit,
    init,
    pull,
    push,
    set_remote,
    stage_all,
    status,
)

__all__ = ["commit", "init", "pull", "push", "set_remote", "stage_all", "status"]
