"""Tests for background subagent tasks: immediate task id, result cache, notifications.

Stubs only — no real LLM. This file carries its own copies of the FakeClient
and helper fixtures (copied verbatim from tests/test_subagent.py and
tests/test_background.py) and never imports across test files. Background
scenarios run inside a single ``asyncio.run`` so the wrapper tasks stay bound
to a live loop for the whole scenario and always reach a terminal state
before the loop closes.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from nexuscli.agent.bg_tasks import bg_subagent_registry
from nexuscli.agent.subagent import BUILTIN_AGENTS
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import _announce_bg_subagents
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.background import background_registry
from nexuscli.tools.base import ToolContext


def _use_fake_llm(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    # **_kwargs: production call sites pass telemetry_enabled= (factory seam).
    monkeypatch.setattr("nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: client)


def _test_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


def _tool_call_event(name: str, arguments: str, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "type": "tool_call_delta",
        "tool_call": {
            "index": 0,
            "id": call_id,
            "function": {"name": name, "arguments": arguments},
        },
    }


class FakeClient:
    """Minimal LLM stub: replays one scripted streaming-event turn per chat call."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, *turns: list[dict[str, Any]]):
        self.turns = list(turns)
        self.calls: list[int] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls.append(len(messages))
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        for event in turn:
            yield event


@pytest.fixture
def background_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home.

    Both registries resolve their on-disk directory through Path.home(). On
    Windows Path.home() reads USERPROFILE (not HOME), so both environment
    variables are redirected as well, mirroring tests/test_background.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@pytest.fixture(autouse=True)
def _clean_bg_subagent_registry():
    """The registry is a process-wide singleton: drop all state after each test."""
    yield
    bg_subagent_registry._tasks.clear()
    bg_subagent_registry._results.clear()
    bg_subagent_registry._notified.clear()


def _builtin_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


async def _wait_terminal(task_id: str, timeout: float = 10.0) -> str:
    """Poll until the background subagent reaches a terminal state (same loop)."""
    deadline = time.monotonic() + timeout
    status = bg_subagent_registry.status(task_id)
    while status == "running":
        if time.monotonic() > deadline:
            raise AssertionError(f"background subagent {task_id} never reached a terminal state")
        await asyncio.sleep(0.01)
        status = bg_subagent_registry.status(task_id)
    return status


def _report_file(home: Path, task_id: str) -> Path:
    return home / ".nexuscli" / "bg-subagents" / f"{task_id}.md"


def _task_output_payload(raw_id: str) -> dict[str, Any]:
    """Build the task_output tool payload for *raw_id*."""
    return {"task_id": raw_id}


# ---------------------------------------------------------------------------
# task tool with run_in_background
# ---------------------------------------------------------------------------


def test_task_tool_background_returns_task_id_immediately(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "中间草稿"},
            _tool_call_event("list_dir", "{}"),
            {"type": "message_end", "stop_reason": "tool_use"},
        ],
        [
            {"type": "text_delta", "text": "最终报告：目录结构如下"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    registry = _builtin_registry()
    task = registry.get("task")
    assert task is not None

    async def scenario():
        result = await task.execute(
            {
                "description": "调研",
                "prompt": "任务指令",
                "agent_type": "general-purpose",
                "run_in_background": True,
            },
            ToolContext(cwd=str(tmp_path), config=config),
        )
        match = re.search(r"Started background subagent task ([0-9a-f]{12})", result.content)
        assert match, result.content
        task_id = match.group(1)
        observed_status = bg_subagent_registry.status(task_id)
        entry = bg_subagent_registry.get(task_id)
        final_status = await _wait_terminal(task_id)
        return result, task_id, observed_status, entry, final_status

    result, task_id, observed_status, entry, final_status = asyncio.run(scenario())

    assert not result.is_error
    assert observed_status == "running"  # execute returned without awaiting the subagent
    assert entry is not None
    assert str(entry.report_path) in result.content
    assert final_status == "completed"
    finished = bg_subagent_registry.result(task_id)
    assert finished is not None
    assert finished.report == "最终报告：目录结构如下"
    report_file = _report_file(background_home, task_id)
    assert report_file.exists()
    assert "最终报告：目录结构如下" in report_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Registry: result cache, report file, drain idempotence
# ---------------------------------------------------------------------------


def test_bg_registry_result_cache_and_report_file(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "后台报告正文"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "缓存测试指令",
            context,
            agent_type="general-purpose",
            description="缓存测试",
        )
        status = await _wait_terminal(entry.task_id)
        return entry, status

    entry, status = asyncio.run(scenario())

    assert status == "completed"
    finished = bg_subagent_registry.result(entry.task_id)
    assert finished is not None
    report_file = _report_file(background_home, entry.task_id)
    assert report_file.exists()
    assert finished.report == report_file.read_text(encoding="utf-8")

    notes = bg_subagent_registry.drain_notifications()
    assert len(notes) == 1
    note = notes[0]
    assert note["task_id"] == entry.task_id
    assert note["agent_type"] == "general-purpose"
    assert note["description"] == "缓存测试"
    assert note["status"] == "completed"
    assert note["report_path"] == str(entry.report_path)
    assert note["error"] == ""
    assert bg_subagent_registry.drain_notifications() == []  # drain is idempotent


def test_bg_registry_captures_failure_without_escaping(tmp_path, background_home, monkeypatch):
    class _BoomClient:
        model_name = "fake-model"
        provider_name = "fake-provider"
        max_context_window = 1000

        async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
            raise RuntimeError("boom")
            yield  # makes chat an async generator like FakeClient

    monkeypatch.setattr(
        "nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: _BoomClient()
    )
    config = _test_config(tmp_path)

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "失败指令",
            context,
            agent_type="general-purpose",
            description="失败任务",
        )
        status = await _wait_terminal(entry.task_id)
        return entry, status

    # asyncio.run itself must not raise: the failure never escapes _run.
    entry, status = asyncio.run(scenario())

    assert status == "failed"
    finished = bg_subagent_registry.result(entry.task_id)
    assert finished is not None
    assert finished.report == ""
    assert "boom" in finished.error
    report_file = _report_file(background_home, entry.task_id)
    assert report_file.exists()
    assert "failed" in report_file.read_text(encoding="utf-8")
    assert "boom" in report_file.read_text(encoding="utf-8")


def test_bg_depth_limit_becomes_failed_result(tmp_path, background_home):
    config = _test_config(tmp_path)

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config, subagent_depth=1)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "越权委派",
            context,
            agent_type="general-purpose",
            description="深度越限",
        )
        status = await _wait_terminal(entry.task_id)
        return entry, status

    entry, status = asyncio.run(scenario())

    assert status == "failed"
    finished = bg_subagent_registry.result(entry.task_id)
    assert finished is not None
    assert "depth limit reached (1)" in finished.error


# ---------------------------------------------------------------------------
# task_output reads background subagent results
# ---------------------------------------------------------------------------


def test_task_output_reads_bg_subagent_result(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "任务产出报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    registry = _builtin_registry()
    tool = registry.get("task_output")
    assert tool is not None

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "读取指令",
            context,
            agent_type="general-purpose",
            description="结果读取",
        )
        await _wait_terminal(entry.task_id)
        result = await tool.handler(_task_output_payload(entry.task_id), context)
        unknown = await tool.handler(_task_output_payload("deadbeefdead"), context)
        return result, unknown

    result, unknown = asyncio.run(scenario())

    assert not result.is_error
    assert "[completed]" in result.content
    assert "report_path=" in result.content
    assert "任务产出报告" in result.content
    # The bash-side unknown-id semantics must not regress: an id unknown to
    # both registries still returns the established error.
    assert unknown.is_error
    assert 'Unknown background task id "deadbeefdead".' in unknown.content


# ---------------------------------------------------------------------------
# End-of-turn REPL announcement
# ---------------------------------------------------------------------------


def test_repl_announce_prints_batch_summary_once(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "批量报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config)
        entries = [
            bg_subagent_registry.start(
                BUILTIN_AGENTS["general-purpose"],
                f"指令{label}",
                context,
                agent_type="general-purpose",
                description=f"调研{label}",
            )
            for label in ("甲", "乙")
        ]
        for entry in entries:
            await _wait_terminal(entry.task_id)
        return entries

    entries = asyncio.run(scenario())

    console = Console(record=True, width=400)
    _announce_bg_subagents(console)
    text = console.export_text()
    assert "completed general-purpose" in text
    assert "调研甲" in text
    assert str(entries[0].report_path) in text
    assert "调研乙" in text
    assert str(entries[1].report_path) in text

    before = console.export_text()
    _announce_bg_subagents(console)  # drain is idempotent: no new output
    assert console.export_text() == before

    # Zero-noise anchor: nothing finished means nothing printed.
    empty_console = Console(record=True)
    _announce_bg_subagents(empty_console)
    assert empty_console.export_text() == ""


# ---------------------------------------------------------------------------
# Approval chain passthrough
# ---------------------------------------------------------------------------


def test_approval_callback_still_fires_for_bg_subagent(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "先执行命令"},
            _tool_call_event("bash", '{"command": "echo delegated"}'),
            {"type": "message_end", "stop_reason": "tool_use"},
        ],
        [
            {"type": "text_delta", "text": "命令被拒绝后的子代理报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    requests: list[dict[str, Any]] = []

    async def callback(request: dict[str, Any]) -> str:
        requests.append(request)
        return "deny"

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "运行命令",
            context,
            agent_type="general-purpose",
            description="审批透传",
        )
        status = await _wait_terminal(entry.task_id)
        return entry, status

    entry, status = asyncio.run(scenario())

    assert status == "completed"
    assert [request["tool_name"] for request in requests] == ["bash"]
    finished = bg_subagent_registry.result(entry.task_id)
    assert finished is not None
    assert finished.report == "命令被拒绝后的子代理报告"


# ---------------------------------------------------------------------------
# The bash background registry stays untouched
# ---------------------------------------------------------------------------


def test_background_bash_registry_untouched(tmp_path, background_home, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "并行子代理报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    # Child script sleeps long enough to be observable, then stopped below
    # before the loop closes (same convention as tests/test_background.py).
    script = tmp_path / "bg_child.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    command = f'"{sys.executable}" "{script}"'

    async def scenario():
        bash_task = await background_registry.start(command, str(tmp_path))
        context = ToolContext(cwd=str(tmp_path), config=config)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "并行指令",
            context,
            agent_type="general-purpose",
            description="并行子代理",
        )
        status = await _wait_terminal(entry.task_id)
        stop_status = await background_registry.stop(bash_task.task_id)
        return bash_task, entry, status, stop_status

    bash_task, entry, status, stop_status = asyncio.run(scenario())

    assert status == "completed"
    # The bash task lives in the bash registry with its usual status/stop
    # semantics; stopping it still reports "stopped".
    assert background_registry.get(bash_task.task_id) is not None
    assert stop_status == "stopped"
    assert background_registry.status(bash_task.task_id) == "stopped"
    # The two registries are mutually invisible.
    assert bash_task.task_id not in bg_subagent_registry._tasks
    assert entry.task_id not in background_registry._tasks
    # The report directories never mix: bash logs under background/,
    # subagent reports under bg-subagents/.
    bash_log = background_home / ".nexuscli" / "background" / f"{bash_task.task_id}.log"
    bg_report = background_home / ".nexuscli" / "bg-subagents" / f"{entry.task_id}.md"
    assert bash_log.exists()
    assert bg_report.exists()
    assert bg_report.read_text(encoding="utf-8") == "并行子代理报告"
