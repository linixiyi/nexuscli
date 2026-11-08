"""Task 0038: 固定 prompt/tool 开销可诊断。

- Agent react 循环在每次 LLM 请求前记录 ``system_chars`` / ``tools_chars`` /
  ``history_chars`` / ``tool_count`` 与 ``estimated_tokens = ceil(chars/4)``，
  存在 ``Agent.last_prompt_breakdown``（最后一次请求的快照）。刻意**不进事件流**：
  react 与 ``query()`` 的事件必须逐键相等（tests/test_agent_overflow_parity.py）；
- ``-p --json`` 的 ``usage.prompt_breakdown`` 由 CLI 在自己的 llm client 边界
  （``cli._MeasuredLlmClient`` 透明包装）测同一批 chat() 调用得到，只对 react
  模式输出；没测到请求（plan/team、engine double）保持旧契约不添加键；
- ``agent.prompt_profile``（默认 full）解析：lean 只保留核心工具 schema，
  ``tool_count`` 与 ``tools_chars`` 下降；非法值回落 full，默认行为不变；
- ``doctor --token-budget`` 打印当前配置的基线（profile、窗口、max_tokens、
  空 history 的固定开销）。

fake LLM 风格沿用 tests/test_agent_turns.py（直构 Agent）；CLI payload 用
StubQueryEngine（风格同 tests/test_headless_status.py）。
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from typing import Any

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import Agent
from nexuscli.agent.agent import prompt_breakdown
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import Tool, ToolResult, object_schema
from nexuscli.tools.registry import LEAN_CORE_TOOLS
from nexuscli.types import Message

# ---------------------------------------------------------------------------
# Shared doubles (风格同 test_agent_turns.py 的 fake LLM)
# ---------------------------------------------------------------------------


class _NaturalEndClient:
    """One request, no tool call; records what went on the wire."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.requests.append(
            {"messages": list(messages), "tools": list(tools), "system_prompt": system_prompt}
        )
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _ToolThenAnswerClient:
    """First request calls a real tool, second answers — two recorded requests."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.requests.append(
            {"messages": list(messages), "tools": list(tools), "system_prompt": system_prompt}
        )
        if len(self.requests) == 1:
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": 0,
                    "id": "call_1",
                    "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                },
            }
            yield {"type": "message_end", "stop_reason": "tool_use"}
        else:
            yield {"type": "text_delta", "text": "all done"}
            yield {"type": "message_end", "stop_reason": "end_turn"}


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()

    async def handler(payload, _context):
        _ = payload
        return ToolResult("ECHO-BODY")

    registry.register(
        Tool(
            name="echo",
            description="fake echo tool",
            parameters=object_schema({"text": {"type": "string"}}),
            handler=handler,
        )
    )
    return registry


def _hermetic_config() -> NexusCliConfig:
    """Direct-construction config: the recall/skill/compression side channels
    are off so the breakdown numbers stay deterministic."""
    config = NexusCliConfig()
    config.llm.api_key = "test-key"
    config.features.context_compression = False
    config.features.skill = False
    config.features.memory = False
    config.features.audit_log = False
    return config


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


# ---------------------------------------------------------------------------
# prompt_breakdown helper: fields, totals, ceil
# ---------------------------------------------------------------------------


def test_prompt_breakdown_fields_and_totals():
    system = "abcd" * 2  # 8 chars
    tools = [{"type": "function", "function": {"name": "a", "parameters": {}}}]
    tools_chars = len(json.dumps(tools, ensure_ascii=False))

    breakdown = prompt_breakdown(
        system_prompt=system,
        tool_definitions=tools,
        messages=[Message(role="user", content="x" * 10)],
    )

    assert breakdown["system_chars"] == 8
    assert breakdown["tools_chars"] == tools_chars
    assert breakdown["history_chars"] == 10
    assert breakdown["tool_count"] == 1
    assert breakdown["estimated_tokens"] == math.ceil((8 + tools_chars + 10) / 4)


def test_prompt_breakdown_empty_history():
    breakdown = prompt_breakdown(system_prompt="abc", tool_definitions=[], messages=[])
    # Even an empty tool list rides the request as "[]" (2 chars), and the
    # /4 estimate rounds up: 5 chars → 2 tokens.
    assert breakdown == {
        "system_chars": 3,
        "tools_chars": 2,
        "history_chars": 0,
        "tool_count": 0,
        "estimated_tokens": 2,  # ceil(5 / 4)
    }


def test_prompt_breakdown_counts_image_lists_and_tool_calls():
    call = [{"id": "call_1", "function": {"name": "echo", "arguments": "{}"}}]
    messages = [
        Message(role="user", content=[{"type": "image_url", "url": "data:image/png;base64,xx"}]),
        Message(role="assistant", content="", tool_calls=call),
    ]

    breakdown = prompt_breakdown(system_prompt="", tool_definitions=[], messages=messages)

    assert breakdown["history_chars"] == (
        len(json.dumps(messages[0].content, ensure_ascii=False))
        + len(json.dumps(call, ensure_ascii=False))
    )


# ---------------------------------------------------------------------------
# Agent react loop: record before every request, done event carries the last
# ---------------------------------------------------------------------------


def test_react_agent_records_breakdown_of_last_request():
    client = _ToolThenAnswerClient()
    agent = Agent(
        llm_client=client,
        tool_registry=_echo_registry(),
        config=_hermetic_config(),
        cwd="/tmp",
    )

    events = asyncio.run(_collect(agent, "hi"))
    done = events[-1]
    assert done["type"] == "done"
    # The breakdown does NOT ride the event stream: react and query() must
    # stay event-equal (test_agent_overflow_parity). It lives on the agent.
    assert "prompt_breakdown" not in done

    breakdown = agent.last_prompt_breakdown
    assert set(breakdown) == {
        "system_chars",
        "tools_chars",
        "history_chars",
        "tool_count",
        "estimated_tokens",
    }
    # The record is the LAST request's snapshot, cross-checked against what
    # actually went on the wire.
    last = client.requests[-1]
    assert breakdown["tool_count"] == len(last["tools"])
    assert breakdown["tools_chars"] == len(json.dumps(last["tools"], ensure_ascii=False))
    assert breakdown["system_chars"] == len(last["system_prompt"])
    assert breakdown["history_chars"] > len("hi")  # history grew past turn 1
    assert breakdown["estimated_tokens"] == math.ceil(
        (breakdown["system_chars"] + breakdown["tools_chars"] + breakdown["history_chars"]) / 4
    )
    # Two requests happened (tool turn + answer turn): the record is
    # refreshed before every request, not computed once at run start.
    assert len(client.requests) == 2


def test_full_profile_advertises_whole_registry():
    config = _hermetic_config()
    config.agent.prompt_profile = "full"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    client = _NaturalEndClient()
    agent = Agent(llm_client=client, tool_registry=registry, config=config, cwd="/tmp")

    asyncio.run(_collect(agent, "hi"))
    breakdown = agent.last_prompt_breakdown

    assert breakdown["tool_count"] == len(registry.list_names())
    assert breakdown["tool_count"] == len(client.requests[-1]["tools"])


def test_lean_profile_shrinks_tool_count_and_chars():
    config = _hermetic_config()
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())

    full_client = _NaturalEndClient()
    config.agent.prompt_profile = "full"
    full_agent = Agent(llm_client=full_client, tool_registry=registry, config=config, cwd="/tmp")
    asyncio.run(_collect(full_agent, "hi"))

    lean_client = _NaturalEndClient()
    config.agent.prompt_profile = "lean"
    lean_agent = Agent(llm_client=lean_client, tool_registry=registry, config=config, cwd="/tmp")
    asyncio.run(_collect(lean_agent, "hi"))

    full = full_agent.last_prompt_breakdown
    lean = lean_agent.last_prompt_breakdown
    # The lean run keeps only the core tools that exist in this registry, and
    # the drop shows in both the count and the serialized schema size.
    expected_lean = sorted(set(registry.list_names()) & LEAN_CORE_TOOLS)
    assert expected_lean, "builtin registry must carry some core tools"
    assert lean["tool_count"] == len(expected_lean)
    assert lean["tool_count"] < full["tool_count"]
    assert lean["tools_chars"] < full["tools_chars"]
    # The profile only narrows the tool schemas (Task 0038 wording): the
    # system prompt keeps advertising the whole registry, so it is unchanged.
    assert lean["system_chars"] == full["system_chars"]
    assert lean["estimated_tokens"] < full["estimated_tokens"]


def test_registry_definitions_lean_filter():
    registry = _echo_registry()  # only "echo" — not a core tool

    full = registry.definitions()
    lean = registry.definitions(profile="lean")

    assert [item["function"]["name"] for item in full] == ["echo"]
    assert lean == []  # the core-only filter keeps nothing here
    # Unknown profiles are full, not lean.
    assert registry.definitions(profile="bogus") == full
    assert registry.definitions() == full  # default stays "full"


def test_agent_prompt_profile_config_parsing(tmp_path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".nexuscli").mkdir(parents=True)
    (project / ".nexuscli").mkdir(parents=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)

    def _load(section: dict[str, Any] | None) -> NexusCliConfig:
        config_path = project / ".nexuscli" / "config.json"
        if section is None:
            config_path.unlink(missing_ok=True)
        else:
            config_path.write_text(json.dumps({"agent": section}), encoding="utf-8")
        return load_config(project_root=project)

    # Default: full — the default behaviour is unchanged.
    assert _load(None).agent.prompt_profile == "full"
    assert _load({"prompt_profile": "lean"}).agent.prompt_profile == "lean"
    # Typos / wrong types fall back to full instead of failing the CLI start.
    assert _load({"prompt_profile": "Lean"}).agent.prompt_profile == "full"
    assert _load({"prompt_profile": 123}).agent.prompt_profile == "full"
    # Unknown keys are still dropped silently.
    config = _load({"prompt_profile": "lean", "bogus": True})
    assert config.agent.prompt_profile == "lean"


# ---------------------------------------------------------------------------
# -p --json: usage.prompt_breakdown（CLI 在 llm client 边界测量）
# ---------------------------------------------------------------------------


_DONE_EVENTS = [
    {"type": "text_delta", "text": "Hello"},
    {
        "type": "done",
        "total_turns": 1,
        "total_tokens": 7,
        "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
    },
]


class _ChatRecordingInnerClient:
    """Fake provider client: chat() yields a minimal stream and records calls."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000
    price_profile = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools, *, system_prompt):
        self.calls.append(
            {"messages": list(messages), "tools": list(tools), "system_prompt": system_prompt}
        )

        async def _stream():
            yield {"type": "text_delta", "text": "..."}
            yield {"type": "message_end", "stop_reason": "end_turn"}

        return _stream()


class StubQueryEngine:
    """Scripted QueryEngine double reusing the real event aggregation helpers.

    When it received the run's (wrapped) llm_client it drives one real
    ``chat()`` call first — the same call the react Agent would make — so the
    CLI-side measurement in ``_MeasuredLlmClient`` actually records.
    """

    _complete_from_events = cli.QueryEngine._complete_from_events

    def __init__(self, events, llm_client=None, drive_chat=False, **_kwargs):
        self._events = events
        self._llm_client = llm_client
        self._drive_chat = drive_chat

    async def ask(self, message, history=None):
        _ = message, history
        if self._drive_chat and self._llm_client is not None:
            user = Message(role="user", content="hi")
            async for _event in self._llm_client.chat([user], [], system_prompt="S" * 12):
                pass
        for event in self._events:
            yield event


def _install_stub_engine(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict[str, Any]],
    *,
    llm_client: Any = None,
    drive_chat: bool = False,
) -> None:
    async def fake_build_registry(config, cwd):
        _ = config, cwd
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: llm_client)
    monkeypatch.setattr(
        cli,
        "QueryEngine",
        lambda **kwargs: StubQueryEngine(
            events, llm_client=kwargs.get("llm_client"), drive_chat=drive_chat
        ),
    )


class _WriteCapture:
    """stdout replacement recording each write() call separately."""

    # click's echo treats a stdout without ``encoding`` as binary; declare
    # this a text stream so typer.echo lands here as str.
    encoding = "utf-8"

    def __init__(self):
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        # Reject bytes so click's binary-writer probe (it trial-writes b"")
        # classifies this capture as a text stream, like real stdout.
        if isinstance(s, bytes):
            raise TypeError("write() argument must be str, not bytes")
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass


def test_json_payload_usage_carries_prompt_breakdown(tmp_path, monkeypatch: pytest.MonkeyPatch):
    inner = _ChatRecordingInnerClient()
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, _DONE_EVENTS, llm_client=inner, drive_chat=True)
    monkeypatch.setattr(sys, "stdout", capture)

    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    asyncio.run(cli._run_prompt("hello", str(tmp_path), config, json_output=True))

    payload = json.loads("".join(capture.writes))
    usage = payload["usage"]
    # The wrapper measured the engine's chat() call: system "S"*12, empty tool
    # list ("[]"), one "hi" user message → ceil((12 + 2 + 2) / 4) == 4.
    assert usage["prompt_breakdown"] == {
        "system_chars": 12,
        "tools_chars": 2,
        "history_chars": 2,
        "tool_count": 0,
        "estimated_tokens": 4,
    }
    # The token accounting fields stay untouched next to the new key.
    assert usage["input_tokens"] == 5
    assert usage["total_tokens"] == 7
    assert payload["total_tokens"] == 7


def test_json_payload_without_breakdown_keeps_old_contract(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    """A run with no measured request (plan/team, engine doubles) adds no key."""
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, _DONE_EVENTS, llm_client=None)
    monkeypatch.setattr(sys, "stdout", capture)

    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    asyncio.run(cli._run_prompt("hello", str(tmp_path), config, json_output=True))

    payload = json.loads("".join(capture.writes))
    assert "prompt_breakdown" not in payload["usage"]
    assert payload["usage"] == {
        "input_tokens": 5,
        "output_tokens": 2,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 7,
    }


def test_measured_client_delegates_and_records():
    """The wrapper is transparent: protocol members delegate, chat records."""
    inner = _ChatRecordingInnerClient()
    measured = cli._MeasuredLlmClient(inner)

    assert measured.model_name == "fake-model"
    assert measured.provider_name == "fake-provider"
    assert measured.max_context_window == 1000
    assert measured.price_profile is None
    assert measured.last_prompt_breakdown is None

    messages = [Message(role="user", content="hi")]
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]
    asyncio.run(
        _drain(measured.chat(messages, tools, system_prompt="S" * 12)),
    )

    # The call reached the inner client untouched, and the measurement used
    # the same inputs.
    assert inner.calls == [{"messages": messages, "tools": tools, "system_prompt": "S" * 12}]
    assert measured.last_prompt_breakdown == {
        "system_chars": 12,
        "tools_chars": len(json.dumps(tools, ensure_ascii=False)),
        "history_chars": 2,
        "tool_count": 1,
        "estimated_tokens": math.ceil((12 + len(json.dumps(tools, ensure_ascii=False)) + 2) / 4),
    }


async def _drain(stream) -> list[dict[str, Any]]:
    return [event async for event in stream]


# ---------------------------------------------------------------------------
# doctor --token-budget: the configured baseline
# ---------------------------------------------------------------------------


def _install_builtin_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic stand-in for bootstrap.build_tool_registry (no MCP subprocess)."""

    async def fake_build_registry(config, cwd):
        _ = config, cwd
        registry = ToolRegistry()
        registry.register_all(get_builtin_tools())
        return registry, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)


def test_doctor_token_budget_prints_configured_baseline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _install_builtin_registry(monkeypatch)
    result = CliRunner().invoke(cli.app, ["doctor", "--token-budget", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    report = json.loads(result.output)
    assert report["prompt_profile"] == "full"
    assert report["context_window"] is None  # default config sets no window
    assert report["max_tokens"] == NexusCliConfig().llm.max_tokens
    baseline = report["baseline"]
    assert set(baseline) == {
        "system_chars",
        "tools_chars",
        "history_chars",
        "tool_count",
        "estimated_tokens",
    }
    # Empty history by construction: this is the fixed baseline.
    assert baseline["history_chars"] == 0
    assert baseline["tool_count"] == len(get_builtin_tools())
    assert baseline["system_chars"] > 0
    assert baseline["tools_chars"] > 0
    assert baseline["estimated_tokens"] == math.ceil(
        (baseline["system_chars"] + baseline["tools_chars"] + baseline["history_chars"]) / 4
    )


def test_doctor_token_budget_reflects_lean_profile(tmp_path, monkeypatch: pytest.MonkeyPatch):
    _install_builtin_registry(monkeypatch)
    (tmp_path / ".nexuscli").mkdir()
    (tmp_path / ".nexuscli" / "config.json").write_text(
        json.dumps({"agent": {"prompt_profile": "lean"}}), encoding="utf-8"
    )

    result = CliRunner().invoke(cli.app, ["doctor", "--token-budget", "--cwd", str(tmp_path)])
    report = json.loads(result.output)

    assert result.exit_code == 0
    assert report["prompt_profile"] == "lean"
    expected_count = len({tool.name for tool in get_builtin_tools()} & LEAN_CORE_TOOLS)
    assert report["baseline"]["tool_count"] == expected_count
    assert expected_count < len(get_builtin_tools())
    assert report["baseline"]["tools_chars"] > 0
