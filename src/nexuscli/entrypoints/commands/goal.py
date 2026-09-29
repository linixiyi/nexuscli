"""goal.py — Per-turn prompt-injection seam and the session-goal reminder (/goal)."""

from __future__ import annotations

from nexuscli.agent import Agent
from nexuscli.context.goal import GoalStore


def _push_turn_context(agent: Agent, name: str, body: str) -> None:
    """Shared per-turn prompt-injection seam (W1 /skill load, W6 /goal).

    Both producers queue onto agent.skill_context_buffer; the buffer is
    drained once per turn by agent._prepend_skill_context (agent.py:221)
    into the user message. /skill load queues once (one-shot); the session
    goal reminder re-queues at every turn start while a goal is set.
    """
    agent.skill_context_buffer.push(name, body)


_GOAL_REMINDER_NAME = "session-goal"


def _queue_goal_reminder(agent: Agent, session_id: str) -> None:
    """Re-queue the session goal reminder for this turn (no goal → no-op).

    Called from both main-loop turn branches right before the agent runs, so
    the reminder survives the buffer's one-shot drain and reaches every turn
    until /goal clear. Coverage boundary: turns dispatched inside /init,
    /plan and other slash-dispatched agent runs bypass these call sites and
    get no reminder — the same applicability /skill load has in plan mode.
    """
    goal = GoalStore(session_id).show()
    if goal:
        _push_turn_context(agent, _GOAL_REMINDER_NAME, f"[session goal] {goal}")
