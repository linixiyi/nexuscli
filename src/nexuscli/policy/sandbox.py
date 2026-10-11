"""sandbox.py — Subprocess environment sanitization (the bash guard's floor).

The bash tool and the background registry hand the child process an
environment the model can read through the command's output. Passing the
full ``os.environ`` meant one ``env`` (or ``printenv``) command could
exfiltrate every API key the CLI host process carries. This module builds
the child environment instead: start from the parent's environment, drop
secret-shaped variables and NexusCLI's own internal variables, and force
keep the handful of keys subprocesses need to run at all (``PATH``,
``HOME``, ``TEMP``, ``SYSTEMROOT`` and friends).

Honest boundaries:

- This is a blocklist with a safety net, not a capability system. Any
  variable whose name does not look secret-shaped still crosses. Use it to
  remove the accidental-leak floor, not to contain a hostile child.
- Only the two agent-facing spawn points (foreground bash in
  ``tools/builtins.py`` and the background registry in
  ``tools/background.py``) sanitize. The CommandExecutor slash-injection
  path does not, and OS-level sandboxing (containers, restricted tokens)
  is an L-sized external effort outside this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Name fragments that mark a variable as secret-shaped, matched
# case-insensitively as substrings of the variable name.
SECRET_KEY_PATTERNS: tuple[str, ...] = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "PRIVATE_KEY",
)

# NexusCLI's own configuration channel: the child has no business seeing
# which provider/model the parent talks to.
INTERNAL_ENV_PREFIX = "NEXUSCLI_"

# Keys every subprocess needs to function (command lookup, temp files,
# Windows loader internals). They are force-kept even when their name would
# match the blocklist above, and the match is case-insensitive because
# Windows environments are.
ESSENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "PATH",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMFILES",
        "PROGRAMDATA",
        "SHELL",
        "TERM",
        "LANG",
        "TZ",
    }
)


def _is_secret_shaped(key: str) -> bool:
    upper = key.upper()
    return any(pattern in upper for pattern in SECRET_KEY_PATTERNS)


def _is_internal(key: str) -> bool:
    return key.upper().startswith(INTERNAL_ENV_PREFIX)


def _is_essential(key: str) -> bool:
    return key.upper() in ESSENTIAL_KEYS


def sanitize_environment(
    base: Mapping[str, str] | None = None,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a child-process environment without secret-shaped variables.

    Copies *base* (default: ``os.environ``), drops keys whose name matches
    :data:`SECRET_KEY_PATTERNS` or the :data:`INTERNAL_ENV_PREFIX` prefix,
    force-keeps :data:`ESSENTIAL_KEYS`, and finally applies *overrides*
    verbatim — overrides are caller-supplied and therefore trusted.

    The blocklist errs on the side of removal: a benign name that happens
    to contain a pattern (``MONKEY``) is dropped too. That is deliberate —
    a missing variable is a visible bug, a leaked key is not.
    """
    source = os.environ if base is None else base
    result: dict[str, str] = {}
    for key, value in source.items():
        if _is_essential(key):
            result[key] = value
            continue
        if _is_secret_shaped(key) or _is_internal(key):
            continue
        result[key] = value
    if overrides:
        result.update(overrides)
    return result
