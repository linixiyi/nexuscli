"""Tests for the session goal store and its per-turn reminder injection.

/goal set writes ~/.nexuscli/goals/<session_id>.json; the REPL re-queues the
goal text onto the agent's skill_context_buffer at every turn start via
_queue_goal_reminder (the same injection seam /skill load uses), so the
reminder is prepended to each user message until /goal clear. These tests
assert the reminder body only — the drain header wording ("## Loaded Skill:")
is a known cosmetic trace pinned elsewhere, never asserted here.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from nexuscli.agent.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.context.goal import GoalStore, goal_path
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
    _queue_goal_reminder,
)
from nexuscli.session import SessionStore
from nexuscli.tools import ToolRegistry
from nexuscli.types import Message


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home so user-level files are isolated.

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


def _make_state(tmp_path: Path) -> ReplSessionState:
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="p")
    return ReplSessionState(store=store, writer=writer)


def _run_slash(raw: str, console: Console, tmp_path: Path, config, agent, state) -> bool:
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
            state,
            custom_commands=None,
        )
    )


def _consume(agent: Agent, message: str) -> None:
    async def _run() -> None:
        async for _event in agent.run(message):
            pass

    asyncio.run(_run())


def _last_user_message(client: RecordingClient) -> str:
    user_messages = [m for m in client.seen_messages[-1] if m.role == "user"]
    return user_messages[-1].content


def test_goal_store_crud_roundtrip(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    store = GoalStore("sess1")
    assert store.path == home / ".nexuscli" / "goals" / "sess1.json"
    assert store.show() is None

    goal = store.set("修复登录并补测试")
    assert goal == "修复登录并补测试"
    assert store.show() == "修复登录并补测试"
    path = goal_path("sess1")
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["goal"] == "修复登录并补测试"
    assert "updated_at" in data

    assert store.clear() is True
    assert store.show() is None
    assert store.clear() is False


def test_goal_store_rejects_bad_session_id(tmp_path):
    for bad in ("../evil", "a/b", ""):
        with pytest.raises(ValueError):
            GoalStore(bad)


def test_goal_store_tolerates_corrupt_file(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = GoalStore("sess1")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("not json", encoding="utf-8")
    assert store.show() is None
    assert store.clear() is False


def test_goal_reminder_injected_every_turn_and_cleared(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    state = _make_state(tmp_path)
    config = _test_config(tmp_path)
    client = RecordingClient([{"type": "message_end", "stop_reason": "end_turn"}])
    agent = _make_agent(tmp_path, config, client)
    console = _console()

    should_exit = _run_slash("/goal set 保持输出简洁", console, tmp_path, config, agent, state)
    assert should_exit is False
    assert "Session goal set" in _console_text(console)

    # Main-loop seam: the reminder is queued right before each agent turn.
    _queue_goal_reminder(agent, state.writer.meta.id)
    _consume(agent, "第一回合")
    first = _last_user_message(client)
    assert "[session goal] 保持输出简洁" in first
    assert "第一回合" in first

    # Next turn: the buffer was drained once, the reminder is re-queued.
    _queue_goal_reminder(agent, state.writer.meta.id)
    _consume(agent, "第二回合")
    second = _last_user_message(client)
    assert "[session goal] 保持输出简洁" in second
    assert "第二回合" in second

    should_exit = _run_slash("/goal clear", console, tmp_path, config, agent, state)
    assert should_exit is False
    assert "Cleared session goal." in _console_text(console)

    _queue_goal_reminder(agent, state.writer.meta.id)  # no goal → no-op
    _consume(agent, "第三回合")
    third = _last_user_message(client)
    assert "[session goal]" not in third
    assert "第三回合" in third


def test_goal_and_skill_share_injection_point(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    state = _make_state(tmp_path)
    config = _test_config(tmp_path)
    client = RecordingClient([{"type": "message_end", "stop_reason": "end_turn"}])
    agent = _make_agent(tmp_path, config, client)
    console = _console()

    _run_slash("/goal set G", console, tmp_path, config, agent, state)
    _write_skill(tmp_path / ".nexuscli" / "skills", "demo", "demo body")
    _run_slash("/skill load demo", console, tmp_path, config, agent, state)

    # Main-loop seam: the goal joins the queued skill on the shared buffer.
    _queue_goal_reminder(agent, state.writer.meta.id)
    assert not agent.skill_context_buffer.is_empty()

    _consume(agent, "原始输入")
    last = _last_user_message(client)
    assert "## Loaded Skill: demo" in last
    assert "demo body" in last
    assert "[session goal] G" in last
    assert "原始输入" in last
