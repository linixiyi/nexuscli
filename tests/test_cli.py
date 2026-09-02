from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.config import NexusCliConfig
from nexuscli.session import SessionStore
from nexuscli.types import Message

runner = CliRunner()


def test_version_flag_reports_package_version():
    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert "nexuscli" in result.output


def test_help_advertises_sessions_and_resume_flags():
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for needle in ("--resume", "--continue", "sessions"):
        assert needle in result.output


def test_single_prompt_rejects_unknown_mode(tmp_path):
    result = runner.invoke(cli.app, ["-p", "do it", "--mode", "wizard", "--cwd", str(tmp_path)])

    assert result.exit_code != 0
    assert "mode must be react, plan, or team" in result.output


def test_single_prompt_rejects_unknown_worker_mode(tmp_path):
    result = runner.invoke(
        cli.app, ["-p", "do it", "--mode", "team", "--worker-mode", "solo", "--cwd", str(tmp_path)]
    )

    assert result.exit_code != 0
    assert "worker-mode must be react or plan" in result.output


def _patch_config(monkeypatch: pytest.MonkeyPatch, *, api_key: str | None) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = api_key
    monkeypatch.setattr(cli, "load_config", lambda project_root, overrides=None: config)
    return config


def test_single_prompt_without_api_key_fails_cleanly(tmp_path, monkeypatch):
    _patch_config(monkeypatch, api_key=None)

    result = runner.invoke(cli.app, ["-p", "hello", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "API key is not configured" in _all_output(result)


def test_sessions_command_lists_saved_transcripts(tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)
    store = SessionStore()
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="ship the release")])

    result = runner.invoke(cli.app, ["sessions", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert writer.meta.id in result.output
    assert "ship the release" in result.output


def test_sessions_command_says_when_empty(tmp_path, monkeypatch):
    _isolate_home(monkeypatch, tmp_path)

    result = runner.invoke(cli.app, ["sessions", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "No saved sessions" in result.output


def test_resume_with_unknown_id_exits(tmp_path, monkeypatch):
    _patch_config(monkeypatch, api_key="test-key")

    result = runner.invoke(
        cli.app, ["-p", "hello", "--resume", "no-such-id", "--cwd", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "Session not found" in _all_output(result)


def test_continue_without_history_exits(tmp_path, monkeypatch):
    _patch_config(monkeypatch, api_key="test-key")

    result = runner.invoke(cli.app, ["-p", "hello", "-c", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "No previous session" in _all_output(result)


def test_continue_loads_latest_session_into_single_prompt(tmp_path, monkeypatch):
    config = _patch_config(monkeypatch, api_key="test-key")
    _isolate_home(monkeypatch, tmp_path)
    store = SessionStore()
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    convo = [
        Message(role="user", content="earlier task"),
        Message(role="assistant", content="done"),
    ]
    writer.append(convo)

    captured = {}

    async def fake_run_prompt(prompt, cwd, config_arg, *, mode, worker_mode, json_output, history):
        captured["history"] = history
        captured["cwd"] = cwd

    monkeypatch.setattr(cli, "_run_prompt", fake_run_prompt)

    result = runner.invoke(cli.app, ["-p", "continue working", "-c", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert captured["history"] == convo
    assert captured["cwd"] == str(tmp_path)
    _ = config


def test_resume_rejected_for_non_react_single_prompt_modes(tmp_path, monkeypatch):
    _patch_config(monkeypatch, api_key="test-key")
    _isolate_home(monkeypatch, tmp_path)
    store = SessionStore()
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="earlier task")])

    captured = {}

    async def fake_run_prompt(prompt, cwd, config_arg, *, mode, worker_mode, json_output, history):
        captured["history"] = history

    monkeypatch.setattr(cli, "_run_prompt", fake_run_prompt)

    result = runner.invoke(
        cli.app,
        ["-p", "next", "--mode", "plan", "--resume", writer.meta.id, "--cwd", str(tmp_path)],
    )

    assert result.exit_code == 0
    assert captured["history"] == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def _all_output(result) -> str:
    parts = [result.output]
    with contextlib.suppress(ValueError, AttributeError):
        parts.append(result.stderr)
    return "".join(parts)
