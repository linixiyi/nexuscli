"""Tests for the built-in ``/init`` command (workspace AGENTS.md bootstrap).

Covers the shared prompt builder, the ``-p "/init"`` expansion in cli.py
(including custom-command priority), and the REPL ``/init`` branch running on
the current agent.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.cli import _expand_custom_prompt
from nexuscli.entrypoints.repl import (
    SLASH_COMMANDS,
    PermissionModeController,
    ReplSessionState,
    _handle_slash,
)
from nexuscli.entrypoints.slash_commands import build_init_prompt
from nexuscli.session import SessionStore

# ---------------------------------------------------------------------------
# build_init_prompt
# ---------------------------------------------------------------------------


def test_build_init_prompt_contains_required_semantics(tmp_path):
    prompt = build_init_prompt("关注测试约定", str(tmp_path))

    assert "AGENTS.md" in prompt
    assert "增量更新" in prompt
    assert "覆盖" in prompt
    assert "write_file" in prompt
    assert "edit_file" in prompt
    assert "NEXUS.md" in prompt
    # notes are folded in verbatim
    assert "关注测试约定" in prompt
    # targets the workspace root, not the user home
    assert str(tmp_path) in prompt
    assert str(Path(tmp_path) / "AGENTS.md") in prompt


def test_build_init_prompt_without_notes_omits_notes_section(tmp_path):
    prompt = build_init_prompt("", str(tmp_path))

    assert "AGENTS.md" in prompt
    assert "重点关注" not in prompt


# ---------------------------------------------------------------------------
# _expand_custom_prompt (-p "/init" one-shot mode)
# ---------------------------------------------------------------------------


def test_expand_custom_prompt_expands_builtin_init(tmp_path, monkeypatch):
    # Redirect the user command scope away from the real home so no local
    # custom command can shadow the built-in one in this test.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    expanded = _expand_custom_prompt("/init 关注安全", str(tmp_path))

    assert expanded == build_init_prompt("关注安全", str(tmp_path))


def test_expand_custom_prompt_custom_init_command_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    project_dir = tmp_path / ".nexuscli" / "commands"
    project_dir.mkdir(parents=True)
    (project_dir / "init.md").write_text("自定义 init：$ARGUMENTS", encoding="utf-8")

    expanded = _expand_custom_prompt("/init 关注安全", str(tmp_path))

    assert expanded == "自定义 init：关注安全"


def test_expand_custom_prompt_leaves_unmatched_prompts_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    assert _expand_custom_prompt("/unknown-command x", str(tmp_path)) == "/unknown-command x"
    assert _expand_custom_prompt("plain message", str(tmp_path)) == "plain message"


# ---------------------------------------------------------------------------
# REPL /init branch
# ---------------------------------------------------------------------------


def _stub_renderer() -> SimpleNamespace:
    return SimpleNamespace(
        set_context_window=lambda *_args: None,
        start_run=lambda: None,
        newline=lambda: None,
        handle=lambda _event: None,
    )


def _capturing_agent(tmp_path, captured: list[str]) -> SimpleNamespace:
    async def run(prompt: str):
        captured.append(prompt)
        yield {"type": "done"}

    return SimpleNamespace(
        run=run,
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(max_context_window=128_000),
    )


def _run_slash(tmp_path, agent, raw: str) -> Console:
    console = Console(file=io.StringIO(), width=200)
    store = SessionStore(root=tmp_path / "sessions")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    should_exit = asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            _stub_renderer(),
            state,
        )
    )
    assert should_exit is False
    return console


def test_handle_slash_init_runs_current_agent_with_init_prompt(tmp_path):
    captured: list[str] = []
    agent = _capturing_agent(tmp_path, captured)

    _run_slash(tmp_path, agent, "/init 关注安全")

    assert captured == [build_init_prompt("关注安全", str(tmp_path))]


def test_handle_slash_init_without_args_is_legal(tmp_path):
    captured: list[str] = []
    agent = _capturing_agent(tmp_path, captured)

    _run_slash(tmp_path, agent, "/init")

    assert captured == [build_init_prompt("", str(tmp_path))]


def test_init_slash_command_is_listed_in_help():
    assert "/init" in SLASH_COMMANDS
