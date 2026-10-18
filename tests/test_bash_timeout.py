"""B2 regression: the timeout must fire even when a grandchild holds the pipe.

The defect: ``timeout=1`` against ``sh -c 'sleep 12 & sleep 12'`` blocked for
the full 12s. ``communicate()`` only returns at pipe EOF, and the backgrounded
grandchild inherited the pipe, so the kill of the direct shell changed nothing.
Timing is measured with ``time.monotonic`` in-process — macOS ships no
``timeout(1)`` binary to lean on.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.tools.commands import CommandExecutor

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX shell semantics")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_pid_file(path: Path, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.02)
    raise AssertionError("the grandchild never reported its pid")


def _make_context(tmp_path: Path) -> ToolContext:
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return ToolContext(cwd=str(tmp_path), config=config)


def _bash_tool():
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get("bash")
    assert tool is not None
    return tool


def test_executor_timeout_returns_despite_grandchild_holding_the_pipe(tmp_path):
    executor = CommandExecutor()

    async def scenario():
        started = time.monotonic()
        result = await executor.execute("sh -c 'sleep 5 & sleep 5'", timeout=1.0)
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())

    assert result.timed_out
    assert result.exit_code == -1
    assert elapsed < 4.0, f"timeout ignored the grandchild: {elapsed:.2f}s"


def test_executor_timeout_kills_the_grandchild_too(tmp_path):
    executor = CommandExecutor()
    pid_file = tmp_path / "grandchild.pid"
    # The grandchild announces its pid, then outlives the shell that forked it.
    command = f"sh -c 'sh -c \"echo $$ > {pid_file}; sleep 30\" & sleep 30'"

    async def scenario():
        started = time.monotonic()
        result = await executor.execute(command, timeout=1.5)
        elapsed = time.monotonic() - started
        pid = _wait_pid_file(pid_file)
        await asyncio.sleep(0.2)
        return result, elapsed, pid, _alive(pid)

    result, elapsed, pid, alive_after = asyncio.run(scenario())

    assert result.timed_out
    assert elapsed < 4.5, f"timeout ignored the grandchild: {elapsed:.2f}s"
    assert not alive_after, f"grandchild {pid} survived the timeout"


def test_executor_keeps_output_written_before_the_timeout(tmp_path):
    executor = CommandExecutor()

    async def scenario():
        # Output already in the pipe must survive the group kill: the drain
        # reads it back instead of discarding everything on timeout.
        return await executor.execute("sh -c 'echo early; sleep 5 & sleep 5'", timeout=1.0)

    result = asyncio.run(scenario())

    assert result.timed_out
    assert "early" in result.stdout


def test_bash_tool_timeout_returns_despite_grandchild(tmp_path):
    context = _make_context(tmp_path)
    bash = _bash_tool()

    async def scenario():
        started = time.monotonic()
        result = await bash.execute({"command": "sh -c 'sleep 5 & sleep 5'", "timeout": 1}, context)
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())

    assert result.is_error
    assert "timed out" in result.content
    assert elapsed < 4.0, f"bash tool ignored the grandchild: {elapsed:.2f}s"


def test_executor_without_timeout_still_returns_full_output(tmp_path):
    executor = CommandExecutor()

    async def scenario():
        return await executor.execute("echo hello; echo oops >&2")

    result = asyncio.run(scenario())

    assert not result.timed_out
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "oops" in result.stderr
