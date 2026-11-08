"""Task 0036: headless（``-p``）被拦截时可机读。

单次模式 run 必须总是报告 ``status``（``ok`` | ``blocked`` | ``failed``）：

- 审批/权限门拒绝（react 的 is_error tool_result 拒绝文案）与 plan 只读闸
  （``plan_failed`` 事件）记为 ``blocked``，带 ``blocked_reason``；
- 异常记为 ``failed``（``--json`` 在该路径也照常吐一个 JSON 对象）；
- 默认退出码保持兼容（ok/react 拒绝 → 0，异常与 0034 的 blocked plan → 1）；
  新增 ``--fail-on-blocked`` 在 blocked/failed 时 exit 2；
- 非 JSON 模式在 stderr 打印一行 ``status: ...``。

引擎用 fake agent（脚本化 QueryEngine double，风格同 test_cli_single_prompt.py
与 test_plan_readonly.py）覆盖三种状态、默认/开关退出码与 JSON 字段；分类器
再与真实 ToolExecutor 的拒绝文案对齐，防止标记表漂移。
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import pytest
import typer

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import QueryEngine
from nexuscli.agent.results import GATE_DENIAL_MARKERS, gate_denial_reason
from nexuscli.config import NexusCliConfig
from nexuscli.llm.pricing import CostBreakdown
from nexuscli.tools import get_builtin_tools
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry

# The executor's fail-closed approval denial in single-prompt mode (-p has no
# approval callback). Its full text (with the escape-hatch guidance) is what
# the fake agent scripts and what blocked_reason carries.
_DENIAL_CONTENT = (
    'Tool "write_file" was denied by approval policy. This run has no interactive '
    "approver (single-prompt mode), so no human could be asked. Allow the tool "
    'beforehand with permissions.allow (for example {"allow": ["write_file"]}) in '
    "config.json, or run the interactive REPL for approval prompts."
)

_DELTA_EVENTS = [
    {"type": "text_delta", "text": "Hello"},
    {
        "type": "done",
        "total_turns": 1,
        "total_tokens": 7,
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        "cost": {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
    },
]

# A react run whose write call the gate refused: the run still finishes with
# done — the refusal only shows up as the is_error tool_result content.
_DENIED_REACT_EVENTS = [
    {"type": "tool_call", "name": "write_file", "input": {"path": "out.txt"}},
    {
        "type": "tool_result",
        "name": "write_file",
        "result": _DENIAL_CONTENT,
        "is_error": True,
    },
    {"type": "text_delta", "text": "写入被拒绝，无法继续。"},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 9,
        "usage": {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9},
        "cost": {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
    },
]

# An ordinary tool failure (not a permission outcome) must stay "ok".
_PLAIN_ERROR_REACT_EVENTS = [
    {
        "type": "tool_result",
        "name": "write_file",
        "result": 'Tool "write_file" execution error: disk on fire',
        "is_error": True,
    },
    {"type": "text_delta", "text": "工具执行失败。"},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 9,
        "usage": {"input_tokens": 6, "output_tokens": 3, "total_tokens": 9},
    },
]


class _WriteCapture:
    """stdout replacement recording each write() call separately."""

    encoding = "utf-8"

    def __init__(self):
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        if isinstance(s, bytes):
            raise TypeError("write() argument must be str, not bytes")
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass


class StubQueryEngine:
    """Scripted QueryEngine double reusing the real event aggregation helpers."""

    _complete_from_events = QueryEngine._complete_from_events
    team_complete_async = QueryEngine.team_complete_async

    def __init__(self, events, ask_error: Exception | None = None, **_kwargs):
        self._events = events
        self._ask_error = ask_error

    async def ask(self, message, history=None):
        _ = message, history
        if self._ask_error is not None:
            raise self._ask_error
        for event in self._events:
            yield event

    async def team(self, message, *, worker_mode="react"):
        _ = message, worker_mode
        for event in self._events:
            yield event


def _plan_engine(monkeypatch: pytest.MonkeyPatch, events) -> StubQueryEngine:
    """Engine double whose plan() replays the scripted plan-mode events."""

    async def fake_plan(message):
        _ = message
        for event in events:
            yield event

    engine = StubQueryEngine([])
    engine.plan = fake_plan
    _install_stub_engine(monkeypatch, engine)
    return engine


def _install_stub_engine(monkeypatch: pytest.MonkeyPatch, engine: StubQueryEngine) -> None:
    async def fake_build_registry(config, cwd):
        _ = config, cwd
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: engine)


def _test_config() -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    return config


def _payload_of(capture: _WriteCapture) -> dict[str, Any]:
    return json.loads("".join(capture.writes))


# ---------------------------------------------------------------------------
# JSON fields: ok / blocked / failed
# ---------------------------------------------------------------------------


def test_ok_json_reports_status_and_no_reason(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DELTA_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config(), json_output=True))

    payload = _payload_of(capture)
    assert payload["status"] == "ok"
    assert "blocked_reason" not in payload
    # The pre-0036 contract stays identical on the ok path.
    assert payload["text"] == "Hello"
    assert payload["cost"] == {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}}


def test_react_denial_json_is_blocked_with_reason_and_exits_zero(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DENIED_REACT_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    # Default exit code stays 0: the denial used to pass silently, and only
    # --fail-on-blocked turns the new visibility into a non-zero exit.
    asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config(), json_output=True))

    payload = _payload_of(capture)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == _DENIAL_CONTENT
    assert payload["text"] == "写入被拒绝，无法继续。"


def test_plain_tool_error_json_stays_ok(tmp_path, monkeypatch):
    """An execution error is not a permission outcome — no false "blocked"."""
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_PLAIN_ERROR_REACT_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config(), json_output=True))

    payload = _payload_of(capture)
    assert payload["status"] == "ok"
    assert "blocked_reason" not in payload


def test_exception_json_is_failed_with_error_and_exits_one(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(
        monkeypatch, StubQueryEngine(_DELTA_EVENTS, ask_error=RuntimeError("boom"))
    )
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config(), json_output=True))

    assert excinfo.value.exit_code == 1
    payload = _payload_of(capture)
    assert payload["status"] == "failed"
    assert payload["error"] == "boom"
    assert "blocked_reason" not in payload
    # stdout is still exactly one JSON object even on this path.
    assert "".join(capture.writes) == json.dumps(payload, ensure_ascii=False) + "\n"


_BLOCKED_PLAN_EVENTS = [
    {"type": "text_delta", "text": "计划未完成：1 个任务被 plan 只读闸拒绝。\n\n"},
    {"type": "plan_failed", "plan_id": "plan_1", "blocked_tasks": {"task_1": "拒绝原因"}},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 10,
        "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
    },
]

_STALLED_PLAN_EVENTS = [
    {"type": "text_delta", "text": "计划已停滞。\n\n"},
    {"type": "plan_failed", "plan_id": "plan_1", "blocked_tasks": {}},
    {"type": "done", "total_turns": 1, "total_tokens": 2, "usage": {}},
]

_CLEAN_PLAN_EVENTS = [
    {"type": "text_delta", "text": "计划执行完成。\n\n"},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 10,
        "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
    },
]


def test_plan_blocked_json_is_blocked_with_task_reason(tmp_path, monkeypatch):
    """The plan_failed event's blocked_tasks feed blocked_reason; exit stays 1 (0034)."""
    capture = _WriteCapture()
    _plan_engine(monkeypatch, _BLOCKED_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt("goal", str(tmp_path), _test_config(), mode="plan", json_output=True)
        )

    assert excinfo.value.exit_code == 1
    payload = _payload_of(capture)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "task_1: 拒绝原因"


def test_plan_stall_without_gate_rejections_reports_generic_reason(tmp_path, monkeypatch):
    """plan_failed without blocked_tasks (stalled plan) still says blocked."""
    capture = _WriteCapture()
    _plan_engine(monkeypatch, _STALLED_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt("goal", str(tmp_path), _test_config(), mode="plan", json_output=True)
        )

    assert excinfo.value.exit_code == 1
    payload = _payload_of(capture)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "plan did not complete (tasks blocked or failed)"


def test_plan_ok_json_reports_ok(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _plan_engine(monkeypatch, _CLEAN_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(
        cli._run_prompt("goal", str(tmp_path), _test_config(), mode="plan", json_output=True)
    )

    payload = _payload_of(capture)
    assert payload["status"] == "ok"
    assert "blocked_reason" not in payload


def test_team_ok_json_reports_ok(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DELTA_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(
        cli._run_prompt("hello", str(tmp_path), _test_config(), mode="team", json_output=True)
    )

    payload = _payload_of(capture)
    assert payload["status"] == "ok"
    assert payload["worker_mode"] == "react"


# ---------------------------------------------------------------------------
# Team mode: worker gate denials are forwarded and reported (Task 0036)
# ---------------------------------------------------------------------------


class _TeamDeniedWorkerClient:
    """Scripted team LLM: planner → worker whose write_file the gate refuses.

    The live repro of the 0036 team gap: the worker's fail-closed approval
    denial stays inside the SubAgent fold (its tool_result events never reach
    the outer stream), so the CLI only sees it via the orchestrator's
    forwarded ``gate_denial`` event. Runs against the real QueryEngine →
    AgentOrchestrator → ToolExecutor chain.
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def calculate_cost(self, usage, *, currency="usd"):
        # The _MeasuredLlmClient wrapper delegates calculate_cost
        # unconditionally; zero-token usage means an honest zero cost.
        return CostBreakdown(
            currency=currency,
            input_cache_hit_tokens=0,
            input_cache_miss_tokens=0,
            input_uncategorized_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            input_cache_hit_cost=0.0,
            input_cache_miss_cost=0.0,
            input_uncategorized_cost=0.0,
            output_cost=0.0,
            total_cost=0.0,
        )

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        last = messages[-1]
        body = last.content if isinstance(last.content, str) else ""
        if "Create an execution plan" in body:
            yield {
                "type": "text_delta",
                "text": (
                    '{"steps": [{"id": "a", "description": "Write the report", '
                    '"type": "COMMAND", "dependencies": []}]}'
                ),
            }
            yield {"type": "message_end", "stop_reason": "end_turn"}
        elif "Original task" in body:
            yield {
                "type": "text_delta",
                "text": '{"approved": true, "summary": "ok", "issues": []}',
            }
            yield {"type": "message_end", "stop_reason": "end_turn"}
        elif getattr(last, "role", "") == "tool":
            # The worker saw the denial tool_result and gives up in words —
            # this text alone must not flip the status back to ok.
            yield {"type": "text_delta", "text": "写入被拒绝，无法完成。"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
        else:
            # The worker asks for the tool call the approval gate refuses.
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": '{"path": "out.txt"}'},
                },
            }
            yield {"type": "message_end", "stop_reason": "tool_use"}


def _install_team_denial_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire _run_prompt to the real engine + orchestrator + executor."""

    async def fake_build_registry(config, cwd):
        _ = config, cwd
        return _registry_with_write_tool(), None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(
        cli, "create_llm_client", lambda llm, telemetry_enabled: _TeamDeniedWorkerClient()
    )


def test_team_denial_json_is_blocked_with_reason_and_exits_zero(tmp_path, monkeypatch):
    """``-p --mode team`` with a fail-closed worker denial reports blocked."""
    capture = _WriteCapture()
    _install_team_denial_engine(monkeypatch)
    monkeypatch.setattr(sys, "stdout", capture)

    # No typer.Exit: the default exit code stays 0 (the denial used to pass
    # silently as ok), only the machine-readable status becomes correct.
    asyncio.run(
        cli._run_prompt(
            "write the report",
            str(tmp_path),
            _test_config(),
            mode="team",
            json_output=True,
        )
    )

    payload = _payload_of(capture)
    assert payload["status"] == "blocked"
    assert payload["worker_mode"] == "react"
    assert 'Tool "write_file" was denied by approval policy' in payload["blocked_reason"]


def test_team_denial_fail_on_blocked_exits_two(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_team_denial_engine(monkeypatch)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt(
                "write the report",
                str(tmp_path),
                _test_config(),
                mode="team",
                json_output=True,
                fail_on_blocked=True,
            )
        )

    assert excinfo.value.exit_code == 2
    assert _payload_of(capture)["status"] == "blocked"


# ---------------------------------------------------------------------------
# Exit codes: default vs --fail-on-blocked
# ---------------------------------------------------------------------------


def test_fail_on_blocked_exits_two_on_react_denial(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DENIED_REACT_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt(
                "hello", str(tmp_path), _test_config(), json_output=True, fail_on_blocked=True
            )
        )

    assert excinfo.value.exit_code == 2
    assert _payload_of(capture)["status"] == "blocked"


def test_fail_on_blocked_exits_two_on_exception(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(
        monkeypatch, StubQueryEngine(_DELTA_EVENTS, ask_error=RuntimeError("boom"))
    )
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt(
                "hello", str(tmp_path), _test_config(), json_output=True, fail_on_blocked=True
            )
        )

    assert excinfo.value.exit_code == 2
    assert _payload_of(capture)["status"] == "failed"


def test_fail_on_blocked_exits_two_on_plan_blocked(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _plan_engine(monkeypatch, _BLOCKED_PLAN_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(
            cli._run_prompt(
                "goal",
                str(tmp_path),
                _test_config(),
                mode="plan",
                json_output=True,
                fail_on_blocked=True,
            )
        )

    # --fail-on-blocked upgrades 0034's exit 1 to 2.
    assert excinfo.value.exit_code == 2


def test_fail_on_blocked_keeps_exit_zero_on_ok(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DELTA_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(
        cli._run_prompt(
            "hello", str(tmp_path), _test_config(), json_output=True, fail_on_blocked=True
        )
    )

    assert _payload_of(capture)["status"] == "ok"


# ---------------------------------------------------------------------------
# Non-JSON mode: one status line on stderr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("events", "ask_error", "expected_prefix", "exit_code"),
    [
        (_DELTA_EVENTS, None, "status: ok", None),
        (_DENIED_REACT_EVENTS, None, "status: blocked", None),
        (_DELTA_EVENTS, RuntimeError("boom"), "status: failed (boom)", 1),
    ],
)
def test_non_json_prints_one_stderr_status_line(
    tmp_path, monkeypatch, capsys, events, ask_error, expected_prefix, exit_code
):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(events, ask_error=ask_error))
    monkeypatch.setattr(sys, "stdout", capture)

    if exit_code is None:
        asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config()))
    else:
        with pytest.raises(typer.Exit) as excinfo:
            asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config()))
        assert excinfo.value.exit_code == exit_code

    status_lines = [
        line for line in capsys.readouterr().err.splitlines() if line.startswith("status: ")
    ]
    assert len(status_lines) == 1
    assert status_lines[0] == expected_prefix or status_lines[0].startswith(expected_prefix)


def test_non_json_blocked_status_line_carries_reason(tmp_path, monkeypatch, capsys):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DENIED_REACT_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config()))

    err = capsys.readouterr().err
    assert "status: blocked" in err
    assert 'Tool "write_file" was denied by approval policy' in err


def test_non_json_ok_status_line_stays_off_stdout(tmp_path, monkeypatch, capsys):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, StubQueryEngine(_DELTA_EVENTS))
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(cli._run_prompt("hello", str(tmp_path), _test_config()))

    # stdout keeps the plain answer contract; the status rides stderr only.
    assert "".join(capture.writes) == "Hello\n"
    assert "status: ok" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# CLI wiring: --fail-on-blocked through main()
# ---------------------------------------------------------------------------


def _stub_cli_engine(monkeypatch: pytest.MonkeyPatch, events) -> None:
    monkeypatch.setenv("NEXUSCLI_API_KEY", "unit-test-value")

    async def fake_build_registry(config, cwd):
        _ = config, cwd
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: StubQueryEngine(events))


def test_cli_fail_on_blocked_end_to_end(tmp_path, monkeypatch):
    """``-p --json --fail-on-blocked`` exits 2 through the real CLI entrypoint."""
    from typer.testing import CliRunner

    _stub_cli_engine(monkeypatch, _DENIED_REACT_EVENTS)

    base = ["-p", "hello", "--cwd", str(tmp_path), "--json"]
    without_flag = CliRunner().invoke(cli.app, base)
    assert without_flag.exit_code == 0
    assert json.loads(without_flag.output)["status"] == "blocked"

    with_flag = CliRunner().invoke(cli.app, [*base, "--fail-on-blocked"])
    assert with_flag.exit_code == 2


def test_cli_fail_on_blocked_end_to_end_ok(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    _stub_cli_engine(monkeypatch, _DELTA_EVENTS)

    result = CliRunner().invoke(
        cli.app, ["-p", "hello", "--cwd", str(tmp_path), "--fail-on-blocked"]
    )
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Classifier sync with the real executor
# ---------------------------------------------------------------------------


def _registry_with_write_tool() -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(payload, _context):
        _ = payload
        return ToolResult("written")

    registry.register(
        Tool(
            name="write_file",
            description="fake write tool",
            parameters=object_schema({"path": {"type": "string"}}),
            handler=handler,
            is_read_only=False,
            requires_approval=True,
        )
    )
    return registry


_WRITE_CALL = [
    {
        "id": "call-1",
        "type": "function",
        "function": {"name": "write_file", "arguments": '{"path": "out.txt"}'},
    }
]


def test_gate_denial_reason_matches_executor_approval_denial(tmp_path):
    """The marker list stays in sync with the executor's real denial texts."""
    config = NexusCliConfig()
    config.features.audit_log = False
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    results = asyncio.run(
        ToolExecutor(_registry_with_write_tool()).execute_all(_WRITE_CALL, context)
    )

    assert len(results) == 1
    assert results[0].is_error
    assert gate_denial_reason(results[0].content) == results[0].content
    assert "was denied by approval policy" in results[0].content


def test_gate_denial_reason_matches_executor_plan_gate(tmp_path):
    config = NexusCliConfig()
    config.policy.plan_mode = True
    config.features.audit_log = False
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    results = asyncio.run(
        ToolExecutor(_registry_with_write_tool()).execute_all(_WRITE_CALL, context)
    )

    assert gate_denial_reason(results[0].content) == results[0].content
    assert "is not allowed in plan mode" in results[0].content


# The real bash handler's write-scope guard produces the two remaining denial
# texts (tools/builtins.py _reject_outside_write_scope). The tilde redirect
# target is unresolvable to the static analysis, so the verdict fails closed
# to "outside" no matter where the redirected HOME points.
_BASH_OUT_OF_SCOPE_CALL = [
    {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "bash",
            "arguments": '{"command": "echo probe > ~/nexuscli-scope-probe.txt"}',
        },
    }
]


def _registry_with_real_bash_tool() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        if tool.name == "bash":
            registry.register(tool)
            break
    return registry


def _approve_gate_deny_write_scope(request: dict) -> str:
    """Approve the executor's tool gate, deny the guard's write-scope prompt.

    The two prompts differ in their description: the executor's gate request
    carries the tool description, the guard's carries the static-analysis
    reason.
    """

    if "Static analysis found a write target" in str(request.get("description") or ""):
        return "deny"
    return "approve"


def test_gate_denial_reason_matches_bash_write_scope_fail_closed(tmp_path):
    """hitl_mode=never 且无回调：写域守卫 fail closed 的拒绝也归为 blocked。"""
    config = NexusCliConfig()
    config.features.audit_log = False
    config.policy.hitl_mode = "never"
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=None)
    results = asyncio.run(
        ToolExecutor(_registry_with_real_bash_tool()).execute_all(_BASH_OUT_OF_SCOPE_CALL, context)
    )

    assert len(results) == 1
    assert results[0].is_error
    assert "Command rejected." in results[0].content
    assert "no approval channel is available" in results[0].content
    assert gate_denial_reason(results[0].content) == results[0].content


def test_gate_denial_reason_matches_bash_write_scope_approval_denial(tmp_path):
    """auto 模式回调在写域提示上 deny：写域审批拒绝文案也归为 blocked。"""
    config = NexusCliConfig()
    config.features.audit_log = False
    context = ToolContext(
        cwd=str(tmp_path), config=config, approval_callback=_approve_gate_deny_write_scope
    )
    results = asyncio.run(
        ToolExecutor(_registry_with_real_bash_tool()).execute_all(_BASH_OUT_OF_SCOPE_CALL, context)
    )

    assert len(results) == 1
    assert results[0].is_error
    assert "Command rejected by write-scope approval." in results[0].content
    assert gate_denial_reason(results[0].content) == results[0].content


def test_gate_denial_reason_ignores_plain_failures():
    assert gate_denial_reason('Tool "x" execution error: boom') is None
    assert gate_denial_reason("") is None
    assert gate_denial_reason("ok result") is None
    # Every marker classifies its own text — guards against typos in the table.
    for marker in GATE_DENIAL_MARKERS:
        assert gate_denial_reason(f"pre {marker} post") is not None
