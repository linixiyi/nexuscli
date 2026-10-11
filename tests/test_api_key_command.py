"""Tests for the ``!command`` resolution of the LLM credential (pi-compatible
secret-manager form).

Covers:
- the ``!command`` form in the ``llm`` config section resolves the secret
  from the command's first stdout line at load time;
- multi-line stdout takes the first line;
- a failing / missing command degrades to an empty value (the CLI still
  starts, the agent later reports the normal "not configured" error);
- an environment-variable credential still wins over the ``!command`` form;
- a plain (non-``!``) config value is passed through untouched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nexuscli.config import load_config

# The resolver runs commands without a shell, so the test exercises it with
# a plain executable + arguments. sys.executable is spelled with forward
# slashes because shlex (posix mode) would eat backslashes; Windows
# CreateProcess accepts forward-slash paths.
_PY = Path(sys.executable).as_posix()

# Placeholder outputs, obviously not real credentials: the point of the
# tests is plumbing (which line of stdout wins, failure degradation,
# precedence), never the secret itself.
_STUB_CMD = "stub-placeholder-value"
_STUB_ENV = "stub-env-value"
_ENV_NAME = "NEXUSCLI_API_KEY"


def _write_config(project: Path, llm_value: str) -> None:
    (project / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (project / ".nexuscli" / "config.json").write_text(
        json.dumps({"llm": {"provider": "deepseek", "api_key": llm_value}}),
        encoding="utf-8",
    )


def _command_config(project: Path) -> None:
    _write_config(project, f"!{_PY} -c \"print('{_STUB_CMD}')\"")


def _load(project: Path, tmp_path, monkeypatch, env: dict[str, str] | None = None):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    for var in (_ENV_NAME, "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return load_config(project_root=project, env=env or {})


def test_command_form_resolves_from_stdout(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _command_config(project)

    config = _load(project, tmp_path, monkeypatch)

    assert config.llm.api_key == _STUB_CMD


def test_command_form_takes_first_stdout_line(tmp_path, monkeypatch):
    project = tmp_path / "project"
    # The quoted \n stays literal for shlex, then Python's string literal
    # turns it into a real newline: stdout is "first\nsecond\n".
    _write_config(project, f"!{_PY} -c \"print('first\\nsecond')\"")

    config = _load(project, tmp_path, monkeypatch)

    assert config.llm.api_key == "first"


def test_command_form_failure_degrades_to_empty(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write_config(project, "!nexuscli-missing-secret-binary-xyz read MyKey")

    config = _load(project, tmp_path, monkeypatch)

    assert config.llm.api_key == ""


def test_command_form_empty_output_degrades_to_empty(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write_config(project, f'!{_PY} -c "import sys; sys.exit(0)"')

    config = _load(project, tmp_path, monkeypatch)

    assert config.llm.api_key == ""


def test_env_value_wins_over_command_form(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _command_config(project)

    config = _load(project, tmp_path, monkeypatch, env={_ENV_NAME: _STUB_ENV})

    assert config.llm.api_key == _STUB_ENV


def test_plain_value_untouched(tmp_path, monkeypatch):
    project = tmp_path / "project"
    _write_config(project, "plain-config-value")

    config = _load(project, tmp_path, monkeypatch)

    assert config.llm.api_key == "plain-config-value"


def test_public_dict_masks_resolved_value(tmp_path, monkeypatch):
    from nexuscli.config import config_to_public_dict

    project = tmp_path / "project"
    _command_config(project)

    config = _load(project, tmp_path, monkeypatch)
    public = config_to_public_dict(config)

    assert public["llm"]["api_key"] == "***"
