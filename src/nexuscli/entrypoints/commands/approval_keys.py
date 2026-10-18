"""approval_keys.py — Tolerant interpretation of the approval prompt's answer.

Kept separate from the prompt itself so the keymap has one tested definition:
the prompt, its documentation, and the audit vocabulary must not drift apart.
"""

from __future__ import annotations

# approve_run is the run-scoped grant ("r"): the executor records the tool for
# the rest of this agent run. approve_auto ("a") flips the session to auto mode,
# which the caller performs before returning plain "approve".
APPROVAL_HELP = (
    "y approve once · r approve this tool for the rest of this run "
    "(subagents prompt separately) · a switch to auto mode · s skip · "
    "n deny · Esc deny"
)

_ANSWERS: dict[str, str] = {
    "y": "approve",
    "yes": "approve",
    "r": "approve_run",
    "run": "approve_run",
    "a": "approve_auto",
    "auto": "approve_auto",
    "s": "skip",
    "skip": "skip",
    "n": "deny",
    "no": "deny",
    # Esc reaches Prompt.ask as an empty string when the terminal sends it as a
    # bare escape; treat both that and an explicit "q" as "give up on this call".
    "": "deny",
    "q": "deny",
    "esc": "deny",
    "escape": "deny",
}


def interpret_approval_answer(raw: str) -> str | None:
    """Map one typed answer onto a decision, or None when it is not an option.

    Returning None (instead of looping inside rich) lets the caller restate the
    options once and ask again, so a mistyped slash command costs one line of
    feedback rather than an unbounded re-prompt.
    """
    return _ANSWERS.get(raw.strip().lower())
