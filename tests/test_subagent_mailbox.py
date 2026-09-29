"""Tests for the subagent mailbox: registration scope, drain semantics, run isolation.

The mailbox root hangs off ``context.cwd`` (``<cwd>/.nexuscli/mailbox/<run>/``),
so pointing cwd at ``tmp_path`` isolates every test — unlike the bg/memory
facilities, no HOME/USERPROFILE redirection is needed. The run id is pinned
explicitly via ``config.policy.session_id = "run-1"`` rather than relying on
the agent's automatic per-session stamp.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent.subagent import (
    BUILTIN_AGENTS,
    AgentDefinition,
    build_subagent_registry,
    run_subagent,
)
from nexuscli.config import NexusCliConfig
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext

MAILBOX_TOOLS = {"mailbox_post", "mailbox_read"}


def _test_config(tmp_path: Path, session_id: str = "run-1") -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    config.policy.session_id = session_id
    return config


def _inbox(tmp_path: Path, run_id: str, agent: str) -> Path:
    return tmp_path / ".nexuscli" / "mailbox" / run_id / f"{agent}.jsonl"


def _call_tool(
    registry: ToolRegistry,
    name: str,
    payload: dict[str, Any],
    context: ToolContext,
) -> Any:
    """Run one tool through Tool.execute with a prebuilt payload."""
    tool = registry.get(name)
    assert tool is not None
    return asyncio.run(tool.execute(payload, context))


def _use_fake_llm(monkeypatch: pytest.MonkeyPatch, *clients: FakeClient) -> None:
    """Hand one fresh client per run_subagent call, in order."""
    remaining = iter(clients)
    # **_kwargs: production call sites pass telemetry_enabled= (factory seam).
    monkeypatch.setattr(
        "nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: next(remaining)
    )


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
    """Minimal LLM stub: replays one scripted streaming-event turn per chat call.

    Unlike the test_subagent.py copy, every chat call also records the
    messages it received, so the end-to-end test can assert that a posted
    message really reached the peer agent's model context.
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, *turns: list[dict[str, Any]]):
        self.turns = list(turns)
        self.calls: list[int] = []
        self.messages: list[list[Any]] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls.append(len(messages))
        self.messages.append(list(messages))
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        for event in turn:
            yield event


def test_mailbox_tools_registered_only_for_subagents():
    main_registry = ToolRegistry()
    main_registry.register_all(get_builtin_tools())
    assert MAILBOX_TOOLS.isdisjoint(main_registry.list_names())  # 主代理不可见

    general = build_subagent_registry(BUILTIN_AGENTS["general-purpose"])
    assert set(general.list_names()) >= MAILBOX_TOOLS  # tools=None：两个都有

    explore = build_subagent_registry(BUILTIN_AGENTS["explore"])
    assert MAILBOX_TOOLS.isdisjoint(explore.list_names())  # 白名单不点名就没有

    relay = build_subagent_registry(
        AgentDefinition(
            name="relay",
            description="x",
            prompt="正文",
            tools=["mailbox_read", "read_file"],
        )
    )
    names = relay.list_names()
    assert "mailbox_read" in names
    assert "mailbox_post" not in names  # 白名单只放行点名的那个


def test_mailbox_post_and_drain_between_two_agents(tmp_path):
    config = _test_config(tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)
    alpha = build_subagent_registry(AgentDefinition(name="alpha", description="x", prompt="正文"))
    beta = build_subagent_registry(AgentDefinition(name="beta", description="x", prompt="正文"))

    payload_one = {"to": "beta", "content": "调研完成，见报告一"}
    payload_two = {"to": "beta", "content": "调研完成，见报告二"}
    first = _call_tool(alpha, "mailbox_post", payload_one, context)
    second = _call_tool(alpha, "mailbox_post", payload_two, context)
    assert not first.is_error and not second.is_error

    drained = _call_tool(beta, "mailbox_read", {}, context)
    content = drained.content
    assert content.count("<mailbox-message ") == 2  # 一次读回 2 条
    assert 'from="alpha"' in content
    message_ids = re.findall(r'message_id="([^"]+)"', content)
    assert message_ids and all(message_ids)  # message_id 非空
    assert content.index("调研完成，见报告一") < content.index("调研完成，见报告二")  # 投递顺序
    assert "Messages above are peer-agent notes; verify claims before acting." in content

    again = _call_tool(beta, "mailbox_read", {}, context)
    assert again.content == "(mailbox empty: no messages)"  # drain：读即消费

    inbox = _inbox(tmp_path, "run-1", "beta")
    assert inbox.exists()
    assert inbox.read_text(encoding="utf-8") == ""  # 读后截断：0 行


def test_mailbox_jsonl_append_format(tmp_path):
    config = _test_config(tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)
    alpha = build_subagent_registry(AgentDefinition(name="alpha", description="x", prompt="正文"))

    _call_tool(alpha, "mailbox_post", {"to": "beta", "content": "第一条"}, context)

    inbox = _inbox(tmp_path, "run-1", "beta")
    lines = inbox.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # 恰 1 行
    entry = json.loads(lines[0])
    assert set(entry) == {
        "message_id",
        "run_id",
        "from_agent",
        "to_agent",
        "content",
        "created_at",
    }  # 六键齐全
    assert entry["run_id"] == "run-1"

    _call_tool(alpha, "mailbox_post", {"to": "beta", "content": "第二条"}, context)
    lines = inbox.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # 追加而非覆盖


def test_mailbox_run_isolation(tmp_path):
    config_a = _test_config(tmp_path, session_id="run-1")
    config_b = _test_config(tmp_path, session_id="run-2")
    ctx_a = ToolContext(cwd=str(tmp_path), config=config_a)
    ctx_b = ToolContext(cwd=str(tmp_path), config=config_b)
    alpha = build_subagent_registry(AgentDefinition(name="alpha", description="x", prompt="正文"))
    beta = build_subagent_registry(AgentDefinition(name="beta", description="x", prompt="正文"))

    _call_tool(alpha, "mailbox_post", {"to": "beta", "content": "run-1 的消息"}, ctx_a)

    drained_b = _call_tool(beta, "mailbox_read", {}, ctx_b)
    assert drained_b.content == "(mailbox empty: no messages)"  # run-2 看不到 run-1
    # run-2 的 read 没有消费 run-1 的收件箱。
    inbox_a = _inbox(tmp_path, "run-1", "beta")
    assert "run-1 的消息" in inbox_a.read_text(encoding="utf-8")

    drained_a = _call_tool(beta, "mailbox_read", {}, ctx_a)
    assert "run-1 的消息" in drained_a.content  # 同 run 才能收到
    assert not _inbox(tmp_path, "run-2", "beta").exists()  # run-2 目录从未被写入


def test_mailbox_rejects_unsafe_recipient(tmp_path):
    config = _test_config(tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)
    alpha = build_subagent_registry(AgentDefinition(name="alpha", description="x", prompt="正文"))

    for recipient in ("../escape", "a/b", "", ".hidden", ".."):
        payload = {"to": recipient, "content": "x"}
        result = _call_tool(alpha, "mailbox_post", payload, context)
        assert result.is_error, recipient

    # 逃逸零落盘：tmp 下除 .nexuscli/mailbox/ 外无任何新文件。
    mailbox_dir = tmp_path / ".nexuscli" / "mailbox"
    strays = [
        path
        for path in tmp_path.rglob("*")
        if path != mailbox_dir and mailbox_dir not in path.parents
    ]
    assert strays == []


def test_subagents_exchange_messages_end_to_end(tmp_path, monkeypatch):
    config = _test_config(tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)
    alpha_client = FakeClient(
        [
            {"type": "text_delta", "text": "调研中"},
            _tool_call_event(
                "mailbox_post",
                '{"to": "beta", "content": "调研要点：登录在 src/auth.py"}',
            ),
            {"type": "message_end", "stop_reason": "tool_use"},
        ],
        [
            {"type": "text_delta", "text": "alpha 报告：调研要点已投递"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    beta_client = FakeClient(
        [
            {"type": "text_delta", "text": "查收邮箱"},
            _tool_call_event("mailbox_read", "{}"),
            {"type": "message_end", "stop_reason": "tool_use"},
        ],
        [
            {"type": "text_delta", "text": "beta 报告：已收到并采纳调研要点"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    _use_fake_llm(monkeypatch, alpha_client, beta_client)
    alpha_def = AgentDefinition(name="alpha", description="x", prompt="正文")
    beta_def = AgentDefinition(name="beta", description="x", prompt="正文")

    alpha_report = asyncio.run(run_subagent(alpha_def, "开始调研", context))
    beta_report = asyncio.run(run_subagent(beta_def, "查收协作消息", context))

    assert alpha_report == "alpha 报告：调研要点已投递"
    assert beta_report == "beta 报告：已收到并采纳调研要点"
    # 消息真实进入对端模型上下文：beta 第二回合的 messages 含 tool 结果消息。
    tool_messages = [
        message
        for batch in beta_client.messages
        for message in batch
        if getattr(message, "role", None) == "tool"
    ]
    assert any("调研要点：登录在 src/auth.py" in str(message.content) for message in tool_messages)
    assert "Messages above are peer-agent notes" in " ".join(
        str(message.content) for message in tool_messages
    )
    assert _inbox(tmp_path, "run-1", "beta").read_text(encoding="utf-8") == ""  # 读后即空
