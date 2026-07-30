"""Phase 20: `McpProcess` -- running the MCP server in-process, on a
background thread. Starts a real `uvicorn` server against a fixture
vault and confirms it's genuinely listening (a raw socket connect),
rather than mocking `uvicorn.Server`, since the whole point is confirming
the real server actually binds and serves.
"""

import socket
import time
from pathlib import Path

import pytest

from llm_wiki.mcp.process import McpProcess
from llm_wiki.vault import create_vault

HOST = "127.0.0.1"


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
        time.sleep(0.02)
    raise AssertionError("condition not met within timeout")


def _port_is_open(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def test_not_running_before_start() -> None:
    proc = McpProcess()
    assert proc.running is False


def test_start_launches_a_live_server(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root, host=HOST, port=18601)
        _wait_until(lambda: proc.running)

        assert proc.running is True
        assert _port_is_open(18601)  # a real listener, not just a live thread
    finally:
        proc.stop()


def test_start_while_already_running_does_not_spawn_a_second_server(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root, host=HOST, port=18602)
        _wait_until(lambda: proc.running)
        first_thread = proc._thread

        proc.start(vault_root, host=HOST, port=18602)

        assert proc._thread is first_thread
    finally:
        proc.stop()


def test_stop_terminates_the_server_and_releases_the_port(vault_root: Path) -> None:
    proc = McpProcess()
    proc.start(vault_root, host=HOST, port=18603)
    _wait_until(lambda: proc.running)
    assert _port_is_open(18603)

    proc.stop()

    assert proc.running is False
    assert not _port_is_open(18603)

    # A fresh process can immediately rebind the same port -- proves the
    # socket was genuinely released, not just that `running` flipped.
    second = McpProcess()
    try:
        second.start(vault_root, host=HOST, port=18603)
        _wait_until(lambda: second.running)
        assert _port_is_open(18603)
    finally:
        second.stop()


def test_stop_without_a_running_server_is_a_no_op() -> None:
    proc = McpProcess()
    proc.stop()  # must not raise
    assert proc.running is False


def test_restart_serves_again_on_a_fresh_thread(vault_root: Path) -> None:
    proc = McpProcess()
    try:
        proc.start(vault_root, host=HOST, port=18604)
        _wait_until(lambda: proc.running)
        first_thread = proc._thread

        proc.restart(vault_root, host=HOST, port=18604)
        _wait_until(lambda: proc.running)

        assert proc.running is True
        assert proc._thread is not first_thread
        assert _port_is_open(18604)
    finally:
        proc.stop()
