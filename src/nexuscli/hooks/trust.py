"""trust.py — sha256 trust gate for project-layer (workspace) hooks.

Project ``config.json`` hooks are merged into the effective config at load
time with no confirmation, so cloning a repository could otherwise run
arbitrary shell commands on every tool event. This module fingerprints the
whole project-layer ``hooks`` section once (canonical JSON, sha256 hex) and
persists one trust record per workspace in ``~/.nexuscli/hooks-trust.json``.

Deliberate minimal slice (ZCode reference consulted read-only, TS not ported):
ZCode records per-hook declaration digests plus a bundle digest through a
review-flow / runtime-admission approval pipeline, anonymizes workspace
identity for telemetry, and keeps trust per individual hook. nexusCLI instead
takes one fingerprint over the entire project hooks section and one trust
record per workspace; the store holds fingerprints only — never secrets, hook
commands, or workspace contents — so losing its 0600 protection is acceptable.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import warnings
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexuscli.config import NexusCliConfig, user_level_hooks

TRUST_STORE: Path = Path.home() / ".nexuscli" / "hooks-trust.json"


def workspace_hooks_section(project_root: str | Path) -> dict[str, Any] | None:
    """Return the ``hooks`` section of ``<root>/.nexuscli/config.json``.

    Tolerance mirrors ``config._read_json`` (missing file, unreadable file, or
    invalid JSON all yield nothing) with a local implementation so this module
    stays decoupled from config.py internals. A missing or non-dict ``hooks``
    key also yields ``None``.
    """
    path = Path(project_root) / ".nexuscli" / "config.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    section = raw.get("hooks")
    return section if isinstance(section, dict) else None


def hooks_fingerprint(hooks_section: dict[str, Any]) -> str:
    """sha256 hex digest over the canonical JSON of *hooks_section*.

    Canonical form is pinned: sorted keys + compact separators +
    ``ensure_ascii=False``, so the same content yields the same fingerprint
    regardless of key order or indentation in the source file.
    """
    canonical = json.dumps(
        hooks_section,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HookTrustStore:
    """Persisted ``{workspace: {fingerprint, trusted_at}}`` map (JSON on disk)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path if path is not None else TRUST_STORE

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

    def is_trusted(self, workspace: str, fingerprint: str) -> bool:
        workspaces = self.load().get("workspaces")
        if not isinstance(workspaces, dict):
            return False
        record = workspaces.get(workspace)
        return isinstance(record, dict) and record.get("fingerprint") == fingerprint

    def trust(self, workspace: str, fingerprint: str) -> None:
        """Merge-write the trust record for *workspace*, creating the directory."""
        data = self.load()
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        workspaces[workspace] = {
            "fingerprint": fingerprint,
            "trusted_at": datetime.now(UTC).isoformat(),
        }
        data["version"] = 1
        data["workspaces"] = workspaces
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # chmod 0o600 is best effort only: NTFS does not interpret POSIX mode
        # bits (at most the read-only flag lands). The store holds fingerprints,
        # not secrets, so the degraded protection is acceptable.
        with suppress(OSError):
            os.chmod(self.path, 0o600)


class WorkspaceHooksWarning(UserWarning):
    """Warned (via ``warnings.warn``) when untrusted project hooks are disabled."""


def _warning_message(workspace: str, fingerprint: str) -> str:
    return (
        f"Untrusted project hooks at {workspace} (fingerprint {fingerprint[:16]}): "
        "project-layer hooks are disabled for this run (user-level hooks unaffected). "
        "Start nexuscli interactively to review and trust this project."
    )


def _interactive_confirm(description: str) -> bool:
    """Prompt once on the TTY; a closed or unread stdin refuses (fail-closed).

    Mirrors ``policy.project_trust._interactive_confirm``: the notice goes to
    stderr so headless entrypoints that own stdout (``-p`` plain/``--json``)
    stay machine-clean even in the interactive branch.
    """
    print(f"[hooks trust] Project-layer hooks detected: {description}", file=sys.stderr)
    try:
        answer = input("Trust workspace hooks? [y/N] ")
    except (EOFError, OSError):
        return False
    return answer.strip().lower() in {"y", "yes"}


def gate_project_hooks(
    config: NexusCliConfig,
    cwd: str | Path,
    *,
    confirm: Callable[[str], bool] | None = None,
) -> bool:
    """Require one-time sha256 trust before project-layer hooks take effect.

    Headless extraction of the REPL gate (``hooks_cmds._gate_workspace_hooks``,
    whose REPL behavior is unchanged): the project layer of ``config.hooks``
    was merged in at load time with no confirmation, so the one-shot ``-p`` and
    serve/worker startup paths must fingerprint the project layer's ``hooks``
    section (canonical JSON + sha256, shared :class:`HookTrustStore`) before
    any of those hooks can fire. On refusal the merged config is swapped back
    to :func:`user_level_hooks` — user matchers first, project matchers
    appended, so the swap strips exactly the project layer — and one
    :class:`WorkspaceHooksWarning` is emitted. User-level hooks are never
    touched. Returns ``True`` when the project layer may stay, ``False`` when
    it was stripped.

    The ``confirm`` parameter is the injection seam for tests and embedders;
    the default prompts on a TTY (remembering the fingerprint on yes, same
    store as the REPL) and refuses without one, which is what makes headless
    ``-p`` / serve fail closed. A refusal is never remembered.
    """
    section = workspace_hooks_section(cwd)
    if not section:
        return True
    fingerprint = hooks_fingerprint(section)
    store = HookTrustStore()
    workspace = str(Path(cwd).resolve())
    if store.is_trusted(workspace, fingerprint):
        return True
    description = f"{workspace} (fingerprint {fingerprint[:16]})"
    if confirm is not None:
        trusted = confirm(description)
    elif sys.stdin.isatty():
        trusted = _interactive_confirm(description)
    else:
        # Non-interactive: refuse conservatively, same posture as the REPL
        # gate's no-TTY branch and policy.project_trust's headless default.
        trusted = False
    if trusted:
        store.trust(workspace, fingerprint)
        return True
    config.hooks = user_level_hooks()
    warnings.warn(_warning_message(workspace, fingerprint), WorkspaceHooksWarning, stacklevel=2)
    return False
