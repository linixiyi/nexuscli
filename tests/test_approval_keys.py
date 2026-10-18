"""Tests for the approval prompt's answer interpretation.

The prompt used to be rich.prompt.Prompt(choices=...), which re-asks forever on
unknown input and ignores Esc: a stray keystroke trapped the user with no exit
but two Ctrl+C presses, and the second one killed the session (pty-verified).
These tests pin the mapping that replaced it.
"""

from __future__ import annotations

import pytest

from nexuscli.entrypoints.commands.approval_keys import (
    APPROVAL_HELP,
    interpret_approval_answer,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("y", "approve"),
        ("Y", "approve"),
        ("yes", "approve"),
        (" r ", "approve_run"),
        ("run", "approve_run"),
        ("a", "approve_auto"),
        ("auto", "approve_auto"),
        ("s", "skip"),
        ("skip", "skip"),
        ("n", "deny"),
        ("no", "deny"),
        ("q", "deny"),
        ("", "deny"),
        ("esc", "deny"),
    ],
)
def test_known_answers_map_to_decisions(raw: str, expected: str) -> None:
    assert interpret_approval_answer(raw) == expected


@pytest.mark.parametrize("raw", ["/usage", "/exit", "please", "yy", "1"])
def test_unknown_answers_are_reported_instead_of_looping(raw: str) -> None:
    assert interpret_approval_answer(raw) is None


def test_help_text_lists_every_decision_including_escape() -> None:
    # The help line is the only place a user learns they can get out.
    for token in ("y", "r", "a", "s", "n", "Esc"):
        assert token in APPROVAL_HELP
