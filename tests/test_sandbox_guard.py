"""Tests for the subprocess environment sanitization guard (policy/sandbox.py).

Covers the sanitizer itself, both agent-facing spawn points (foreground
bash and bash run_in_background), and the ``policy.env_sanitization``
kill switch. Child commands are spawned as small script files instead of
``-c`` one-liners — the same quoting-free convention as test_background.py,
so the command lines stay valid on every platform. The child never echoes
environment *values*: it reports only whether each key is PRESENT/ABSENT,
so no variable content leaks into the test output.

Every value used here is a dummy placeholder, not a credential.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import pytest

from nexuscli.config import PolicyConfig, load_config
from nexuscli.policy import sanitize_environment
from nexuscli.policy.sandbox import ESSENTIAL_KEYS
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.background import background_registry
from nexuscli.tools.base import ToolContext

# Placeholder values: deliberately not usable credentials.
_DUMMY = "dummy-value-not-a-credential"

# Secret-shaped probe keys exercising every blocklist pattern.
_SECRET_KEYS = (
    "FAKE_API_KEY",
    "SECRET_A",
    "DB_PASSWORD",
    "GH_TOKEN_VALUE",
    "AWS_CREDENTIALS_FILE",
    "SSH_PRIVATE_KEY_PROBE",
)

_PRINT_KEYS_BODY = (
    "import os, sys\n"
    "for name in sys.argv[1:]:\n"
    "    present = os.environ.get(name) is not None\n"
    "    print(name, 'PRESENT' if present else 'ABSENT')\n"
)


@pytest.fixture
def sandbox_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the config/background home into a temp directory.

    load_config and the background registry resolve paths through
    Path.home(); on Windows Path.home() reads USERPROFILE (not HOME), so
    both are redirected, mirroring the repo's HOME-patching convention.
    Keeps the tests away from the real ~/.nexuscli.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _base_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin", "HOME": "/home/tester", "TEMP": "/tmp", "SYSTEMROOT": "C:\\Windows"}
    for key in _SECRET_KEYS:
        env[key] = _DUMMY
    env["NEXUSCLI_API_KEY"] = _DUMMY
    env["nexuscli_debug"] = "1"
    return env


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    return '"' + sys.executable + '" "' + str(script) + '"'


def _probe_command(tmp_path: Path, keys: tuple[str, ...]) -> str:
    return _write_script(tmp_path, "probe_env.py", _PRINT_KEYS_BODY) + " " + " ".join(keys)


def _make_context(tmp_path: Path) -> ToolContext:
    config = load_config(project_root=tmp_path, env={})
    config.policy.hitl_mode = "never"
    return ToolContext(cwd=str(tmp_path), config=config)


def _tool(name: str):
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get(name)
    assert tool is not None
    return tool


# ---------------------------------------------------------------------------
# sanitize_environment unit tests
# ---------------------------------------------------------------------------


def test_sanitize_drops_secret_shaped_and_internal_keys():
    result = sanitize_environment(base=_base_env())

    for key in _SECRET_KEYS:
        assert key not in result, key
    assert "NEXUSCLI_API_KEY" not in result
    assert "nexuscli_debug" not in result


def test_sanitize_keeps_essential_whitelist():
    result = sanitize_environment(base=_base_env())

    assert result["PATH"] == "/usr/bin"
    assert result["HOME"] == "/home/tester"
    assert result["TEMP"] == "/tmp"
    assert result["SYSTEMROOT"] == "C:\\Windows"
    for key in ("PATH", "HOME", "TEMP", "TMP", "SYSTEMROOT"):
        assert key in ESSENTIAL_KEYS


def test_sanitize_matching_is_case_insensitive():
    result = sanitize_environment(
        base={
            "my_secret_value": "x",
            "Github_Token_x": "y",
            "clientpassword": "z",
            "PRIVATE_KEY_PEM": "w",
            "SAFE_NAME": "keep",
        }
    )

    for key in ("my_secret_value", "Github_Token_x", "clientpassword", "PRIVATE_KEY_PEM"):
        assert key not in result, key
    assert result["SAFE_NAME"] == "keep"


def test_sanitize_default_base_reads_process_environ(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SANDBOX_TEST_TOKEN", _DUMMY)
    monkeypatch.setenv("SANDBOX_TEST_SAFE", "ok")

    result = sanitize_environment()

    assert "SANDBOX_TEST_TOKEN" not in result
    assert result["SANDBOX_TEST_SAFE"] == "ok"


def test_sanitize_overrides_are_applied_verbatim():
    result = sanitize_environment(
        base={"SECRET_A": "x", "PATH": "/bin"},
        overrides={"EXTRA": "1", "SECRET_A": "trusted"},
    )

    # Overrides win after filtering: caller-supplied, therefore trusted.
    assert result["SECRET_A"] == "trusted"
    assert result["EXTRA"] == "1"
    assert result["PATH"] == "/bin"


# ---------------------------------------------------------------------------
# Config switch
# ---------------------------------------------------------------------------


def test_policy_config_defaults_to_sanitization_on(sandbox_home: Path):
    assert PolicyConfig().env_sanitization is True
    assert load_config(project_root=None, env={}).policy.env_sanitization is True


def test_env_sanitization_reads_from_project_config_json(tmp_path: Path, sandbox_home: Path):
    project_config = tmp_path / ".nexuscli"
    project_config.mkdir()
    (project_config / "config.json").write_text(
        json.dumps({"policy": {"env_sanitization": False}}), encoding="utf-8"
    )

    config = load_config(project_root=tmp_path, env={})

    assert config.policy.env_sanitization is False


# ---------------------------------------------------------------------------
# Foreground bash integration
# ---------------------------------------------------------------------------


def test_bash_child_env_hides_secrets(tmp_path: Path, sandbox_home: Path, monkeypatch):
    for key in _SECRET_KEYS:
        monkeypatch.setenv(key, _DUMMY)
    monkeypatch.setenv("NEXUSCLI_INTERNAL_PROBE", _DUMMY)
    context = _make_context(tmp_path)
    command = _probe_command(tmp_path, (*_SECRET_KEYS, "NEXUSCLI_INTERNAL_PROBE", "PATH"))

    result = asyncio.run(_tool("bash").execute({"command": command}, context))

    assert not result.is_error, result.content
    for key in (*_SECRET_KEYS, "NEXUSCLI_INTERNAL_PROBE"):
        assert f"{key} ABSENT" in result.content, key
    assert "PATH PRESENT" in result.content


def test_env_sanitization_disabled_restores_full_environ(
    tmp_path: Path, sandbox_home: Path, monkeypatch
):
    monkeypatch.setenv("FAKE_API_KEY", _DUMMY)
    context = _make_context(tmp_path)
    context.config.policy.env_sanitization = False
    command = _probe_command(tmp_path, ("FAKE_API_KEY", "PATH"))

    result = asyncio.run(_tool("bash").execute({"command": command}, context))

    assert not result.is_error, result.content
    assert "FAKE_API_KEY PRESENT" in result.content
    assert "PATH PRESENT" in result.content


# ---------------------------------------------------------------------------
# Background path integration
# ---------------------------------------------------------------------------


def test_background_child_env_hides_secrets(tmp_path: Path, sandbox_home: Path, monkeypatch):
    monkeypatch.setenv("FAKE_API_KEY", _DUMMY)
    monkeypatch.setenv("NEXUSCLI_INTERNAL_PROBE", _DUMMY)
    context = _make_context(tmp_path)
    command = _probe_command(tmp_path, ("FAKE_API_KEY", "NEXUSCLI_INTERNAL_PROBE", "PATH"))

    async def scenario() -> str:
        result = await _tool("bash").execute(
            {"command": command, "run_in_background": True}, context
        )
        match = re.search(r"Started background task ([0-9a-f]{12})\.", result.content)
        assert match, result.content
        task_id = match.group(1)
        # Poll the merged log until the probe flushed; stop before the loop
        # closes so no transport is left running.
        deadline = time.monotonic() + 5.0
        tail = ""
        while time.monotonic() < deadline:
            tail = background_registry.read_tail(task_id)
            if "FAKE_API_KEY" in tail:
                break
            await asyncio.sleep(0.2)
        await background_registry.stop(task_id)
        return tail

    tail = asyncio.run(scenario())

    assert "FAKE_API_KEY ABSENT" in tail
    assert "NEXUSCLI_INTERNAL_PROBE ABSENT" in tail
    assert "PATH PRESENT" in tail


def test_background_start_env_defaults_to_parent_environ(
    tmp_path: Path, sandbox_home: Path, monkeypatch
):
    """start(env=None) keeps the historical inherit-full-environ behaviour."""
    monkeypatch.setenv("FAKE_API_KEY", _DUMMY)
    command = _probe_command(tmp_path, ("FAKE_API_KEY",))

    async def scenario() -> str:
        from nexuscli.tools.background import BackgroundTaskRegistry

        registry = BackgroundTaskRegistry()
        task = await registry.start(command, cwd=str(tmp_path))
        deadline = time.monotonic() + 5.0
        tail = ""
        while time.monotonic() < deadline:
            tail = registry.read_tail(task.task_id)
            if "FAKE_API_KEY" in tail:
                break
            await asyncio.sleep(0.2)
        await registry.stop(task.task_id)
        return tail

    tail = asyncio.run(scenario())

    assert "FAKE_API_KEY PRESENT" in tail
