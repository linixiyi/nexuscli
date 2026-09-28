"""Tests for the subagent task tool: agent definitions, registry filtering, isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent.agent import Agent
from nexuscli.agent.subagent import (
    BUILTIN_AGENTS,
    AgentDefinition,
    build_subagent_registry,
    load_subagents,
    run_subagent,
)
from nexuscli.config import NexusCliConfig
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext


def _write_agent(directory: Path, filename: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")


def _test_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


def _task_tool() -> Any:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry.get("task")


def _use_fake_llm(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    monkeypatch.setattr("nexuscli.llm.factory.create_llm_client", lambda _config: client)


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


def test_load_subagents_parses_frontmatter_and_overrides_builtins(tmp_path):
    user_dir = tmp_path / "home" / ".nexuscli" / "agents"
    project_dir = tmp_path / "proj" / ".nexuscli" / "agents"
    _write_agent(
        user_dir,
        "researcher.md",
        "---\n"
        "name: researcher\n"
        "description: 深度研究代理\n"
        "tools: read_file, grep , grep\n"
        "model: glm-5.2\n"
        "---\n"
        "研究任务提示词正文",
    )
    _write_agent(
        user_dir,
        "planner.md",
        "---\nname: planner\ndescription: 用户版规划\n---\n用户规划提示词",
    )
    _write_agent(
        project_dir,
        "planner.md",
        "---\nname: planner\ndescription: 项目版规划\n---\n项目规划提示词",
    )
    _write_agent(
        project_dir,
        "explore.md",
        "---\nname: explore\ndescription: 项目定制探索\n---\n项目专属探索提示词",
    )

    definitions = load_subagents(str(tmp_path / "proj"), home=tmp_path / "home")

    assert {"general-purpose", "explore", "researcher", "planner"} <= set(definitions)
    researcher = definitions["researcher"]
    assert researcher.name == "researcher"
    assert researcher.description == "深度研究代理"
    assert researcher.tools == ["read_file", "grep"]  # 去重；model 字段解析但被忽略
    assert researcher.prompt == "研究任务提示词正文"
    assert "model" not in AgentDefinition.__dataclass_fields__
    assert definitions["planner"].description == "项目版规划"  # project 覆盖 user
    assert definitions["explore"].description == "项目定制探索"  # 覆盖内置同名代理
    assert definitions["explore"].prompt == "项目专属探索提示词"
    assert definitions["general-purpose"].tools is None  # 内置代理始终存在


def test_load_subagents_skips_invalid_files(tmp_path):
    agents_dir = tmp_path / "proj" / ".nexuscli" / "agents"
    _write_agent(agents_dir, "no-name.md", "---\ndescription: 缺 name\n---\n正文")
    _write_agent(agents_dir, "no-frontmatter.md", "纯正文，没有 frontmatter")
    _write_agent(agents_dir, "no-body.md", "---\nname: empty\ndescription: 无正文\n---\n")
    _write_agent(agents_dir, "notes.txt", "---\nname: txt\ndescription: 非 md\n---\n正文")

    definitions = load_subagents(str(tmp_path / "proj"), home=tmp_path / "home")

    assert set(definitions) == {"general-purpose", "explore"}


def test_explore_registry_is_filtered_to_readonly_whitelist():
    registry = build_subagent_registry(BUILTIN_AGENTS["explore"])

    names = registry.list_names()
    assert {"write_file", "edit_file", "bash", "execute_command", "task"}.isdisjoint(names)
    assert {"read_file", "list_dir", "glob_files", "grep", "web_search"} <= set(names)


def test_general_purpose_registry_denies_task_by_default():
    registry = build_subagent_registry(BUILTIN_AGENTS["general-purpose"])

    assert "task" not in registry.list_names()  # 白名单未显式允许时默认移除，双保险
    assert "write_file" in registry.list_names()


def test_task_tool_unknown_agent_type_lists_available_agents(tmp_path):
    config = _test_config(tmp_path)
    task = _task_tool()

    result = asyncio.run(
        task.execute(
            {"description": "调研", "prompt": "任务指令", "agent_type": "ghost"},
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert result.is_error
    assert "ghost" in result.content
    assert "general-purpose" in result.content
    assert "explore" in result.content


def test_task_tool_depth_limit_returns_error_result(tmp_path):
    config = _test_config(tmp_path)
    task = _task_tool()
    context = ToolContext(cwd=str(tmp_path), config=config, subagent_depth=1)

    result = asyncio.run(
        task.execute(
            {
                "description": "再委派",
                "prompt": "再次委派任务",
                "agent_type": "general-purpose",
            },
            context,
        )
    )

    assert result.is_error
    assert "depth limit reached (1)" in result.content


def test_run_subagent_returns_text_after_last_tool_call(tmp_path, monkeypatch):
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

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "列出目录并总结",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert report == "最终报告：目录结构如下"


def test_run_subagent_returns_placeholder_without_text(tmp_path, monkeypatch):
    client = FakeClient([{"type": "message_end", "stop_reason": "end_turn"}])
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["explore"],
            "探索仓库",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert report == "(subagent returned no text)"


def test_subagent_history_is_isolated_from_main_agent(tmp_path, monkeypatch):
    client = FakeClient(
        [
            {"type": "text_delta", "text": "草稿"},
            _tool_call_event("list_dir", "{}"),
            {"type": "message_end", "stop_reason": "tool_use"},
        ],
        [
            {"type": "text_delta", "text": "完成"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    main_agent = Agent(
        llm_client=FakeClient(),
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
        system_prompt="主代理提示",
    )
    task = registry.get("task")

    result = asyncio.run(
        task.execute(
            {
                "description": "委派",
                "prompt": "子任务指令",
                "agent_type": "general-purpose",
            },
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert not result.is_error
    assert "Subagent report (general-purpose):" in result.content
    assert main_agent.history == []  # 主会话历史不增长
    assert client.calls == [1, 3]  # 子代理独立对话：user → assistant+tool_call → tool


def test_approval_callback_is_forwarded_to_subagent(tmp_path, monkeypatch):
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

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "运行命令",
            ToolContext(cwd=str(tmp_path), config=config, approval_callback=callback),
        )
    )

    assert [request["tool_name"] for request in requests] == ["bash"]
    assert report == "命令被拒绝后的子代理报告"
