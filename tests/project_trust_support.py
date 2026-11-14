"""Record explicit project-trust decisions for tests that use project layers.

Production fails closed: an untrusted project `.nexuscli/config.json` /
`.env` / `.nexuscli/mcp.json` has its security-sensitive content ignored.
Tests that intentionally exercise the *trusted* branch (the old behaviour,
kept for users who accepted a repository's project layer) must record that
decision explicitly instead of bypassing the gate. Keeping the fingerprint and
store wiring in one helper means the test states its intent once
("this project is trusted") and cannot drift from the gate's canonical form.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexuscli.hooks.trust import HookTrustStore, hooks_fingerprint
from nexuscli.mcp.config import MCP_TRUST_STORE_FILENAME
from nexuscli.policy.project_trust import ProjectTrustStore, fingerprint


def _read_json(path: Path) -> dict[str, Any]:
    """Best-effort dict read, mirroring the loader's tolerance for bad files."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_env(path: Path) -> dict[str, str]:
    """Parse KEY=value lines the same way nexuscli.config._read_env does."""
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip().strip("'\"")
    return result


def trust_project_config(root: str | Path) -> str:
    """Trust the project's current config + .env sensitive projection."""
    root_path = Path(root).resolve()
    config = _read_json(root_path / ".nexuscli" / "config.json")
    env = _read_env(root_path / ".env")
    value = fingerprint(config, env)
    ProjectTrustStore().trust(str(root_path), value)
    return value


def trust_project_mcp(root: str | Path) -> str:
    """Trust the project's current .nexuscli/mcp.json server layer."""
    root_path = Path(root).resolve()
    data = _read_json(root_path / ".nexuscli" / "mcp.json")
    servers = data.get("mcpServers", data)
    value = hooks_fingerprint(servers)
    store = HookTrustStore(Path.home() / ".nexuscli" / MCP_TRUST_STORE_FILENAME)
    store.trust(str(root_path), value)
    return value


def trust_project(root: str | Path) -> None:
    """Trust both project faces, as an interactive user would have."""
    trust_project_config(root)
    trust_project_mcp(root)
