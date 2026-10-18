"""start_repl's non-TTY stdin guard.

Piping into ``nexuscli`` (``echo '/help' | nexuscli``) leaves ``sys.stdin``
without a terminal; prompt_toolkit then fails mid-event-loop ("Event loop
stopped before Future completed") behind a full rich traceback. The guard must
print a guidance line and exit 1 before any session/agent setup runs.
"""

from __future__ import annotations

import asyncio
import sys

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints import repl


class _PipeStdin:
    """Stands in for a piped stdin: has ``isatty()``, and it returns False."""

    def isatty(self) -> bool:
        return False


class _SentinelPromptSession:
    """Any construction means the guard let the REPL reach prompt_toolkit."""

    def __init__(self, *args, **kwargs):
        raise AssertionError("PromptSession must not be constructed on non-TTY stdin")


def test_start_repl_non_tty_prints_guidance_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", _PipeStdin())
    monkeypatch.setattr(repl, "PromptSession", _SentinelPromptSession)

    with pytest.raises(SystemExit) as excinfo:
        asyncio.run(repl.start_repl(str(tmp_path), NexusCliConfig()))

    assert excinfo.value.code == 1
    # The guidance goes to stderr: on a pipeline stdout is data, and a
    # diagnostic written there corrupts whatever the caller was collecting.
    # It is also emitted as one line so it never reads like a wrapped crash.
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert "Interactive REPL needs a terminal" in err
    assert 'nexuscli -p "<prompt>"' in err
    assert err.count("\n") == 1
