"""Tests for the run-scoped approval tier ("r" / approve_run).

The fourth approval tier: a human grant that authorizes one tool for the rest
of the agent run — between single-shot approve (y) and the session-wide auto
mode (a). Run scope only applies where the HITL prompt would have been the
deciding gate: ask rules and hook ask hints keep prompting, deny rules keep
blocking, and the audit names ``run-scope`` instead of ``hitl`` when the grant
bypassed the prompt.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.approval import _approval_prompt
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Keep audit writes out of the real ``~/.nexuscli``.

    Several executor cases here use ``NexusCliConfig()`` defaults, whose audit
    log lands in ``~/.nexuscli/audit.jsonl`` — without HOME isolation each run
    would append to the real user home (same convention as
    ``tests/test_cli.py::_isolate_home`` and ``tests/test_permissions.py``).
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _registry_with_tools(executed: list[str], names: list[str] | None = None) -> ToolRegistry:
    registry = ToolRegistry()

    def make_handler(tool_name: str):
        async def handler(payload, _context):
            executed.append(f"{tool_name}:{payload['value']}")
            return ToolResult(f"ran {tool_name}")

        return handler

    for name in names or ["mutate"]:
        registry.register(
            Tool(
                name=name,
                description=f"Mutate test state ({name})",
                parameters=object_schema({"value": {"type": "string"}}, ["value"]),
                required_keys=["value"],
                handler=make_handler(name),
                is_read_only=False,
                requires_approval=True,
            )
        )
    return registry


def _run_calls(
    registry: ToolRegistry,
    context: ToolContext,
    names: list[str] | None = None,
) -> list[ToolResult]:
    executor = ToolExecutor(registry)
    calls = [
        {"id": f"call-{i}", "name": name, "arguments": {"value": "ok"}}
        for i, name in enumerate(names or ["mutate"])
    ]
    return asyncio.run(executor.execute_all(calls, context))


# ---------------------------------------------------------------------------
# Approval prompt: the "r" option
# ---------------------------------------------------------------------------


class _FakeStdin:
    def isatty(self) -> bool:
        return True


def _ask_prompt(answer: str, monkeypatch) -> tuple[str, list[str]]:
    """Drive _approval_prompt with a canned answer; return (decision, set-calls)."""
    mode_calls: list[str] = []

    class _Mode:
        def set(self, mode: str) -> str:
            mode_calls.append(mode)
            return mode

    monkeypatch.setattr(sys, "stdin", _FakeStdin())

    import rich.prompt

    monkeypatch.setattr(
        rich.prompt.Prompt,
        "ask",
        classmethod(
            lambda _cls, _prompt, choices=None, default=None: answer  # noqa: ARG005
        ),
    )

    decision = _approval_prompt(
        {"tool_name": "bash", "danger_level": "high", "input": {"command": "rm -rf /"}},
        console=_FakeConsole(),
        permission_mode=_Mode(),
    )
    return decision, mode_calls


class _FakeConsole:
    def print(self, *_args, **_kwargs) -> None:
        return None


def test_approval_prompt_r_returns_approve_run(monkeypatch) -> None:
    decision, mode_calls = _ask_prompt("r", monkeypatch)
    assert decision == "approve_run"
    assert mode_calls == []


def test_approval_prompt_keeps_existing_choices(monkeypatch) -> None:
    assert _ask_prompt("y", monkeypatch)[0] == "approve"
    assert _ask_prompt("n", monkeypatch)[0] == "deny"
    assert _ask_prompt("s", monkeypatch)[0] == "skip"
    decision, mode_calls = _ask_prompt("a", monkeypatch)
    assert decision == "approve"
    assert mode_calls == ["auto"]


# ---------------------------------------------------------------------------
# Executor: run-scoped grant behavior
# ---------------------------------------------------------------------------


def test_approve_run_grants_same_tool_without_prompt(tmp_path) -> None:
    config = NexusCliConfig()
    prompts: list[dict] = []

    def callback(request):
        prompts.append(request)
        # First call: the human presses "r"; any later call must not be asked.
        return "approve_run"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    results = _run_calls(_registry_with_tools(executed), context, ["mutate", "mutate"])
    assert not any(r.is_error for r in results)
    assert executed == ["mutate:ok", "mutate:ok"]
    assert len(prompts) == 1
    assert "mutate" in context.run_scope_approved


def test_run_scope_grant_does_not_extend_to_other_tools(tmp_path) -> None:
    config = NexusCliConfig()
    prompts: list[str] = []

    def callback(request):
        prompts.append(request["tool_name"])
        return "approve_run"

    executed: list[str] = []
    registry = _registry_with_tools(executed, ["mutate", "deploy"])
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    results = _run_calls(registry, context, ["mutate", "mutate", "deploy"])
    assert not any(r.is_error for r in results)
    assert prompts == ["mutate", "deploy"]
    assert executed == ["mutate:ok", "mutate:ok", "deploy:ok"]


def test_deny_rule_still_blocks_run_approved_tool(tmp_path) -> None:
    config = NexusCliConfig()
    config.permissions.deny = ["mutate"]

    def callback(_request):
        raise AssertionError("approval callback must not fire for a denied rule")

    executed: list[str] = []
    context = ToolContext(
        cwd=str(tmp_path),
        config=config,
        approval_callback=callback,
        run_scope_approved={"mutate"},
    )
    result = _run_calls(_registry_with_tools(executed), context)[0]
    assert result.is_error
    assert "denied by permission rule: mutate" in result.content
    assert executed == []


def test_ask_rule_still_prompts_run_approved_tool(tmp_path) -> None:
    config = NexusCliConfig()
    config.permissions.ask = ["mutate"]
    prompts: list[str] = []

    def callback(_request):
        prompts.append("asked")
        return "approve"

    executed: list[str] = []
    context = ToolContext(
        cwd=str(tmp_path),
        config=config,
        approval_callback=callback,
        run_scope_approved={"mutate"},
    )
    results = _run_calls(_registry_with_tools(executed), context, ["mutate", "mutate"])
    assert not any(r.is_error for r in results)
    # The run grant never overrides an ask rule: both calls prompt.
    assert prompts == ["asked", "asked"]
    assert executed == ["mutate:ok", "mutate:ok"]


def test_run_scope_bypass_is_audited_as_run_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = NexusCliConfig()
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    prompts: list[str] = []

    def callback(_request):
        prompts.append("asked")
        return "approve_run"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    results = _run_calls(_registry_with_tools(executed), context, ["mutate", "mutate"])
    assert not any(r.is_error for r in results)
    assert len(prompts) == 1
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    approvers = [json.loads(line)["approver"] for line in lines]
    # First call: a human decided ("hitl"). Second call: the run grant
    # bypassed the prompt, so it must not be claimed as a fresh human
    # approval ("run-scope", named after "readonly-rule").
    assert approvers == ["hitl", "run-scope"]


def test_new_tool_context_clears_run_scope(tmp_path) -> None:
    """Run semantics = one agent run: a fresh ToolContext starts empty."""
    config = NexusCliConfig()

    def make_callback() -> tuple[object, list[str]]:
        seen: list[str] = []

        def callback(_request):
            seen.append("asked")
            return "approve_run"

        return callback, seen

    first_callback, first_seen = make_callback()
    executed: list[str] = []
    first_context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=first_callback)
    _run_calls(_registry_with_tools(executed), first_context, ["mutate", "mutate"])
    assert first_seen == ["asked"]
    assert first_context.run_scope_approved == {"mutate"}

    # A new agent run builds a fresh ToolContext — the grant is gone and the
    # tool must be prompted again.
    second_callback, second_seen = make_callback()
    second_context = ToolContext(
        cwd=str(tmp_path), config=config, approval_callback=second_callback
    )
    _run_calls(_registry_with_tools(executed), second_context, ["mutate"])
    assert second_seen == ["asked"]
    assert second_context.run_scope_approved == {"mutate"}


def test_clear_run_scope_drops_existing_grants(tmp_path) -> None:
    config = NexusCliConfig()
    prompts: list[str] = []

    def callback(_request):
        prompts.append("asked")
        return "approve_run"

    executed: list[str] = []
    context = ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback)
    _run_calls(_registry_with_tools(executed), context, ["mutate", "mutate"])
    assert prompts == ["asked"]
    assert context.run_scope_approved == {"mutate"}

    # Explicit clear (new user turn) → the tool prompts again.
    context.clear_run_scope()
    _run_calls(_registry_with_tools(executed), context, ["mutate"])
    assert prompts == ["asked", "asked"]
    assert context.run_scope_approved == {"mutate"}
