"""Tests for background bash execution and the task_output/task_stop tools.

Every scenario that touches a real subprocess runs inside a single
``asyncio.run`` so the asyncio transport stays bound to a live loop for the
whole scenario (terminate/wait on a foreign loop would fail). Tasks are
always stopped before the loop closes so no transport is left running.

Child commands are spawned as small script files instead of ``-c`` one-liners:
the payload then needs no shell/argv quoting at all, which keeps the command
lines valid on every platform.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.background import BackgroundTask, background_registry
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import _task_output as task_output_handler
from nexuscli.tools.builtins import _task_stop as task_stop_handler
from nexuscli.tools.executor import ToolExecutor

# The child writes to a redirected file, where CPython switches stdout to
# block buffering — flush=True is what makes the output observable in time.
_HELLO_BODY = "print('hello', flush=True)\nimport time\ntime.sleep(30)\n"
_SLEEPER_BODY = "import time\ntime.sleep(5)\n"
_ECHO_BODY = "print('fg-ok')\n"

_PYTHON = sys.executable


@pytest.fixture
def background_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the background log directory into a temp home.

    background.py resolves ~/.nexuscli/background/ through Path.home(). On
    Windows Path.home() reads USERPROFILE (not HOME), so both environment
    variables are redirected as well, mirroring the repo's HOME-patching
    test convention.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    """Materialize a child script and return its shell command line.

    Both paths are quoted explicitly; the interpreter path comes from
    ``sys.executable``, a constant of the running test process, not from any
    external input.
    """
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return '"' + _PYTHON + '" "' + str(script) + '"'


def _make_context(tmp_path: Path) -> ToolContext:
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return ToolContext(cwd=str(tmp_path), config=config)


def _builtin_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


def _tool(name: str):
    tool = _builtin_registry().get(name)
    assert tool is not None
    return tool


# ---------------------------------------------------------------------------
# Registration and schema
# ---------------------------------------------------------------------------


def test_background_tools_registered_before_revert_turn():
    tools = get_builtin_tools()
    names = [tool.name for tool in tools]

    assert "task_output" in names
    assert "task_stop" in names
    assert names.index("task_output") < names.index("revert_turn")
    assert names.index("task_stop") < names.index("revert_turn")

    by_name = {tool.name: tool for tool in tools}
    # task_output is a read-only concurrent tool; task_stop is gated.
    assert by_name["task_output"].is_read_only
    assert by_name["task_output"].is_concurrency_safe
    assert not by_name["task_stop"].is_read_only
    assert by_name["task_stop"].requires_approval
    for name in ("bash", "execute_command"):
        assert by_name[name].parameters["properties"]["run_in_background"] == {
            "type": "boolean",
            "description": "Run in the background; returns a task id immediately",
        }


# ---------------------------------------------------------------------------
# bash with run_in_background
# ---------------------------------------------------------------------------


def test_bash_run_in_background_returns_task_id_immediately(tmp_path, background_home):
    context = _make_context(tmp_path)
    command = _write_script(tmp_path, "bg_hello.py", _HELLO_BODY)
    bash = _tool("bash")

    async def scenario():
        started = time.monotonic()
        result = await bash.execute({"command": command, "run_in_background": True}, context)
        elapsed = time.monotonic() - started
        match = re.search(r"Started background task ([0-9a-f]{12})\.", result.content)
        assert match, result.content
        task_id = match.group(1)
        # Cleanup before the loop closes: the task keeps running otherwise.
        await background_registry.stop(task_id)
        return result, elapsed, task_id

    result, elapsed, task_id = asyncio.run(scenario())

    assert not result.is_error
    assert elapsed < 2.0, "background start must not wait for the process"
    # The output file is created in the redirected home, not the real one.
    output_path = background_home / ".nexuscli" / "background" / f"{task_id}.log"
    assert output_path.exists()
    task = background_registry.get(task_id)
    assert task is not None
    assert task.command == command


# ---------------------------------------------------------------------------
# task_output (handler level; the executor tests below cover Tool.execute)
# ---------------------------------------------------------------------------


def test_task_output_shows_output_and_running_status(tmp_path, background_home):
    context = _make_context(tmp_path)
    command = _write_script(tmp_path, "bg_hello.py", _HELLO_BODY)
    bash = _tool("bash")

    async def scenario():
        result = await bash.execute({"command": command, "run_in_background": True}, context)
        task_id = re.search(r"Started background task ([0-9a-f]{12})\.", result.content)
        assert task_id
        # Poll briefly for the flushed output; the process keeps sleeping
        # after printing, so the status stays "running" throughout.
        deadline = time.monotonic() + 2.0
        output = None
        while time.monotonic() < deadline:
            output = await task_output_handler({"task_id": task_id.group(1)}, context)
            assert not output.is_error
            if "hello" in output.content:
                break
            await asyncio.sleep(0.2)
        status_at_poll = output.content.split("]", 1)[0].lstrip("[")
        await background_registry.stop(task_id.group(1))
        return output, status_at_poll

    output, status_at_poll = asyncio.run(scenario())

    assert output is not None
    assert "hello" in output.content
    assert status_at_poll == "running"
    assert output.content.startswith("[running] exit=None")


# ---------------------------------------------------------------------------
# task_stop (handler level; the executor tests below cover Tool.execute)
# ---------------------------------------------------------------------------


def test_task_stop_terminates_process_and_freezes_output(tmp_path, background_home):
    context = _make_context(tmp_path)
    command = _write_script(tmp_path, "bg_hello.py", _HELLO_BODY)
    bash = _tool("bash")

    async def scenario():
        result = await bash.execute({"command": command, "run_in_background": True}, context)
        task_id = re.search(r"Started background task ([0-9a-f]{12})\.", result.content)
        assert task_id
        task = background_registry.get(task_id.group(1))
        assert task is not None
        for _ in range(10):
            await asyncio.sleep(0.2)
            if "hello" in background_registry.read_tail(task.task_id):
                break
        stopped = await task_stop_handler({"task_id": task.task_id}, context)
        status = background_registry.status(task.task_id)
        exit_code = task.process.returncode
        tail_before = background_registry.read_tail(task.task_id)
        await asyncio.sleep(0.6)
        tail_after = background_registry.read_tail(task.task_id)
        return stopped, status, exit_code, tail_before, tail_after

    stopped, status, exit_code, tail_before, tail_after = asyncio.run(scenario())

    assert not stopped.is_error
    assert "stopped" in stopped.content
    # The process really exited, and it is reported as stopped (not failed),
    # even though the kill signal yields a non-zero exit code.
    assert status == "stopped"
    assert exit_code is not None
    # No further output after the stop: the log file settles.
    assert "hello" in tail_before
    assert tail_after == tail_before


# ---------------------------------------------------------------------------
# stop() escalation ladder: terminate -> kill (stubbed process)
# ---------------------------------------------------------------------------


class _IgnoreTerminateProcess:
    """Stub subprocess whose terminate() is ignored, forcing the kill escalation.

    A real child cannot reliably ignore terminate (on Windows terminate is a
    hard kill), so the ladder in ``BackgroundTaskRegistry.stop()`` is
    exercised with this duck-typed stub: ``BackgroundTask`` is a plain
    dataclass and never validates the process type. The ``asyncio.Event`` is
    created by the test body (outside any loop) and handed in, so ``wait()``
    resolves only once ``kill()`` has fired.
    """

    def __init__(self, killed: asyncio.Event) -> None:
        self.calls: list[str] = []
        self.returncode: int | None = None
        self._killed = killed

    def terminate(self) -> None:
        # Signal ignored: returncode stays None, so the grace-period wait
        # times out and stop() must escalate to kill().
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = 1
        self._killed.set()

    async def wait(self) -> int:
        await self._killed.wait()
        return self.returncode


def test_task_stop_escalates_to_kill_when_terminate_is_ignored(tmp_path, monkeypatch):
    killed = asyncio.Event()
    process = _IgnoreTerminateProcess(killed)
    task = BackgroundTask(
        task_id="escalate0001",
        command="stub",
        process=process,
        output_path=tmp_path / "escalate.log",
        started_at=time.time(),
    )
    # stop() reads the module global at call time, so patching it here
    # shortens the grace period without touching anything else.
    monkeypatch.setattr("nexuscli.tools.background._STOP_GRACE_SECONDS", 0.05)
    background_registry._tasks[task.task_id] = task
    try:
        status = asyncio.run(background_registry.stop(task.task_id))
    finally:
        # The registry is process-wide shared state: drop the stub task so
        # nothing leaks into the other tests.
        background_registry._tasks.pop(task.task_id, None)
        background_registry._stopped.discard(task.task_id)

    # The kill-produced non-zero exit code is still reported as "stopped"
    # because the registry recorded the task in its _stopped set.
    assert status == "stopped"
    # Order proves the ladder: terminate first, then the escalation to kill.
    assert process.calls == ["terminate", "kill"]
    assert process.returncode is not None


# ---------------------------------------------------------------------------
# Unknown ids
# ---------------------------------------------------------------------------


def test_unknown_task_ids_return_errors(tmp_path, background_home):
    context = _make_context(tmp_path)

    async def scenario():
        output = await task_output_handler({"task_id": "0" * 12}, context)
        stop = await task_stop_handler({"task_id": "0" * 12}, context)
        return output, stop

    output, stop = asyncio.run(scenario())

    assert output.is_error
    assert "Unknown background task id" in output.content
    assert stop.is_error
    assert "Unknown background task id" in stop.content


# ---------------------------------------------------------------------------
# Foreground bash regression
# ---------------------------------------------------------------------------


def test_foreground_bash_output_and_timeout_unchanged(tmp_path, background_home):
    context = _make_context(tmp_path)
    bash = _tool("bash")

    async def scenario():
        ok = await bash.execute(
            {"command": _write_script(tmp_path, "bg_echo.py", _ECHO_BODY)}, context
        )
        slow = await bash.execute(
            {
                "command": _write_script(tmp_path, "bg_sleeper.py", _SLEEPER_BODY),
                "timeout": 1,
            },
            context,
        )
        return ok, slow

    ok, slow = asyncio.run(scenario())

    assert not ok.is_error
    assert "fg-ok" in ok.content
    assert slow.is_error
    assert "timed out" in slow.content


# ---------------------------------------------------------------------------
# Approval and audit chain (executor level; also covers Tool.execute)
# ---------------------------------------------------------------------------


def test_task_stop_requires_approval_and_is_audited(tmp_path, background_home):
    # Default hitl_mode is "auto": task_stop requires approval, and with no
    # approval callback configured the executor must deny and audit it.
    config = load_config(project_root=tmp_path)
    executor = ToolExecutor(_builtin_registry())
    context = ToolContext(cwd=str(tmp_path), config=config)

    call = {"id": "call-1", "name": "task_stop", "arguments": {"task_id": "0" * 12}}
    result = asyncio.run(executor.execute_all([call], context))[0]

    assert result.is_error
    assert "approval policy" in result.content
    assert "Unknown background task id" not in result.content
    audit_file = background_home / ".nexuscli" / "audit.jsonl"
    assert audit_file.exists()
    event = json.loads(audit_file.read_text(encoding="utf-8").splitlines()[-1])
    assert event["tool_name"] == "task_stop"
    assert event["outcome"] == "deny"


def test_task_output_flows_without_approval_prompt(tmp_path, background_home):
    # task_output is read-only: in auto mode it must reach the handler even
    # with no approval callback (an unknown id surfaces as a tool error, not
    # an approval denial).
    config = load_config(project_root=tmp_path)
    executor = ToolExecutor(_builtin_registry())
    context = ToolContext(cwd=str(tmp_path), config=config)

    call = {"id": "call-1", "name": "task_output", "arguments": {"task_id": "0" * 12}}
    result = asyncio.run(executor.execute_all([call], context))[0]

    assert result.is_error
    assert "Unknown background task id" in result.content
    assert "approval" not in result.content
