"""Tests for the offline deterministic demo provider and its factory seam.

Covers (lane A11 — Ported from kitagent (MIT, zed123214/kitagent)
src/agent_runtime/web/demo.py:18-152):

- the OfflineDemoProvider driving a REAL Agent (real ToolRegistry tools, real
  executor gates, real approval callback, no network, no real API key) through
  the full scripted chain: read_file → write_file (real approval gate) →
  read-back verification → end_turn;
- token streaming in fixed 8-character chunks;
- graceful degradation when the write is denied at the approval gate and when
  the input file is missing (kitagent's `denied` branch);
- the factory injection seam: NEXUSCLI_OFFLINE_DEMO=1 swaps every client for
  the offline provider, and the default path is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import LlmConfig, load_config
from nexuscli.llm.factory import OFFLINE_DEMO_ENV_VAR, create_llm_client
from nexuscli.llm.offline_demo import CHUNK_SIZE, OfflineDemoProvider
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.tools import ToolRegistry, get_builtin_tools


def _config(monkeypatch, tmp_path):
    """Config for direct Agent construction. The API key is a placeholder —
    the offline provider never contacts a network, but Agent._validate_config
    refuses an empty key (same pattern as tests/test_agent_turns.py)."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "offline-demo-no-network")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    # Deterministic event streams: no compression events, no skill prepends.
    config.features.context_compression = False
    config.features.skill = False
    return config


def _agent(monkeypatch, tmp_path, approval_callback=None) -> Agent:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return Agent(
        llm_client=OfflineDemoProvider(),
        tool_registry=registry,
        config=_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        approval_callback=approval_callback,
        max_turns=8,
    )


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


def _tool_calls(events: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (str(event["name"]), str(event["input"].get("path")))
        for event in events
        if event["type"] == "tool_call"
    ]


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(str(event["text"]) for event in events if event["type"] == "text_delta")


def test_offline_demo_drives_real_agent_through_full_chain(monkeypatch, tmp_path):
    """The scripted chain completes against the real agent loop: read_file →
    write_file (through the real approval gate) → read back → end_turn."""
    (tmp_path / "README.md").write_text(
        "# 示例 Python 项目\n\n运行检查流程生成报告。\n", encoding="utf-8"
    )
    approvals: list[dict[str, Any]] = []

    def approve(request: dict[str, Any]) -> str:
        approvals.append(request)
        return "approve"

    agent = _agent(monkeypatch, tmp_path, approval_callback=approve)
    events = asyncio.run(_collect(agent, "请检查示例项目并生成检查报告。"))

    # Scripted chain, in order, with the real tool names.
    assert _tool_calls(events) == [
        ("read_file", "README.md"),
        ("write_file", "demo-report.md"),
        ("read_file", "demo-report.md"),
    ]
    # The write passed the REAL approval gate: exactly one human-equivalent
    # prompt, for write_file, carrying the Reason line (AGENTS.md 审批守则).
    assert [request["tool_name"] for request in approvals] == ["write_file"]
    assert all(str(request.get("description")) for request in approvals)
    # The report was written by the real write_file tool and embeds a line
    # read from the real read_file round-trip.
    report = tmp_path / "demo-report.md"
    assert report.is_file()
    body = report.read_text(encoding="utf-8")
    assert "# 离线演示检查报告" in body
    assert "示例 Python 项目" in body
    assert "运行标识：" in body
    # No error events; the stream ends with done.
    assert not [event for event in events if event["type"] == "error"]
    assert events[-1]["type"] == "done"
    # Tokens streamed in fixed 8-character chunks.
    deltas = [str(event["text"]) for event in events if event["type"] == "text_delta"]
    assert deltas, "the demo must stream its announcements"
    assert all(len(delta) <= CHUNK_SIZE for delta in deltas)
    assert "检查报告" in _text(events)


def test_offline_demo_denied_write_ends_without_report(monkeypatch, tmp_path):
    """A write denied at the approval gate ends the demo honestly: no report
    on disk, no read-back attempt, no error event (kitagent demo.py:56-57)."""
    (tmp_path / "README.md").write_text("# 示例\n", encoding="utf-8")
    agent = _agent(monkeypatch, tmp_path, approval_callback=lambda request: "deny")
    events = asyncio.run(_collect(agent, "请检查示例项目并生成检查报告。"))

    assert _tool_calls(events) == [
        ("read_file", "README.md"),
        ("write_file", "demo-report.md"),
    ]
    assert not (tmp_path / "demo-report.md").exists()
    assert "未获准或未完成" in _text(events)
    assert not [event for event in events if event["type"] == "error"]
    assert events[-1]["type"] == "done"


def test_offline_demo_survives_missing_input(monkeypatch, tmp_path):
    """A failed input read takes the same failure branch instead of looping
    (kitagent demo.py:70 "模拟工具错误" scenario shape)."""
    agent = _agent(monkeypatch, tmp_path, approval_callback=lambda request: "approve")
    events = asyncio.run(_collect(agent, "请检查示例项目并生成检查报告。"))

    assert _tool_calls(events) == [("read_file", "README.md")]
    assert not (tmp_path / "demo-report.md").exists()
    assert "未获准或未完成" in _text(events)
    assert events[-1]["type"] == "done"


def test_factory_default_path_unchanged(monkeypatch):
    """Without the env switch the factory returns the real network client it
    always did — the seam is invisible on the default path."""
    monkeypatch.delenv(OFFLINE_DEMO_ENV_VAR, raising=False)
    client = create_llm_client(LlmConfig(provider="deepseek", api_key="k"))
    assert isinstance(client, OpenAICompatibleClient)
    assert not isinstance(client, OfflineDemoProvider)
    assert client.model_name == "deepseek-v4-flash"


def test_factory_env_switch_injects_offline_provider(monkeypatch):
    """NEXUSCLI_OFFLINE_DEMO=1 short-circuits provider resolution entirely —
    even a config that would raise for an unknown provider gets the offline
    provider (no network config is consulted)."""
    monkeypatch.setenv(OFFLINE_DEMO_ENV_VAR, "1")
    client = create_llm_client(LlmConfig(provider="definitely-not-a-provider", api_key=""))
    assert isinstance(client, OfflineDemoProvider)
    assert client.model_name == "offline-demo"

    # Any value other than exactly "1" keeps the default path.
    monkeypatch.setenv(OFFLINE_DEMO_ENV_VAR, "0")
    assert not isinstance(
        create_llm_client(LlmConfig(provider="deepseek", api_key="k")), OfflineDemoProvider
    )
