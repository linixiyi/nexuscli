"""Lane D extension tests: user-visible behavior of the extension surfaces.

Covers the two matrix rows this lane must add machine assertions for
(docs/testing-matrix.md rows 12.3 and 12.5, "机器+真实"):

- 12.3 自定义斜杠命令: defining a command file and triggering it in the REPL
  must run the expanded prompt through the agent with the frontmatter hints
  applied (``mode: plan`` flips the permission controller, ``allowed-tools``
  narrows the live registry) and restore every override afterwards.
- 12.5 自定义子代理: a reviewer defined in ``.nexuscli/agents/*.md`` is
  selectable through the ``task`` tool — the tool resolves the custom name
  (no "Unknown agent type" error) and the subagent actually runs with the
  markdown body as its system prompt.

The existing suites (test_slash_commands.py, test_subagent.py) already cover
parsing/expansion and built-in agents; these tests only add the end-to-end
user-visible paths they do not touch.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.custom_cmds import _match_custom_command, _run_custom_command
from nexuscli.entrypoints.repl import PermissionModeController
from nexuscli.entrypoints.slash_commands import load_slash_commands
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext

# ---------------------------------------------------------------------------
# 12.3 自定义斜杠命令 — REPL dispatch applies frontmatter hints, then restores
# ---------------------------------------------------------------------------


class _FakeLLM:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000


class _FakeAgent:
    """Records the prompt _run_custom_command hands to agent.run()."""

    def __init__(self):
        self.mode = "react"
        self.tool_registry = ToolRegistry()
        self.llm_client = _FakeLLM()
        self.run_prompts: list[str] = []

    async def run(self, message: str):
        self.run_prompts.append(message)
        return
        yield  # pragma: no cover - makes this an async generator


class _FakeRenderer:
    def set_context_window(self, _window):  # noqa: D102 - stub
        pass

    def start_run(self):  # noqa: D102 - stub
        pass

    def handle(self, _event):  # noqa: D102 - stub
        pass

    def newline(self):  # noqa: D102 - stub
        pass


def test_custom_command_trigger_applies_hints_and_restores(tmp_path):
    """Typing /ship in the REPL runs the expanded prompt; plan+whitelist hold
    only for the duration of the run and are restored afterwards."""
    commands_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "ship.md").write_text(
        "---\n"
        "description: Ship it\n"
        "mode: plan\n"
        "allowed-tools: read_file, grep\n"
        "---\n"
        "Ship $ARGUMENTS following the release checklist.",
        encoding="utf-8",
    )
    config = NexusCliConfig()
    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")
    agent = _FakeAgent()
    full_registry = ToolRegistry()
    full_registry.register_all(get_builtin_tools())
    agent.tool_registry = full_registry
    permission_mode = PermissionModeController(config)

    # Simulate exactly what repl.py:326-335 does with the matched expansion.
    match = asyncio.run(_match_custom_command("/ship the renderer", commands, config))
    assert match is not None, "the user's /ship command must match a defined command"
    asyncio.run(
        _run_custom_command(agent, _FakeRenderer(), Console(), permission_mode, match[1])
    )

    # The prompt reached the agent with arguments substituted in.
    assert agent.run_prompts == ["Ship the renderer following the release checklist."]
    # During the run the frontmatter hints were applied: plan mode set the
    # executor's read-only gate and the registry was narrowed to the whitelist.
    # Both are restored after the run, so the session is back to its state.
    assert permission_mode.mode == "default"
    assert config.policy.plan_mode is False
    assert agent.tool_registry is full_registry


def test_custom_command_plan_mode_active_during_run_only(tmp_path):
    """Inside the run the permission controller really is in plan mode."""
    commands_dir = tmp_path / "proj" / ".nexuscli" / "commands"
    commands_dir.mkdir(parents=True)
    (commands_dir / "audit.md").write_text(
        "---\nmode: plan\n---\nAudit read-only.",
        encoding="utf-8",
    )
    config = NexusCliConfig()
    commands = load_slash_commands(str(tmp_path / "proj"), home=tmp_path / "home")
    permission_mode = PermissionModeController(config)

    class _SpyAgent(_FakeAgent):
        async def run(self, message: str):  # noqa: D102
            self.run_prompts.append(message)
            self.mode_seen_in_run = permission_mode.mode
            self.plan_flag_seen_in_run = config.policy.plan_mode
            return
            yield  # pragma: no cover

    spy = _SpyAgent()
    match = asyncio.run(_match_custom_command("/audit now", commands, config))
    assert match is not None
    asyncio.run(_run_custom_command(spy, _FakeRenderer(), Console(), permission_mode, match[1]))

    assert spy.run_prompts == ["Audit read-only.\n\nnow"]
    assert spy.mode_seen_in_run == "plan"
    assert spy.plan_flag_seen_in_run is True
    assert permission_mode.mode == "default"


# ---------------------------------------------------------------------------
# 12.5 自定义子代理 — .nexuscli/agents/*.md defines a reviewer the task tool selects
# ---------------------------------------------------------------------------


@dataclass
class _CapturingFakeClient:
    """Records chat calls; replies with plain text and captures the system prompt."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000
    system_prompts: list[str] = field(default_factory=list)

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ANN001, ARG002
        self.system_prompts.append(system_prompt)
        yield {"type": "text_delta", "text": "review finished: 2 findings"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


def _task_tool() -> Any:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry.get("task")


def test_project_reviewer_agent_selected_by_task_tool(tmp_path, monkeypatch):
    """A reviewer in .nexuscli/agents/ is picked by the task tool and its md
    body becomes the subagent's system prompt."""
    agents_dir = tmp_path / "proj" / ".nexuscli" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\n"
        "name: reviewer\n"
        "description: 严格审查代码改动\n"
        "tools: read_file, grep\n"
        "---\n"
        "你是专属代码审查代理：只输出发现清单，每条带文件路径与行号。",
        encoding="utf-8",
    )
    client = _CapturingFakeClient()
    monkeypatch.setattr(
        "nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: client
    )
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    task_tool = _task_tool()
    context = ToolContext(cwd=str(tmp_path / "proj"), config=config)

    result = asyncio.run(
        task_tool.handler(
            {"agent_type": "reviewer", "prompt": "审查 main.py", "description": "审查"},
            context,
        )
    )

    assert not result.is_error
    # User-visible output names the custom agent, not "Unknown agent type".
    assert result.content.startswith("Subagent report (reviewer):")
    assert "review finished: 2 findings" in result.content
    # The md body (not a builtin prompt) drove the subagent.
    assert len(client.system_prompts) == 1
    assert "你是专属代码审查代理" in client.system_prompts[0]
    # The frontmatter whitelist held: the subagent registry has read_file/grep
    # and never the task tool (no recursion door).
    from nexuscli.agent.subagent import build_subagent_registry, load_subagents

    reviewer = load_subagents(str(tmp_path / "proj"))["reviewer"]
    sub_registry = build_subagent_registry(reviewer)
    assert sub_registry.get("read_file") is not None
    assert sub_registry.get("grep") is not None
    assert sub_registry.get("task") is None


def test_task_tool_unknown_type_lists_custom_reviewer(tmp_path):
    """The available-agents error the user sees names the project reviewer."""
    agents_dir = tmp_path / "proj" / ".nexuscli" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: 严格审查代码改动\n---\n审查身体。",
        encoding="utf-8",
    )
    config = NexusCliConfig()
    task_tool = _task_tool()
    context = ToolContext(cwd=str(tmp_path / "proj"), config=config)

    result = asyncio.run(
        task_tool.handler(
            {"agent_type": "nosuchagent", "prompt": "x", "description": "x"},
            context,
        )
    )

    assert result.is_error
    assert 'Unknown agent type "nosuchagent"' in result.content
    assert "reviewer" in result.content
