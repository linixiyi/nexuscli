"""Tests for `/skill load <name>`: one-shot injection into the next user prompt.

`/skill load` pushes the skill onto the agent's skill_context_buffer; the next
agent turn drains it and prepends `## Loaded Skill: <name>` to the user message
before the LLM call (agent._prepend_skill_context), so the model reads the
skill first. Bare `/skill` and the other subcommands must stay display-only.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from nexuscli.agent.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import PermissionModeController, _handle_slash
from nexuscli.tools import ToolRegistry
from nexuscli.types import Message


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home so user-level skills are isolated.

    Mirrors the repo's background_home fixture: on Windows Path.home() reads
    USERPROFILE (not HOME), so both environment variables are redirected too.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_skill(root: Path, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} desc\n---\n{body}\n",
        encoding="utf-8",
    )


def _test_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


class RecordingClient:
    """Minimal LLM stub (FakeClient pattern) that records each chat's message list."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 128_000

    def __init__(self, *turns: list[dict[str, Any]]):
        self.turns = list(turns)
        self.seen_messages: list[list[Message]] = []

    async def chat(self, messages, tools, *, system_prompt):
        self.seen_messages.append(list(messages))
        turn = self.turns[min(len(self.seen_messages) - 1, len(self.turns) - 1)]
        for event in turn:
            yield event


def _make_agent(tmp_path: Path, config: NexusCliConfig, client: RecordingClient) -> Agent:
    return Agent(
        llm_client=client,
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
        system_prompt="test system prompt",
    )


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _run_slash(raw: str, console: Console, tmp_path: Path, config, agent) -> bool:
    return asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            config,
            agent,
            None,
            PermissionModeController(config),
            None,
            None,
            custom_commands=None,
        )
    )


def test_skill_load_pushes_body_into_next_prompt(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write_skill(tmp_path / ".nexuscli" / "skills", "demo", "demo body")
    config = _test_config(tmp_path)
    client = RecordingClient([{"type": "message_end", "stop_reason": "end_turn"}])
    agent = _make_agent(tmp_path, config, client)
    console = _console()

    should_exit = _run_slash("/skill load demo", console, tmp_path, config, agent)

    assert should_exit is False
    assert not agent.skill_context_buffer.is_empty()
    assert 'Skill "demo" loaded' in _console_text(console)

    async def _consume() -> None:
        async for _event in agent.run("开始干活"):
            pass

    asyncio.run(_consume())

    # One-shot: the buffer is drained by the next turn, never again.
    assert agent.skill_context_buffer.is_empty()
    user_messages = [m for m in client.seen_messages[-1] if m.role == "user"]
    last_user = user_messages[-1].content
    assert "## Loaded Skill: demo" in last_user
    assert "demo body" in last_user
    assert "开始干活" in last_user


def test_skill_load_unknown_name_keeps_buffer_empty(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    config = _test_config(tmp_path)
    agent = _make_agent(tmp_path, config, RecordingClient())
    console = _console()

    should_exit = _run_slash("/skill load nope", console, tmp_path, config, agent)

    assert should_exit is False
    assert 'Skill "nope" not found.' in _console_text(console)
    assert agent.skill_context_buffer.is_empty()


def test_skill_show_and_list_unchanged(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    _write_skill(tmp_path / ".nexuscli" / "skills", "demo", "demo body")
    config = _test_config(tmp_path)
    agent = _make_agent(tmp_path, config, RecordingClient())
    show_console = _console()
    list_console = _console()

    _run_slash("/skill show demo", show_console, tmp_path, config, agent)
    _run_slash("/skill", list_console, tmp_path, config, agent)

    assert "demo body" in _console_text(show_console)
    assert "demo desc" in _console_text(list_console)
    assert agent.skill_context_buffer.is_empty()  # show/list never touch the buffer
