"""Exfiltration shapes that must fall out of the read-only bash fast path.

The fast path returns tool output straight to the transcript with no human in
the loop, so any shape that reads a credential must be rejected even when the
command itself sits on the whitelist:

- credential locations beyond the original ``.ssh``/``.env`` set: git
  credentials, the gh CLI config, gcloud and azure token stores;
- ``git show/diff/log <rev>:<path>`` — the colon form hides the path from the
  plain token scan, so ``git show HEAD:.env`` used to sail through while
  ``cat .env`` could not;
- ``date 010100002026`` — the bare numeric positional is BSD's clock-set form
  (``[[[[[cc]yy]mm]dd]hh]mm[.ss]``), no ``-s`` flag needed;
- ``file -C`` — compiles (writes) a magic database instead of identifying.

Companion to tests/test_bash_readonly.py, which owns the executor wiring.
"""

from __future__ import annotations

import pytest

from nexuscli.policy import is_readonly_bash_command


@pytest.mark.parametrize(
    "command",
    [
        # the brief's required rejections
        "cat ~/.git-credentials",
        "cat ~/.config/gh/hosts.yml",
        "git show HEAD:.env",
        "date 010100002026",
        "file -C -m /tmp/m",
        # the remaining credential locations the brief names
        "cat ~/.config/gcloud/access_tokens.db",
        "cat ~/.azure/accessTokens.json",
        # <rev>:<path> hides the secret path from the plain token scan
        "git diff HEAD~1:.env HEAD~2:.env",
        "git log HEAD:.env",
        "git show :0:.env",
        # a rev:path pointing at a private key stays rejected too
        "git show HEAD:~/.ssh/id_rsa",
    ],
)
def test_exfiltration_shapes_are_not_readonly(command: str) -> None:
    assert is_readonly_bash_command(command) is False


@pytest.mark.parametrize(
    "command",
    [
        "git show HEAD:README.md",
        "git diff HEAD~1:README.md",
        "git log --oneline -5",
        "git show HEAD",
        "date",
        "date +%Y-%m-%d",
        "date -u",
        "file x",
        "file README.md",
        "cat notes.txt",
        # the bare .config dir itself is not a credential location
        "ls ~/.config",
    ],
)
def test_ordinary_inspection_stays_readonly(command: str) -> None:
    assert is_readonly_bash_command(command) is True
