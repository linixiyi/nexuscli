"""runner.py — Execute hook commands as subprocesses and aggregate outcomes.

Protocol (mirrors Claude Code hooks): each matching hook command runs through
the shell, receives a JSON payload on stdin (``hook_event_name``,
``session_id``, ``cwd`` plus event-specific fields) and reports back via its
exit code and stdout:

- exit 0                     -> success; stdout may carry a JSON object with a
                                top-level ``decision`` (``approve``/``block``
                                + ``reason``) and/or a
                                ``hookSpecificOutput.permissionDecision``
                                (``allow``/``deny``/``ask`` +
                                ``permissionDecisionReason``)
- exit 2                     -> block; stderr becomes the block reason
- other exit codes / timeout -> non-blocking error, surfaced to the user

nexusCLI difference: a hook ``allow`` is recorded on the outcome but never
bypasses the normal ``requires_approval`` flow — hooks may only deny, block,
or attach context.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from nexuscli.config import HOOK_EVENT_FIELDS, HookCommandConfig, NexusCliConfig
from nexuscli.hooks.registry import hooks_for

_BLOCKED_FALLBACK_REASON = "blocked by hook"

# Grace period for reaping the killed shell after a hook timeout. The kill
# itself is immediate, but the exit notification can lag until grandchildren
# holding the pipes exit (observed on Windows); the reaping wait must never
# re-introduce an unbounded block after the timeout already fired.
_KILL_REAP_TIMEOUT = 5.0


@dataclass(slots=True)
class HookOutcome:
    """Aggregated result of running every hook that matched one event."""

    blocked: bool = False
    reason: str = ""  # block reason: hook stderr or the parsed reason field
    additional_context: str = ""  # UserPromptSubmit additionalContext, appended
    errors: list[str] = field(default_factory=list)  # non-blocking errors
    permission_hint: str = ""  # "ask" forces HITL; "allow" is recorded only


def has_hooks(config: NexusCliConfig, event: str) -> bool:
    """Cheap synchronous gate so hot paths skip the hook machinery entirely."""
    field_name = HOOK_EVENT_FIELDS.get(event)
    if field_name is None:
        return False
    return bool(getattr(config.hooks, field_name, None))


async def fire_event(
    config: NexusCliConfig,
    event: str,
    payload: dict[str, Any],
    cwd: str,
) -> HookOutcome:
    """Select the hooks for *event* and run them; a one-line trigger.

    The stdin payload is assembled here so callers (agent, executor, repl)
    never touch subprocesses: ``session_id`` is read live from
    ``config.policy.session_id`` alongside the event name and cwd.
    """
    tool_name = str(payload.get("tool_name") or "") or None
    commands = hooks_for(config.hooks, event, tool_name)
    if not commands:
        return HookOutcome()
    full_payload = {
        "hook_event_name": event,
        "session_id": config.policy.session_id,
        "cwd": cwd,
        **payload,
    }
    return await run_hooks(commands, event, full_payload)


async def run_hooks(
    commands: list[HookCommandConfig],
    event: str,
    payload: dict[str, Any],
) -> HookOutcome:
    """Run every hook command for *event* and aggregate their outcomes."""
    outcome = HookOutcome()
    cwd = str(payload.get("cwd") or ".")
    for hook in commands:
        await _run_single(hook, payload, cwd, outcome)
    return outcome


async def _run_single(
    hook: HookCommandConfig,
    payload: dict[str, Any],
    cwd: str,
    outcome: HookOutcome,
) -> None:
    stdin_data = json.dumps(payload, ensure_ascii=False)
    try:
        process = await asyncio.create_subprocess_shell(
            hook.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
    except OSError as exc:
        outcome.errors.append(f"hook failed to start: {exc}")
        return
    # The timeout is forced policy: a null/zero/negative value in config.json
    # must degrade to the 60s default instead of reaching asyncio.wait_for as
    # None, which would wait forever and hang every tool call behind the hook.
    timeout = hook.timeout if isinstance(hook.timeout, (int, float)) and hook.timeout > 0 else 60
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(stdin_data.encode("utf-8")),
            timeout=timeout,
        )
    except TimeoutError:
        process.kill()
        with suppress(ProcessLookupError, TimeoutError, OSError):
            await asyncio.wait_for(process.wait(), timeout=_KILL_REAP_TIMEOUT)
        outcome.errors.append(f"hook timed out after {timeout}s: {hook.command}")
        return
    except OSError as exc:
        outcome.errors.append(f"hook failed to run: {exc}")
        return

    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    code = process.returncode
    if code == 2:
        # Exit code 2 is the blocking exit: stderr goes back as the reason.
        outcome.blocked = True
        if not outcome.reason:
            outcome.reason = stderr_text or _BLOCKED_FALLBACK_REASON
        return
    if code != 0:
        outcome.errors.append(f"hook exited with code {code}: {stderr_text or hook.command}")
        return
    _apply_stdout(outcome, stdout_text)


def _apply_stdout(outcome: HookOutcome, stdout_text: str) -> None:
    """Fold the hook's stdout JSON decisions into the aggregated outcome."""
    data = _first_json_object(stdout_text)
    if data is None:
        return

    decision = str(data.get("decision") or "").lower()
    if decision == "block":
        outcome.blocked = True
        if not outcome.reason:
            outcome.reason = str(data.get("reason") or "") or _BLOCKED_FALLBACK_REASON

    specific = data.get("hookSpecificOutput")
    specific = specific if isinstance(specific, dict) else {}
    permission = str(specific.get("permissionDecision") or "").lower()
    if permission == "deny":
        outcome.blocked = True
        if not outcome.reason:
            outcome.reason = (
                str(specific.get("permissionDecisionReason") or "") or _BLOCKED_FALLBACK_REASON
            )
    elif permission == "ask":
        # Forced human-in-the-loop: the executor turns this into force_prompt.
        outcome.permission_hint = "ask"
    elif permission == "allow":
        # Recorded for observability only — never bypasses requires_approval.
        if outcome.permission_hint != "ask":
            outcome.permission_hint = "allow"

    context = specific.get("additionalContext")
    if not isinstance(context, str) or not context:
        context = data.get("additionalContext")
    if isinstance(context, str) and context:
        outcome.additional_context = (
            f"{outcome.additional_context}\n{context}" if outcome.additional_context else context
        )


def _first_json_object(text: str) -> dict[str, Any] | None:
    """Parse the first JSON object in *text*; logs before it are tolerated."""
    start = text.find("{")
    while start != -1:
        try:
            value, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        return value if isinstance(value, dict) else None
    return None
