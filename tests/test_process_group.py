"""B1/B3 regression: a background task owns its whole process group.

The defect these tests pin down: ``task_stop`` used to terminate only the
direct shell child and then report ``stopped`` while a grandchild kept
appending to disk. Killing the leader is not the contract — the group is.

Every scenario runs inside one ``asyncio.run`` so the transport stays bound to
a live loop for the whole scenario, and every task is stopped before the loop
closes.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
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
from nexuscli.tools.commands import (
    kill_process_group,
    process_group_alive,
    process_group_of,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX-only")

# The grandchild is the point: ``sh -c '... &'`` forks it into the same group,
# and it keeps the tick file growing after the shell is gone.
_GRANDCHILD_BODY = (
    "import pathlib, sys, time\n"
    "path = pathlib.Path(sys.argv[1])\n"
    "while True:\n"
    "    with path.open('a') as fh:\n"
    "        fh.write('tick\\n')\n"
    "    time.sleep(0.05)\n"
)


@pytest.fixture
def background_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.nexuscli/background into a temp home (win32 prefers USERPROFILE)."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


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


def _spawn_grandchild(tmp_path: Path, ticks: Path) -> str:
    """Shell command whose forked grandchild ticks forever."""
    script = tmp_path / "grandchild.py"
    script.write_text(_GRANDCHILD_BODY, encoding="utf-8")
    return f'sh -c \'"{sys.executable}" "{script}" "{ticks}" & wait\''


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
    raise AssertionError("the child never reported its pid")


def _tick_count(path: Path) -> int:
    return path.read_text(encoding="utf-8").count("tick") if path.exists() else 0


def _wait_for_ticks(path: Path, minimum: int = 1, timeout: float = 5.0) -> int:
    deadline = time.monotonic() + timeout
    count = _tick_count(path)
    while count < minimum and time.monotonic() < deadline:
        time.sleep(0.05)
        count = _tick_count(path)
    return count


def test_task_stop_kills_forked_grandchild(tmp_path, background_home):
    ticks = tmp_path / "ticks.txt"
    command = _spawn_grandchild(tmp_path, ticks)
    bash = _bash_tool()
    context = _make_context(tmp_path)

    async def scenario():
        started = await bash.execute({"command": command, "run_in_background": True}, context)
        task_id = started.content.split("Started background task ")[1].split(".")[0]
        task = background_registry.get(task_id)
        assert task is not None
        # A dedicated group is what makes the killpg below reach the grandchild.
        assert task.pgid == task.process.pid
        before = await asyncio.to_thread(_wait_for_ticks, ticks)
        stopped = await task_stop_handler({"task_id": task_id}, context)
        after_stop = _tick_count(ticks)
        await asyncio.sleep(1.0)
        return stopped, before, after_stop, _tick_count(ticks), task

    stopped, before, after_stop, later, task = asyncio.run(scenario())

    assert before >= 1, "the grandchild never wrote: the fixture did not run"
    assert "stopped" in stopped.content and "still stopping" not in stopped.content
    assert after_stop >= before
    assert later == after_stop, "the grandchild outlived task_stop and kept writing"
    assert not process_group_alive(task.pgid)


def test_task_stop_reports_stopping_when_the_group_survives_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr("nexuscli.tools.commands._KILL_GRACE_SECONDS", 0.05)
    monkeypatch.setattr("nexuscli.tools.commands._GROUP_POLL_SECONDS", 0.01)
    context = _make_context(tmp_path)

    class _SurvivingProcess:
        # The leader dies from the group signal; the group it left behind does
        # not. That is the only shape in which the ladder runs out of steps,
        # and it is what a setsid-ing grandchild produces in the wild.
        returncode = None
        pid = 424242

        async def wait(self) -> int:
            self.returncode = -15
            return self.returncode

    task = BackgroundTask(
        task_id="survivor0001",
        command="stub",
        process=_SurvivingProcess(),
        output_path=tmp_path / "survivor.log",
        started_at=time.time(),
        pgid=424242,
    )
    background_registry._tasks[task.task_id] = task
    # The group never empties, so the honest answer is "stopping", not "stopped".
    monkeypatch.setattr("nexuscli.tools.background.process_group_alive", lambda _pgid: True)
    monkeypatch.setattr("nexuscli.tools.commands.process_group_alive", lambda _pgid: True)
    try:
        result = asyncio.run(task_stop_handler({"task_id": task.task_id}, context))
        status = background_registry.status(task.task_id)
    finally:
        background_registry._tasks.pop(task.task_id, None)
        background_registry._stopped.discard(task.task_id)

    assert status == "stopping"
    assert "still stopping" in result.content
    assert "stopped (status: stopping)" not in result.content


def test_task_output_marks_orphaned_task_and_task_stop_still_reaps_it(tmp_path):
    ticks = tmp_path / "orphan-ticks.txt"
    context = _make_context(tmp_path)
    script = tmp_path / "orphan.py"
    script.write_text(_GRANDCHILD_BODY, encoding="utf-8")

    async def scenario():
        # A raw spawn whose leader exits at once: the grandchild is the leak.
        proc = await asyncio.create_subprocess_shell(
            f'"{sys.executable}" "{script}" "{ticks}" &',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        pgid = process_group_of(proc.pid)
        assert pgid is not None
        await proc.wait()
        log = tmp_path / "orphan.log"
        log.write_bytes(b"leader exited, grandchild still ticking\n")
        task = BackgroundTask(
            task_id="orphan000001",
            command="orphan",
            process=proc,
            output_path=log,
            started_at=time.time(),
            pgid=pgid,
        )
        background_registry._tasks[task.task_id] = task
        before = await asyncio.to_thread(_wait_for_ticks, ticks)
        status = background_registry.status(task.task_id)
        output = await task_output_handler({"task_id": task.task_id}, context)
        stop = await task_stop_handler({"task_id": task.task_id}, context)
        after_stop = _tick_count(ticks)
        await asyncio.sleep(0.6)
        return before, status, output, stop, after_stop, _tick_count(ticks), task

    try:
        before, status, output, stop, after_stop, later, task = asyncio.run(scenario())
    finally:
        background_registry._tasks.pop("orphan000001", None)
        background_registry._stopped.discard("orphan000001")

    assert before >= 1
    assert status == "orphaned"
    assert output.content.startswith("[orphaned]")
    assert "Unknown background task id" not in stop.content
    assert "stopped" in stop.content
    assert later == after_stop
    assert not process_group_alive(task.pgid)


def test_kill_process_group_returns_false_when_the_group_never_empties(monkeypatch):
    # An unkillable group must be reported, not silently declared dead.
    monkeypatch.setattr("nexuscli.tools.commands._KILL_GRACE_SECONDS", 0.02)
    monkeypatch.setattr("nexuscli.tools.commands._GROUP_POLL_SECONDS", 0.005)
    monkeypatch.setattr("nexuscli.tools.commands.process_group_alive", lambda _pgid: True)

    class _Stub:
        returncode = None

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    assert asyncio.run(kill_process_group(999_999, _Stub(), 0.02)) is False


def test_registry_sweeps_live_groups_on_parent_exit(tmp_path):
    ticks = tmp_path / "sweep-ticks.txt"
    script = tmp_path / "sweep.py"
    script.write_text(_GRANDCHILD_BODY, encoding="utf-8")

    async def scenario():
        proc = await asyncio.create_subprocess_shell(
            f'"{sys.executable}" "{script}" "{ticks}" &',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        pgid = process_group_of(proc.pid)
        assert pgid is not None
        await proc.wait()
        task = BackgroundTask(
            task_id="sweep0000001",
            command="sweep",
            process=proc,
            output_path=tmp_path / "sweep.log",
            started_at=time.time(),
            pgid=pgid,
        )
        background_registry._tasks[task.task_id] = task
        before = await asyncio.to_thread(_wait_for_ticks, ticks)
        return before, task

    try:
        before, task = asyncio.run(scenario())
        assert before >= 1
        assert process_group_alive(task.pgid)
        # This is what the atexit hook calls when the CLI exits: the ids are
        # about to be lost, so the groups must not be left running.
        killed = background_registry.kill_live_tasks()
        assert task.task_id in killed
        after_kill = _tick_count(ticks)
        time.sleep(0.4)
        later = _tick_count(ticks)
    finally:
        background_registry._tasks.pop("sweep0000001", None)
        background_registry._stopped.discard("sweep0000001")

    assert not process_group_alive(task.pgid)
    assert later == after_kill


def test_process_exit_sweeps_the_background_group(tmp_path):
    # The real hook, exercised where it matters: a separate process that
    # starts a background task and then returns from main().
    pid_file = tmp_path / "exit-orphan.pid"
    probe = tmp_path / "exit_probe.py"
    probe.write_text(
        "\n".join(
            [
                "import asyncio",
                "import sys",
                "from nexuscli.tools.background import background_registry",
                "",
                "async def main():",
                f"    await background_registry.start(\"sh -c 'echo $$ > {pid_file}; sleep 30'\")",
                "    await asyncio.sleep(0.3)",
                "",
                "asyncio.run(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, timeout=60, env=env
    )
    assert result.returncode == 0, result.stderr
    pid = _wait_pid_file(pid_file)
    deadline = time.monotonic() + 5.0
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid), f"parent exit left the background group running (pid {pid})"


def _wait_pid_file(path: Path, timeout: float = 10.0) -> int:
    """Read the pid the probe child wrote, waiting for it to appear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text.isdigit():
                return int(text)
        time.sleep(0.05)
    raise AssertionError(f"pid file never appeared: {path}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_process_group_of_returns_none_for_unknown_pid():
    # A pid that cannot be resolved degrades to the direct-child ladder
    # instead of raising out of the tool handler.
    assert process_group_of(None) is None
    assert process_group_of(999_999_999) is None
