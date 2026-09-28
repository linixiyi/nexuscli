"""Tests for the configurable agent turn limit (``agent.max_turns``).

Covers:
- the react loop emitting an explicit ``warning`` event when ``max_turns``
  cuts a still-running tool loop short (instead of silently truncating);
- no warning when the model finishes naturally;
- config parsing of the ``agent`` section (default 200, override, clamping);
- pass-through of ``config.agent.max_turns`` into the ``Agent`` constructor
  from both the QueryEngine and the REPL entrypoints (spied kwargs);
- the single-shot complete path (``ask_complete_async`` /
  ``_complete_from_events``, used by ``-p`` mode) surfacing the warning on
  stderr instead of silently dropping it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from nexuscli.agent import Agent
from nexuscli.agent.query_engine import QueryEngine
from nexuscli.config import AgentConfig, NexusCliConfig, config_to_public_dict, load_config
from nexuscli.entrypoints.repl import start_repl
from nexuscli.tools import ToolRegistry, get_builtin_tools


class _AlwaysToolUseClient:
    """Fake LLM that keeps the tool loop alive (stop_reason=tool_use) without
    ever emitting an actual tool call, so the loop runs until the turn limit
    with a minimal, deterministic event stream."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "message_end", "stop_reason": "tool_use"}


class _NaturalEndClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "all done"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _RepeatedToolUseClient:
    """Fake LLM that requests a real read_file tool call every turn."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": "call_1",
                "function": {"name": "read_file", "arguments": '{"path":"note.txt"}'},
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}


def _agent_config(monkeypatch, tmp_path):
    """Config for direct Agent construction; the API key comes from the
    environment (same pattern as tests/test_query.py) so no credential
    literal lives in the source, and the user-level config lookup is
    redirected into tmp_path."""
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    # The turn-limit tests assert exact event streams; context compression
    # would inject context_compressed events and is not what they cover.
    config.features.context_compression = False
    return config


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


def _load_config_with_agent_section(
    tmp_path: Path, monkeypatch: Any, agent_section: dict[str, Any] | None
) -> NexusCliConfig:
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (project / ".nexuscli").mkdir(parents=True, exist_ok=True)
    if agent_section is not None:
        (project / ".nexuscli" / "config.json").write_text(
            json.dumps({"agent": agent_section}),
            encoding="utf-8",
        )
    # Redirect the user-level config lookup (Path.home() based) into tmp_path.
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    return load_config(project_root=project)


def test_react_loop_warns_when_turn_limit_reached(tmp_path, monkeypatch):
    agent = Agent(
        llm_client=_AlwaysToolUseClient(),
        tool_registry=ToolRegistry(),
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=2,
    )

    events = asyncio.run(_collect(agent, "loop forever"))

    types = [event["type"] for event in events]
    assert types == ["turn_complete", "turn_complete", "warning", "done"]
    warning = events[-2]
    assert "max_turns" in str(warning.get("message"))
    assert "max_turns limit (2)" in str(warning.get("message"))


def test_react_loop_does_not_warn_on_natural_completion(tmp_path, monkeypatch):
    agent = Agent(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=5,
    )

    events = asyncio.run(_collect(agent, "just answer"))

    types = [event["type"] for event in events]
    assert "warning" not in types
    assert types[-1] == "done"
    assert events[-1]["total_turns"] == 1


def test_react_loop_warns_when_real_tool_loop_hits_limit(tmp_path, monkeypatch):
    (tmp_path / "note.txt").write_text("1: hello\n", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    agent = Agent(
        llm_client=_RepeatedToolUseClient(),
        tool_registry=registry,
        config=_agent_config(monkeypatch, tmp_path),
        cwd=str(tmp_path),
        max_turns=2,
    )

    events = asyncio.run(_collect(agent, "read the note repeatedly"))

    types = [event["type"] for event in events]
    # Both turns actually ran the tool, then the stream ends warning + done.
    assert types.count("tool_result") == 2
    assert types[-2:] == ["warning", "done"]
    assert "max_turns" in str(events[-2].get("message"))


def test_agent_max_turns_config_default_and_override(tmp_path, monkeypatch):
    assert AgentConfig().max_turns == 200

    config = _load_config_with_agent_section(tmp_path, monkeypatch, None)
    assert config.agent.max_turns == 200

    config = _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": 5})
    assert config.agent.max_turns == 5


def test_agent_max_turns_clamped_to_at_least_one(tmp_path, monkeypatch):
    assert (
        _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": 0}).agent.max_turns
        == 1
    )
    assert (
        _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": -7}).agent.max_turns
        == 1
    )
    # Non-numeric values fall back to the default instead of raising.
    assert (
        _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": "abc"}).agent.max_turns
        == 200
    )
    # Unknown keys are dropped silently and valid values still apply.
    config = _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": 9, "bogus": True})
    assert config.agent.max_turns == 9


def test_agent_section_in_public_dict(tmp_path, monkeypatch):
    config = _load_config_with_agent_section(tmp_path, monkeypatch, {"max_turns": 5})
    assert config_to_public_dict(config)["agent"] == {"max_turns": 5}


def test_query_engine_passes_config_max_turns_to_agent(monkeypatch):
    captured: dict[str, Any] = {}

    class SpyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.history = []
            self.llm_client = kwargs["llm_client"]

        async def run(self, message):
            yield {
                "type": "done",
                "total_turns": 0,
                "total_tokens": 0,
                "usage": {},
                "messages": [],
            }

    monkeypatch.setattr("nexuscli.agent.query_engine.Agent", SpyAgent)
    config = NexusCliConfig()
    config.agent.max_turns = 7
    engine = QueryEngine(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=".",
    )

    asyncio.run(engine.ask_complete_async("hi"))

    assert captured["max_turns"] == 7


def test_ask_complete_prints_max_turns_warning_to_stderr(tmp_path, monkeypatch, capsys):
    """The -p single-shot path has no renderer, so the warning event that the
    agent emits on hitting max_turns must be printed to stderr by the
    QueryEngine instead of being silently dropped."""
    config = _agent_config(monkeypatch, tmp_path)
    config.agent.max_turns = 2
    engine = QueryEngine(
        llm_client=_AlwaysToolUseClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
    )

    result = asyncio.run(engine.ask_complete_async("loop forever"))
    captured = capsys.readouterr()

    assert "max_turns limit (2)" in captured.err
    assert result.turns == 2


def test_ask_complete_writes_nothing_to_stderr_without_warning(tmp_path, monkeypatch, capsys):
    config = _agent_config(monkeypatch, tmp_path)
    engine = QueryEngine(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
    )

    result = asyncio.run(engine.ask_complete_async("just answer"))
    captured = capsys.readouterr()

    assert captured.err == ""
    assert result.text == "all done"


def test_complete_from_events_surfaces_warning_event(capsys):
    """A hand-built fake event stream containing a warning event, consumed
    through ``_complete_from_events`` (shared by ask/plan/team complete),
    must not lose the warning text."""
    engine = QueryEngine(
        llm_client=_NaturalEndClient(),
        tool_registry=ToolRegistry(),
        config=NexusCliConfig(),
        cwd=".",
    )

    async def fake_events():
        yield {"type": "text_delta", "text": "partial answer"}
        yield {"type": "warning", "message": "Reached the agent.max_turns limit (2)"}
        yield {"type": "done", "total_turns": 2, "total_tokens": 10, "usage": {}}

    result = asyncio.run(engine._complete_from_events(fake_events()))
    captured = capsys.readouterr()

    assert "Reached the agent.max_turns limit (2)" in captured.err
    assert result.text == "partial answer"
    assert result.turns == 2


def test_repl_passes_config_max_turns_to_agent(tmp_path, monkeypatch):
    captured: dict[str, Any] = {}
    home = tmp_path / "home"
    home.mkdir()

    class StubClient:
        model_name = "fake-model"
        provider_name = "fake-provider"
        max_context_window = 1000

    class SpyAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.history = []
            self.cwd = kwargs["cwd"]
            self.llm_client = StubClient()
            self.mode = "react"
            self.tool_registry = kwargs["tool_registry"]

    class StubPromptSession:
        def __init__(self, *args, **kwargs):
            pass

        async def prompt_async(self):
            raise EOFError

    async def fake_build_tool_registry(*, config, cwd):  # noqa: ARG001
        return ToolRegistry(), None

    monkeypatch.setattr("nexuscli.entrypoints.repl.build_tool_registry", fake_build_tool_registry)
    monkeypatch.setattr("nexuscli.entrypoints.repl.create_llm_client", lambda config: StubClient())
    monkeypatch.setattr("nexuscli.entrypoints.repl.Agent", SpyAgent)
    monkeypatch.setattr("nexuscli.entrypoints.repl.PromptSession", StubPromptSession)
    # Redirect Path.home() (session store, prompt history) into tmp_path.
    monkeypatch.setattr(Path, "home", lambda: home)

    config = NexusCliConfig()
    config.agent.max_turns = 42
    asyncio.run(start_repl(str(tmp_path), config))

    assert captured["max_turns"] == 42
