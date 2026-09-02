from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    ReplSessionState,
    _format_toolbar_bar,
    _format_toolbar_percent,
    _handle_slash,
    _parse_mode_argument,
    _permission_mode_label,
    _plural_label,
    _shorten_home,
)
from nexuscli.session import SessionStore
from nexuscli.types import Message


def test_parse_mode_argument_defaults_to_react():
    mode, prompt = _parse_mode_argument("just do the thing")
    assert (mode, prompt) == ("react", "just do the thing")


def test_parse_mode_argument_supports_mode_flags():
    assert _parse_mode_argument("--mode plan audit the repo") == ("plan", "audit the repo")
    assert _parse_mode_argument("--plan audit the repo") == ("plan", "audit the repo")
    assert _parse_mode_argument("-m team do it", allowed={"react", "plan", "team"})[0] == "team"


def test_parse_mode_argument_rejects_missing_task_text():
    try:
        _parse_mode_argument("--mode plan")
    except ValueError as exc:
        assert "task text" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    try:
        _parse_mode_argument("--mode wizard do it")
    except ValueError as exc:
        assert "react" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_permission_mode_controller_toggle_restores_policy_defaults():
    config = NexusCliConfig()
    controller = PermissionModeController(config)
    original_hitl = config.policy.hitl_mode

    assert controller.mode == "default"

    controller.toggle()
    assert controller.mode == "auto"
    assert config.policy.hitl_mode == "never"

    controller.toggle()
    assert controller.mode == "default"
    assert config.policy.hitl_mode == original_hitl


def test_toolbar_and_label_helpers():
    assert _permission_mode_label("auto") == "Auto (full access)"
    assert _permission_mode_label("default") == "Default"
    assert _plural_label(1, "skill") == "skill"
    assert _plural_label(2, "skill") == "skills"
    assert _format_toolbar_bar(0.5, width=4) == "██░░"
    assert _format_toolbar_bar(2.0, width=4) == "████"
    assert _format_toolbar_percent(0.005) == "<1%"
    assert _format_toolbar_percent(0.25) == "25%"


def test_shorten_home_replaces_home_prefix():
    home = Path.home()
    assert _shorten_home(str(home)) == "~"
    expected_sub = "~/" + os.sep.join(["projects", "demo"])
    assert _shorten_home(str(home / "projects" / "demo")) == expected_sub
    assert _shorten_home("/elsewhere") == "/elsewhere"


def test_handle_slash_resume_loads_session_into_agent(tmp_path):
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    convo = [Message(role="user", content="fix the login bug")]
    writer.append(convo)
    state = ReplSessionState(store=store, writer=writer)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = Console(file=_string_io(), width=200)

    should_exit = asyncio.run(
        _handle_slash(
            f"/resume {writer.meta.id}",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert should_exit is False
    assert agent.history == convo
    assert state.writer.meta.id == writer.meta.id
    assert "Resumed session" in _console_text(console)


def test_handle_slash_resume_without_arg_lists_sessions(tmp_path):
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="list me")])
    state = ReplSessionState(store=store, writer=writer)
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = Console(file=_string_io(), width=200)

    asyncio.run(
        _handle_slash(
            "/resume",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    text = _console_text(console)
    assert writer.meta.id in text
    assert "list me" in text


def test_handle_slash_resume_unknown_id_reports_error(tmp_path):
    store = SessionStore(root=tmp_path / "sessions")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = Console(file=_string_io(), width=200)

    asyncio.run(
        _handle_slash(
            "/resume 20990101-000000-ffffff",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert "Session not found" in _console_text(console)
    assert agent.history == []


def test_handle_slash_clear_starts_new_writer(tmp_path):
    store = SessionStore(root=tmp_path / "sessions")
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="old talk")])
    state = ReplSessionState(store=store, writer=writer)
    agent = SimpleNamespace(
        history=[Message(role="user", content="old talk")],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="deepseek"),
        clear_history=lambda: setattr(agent, "history", []),
    )
    console = Console(file=_string_io(), width=200)

    asyncio.run(
        _handle_slash(
            "/clear",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert agent.history == []
    assert state.writer.meta.id != writer.meta.id
    assert state.writer.persisted == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _string_io():
    return io.StringIO()


def _console_text(console: Console) -> str:
    return console.file.getvalue()
