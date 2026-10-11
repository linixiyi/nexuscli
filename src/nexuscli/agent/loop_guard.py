"""loop_guard.py — ReAct tool-loop repetition detection.

Detects the same tool being called with identical arguments on consecutive
turns. The fingerprint and the counting transition are pure functions (stable
``json.dumps(sort_keys=True)`` serialization; a new fingerprint resets the
counter), so they can be unit-tested without an agent or an LLM around them.
:class:`LoopGuard` is the thin stateful wrapper the react loop uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Consecutive identical tool calls tolerated before the guard interrupts.
DEFAULT_LOOP_THRESHOLD = 3

# Injected to the model (as the blocked call's tool result) when the same
# fingerprint reaches the threshold: the call is NOT executed and the model
# is asked to change strategy.
LOOP_NUDGE_TEMPLATE = (
    "Loop detected: this tool has now been called with identical arguments "
    "{count} consecutive times and this call was not executed. Change your "
    "approach — adjust the arguments, use a different tool, or finish the "
    "task with a final answer instead of repeating the same call."
)

# Injected as the tool result when the model repeats the same fingerprint
# even after the nudge; the turn is then terminated (see the ``warning``
# event in agent.py) and the message below explains why to the model.
LOOP_ABORT_MESSAGE = (
    "Loop detected: the same tool call was repeated again after the "
    "loop-detection warning. The call was not executed and the turn is "
    "ending now. Summarize what was accomplished or state what blocked you."
)


def loop_abort_warning(turns: int, threshold: int) -> str:
    """The user-facing warning text emitted when the guard terminates a turn."""
    return (
        f"Loop detected: the same tool call was repeated consecutively and "
        f"the turn was terminated after {turns} turns "
        f"(agent.loop_detection_threshold={threshold}). Raise "
        "agent.loop_detection_threshold in config.json to allow more "
        "repetitions."
    )


def tool_call_fingerprint(name: str, arguments: Any) -> str:
    """Stable fingerprint for one tool call: tool name + normalized arguments.

    Arguments are serialized with ``json.dumps(..., sort_keys=True)`` so key
    order never matters; anything unserializable falls back to ``repr`` rather
    than raising (a guard must never be the reason a turn crashes).
    """
    try:
        normalized = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        normalized = repr(arguments)
    return f"{name}({normalized})"


@dataclass(slots=True)
class LoopState:
    """Immutable loop-guard state; :func:`next_loop_state` maps it forward."""

    fingerprint: str | None = None
    repeat_count: int = 0
    nudged: bool = False


@dataclass(slots=True)
class LoopDecision:
    """What the react loop should do with the tool call just recorded."""

    fingerprint: str = ""
    repeat_count: int = 0
    # Skip execution and inject the nudge message to the model.
    blocked: bool = False
    # The nudge was already sent and the same fingerprint came back: abort.
    abort: bool = False
    # True exactly on the turn that first reaches the threshold.
    nudged: bool = False


def next_loop_state(
    state: LoopState, fingerprint: str, threshold: int
) -> tuple[LoopState, LoopDecision]:
    """Pure transition: record *fingerprint* and decide what to do next.

    A new fingerprint resets the counter to 1 (only *consecutive* repeats
    count). When the count reaches ``threshold`` the call is blocked and the
    nudge is armed; one more identical call aborts the turn.
    """
    if fingerprint != state.fingerprint:
        fresh = LoopState(fingerprint=fingerprint, repeat_count=1)
        return fresh, LoopDecision(fingerprint=fingerprint, repeat_count=1)

    repeat_count = state.repeat_count + 1
    decision = LoopDecision(fingerprint=fingerprint, repeat_count=repeat_count)
    if state.nudged:
        aborted_state = LoopState(fingerprint=fingerprint, repeat_count=repeat_count, nudged=True)
        return aborted_state, LoopDecision(
            fingerprint=fingerprint,
            repeat_count=repeat_count,
            blocked=True,
            abort=True,
        )
    if repeat_count >= threshold:
        nudged_state = LoopState(fingerprint=fingerprint, repeat_count=repeat_count, nudged=True)
        return nudged_state, LoopDecision(
            fingerprint=fingerprint,
            repeat_count=repeat_count,
            blocked=True,
            nudged=True,
        )
    return LoopState(fingerprint=fingerprint, repeat_count=repeat_count), decision


@dataclass(slots=True)
class LoopGuard:
    """Stateful wrapper around :func:`next_loop_state` for the react loop.

    ``threshold <= 0`` disables detection entirely: every call records as a
    no-op decision and nothing is ever blocked.
    """

    threshold: int = DEFAULT_LOOP_THRESHOLD
    state: LoopState = field(default_factory=LoopState)

    def record(self, fingerprint: str) -> LoopDecision:
        """Record one tool-call fingerprint and return the guard's decision."""
        if self.threshold <= 0:
            return LoopDecision(fingerprint=fingerprint)
        self.state, decision = next_loop_state(self.state, fingerprint, self.threshold)
        return decision

    def format_nudge(self, decision: LoopDecision) -> str:
        """Build the message injected when a call is blocked."""
        return LOOP_NUDGE_TEMPLATE.format(count=decision.repeat_count)
