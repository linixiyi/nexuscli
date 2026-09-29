"""context/goal.py — Per-session long-horizon goal storage for the REPL.

A session goal is one sentence the user sets via ``/goal set``; the REPL
re-queues it onto the agent's one-shot skill_context_buffer at every turn
start (repl._queue_goal_reminder), so multi-turn work keeps the original
objective in context until ``/goal clear``. Storage is one small JSON file
per session: ``~/.nexuscli/goals/<session_id>.json`` with the fixed shape
``{"goal": str, "updated_at": iso}``.

Not ported from ZCode: the goal_state_change attachment mechanism
(runtime/methods/goal-state-reminder.ts:63-77 — ZCode injects a separate
model-only synthetic notice alongside the request, while our reminder
travels inside the user message) and the SessionGoal state machine
(adapters/src/storage/session-target.ts — token budgets, status/verifier
and run-state fields; we store only the goal text).

Known cosmetic trace (deliberate, not fixable inside this module): the
reminder rides the SkillContextBuffer drain pipeline, whose chunk header is
hard-coded to ``## Loaded Skill: `` in skill/registry.py:82, so the reminder
renders as ``## Loaded Skill: session-goal`` followed by
``[session goal] <text>``. Changing the header would touch skill/registry.py,
which is pinned to zero changes; tests assert the reminder body only, never
the header wording.

Imported as a submodule (``from nexuscli.context.goal import GoalStore``);
context/__init__.py stays untouched — same convention as observability and
agent.bg_tasks.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_GOALS_DIRNAME = "goals"

__all__ = ["GoalStore", "goal_path"]


def goal_path(session_id: str, home: Path | None = None) -> Path:
    """Path of one session's goal file (per-call home, same as sessions_root)."""
    # Path-traversal guard: reject empty ids, separators, and "."/".."
    # (Path("..").name == "" falls through the same check).
    if not session_id or Path(session_id).name != session_id:
        raise ValueError(f"invalid session id: {session_id!r}")
    base = home if home is not None else Path.home()
    return base / ".nexuscli" / _GOALS_DIRNAME / f"{session_id}.json"


class GoalStore:
    """Read/write one session's goal file; every failure mode degrades to None/False."""

    def __init__(self, session_id: str, home: Path | None = None) -> None:
        self.path = goal_path(session_id, home)

    def set(self, text: str) -> str:
        """Persist the stripped goal text and return it."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("goal text is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"goal": cleaned, "updated_at": datetime.now().isoformat(timespec="seconds")}
        # Plain single-line write: a goal file is tiny and rewritten wholesale,
        # so SessionWriter's tmp+rename ceremony is unnecessary here.
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return cleaned

    def show(self) -> str | None:
        """Return the goal text, or None when missing/corrupt/keyless (read tolerance)."""
        if not self.path.is_file():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        goal = raw.get("goal")
        if not isinstance(goal, str) or not goal.strip():
            return None
        return goal

    def clear(self) -> bool:
        """Remove the goal file; True iff a readable goal existed and was removed."""
        if self.show() is None:
            return False
        try:
            self.path.unlink()
        except OSError:
            return False
        return True
