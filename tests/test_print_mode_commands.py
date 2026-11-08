"""Tests for ``-p "/cmd"`` consuming a custom command's frontmatter.

The one-shot path must apply the same frontmatter hints the REPL applies
(entrypoints/commands/custom_cmds.py): ``mode: plan`` turns on the executor's
read-only gate that rejects write tools (without selecting the
Plan-and-Execute runner), ``allowed-tools`` narrows the run's tool registry
to the listed tools (empty keeps the default), and unknown/illegal frontmatter
values fail loudly with a readable error instead of being silently ignored.
"""

from __future__ import annotations

import asyncio

import pytest
import typer
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import QueryEngine
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.slash_commands import (
    CommandFrontmatterError,
    CustomCommand,
    ensure_usable_frontmatter,
    load_slash_commands,
)
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


def _write_project_command(project, name: str, text: str) -> None:
    directory = project / ".nexuscli" / "commands"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


# One terminal event so the stubbed engine satisfies _complete_from_events.
_DONE_EVENT = {
    "type": "done",
    "total_turns": 1,
    "total_tokens": 10,
    "usage": {"input_tokens": 6, "output_tokens": 4, "total_tokens": 10},
    "cost": {"usd": {"total_cost": 0.0}, "cny": {"total_cost": 0.0}},
}


class _StubEngine:
    """QueryEngine double: record constructor kwargs, yield one done event.

    Only ``ask`` is implemented on purpose: the plan/team engine methods are
    missing, so an end-to-end run exiting 0 proves the run took the react
    path the frontmatter (or its absence) selects.
    """

    _complete_from_events = QueryEngine._complete_from_events

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def ask(self, message, history=None):
        yield _DONE_EVENT


def _install_stub_engine(monkeypatch, captured: dict, registry=None) -> None:
    async def fake_build_registry(config, cwd):
        return registry, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    monkeypatch.setattr(
        cli, "QueryEngine", lambda **kwargs: captured.update(kwargs) or _StubEngine()
    )


# ---------------------------------------------------------------------------
# Expansion carries the matched command
# ---------------------------------------------------------------------------


def test_print_expansion_returns_command_with_frontmatter(tmp_path):
    _write_project_command(
        tmp_path,
        "ship.md",
        "---\n"
        "description: Ship it\n"
        "mode: plan\n"
        "allowed-tools: read_file, glob , read_file\n"
        "---\n"
        "Ship $ARGUMENTS",
    )

    expanded = cli._expand_custom_prompt("/ship the release", str(tmp_path))

    assert expanded == "Ship the release"
    assert expanded.command is not None
    assert expanded.command.source == "project"
    assert expanded.command.mode == "plan"
    assert expanded.command.allowed_tools == ("read_file", "glob")


def test_print_expansion_without_command_carries_none(tmp_path):
    # Plain messages, unknown /names and the built-in /init have no custom
    # command behind them, so there is no frontmatter to apply.
    plain = cli._expand_custom_prompt("plain question", str(tmp_path))
    unknown = cli._expand_custom_prompt("/no-such-command hi", str(tmp_path))
    builtin_init = cli._expand_custom_prompt("/init focus on tests", str(tmp_path))

    assert plain == "plain question" and plain.command is None
    assert unknown == "/no-such-command hi" and unknown.command is None
    assert builtin_init.command is None
    assert "AGENTS.md" in builtin_init


# ---------------------------------------------------------------------------
# mode: plan — the read-only gate rejects write tools
# ---------------------------------------------------------------------------


def test_print_mode_plan_gate_rejects_write_tool(tmp_path):
    _write_project_command(tmp_path, "planit.md", "---\nmode: plan\n---\nPlan $ARGUMENTS")
    command = cli._expand_custom_prompt("/planit the refactor", str(tmp_path)).command

    config = NexusCliConfig()
    selected_mode, allowed_tools = cli._apply_prompt_command_frontmatter(config, command, None)

    # Frontmatter plan is the REPL's read-only permission gate, not the
    # Plan-and-Execute runner: the react runner keeps running, the gate
    # rejects every tool that is not read-only.
    assert selected_mode == "react"
    assert allowed_tools == ()
    assert config.policy.plan_mode is True

    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    executor = ToolExecutor(registry)
    context = ToolContext(cwd=str(tmp_path), config=config)

    write = asyncio.run(
        executor.execute_all(
            [
                {
                    "id": "call-write",
                    "name": "write_file",
                    "arguments": {"path": "evil.txt", "content": "nope"},
                }
            ],
            context,
        )
    )[0]
    read = asyncio.run(
        executor.execute_all(
            [{"id": "call-read", "name": "read_file", "arguments": {"path": "notes.txt"}}],
            context,
        )
    )[0]

    assert write.is_error
    assert "plan mode" in write.content
    assert not read.is_error


def test_print_explicit_mode_flag_outranks_frontmatter(tmp_path):
    _write_project_command(tmp_path, "teamit.md", "---\nmode: team\n---\nGo.")
    teamit = cli._expand_custom_prompt("/teamit", str(tmp_path)).command
    config = NexusCliConfig()

    selected_mode, _ = cli._apply_prompt_command_frontmatter(config, teamit, "plan")

    assert selected_mode == "plan"
    assert config.policy.plan_mode is True

    # The gate is OR-ed: frontmatter plan never weakens an explicit flag, and
    # flag react + frontmatter plan still runs read-only.
    _write_project_command(tmp_path, "careful.md", "---\nmode: plan\n---\nCareful.")
    careful = cli._expand_custom_prompt("/careful", str(tmp_path)).command

    selected_mode, _ = cli._apply_prompt_command_frontmatter(config, careful, "react")

    assert selected_mode == "react"
    assert config.policy.plan_mode is True


def test_print_frontmatter_team_selects_team_runner():
    command = CustomCommand(name="teamit", body="Go.", mode="team")
    config = NexusCliConfig()

    selected_mode, allowed_tools = cli._apply_prompt_command_frontmatter(config, command, None)

    assert selected_mode == "team"
    assert not config.policy.plan_mode
    assert allowed_tools == ()


def test_print_plan_command_runs_react_behind_readonly_gate(tmp_path, monkeypatch):
    _write_project_command(tmp_path, "planit.md", "---\nmode: plan\n---\nPlan it.")
    captured: dict = {}
    _install_stub_engine(monkeypatch, captured)

    result = CliRunner().invoke(
        cli.app,
        ["-p", "/planit", "--cwd", str(tmp_path), "--api-key", "unit-test-value"],
    )

    # Exit 0 with an ask-only stub engine proves the react runner; the config
    # reaching the engine proves the frontmatter turned the read-only gate on.
    assert result.exit_code == 0, result.output
    assert captured["config"].policy.plan_mode is True


# ---------------------------------------------------------------------------
# Unknown/illegal frontmatter values fail loudly
# ---------------------------------------------------------------------------


def test_ensure_usable_frontmatter_names_file_value_and_legal_modes(tmp_path):
    _write_project_command(tmp_path, "yolo.md", "---\nmode: yolo\ndescription: skip\n---\nBody.")
    command = load_slash_commands(str(tmp_path), home=tmp_path)["yolo"]

    with pytest.raises(CommandFrontmatterError) as excinfo:
        ensure_usable_frontmatter(command)

    message = str(excinfo.value)
    assert "yolo.md" in message
    assert "'yolo'" in message
    for legal in ("react", "plan", "team"):
        assert legal in message
    # The parse layer keeps the usable fields intact for lenient consumers:
    # no usable mode hint, the rest of the file verbatim.
    assert command.mode == ""
    assert command.description == "skip"


def test_print_unknown_mode_exits_before_any_run(tmp_path, capsys):
    _write_project_command(tmp_path, "yolo.md", "---\nmode: yolo\n---\nBody.")
    command = cli._expand_custom_prompt("/yolo", str(tmp_path)).command
    config = NexusCliConfig()

    with pytest.raises(typer.Exit) as excinfo:
        cli._apply_prompt_command_frontmatter(config, command, None)

    assert excinfo.value.exit_code == 1
    err = capsys.readouterr().err
    assert "yolo" in err
    assert "mode" in err
    # Nothing was applied: the run never started with a guessed mode.
    assert not config.policy.plan_mode


def test_print_unknown_mode_cli_fails_with_readable_error(tmp_path):
    _write_project_command(tmp_path, "yolo.md", "---\nmode: yolo\n---\nBody.")

    result = CliRunner().invoke(cli.app, ["-p", "/yolo", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "Invalid frontmatter" in result.output
    assert "'yolo'" in result.output
    assert "react, plan, team" in result.output


# ---------------------------------------------------------------------------
# allowed-tools — the registry narrows to the listed tools
# ---------------------------------------------------------------------------


def test_narrow_registry_keeps_only_listed_tools():
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())

    narrowed, missing = cli._narrow_registry(registry, ("read_file", "glob"))

    assert narrowed.list_names() == ["glob", "read_file"]
    assert missing == ()


def test_narrow_registry_reports_unknown_names():
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())

    narrowed, missing = cli._narrow_registry(registry, ("read_file", "no_such_tool"))

    assert narrowed.list_names() == ["read_file"]
    assert missing == ("no_such_tool",)


def test_print_allowed_tools_command_runs_with_narrowed_registry(tmp_path, monkeypatch):
    _write_project_command(
        tmp_path,
        "readit.md",
        "---\nallowed-tools: read_file, glob, no_such_tool\n---\nRead it.",
    )
    captured: dict = {}
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    _install_stub_engine(monkeypatch, captured, registry=registry)

    result = CliRunner().invoke(
        cli.app,
        ["-p", "/readit", "--cwd", str(tmp_path), "--api-key", "unit-test-value"],
    )

    assert result.exit_code == 0, result.output
    assert captured["tool_registry"].list_names() == ["glob", "read_file"]
    # Unknown names are reported on stderr, not silently dropped.
    assert "no_such_tool" in result.output


def test_print_command_without_allowed_tools_keeps_default_registry(tmp_path, monkeypatch):
    _write_project_command(tmp_path, "plain.md", "Just do it.")
    captured: dict = {}
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    _install_stub_engine(monkeypatch, captured, registry=registry)

    result = CliRunner().invoke(
        cli.app,
        ["-p", "/plain", "--cwd", str(tmp_path), "--api-key", "unit-test-value"],
    )

    assert result.exit_code == 0, result.output
    assert captured["tool_registry"].list_names() == registry.list_names()
    assert not captured["config"].policy.plan_mode


# ---------------------------------------------------------------------------
# mode + allowed-tools together
# ---------------------------------------------------------------------------


def test_print_plan_command_with_allowed_tools_applies_both(tmp_path, monkeypatch):
    _write_project_command(
        tmp_path,
        "audit.md",
        "---\nmode: plan\nallowed-tools: read_file, grep\n---\nAudit the module.",
    )
    captured: dict = {}
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    _install_stub_engine(monkeypatch, captured, registry=registry)

    result = CliRunner().invoke(
        cli.app,
        ["-p", "/audit src/cli.py", "--cwd", str(tmp_path), "--api-key", "unit-test-value"],
    )

    assert result.exit_code == 0, result.output
    assert captured["config"].policy.plan_mode is True
    # Only the whitelisted read-only tools survive: the plan gate rejects the
    # writes and the narrowed registry never offers anything else.
    assert captured["tool_registry"].list_names() == ["grep", "read_file"]
