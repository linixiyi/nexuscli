"""Slash-command completion must actually complete.

WordCompleter was wired with the right candidate list but never matched:
prompt_toolkit's default word pattern treats / as a delimiter, so with the
cursor after /sk the word under it is sk while every candidate starts with /,
so Tab silently did nothing (pty-verified).
"""

from __future__ import annotations

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from nexuscli.entrypoints.repl import SlashCommandCompleter


def _complete(text: str) -> list[str]:
    completer = SlashCommandCompleter(["/help", "/skill", "/skills", "/exit"])
    return [
        completion.text
        for completion in completer.get_completions(
            Document(text), CompleteEvent(completion_requested=True)
        )
    ]


def test_prefix_completes_with_the_leading_slash():
    assert _complete("/sk") == ["/skill", "/skills"]


def test_bare_slash_lists_every_command():
    assert _complete("/") == ["/exit", "/help", "/skill", "/skills"]


def test_exact_command_is_still_offered():
    assert _complete("/exit") == ["/exit"]


@pytest.mark.parametrize("text", ["hello", "", "/skill web", " /sk"])
def test_no_completion_outside_the_command_position(text: str) -> None:
    # Once an argument is being typed (or the line is not a command at all)
    # there is nothing to complete; offering commands would fight the user.
    assert _complete(text) == []


def test_completion_replaces_the_typed_prefix():
    completer = SlashCommandCompleter(["/skill"])
    completions = list(
        completer.get_completions(Document("/sk"), CompleteEvent(completion_requested=True))
    )
    assert completions[0].start_position == -3
