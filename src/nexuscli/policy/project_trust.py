"""project_trust.py — sha256 trust gate for security-sensitive project-layer config keys.

Project ``.nexuscli/config.json`` and the project ``.env`` are merged into the
effective config at load time with no confirmation, so cloning a repository
could otherwise flip ``policy.hitl_mode`` to ``"never"``, switch the path /
command guards off, point ``llm.base_url`` at an attacker-controlled gateway,
grant itself ``permissions.allow`` rules, disable the OS sandbox
(``sandbox.*``), or export trace metadata to its own OTLP collector
(``telemetry.*``). This module fingerprints the
security-sensitive projection of the project layer (canonical JSON, sha256
hex — the same pinned form as :mod:`nexuscli.hooks.trust`) and persists one
trust record per workspace in ``~/.nexuscli/project-trust.json``.

Deliberately self-contained: ``nexuscli.hooks`` imports ``nexuscli.config``,
so ``config.py`` cannot import ``hooks.trust`` at module level without an
import cycle — the canonical-JSON + sha256 pattern is mirrored here instead
of shared. The store holds fingerprints only, never secrets or config values,
so losing its 0600 protection is acceptable (same stance as hooks trust).

The gate is fail-closed: without a trusted fingerprint the sensitive keys do
not merge (the user layer / defaults win) and one visible warning names what
was ignored. Interactive confirmation remembers the fingerprint; headless
entrypoints (``-p``, serve) run without a TTY and therefore never trust.
Non-sensitive project keys (rendering, prompts, model choice, ...) merge
exactly as before — the gate only filters the keys listed below.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import warnings
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRUST_STORE_FILENAME = "project-trust.json"

# The ``llm`` keys that decide where model traffic and credentials go.
SENSITIVE_LLM_KEYS: tuple[str, ...] = ("base_url", "api_key", "provider")

# Whole sections that hand a cloned repository control over process-level
# containment or outbound data: ``sandbox`` can switch the OS sandbox backend
# to "none" / allow network, and ``telemetry.otlp.endpoint`` can ship trace
# metadata to an attacker-controlled collector. Both are security-sensitive as
# a whole section — a benign-looking sub-key is not worth a finer allowlist.
SENSITIVE_CONFIG_SECTIONS: tuple[str, ...] = ("sandbox", "telemetry")

# Project-.env names that write straight onto a security-sensitive config key.
# ``NEXUSCLI_API_KEY`` / ``NEXUSCLI_BASE_URL`` are covered by the suffix rules;
# the provider-specific names (``DEEPSEEK_API_KEY``, ``GLM_BASE_URL``, ...)
# funnel through ``config._apply_env`` onto the same keys.
_EXACT_SENSITIVE_ENV_KEYS: frozenset[str] = frozenset({"NEXUSCLI_HITL", "NEXUSCLI_PROVIDER"})
_SENSITIVE_ENV_SUFFIXES: tuple[str, ...] = ("_API_KEY", "_BASE_URL")


class ProjectTrustWarning(UserWarning):
    """Raised (via ``warnings.warn``) when untrusted security-sensitive keys are ignored."""


def is_sensitive_env_key(key: str) -> bool:
    """True when *key* (a project-.env name) maps onto a sensitive config key."""
    return key in _EXACT_SENSITIVE_ENV_KEYS or key.endswith(_SENSITIVE_ENV_SUFFIXES)


def sensitive_config_section(project_config: dict[str, Any] | None) -> dict[str, Any]:
    """The security-sensitive projection of a project ``config.json`` dict.

    ``policy`` is sensitive as a whole section (``policy.*``); ``permissions``
    only its ``allow`` list (deny/ask fail closed, so they stay project-settable);
    ``features`` only ``audit_log``; ``llm`` only the traffic/credential keys.
    Non-sensitive keys never appear here, so they neither affect the
    fingerprint nor require trust.
    """
    if not isinstance(project_config, dict):
        return {}
    section: dict[str, Any] = {}
    policy = project_config.get("policy")
    if isinstance(policy, dict) and policy:
        section["policy"] = policy
    permissions = project_config.get("permissions")
    if isinstance(permissions, dict) and "allow" in permissions:
        section["permissions"] = {"allow": permissions["allow"]}
    features = project_config.get("features")
    if isinstance(features, dict) and "audit_log" in features:
        section["features"] = {"audit_log": features["audit_log"]}
    llm = project_config.get("llm")
    if isinstance(llm, dict):
        sensitive_llm = {key: llm[key] for key in SENSITIVE_LLM_KEYS if key in llm}
        if sensitive_llm:
            section["llm"] = sensitive_llm
    for name in SENSITIVE_CONFIG_SECTIONS:
        value = project_config.get(name)
        if isinstance(value, dict) and value:
            section[name] = value
    return section


def sensitive_env_section(project_env: dict[str, str] | None) -> dict[str, str]:
    """The security-sensitive entries of a project ``.env`` mapping."""
    if not isinstance(project_env, dict):
        return {}
    return {key: value for key, value in project_env.items() if is_sensitive_env_key(key)}


def fingerprint(project_config: dict[str, Any] | None, project_env: dict[str, str] | None) -> str:
    """sha256 hex digest over the canonical JSON of the sensitive projection.

    Canonical form is pinned (sorted keys + compact separators +
    ``ensure_ascii=False`` — same as ``hooks.trust.hooks_fingerprint``), so
    the same content yields the same fingerprint regardless of key order or
    indentation in the source files.
    """
    section = {
        "config": sensitive_config_section(project_config),
        "env": sensitive_env_section(project_env),
    }
    canonical = json.dumps(section, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ProjectTrustStore:
    """Persisted ``{workspace: {fingerprint, trusted_at}}`` map (JSON on disk).

    Same store shape as ``hooks.trust.HookTrustStore`` but a separate file, so
    revoking hook trust never silently re-enables trusted config keys (and
    vice versa). The default path resolves ``Path.home()`` at call time, not
    import time, so HOME redirection (tests/conftest.py) keeps every read and
    write inside the redirected home.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else Path.home() / ".nexuscli" / TRUST_STORE_FILENAME

    def load(self) -> dict[str, Any]:
        """Load the store; a missing, corrupt, or non-dict file reads as empty."""
        empty: dict[str, Any] = {"version": 1, "workspaces": {}}
        if not self.path.exists():
            return empty
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return empty
        return raw if isinstance(raw, dict) else empty

    def is_trusted(self, workspace: str, fingerprint_value: str) -> bool:
        workspaces = self.load().get("workspaces")
        if not isinstance(workspaces, dict):
            return False
        record = workspaces.get(workspace)
        return isinstance(record, dict) and record.get("fingerprint") == fingerprint_value

    def trust(self, workspace: str, fingerprint_value: str) -> None:
        """Merge-write the trust record for *workspace*, creating the directory."""
        data = self.load()
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        workspaces[workspace] = {
            "fingerprint": fingerprint_value,
            "trusted_at": datetime.now(UTC).isoformat(),
        }
        data["version"] = 1
        data["workspaces"] = workspaces
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # chmod 0o600 is best effort only (NTFS ignores POSIX mode bits); the
        # store holds fingerprints, not secrets, so this is acceptable.
        with suppress(OSError):
            os.chmod(self.path, 0o600)


def strip_sensitive(
    project_config: dict[str, Any] | None, project_env: dict[str, str] | None
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Copy *project_config* / *project_env* minus the security-sensitive keys.

    Returns ``(config, env, names)`` where *names* lists every removed key
    path (for the warning message). The inputs are never mutated: the caller
    keeps the raw dicts so a later confirmation can still merge them as-is.
    """
    config = deepcopy(project_config) if isinstance(project_config, dict) else {}
    env = dict(project_env) if isinstance(project_env, dict) else {}
    names: list[str] = []

    policy = config.get("policy")
    if isinstance(policy, dict) and policy:
        del config["policy"]
        names.append("policy.*")

    permissions = config.get("permissions")
    if isinstance(permissions, dict) and "allow" in permissions:
        del permissions["allow"]
        names.append("permissions.allow")
        if not permissions:
            del config["permissions"]

    features = config.get("features")
    if isinstance(features, dict) and "audit_log" in features:
        del features["audit_log"]
        names.append("features.audit_log")
        if not features:
            del config["features"]

    llm = config.get("llm")
    if isinstance(llm, dict):
        removed = [key for key in SENSITIVE_LLM_KEYS if key in llm]
        for key in removed:
            del llm[key]
        names.extend(f"llm.{key}" for key in removed)
        if not llm:
            del config["llm"]

    for name in SENSITIVE_CONFIG_SECTIONS:
        value = config.get(name)
        if isinstance(value, dict) and value:
            del config[name]
            names.append(f"{name}.*")

    env_names = sorted(key for key in env if is_sensitive_env_key(key))
    for key in env_names:
        del env[key]
    names.extend(env_names)

    return config, env, names


def _warning_message(workspace: str, names: list[str]) -> str:
    return (
        f"Untrusted project layer at {workspace}: ignoring security-sensitive keys "
        f"({', '.join(names)}). Start nexuscli interactively to review and trust this project."
    )


def _interactive_confirm(description: str) -> bool:
    """Prompt once on the TTY; a closed or unread stdin refuses (fail-closed)."""
    print(
        f"[project trust] Security-sensitive project config detected: {description}",
        file=sys.stderr,
    )
    try:
        answer = input("Trust this project's security-sensitive config? [y/N] ")
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in {"y", "yes"}


def gate_project_layer(
    project_config: dict[str, Any] | None,
    project_env: dict[str, str] | None,
    project_root: str | Path,
    confirm: Callable[[str], bool] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Decide which project-layer dicts may merge into the effective config.

    Returns ``(config, env)``. With a trusted fingerprint — or after a
    confirmed trust — both come back untouched. Untrusted, the security-
    sensitive keys are stripped (user layer / defaults win) and one
    :class:`ProjectTrustWarning` names everything ignored; nothing is
    remembered on refusal, so the next load asks again. The ``confirm``
    parameter is the injection seam for tests and embedders; the default
    prompts on a TTY and refuses without one, which is what makes headless
    ``-p`` / serve fail closed.

    The fingerprint covers the sensitive projection of BOTH inputs together,
    so adding one sensitive key to an already-trusted project re-arms the
    gate; benign edits (model, prompts, rendering) never do.
    """
    raw_config = project_config if isinstance(project_config, dict) else {}
    raw_env = project_env if isinstance(project_env, dict) else {}
    if not sensitive_config_section(raw_config) and not sensitive_env_section(raw_env):
        return raw_config, raw_env

    fingerprint_value = fingerprint(raw_config, raw_env)
    store = ProjectTrustStore()
    workspace = str(Path(project_root).resolve())
    if store.is_trusted(workspace, fingerprint_value):
        return raw_config, raw_env

    description = f"{workspace} (fingerprint {fingerprint_value[:16]})"
    if confirm is not None:
        trusted = confirm(description)
    elif sys.stdin.isatty():
        trusted = _interactive_confirm(description)
    else:
        trusted = False
    if trusted:
        store.trust(workspace, fingerprint_value)
        return raw_config, raw_env

    stripped_config, stripped_env, names = strip_sensitive(raw_config, raw_env)
    warnings.warn(_warning_message(workspace, names), ProjectTrustWarning, stacklevel=2)
    return stripped_config, stripped_env
