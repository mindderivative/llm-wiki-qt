"""Phase 0 smoke test: proves the pytest + package scaffold is wired up."""

import llm_wiki


def test_package_importable_and_versioned() -> None:
    assert llm_wiki.__version__ == "0.1.0"


def test_sanity() -> None:
    assert 1 + 1 == 2
