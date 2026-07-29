"""Phase 16c: `McpProcess` -- launching/stopping the MCP server as a
subprocess. Spawns the real `python -m llm_wiki.mcp.server` entry point
against a fixture vault (stdio transport, so it just blocks on stdin
until stopped) rather than mocking `subprocess.Popen`, since the whole
point is confirming the real command line actually launches.
"""

import time
from pathlib import Path

import pytest

from llm_wiki.mcp.process import McpProcess
from llm_wiki.vault import create_vault


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(root, "Test Vault", "desc")
    return root


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met within timeout")


def test_not_running_before_start() -> None:
    proc = McpProcess()
    assert proc.running is False


def test_start_launches_a_live_process(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root)
        _wait_until(lambda: proc.running)
        assert proc.running is True
    finally:
        proc.stop()


def test_start_while_already_running_does_not_spawn_a_second_process(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root)
        _wait_until(lambda: proc.running)
        first_pid = proc._proc.pid

        proc.start(vault_root)

        assert proc._proc.pid == first_pid
    finally:
        proc.stop()


def test_stop_terminates_the_process(vault_root: Path) -> None:
    proc = McpProcess()
    proc.start(vault_root)
    _wait_until(lambda: proc.running)

    proc.stop()

    assert proc.running is False


def test_stop_without_a_running_process_is_a_no_op() -> None:
    proc = McpProcess()
    proc.stop()  # must not raise
    assert proc.running is False


def test_restart_replaces_the_process(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root)
        _wait_until(lambda: proc.running)
        first_pid = proc._proc.pid

        proc.restart(vault_root)
        _wait_until(lambda: proc.running)

        assert proc.running is True
        assert proc._proc.pid != first_pid
    finally:
        proc.stop()
