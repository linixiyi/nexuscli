"""Tests for the subagent task tool: agent definitions, registry filtering, isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent.agent import Agent
from nexuscli.agent.subagent import (
    BUILTIN_AGENTS,
    SUBAGENT_MAX_TURNS,
    AgentDefinition,
    agent_memory_dir,
    build_subagent_registry,
    build_subagent_system_prompt,
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
    # **_kwargs: production call sites pass telemetry_enabled= (factory seam).
    monkeypatch.setattr("nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: client)


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


def test_run_subagent_agent_gets_explicit_small_max_turns(tmp_path, monkeypatch):
    """子代理回合上限固定为 SUBAGENT_MAX_TURNS，不随 agent.max_turns 配置放大。"""
    client = FakeClient(
        [
            {"type": "text_delta", "text": "子代理报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    config.agent.max_turns = 999  # 配置值不得泄漏进子代理构造

    captured: list[dict[str, Any]] = []

    class _SpyAgent(Agent):
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("nexuscli.agent.subagent.Agent", _SpyAgent)

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "小任务",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert captured[0]["max_turns"] == SUBAGENT_MAX_TURNS
    assert captured[0]["max_turns"] != 999
    assert report == "子代理报告"


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


# ---------------------------------------------------------------------------
# Per-agent persistent memory directory (~/.nexuscli/agent-memory/<name>/)


def _redirect_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv("HOME", str(home))
    # win32 Path.home() prefers USERPROFILE over HOME; set both so the user
    # scope is isolated from the real profile on every platform.
    monkeypatch.setenv("USERPROFILE", str(home))


def test_subagent_system_prompt_contains_memory_directory_section(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _redirect_home(monkeypatch, home)
    client = FakeClient(
        [
            {"type": "text_delta", "text": "子代理报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    captured: list[dict[str, Any]] = []

    class _SpyAgent(Agent):
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("nexuscli.agent.subagent.Agent", _SpyAgent)

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "任务",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    system_prompt = captured[0]["system_prompt"]
    assert report == "子代理报告"
    assert "持久记忆目录" in system_prompt
    assert str(home / ".nexuscli" / "agent-memory" / "general-purpose") in system_prompt
    # 追加而非替换：原内置代理提示词正文原样开头。
    assert system_prompt.startswith(BUILTIN_AGENTS["general-purpose"].prompt)


def test_agent_memory_dir_isolated_per_agent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _redirect_home(monkeypatch, home)

    general = agent_memory_dir("general-purpose", home=home)
    explore = agent_memory_dir("explore", home=home)

    assert general != explore
    assert general.parent == home / ".nexuscli" / "agent-memory"
    assert explore.parent == home / ".nexuscli" / "agent-memory"
    # home=None 时跟随 env 重定向后的 Path.home()。
    assert agent_memory_dir("general-purpose") == (
        Path.home() / ".nexuscli" / "agent-memory" / "general-purpose"
    )


def test_subagent_memory_section_varies_by_agent_name(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _redirect_home(monkeypatch, home)
    client = FakeClient(
        [
            {"type": "text_delta", "text": "通用报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
        [
            {"type": "text_delta", "text": "探索报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ],
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)
    captured: list[dict[str, Any]] = []

    class _SpyAgent(Agent):
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr("nexuscli.agent.subagent.Agent", _SpyAgent)
    context = ToolContext(cwd=str(tmp_path), config=config)

    asyncio.run(run_subagent(BUILTIN_AGENTS["general-purpose"], "任务一", context))
    asyncio.run(run_subagent(BUILTIN_AGENTS["explore"], "任务二", context))

    general_prompt = captured[0]["system_prompt"]
    explore_prompt = captured[1]["system_prompt"]
    general_dir = str(home / ".nexuscli" / "agent-memory" / "general-purpose")
    explore_dir = str(home / ".nexuscli" / "agent-memory" / "explore")
    assert general_dir in general_prompt
    assert explore_dir in explore_prompt
    # 按代理名隔离：互相不引用对方的记忆目录。
    assert general_dir not in explore_prompt
    assert explore_dir not in general_prompt


def test_memory_section_skipped_for_unsafe_agent_names(tmp_path):
    for name in ("../evil", ".", ".."):
        definition = AgentDefinition(name=name, description="x", prompt="正文")

        assert build_subagent_system_prompt(definition, home=tmp_path) == "正文"


def test_memory_section_absent_customization_via_home(tmp_path):
    """同一定义传不同 home → 目录路径随 home 变化（重定向缝钉住）。"""
    definition = AgentDefinition(name="researcher", description="x", prompt="正文")

    first = build_subagent_system_prompt(definition, home=tmp_path / "home-a")
    second = build_subagent_system_prompt(definition, home=tmp_path / "home-b")

    assert str(tmp_path / "home-a" / ".nexuscli" / "agent-memory" / "researcher") in first
    assert str(tmp_path / "home-b" / ".nexuscli" / "agent-memory" / "researcher") in second
    assert first != second
