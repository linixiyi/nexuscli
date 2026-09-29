"""Tests for the workspace hooks sha256 trust gate (project layer only)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
from pathlib import Path

from rich.console import Console

from nexuscli.config import load_config, user_level_hooks
from nexuscli.entrypoints import repl
from nexuscli.hooks import trust

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isolate_home(monkeypatch, tmp_path: Path) -> Path:
    """Point the user layer and the trust store at *tmp_path*.

    HOME + USERPROFILE are both set (win32 Path.home() prefers USERPROFILE;
    see tests/test_hooks.py) so no test reads or writes the real
    ``~/.nexuscli`` directory. The trust store constant is patched too because
    it is bound at import time, before any monkeypatching can run.
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(trust, "TRUST_STORE", home / ".nexuscli" / "hooks-trust.json")
    return home


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_hooks_payload(command: str) -> dict[str, object]:
    return {
        "hooks": {
            "PreToolUse": [{"matcher": "bash", "hooks": [{"command": command}]}],
        }
    }


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False)


def _run_gate(config, cwd: str, confirm) -> None:
    asyncio.run(repl._gate_workspace_hooks(config, cwd, _console(), confirm=confirm))


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------


def test_fingerprint_deterministic_and_sensitive() -> None:
    section = {"PreToolUse": [{"matcher": "bash", "hooks": [{"command": "echo hi"}]}]}
    assert trust.hooks_fingerprint(section) == trust.hooks_fingerprint(section)

    mutated = json.loads(json.dumps(section))
    mutated["PreToolUse"][0]["hooks"][0]["command"] = "echo evil"
    assert trust.hooks_fingerprint(mutated) != trust.hooks_fingerprint(section)

    # Same content, different key order: canonical (sorted-key) JSON must agree.
    reordered = {"PreToolUse": [{"hooks": [{"command": "echo hi"}], "matcher": "bash"}]}
    assert trust.hooks_fingerprint(reordered) == trust.hooks_fingerprint(section)

    # Matches a manual hashlib.sha256 computation over the pinned canonical form.
    canonical = json.dumps(section, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert trust.hooks_fingerprint(section) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Trust store + section reader
# ---------------------------------------------------------------------------


def test_trust_store_roundtrip_and_corruption(tmp_path: Path) -> None:
    store_path = tmp_path / "trust.json"
    store = trust.HookTrustStore(path=store_path)
    assert store.load() == {"version": 1, "workspaces": {}}

    store.trust(str(tmp_path), "fp-1")
    assert store.is_trusted(str(tmp_path), "fp-1")
    assert not store.is_trusted(str(tmp_path), "fp-2")

    record = store.load()["workspaces"][str(tmp_path)]
    assert record["fingerprint"] == "fp-1"
    assert "trusted_at" in record

    # Corrupt JSON reads back as the empty store -> the workspace needs
    # re-confirmation.
    store_path.write_text("{not json", encoding="utf-8")
    assert store.load() == {"version": 1, "workspaces": {}}
    assert not store.is_trusted(str(tmp_path), "fp-1")

    # Valid JSON that is not an object is damage too.
    store_path.write_text("[]", encoding="utf-8")
    assert store.load() == {"version": 1, "workspaces": {}}

    # No .nexuscli/config.json in the directory -> no hooks section.
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert trust.workspace_hooks_section(empty_dir) is None


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_gate_first_confirm_trusts_and_persists(tmp_path: Path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _project_hooks_payload("echo project"))
    config = load_config(project_root=project)

    calls: list[str] = []

    def confirm(description: str) -> bool:
        calls.append(description)
        return True

    _run_gate(config, str(project), confirm)

    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    workspace = str(project.resolve())
    store = trust.HookTrustStore()
    assert store.is_trusted(workspace, fingerprint)
    payload = json.loads((home / ".nexuscli" / "hooks-trust.json").read_text(encoding="utf-8"))
    assert payload["workspaces"][workspace]["fingerprint"] == fingerprint
    # The prompt description shows the workspace path and the fingerprint head.
    assert len(calls) == 1
    assert workspace in calls[0]
    assert fingerprint[:16] in calls[0]
    # Trusted: config.hooks keeps the project layer.
    assert [matcher.matcher for matcher in config.hooks.pre_tool_use] == ["bash"]


def test_gate_refusal_strips_project_layer_only(tmp_path: Path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    _write_json(
        home / ".nexuscli" / "config.json",
        {"hooks": {"SessionStart": [{"matcher": "*", "hooks": [{"command": "echo start"}]}]}},
    )
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _project_hooks_payload("echo project"))
    config = load_config(project_root=project)
    assert len(config.hooks.session_start) == 1  # user layer merged in
    assert len(config.hooks.pre_tool_use) == 1  # project layer merged in

    _run_gate(config, str(project), confirm=lambda description: False)

    # Project layer stripped, user layer intact — exactly the user-level parse.
    assert config.hooks == user_level_hooks()
    assert config.hooks.pre_tool_use == []
    assert [matcher.matcher for matcher in config.hooks.session_start] == ["*"]
    assert config.hooks.session_start[0].hooks[0].command == "echo start"


def test_gate_already_trusted_skips_confirm(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _project_hooks_payload("echo project"))
    config = load_config(project_root=project)
    fingerprint = trust.hooks_fingerprint(trust.workspace_hooks_section(project))
    trust.HookTrustStore().trust(str(project.resolve()), fingerprint)

    def _must_not_ask(description: str) -> bool:
        raise AssertionError("confirm must not run for an already-trusted workspace")

    _run_gate(config, str(project), _must_not_ask)
    assert [matcher.matcher for matcher in config.hooks.pre_tool_use] == ["bash"]


def test_gate_tampered_hooks_require_reconfirm(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    project = tmp_path / "proj"
    _write_json(project / ".nexuscli" / "config.json", _project_hooks_payload("echo original"))
    config = load_config(project_root=project)
    _run_gate(config, str(project), confirm=lambda description: True)

    # Tamper with the project layer: same matcher, different command.
    _write_json(project / ".nexuscli" / "config.json", _project_hooks_payload("echo evil"))

    calls: list[str] = []

    def confirm(description: str) -> bool:
        calls.append(description)
        return False

    _run_gate(config, str(project), confirm)
    assert len(calls) == 1  # the new fingerprint forced a fresh confirmation
    assert config.hooks.pre_tool_use == []  # refusal strips the project layer


def test_gate_no_project_hooks_is_noop(tmp_path: Path, monkeypatch) -> None:
    _isolate_home(monkeypatch, tmp_path)
    project = tmp_path / "proj"

    def _must_not_ask(description: str) -> bool:
        raise AssertionError("confirm must not run without project hooks")

    # Case 1: no project config.json at all.
    config = load_config(project_root=project)
    _run_gate(config, str(project), _must_not_ask)
    # Case 2: project config.json exists but carries no hooks section.
    _write_json(project / ".nexuscli" / "config.json", {"llm": {"model": "m"}})
    config = load_config(project_root=project)
    _run_gate(config, str(project), _must_not_ask)
    assert config.hooks == user_level_hooks()
