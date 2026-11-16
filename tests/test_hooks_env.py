"""Tests for the hook subprocess environment (policy.env_sanitization).

The runner must hand hook children the same sanitized environment the bash
tool does (secret-shaped and ``NEXUSCLI_*`` variables stripped, essential
keys force-kept) when the flag is on, and the historical full ``os.environ``
when it is off.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from nexuscli.config import HookCommandConfig, HookMatcherConfig, NexusCliConfig
from nexuscli.hooks import fire_event, run_hooks

# Dump the hook child's own environment to a file next to the script, the
# same tmp_path pattern tests/test_hooks.py uses for stdin captures (stdout
# JSON is reserved for hook decisions, so the env goes to a file instead).
_DUMP_ENV = (
    "import json, os\n"
    "from pathlib import Path\n"
    "out = Path(__file__).with_suffix('.env.json')\n"
    "out.write_text(json.dumps(dict(os.environ)), encoding='utf-8')\n"
)

_CAPTURE_STDIN = (
    "import json, sys\n"
    "from pathlib import Path\n"
    'sys.stdin.reconfigure(encoding="utf-8")\n'
    "payload = json.load(sys.stdin)\n"
    "capture = Path(__file__).with_suffix('.json')\n"
    "capture.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')\n"
)


def _env_hook(script: Path) -> str:
    script.write_text(_DUMP_ENV, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


# Variables the /bin/sh wrapper manages per-spawn (it rewrites PWD to its
# cwd, bumps SHLVL, drops OLDPWD, points _ at the command). Everything else
# in the parent environment must cross unchanged.
_SHELL_MANAGED_VARS = frozenset({"PWD", "OLDPWD", "SHLVL", "_", "PS1"})


def _hooked_config(tmp_path: Path, command: str, *, env_sanitization: bool) -> NexusCliConfig:
    config = NexusCliConfig()
    config.policy.env_sanitization = env_sanitization
    config.hooks.pre_tool_use = [HookMatcherConfig(hooks=[HookCommandConfig(command=command)])]
    return config


def _child_env(script: Path) -> dict[str, str]:
    return json.loads(script.with_suffix(".env.json").read_text(encoding="utf-8"))


def test_flag_on_strips_secrets_and_internal_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaky")
    monkeypatch.setenv("NEXUSCLI_INTERNAL_MARKER", "host-only")
    monkeypatch.setenv("NEXUS_HOOKS_BENIGN", "passes-through")
    script = tmp_path / "env_on.py"
    config = _hooked_config(tmp_path, _env_hook(script), env_sanitization=True)

    outcome = asyncio.run(fire_event(config, "PreToolUse", {}, str(tmp_path)))

    assert outcome.errors == []
    env = _child_env(script)
    assert "OPENAI_API_KEY" not in env
    assert not any(key.startswith("NEXUSCLI_") for key in env)
    # Essential keys survive so the child can function at all.
    assert env.get("PATH") == os.environ.get("PATH")
    # A benign, non-secret-shaped variable crosses unchanged.
    assert env.get("NEXUS_HOOKS_BENIGN") == "passes-through"


def test_flag_off_keeps_full_environ(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaky")
    monkeypatch.setenv("NEXUSCLI_INTERNAL_MARKER", "host-only")
    snapshot = dict(os.environ)
    script = tmp_path / "env_off.py"
    config = _hooked_config(tmp_path, _env_hook(script), env_sanitization=False)

    outcome = asyncio.run(fire_event(config, "PreToolUse", {}, str(tmp_path)))

    assert outcome.errors == []
    env = _child_env(script)
    # Historical behavior: the child sees the parent environment as-is, key
    # for key and value for value (minus the shell-managed few).
    for key, value in snapshot.items():
        if key in _SHELL_MANAGED_VARS:
            continue
        assert env.get(key) == value, key
    assert env.get("OPENAI_API_KEY") == "sk-leaky"
    assert env.get("NEXUSCLI_INTERNAL_MARKER") == "host-only"


def test_run_hooks_legacy_positional_callers_still_work(tmp_path: Path) -> None:
    # The signature grew a keyword-only sanitize_env; pre-existing 3-argument
    # calls (commands, event, payload) must keep working unchanged.
    script = tmp_path / "capture.py"
    script.write_text(_CAPTURE_STDIN, encoding="utf-8")
    command = f'"{sys.executable}" "{script}"'

    outcome = asyncio.run(
        run_hooks([HookCommandConfig(command=command)], "PreToolUse", {"cwd": str(tmp_path)})
    )

    assert outcome.errors == []
    assert not outcome.blocked
    payload = json.loads(script.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["cwd"] == str(tmp_path)
