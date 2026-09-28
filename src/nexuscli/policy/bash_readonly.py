"""bash_readonly.py — Statically classify bash commands as read-only.

``is_readonly_bash_command`` decides, from the command string alone, whether a
``bash`` / ``execute_command`` call may skip the HITL approval prompt in
default mode and pass the plan-mode read-only gate. The judgment is
deliberately conservative: anything that cannot be judged statically — shell
metacharacters, unparseable quoting, unknown commands, flags outside the
whitelist — is reported as NOT read-only and falls back to the normal chain
(deny/ask rules, PreToolUse hooks, HITL approval). A false "no" only costs one
approval prompt; a false "yes" would silently execute a side-effecting
command, so the whitelist errs on the exclusion side.

The judgment is argv-level. The command must be a single simple command: any
sequencing/piping/redirection/substitution/expansion metacharacter (``;``
``|`` ``&`` ``>`` ``<`` backtick, ``\\n``, ``( )``, ``$``) disqualifies it up
front, then the string is split with ``shlex`` and dispatched on the basename
of ``argv[0]``:

- inspection commands (``ls``, ``cat``, ``grep``...) are read-only in any
  argument shape, except three entries with known side-effecting uses that
  get a narrow guard: ``env`` executes a command argument, ``rg --pre`` runs
  a preprocessor command, ``date -s`` sets the system clock;
- ``find`` only without any write-action flag (``-delete``, ``-exec``, ...);
- ``git`` only for read-only subcommands; ``branch``/``tag``/``remote``/
  ``config`` additionally restrict their flags and positionals;
- interpreters and package managers (``python``, ``node``, ``npm``, ``uv``,
  ``pip``) only for version/help-style invocations or listing subcommands —
  never with a script or other positional argument.

This is a curated subset of ZCode's generated bash-readonly registry (argv
direct judgment + git subcommand table + write-flag blacklist), not the full
per-flag policy database.
"""

from __future__ import annotations

import shlex

__all__ = ["is_readonly_bash_command"]

# Metacharacters that make a command string statically unjudgeable:
# sequencing, piping, redirection, command substitution, variable expansion.
# Any hit falls back to the normal approval chain.
_METACHARACTERS = ";|&><`\n()$"

# Commands that only inspect state, whatever the remaining arguments are.
# Entries with known side-effecting uses get a narrow guard in
# _inspection_readonly below.
UNCONDITIONAL_READONLY_COMMANDS = frozenset(
    {
        "ls",
        "pwd",
        "whoami",
        "uname",
        "date",
        "env",
        "printenv",
        "which",
        "where",
        "type",
        "wc",
        "file",
        "stat",
        "cat",
        "head",
        "tail",
        "grep",
        "rg",
    }
)

# ``env CMD`` executes CMD, so env is read-only only while every argument is
# either a flag or a NAME=VALUE assignment (a bare word would be the command).
# ``rg --pre`` runs a preprocessor command and ``date -s`` sets the clock;
# both flags are rejected in any spelling.
_ENV_ASSIGNMENT_HINT = "="
_RG_PREPROCESSOR_FLAGS = ("--pre",)
_DATE_SET_CLOCK_FLAGS = ("-s", "--set")

# find is read-only only when no write-action flag appears anywhere.
FIND_WRITE_FLAGS = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-files0-from"}
)
FIND_WRITE_FLAG_PREFIX = "-fprint"  # covers -fprint, -fprint0, -fprintf

# git: subcommands that never mutate anything, regardless of their flags —
# except that --output writes a file (log/show/diff support it, reflog show
# inherits it) and reflog expire/delete rewrite the reflog.
GIT_READONLY_SUBCOMMANDS = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "blame",
        "rev-parse",
        "describe",
        "shortlog",
        "reflog",
        "ls-files",
    }
)
_GIT_OUTPUT_FLAGS = ("--output",)
_GIT_REFLOG_WRITE_SUBCOMMANDS = frozenset({"expire", "delete"})

# git: harmless valueless global options skipped before the subcommand; every
# other pre-subcommand flag (-c/-C/--git-dir/...) is un-judged -> False.
GIT_GLOBAL_VALUELESS_FLAGS = frozenset({"--no-pager", "--paginate", "--version"})

# git remote: read-only only when every argument is one of these tokens.
GIT_REMOTE_READONLY_TOKENS = frozenset({"-v", "--get-url", "list", "show"})

# git branch/tag: zero positional arguments (a positional creates one) and
# only valueless read-only flags. Short flags may be clustered (-av).
GIT_BRANCH_READONLY_FLAGS = frozenset({"--list", "--all", "--remotes", "--show-current"})
GIT_BRANCH_READONLY_SHORT_FLAGS = frozenset({"-a", "-r", "-v", "-l"})
GIT_TAG_READONLY_FLAGS = frozenset({"-l", "--list"})

# git config: read-only only in --get/--get-all/--list style queries; at least
# one query flag must be present so ``git config key value`` cannot slip in.
GIT_CONFIG_READONLY_FLAGS = frozenset({"--get", "--get-all", "--get-regexp", "--list", "-l"})

# Interpreters/package managers: only self-describing invocations pass, and
# only with at least one such flag — a bare interpreter starts a REPL.
SELF_DESCRIBING_COMMANDS = frozenset({"python", "python3", "node", "npm"})
SELF_DESCRIBING_FLAGS = frozenset({"--version", "--help", "-V", "-v", "-h"})

# uv/pip: listing subcommands only, any further arguments.
UV_READONLY_SUBCOMMANDS = frozenset({"list", "show", "tree"})
PIP_READONLY_SUBCOMMANDS = frozenset({"list", "show", "freeze"})


def is_readonly_bash_command(command: str) -> bool:
    """Return True when *command* can be auto-approved as read-only.

    Conservative: every unparseable or out-of-whitelist shape returns False.
    """
    text = str(command or "")
    if not text.strip() or any(ch in _METACHARACTERS for ch in text):
        return False
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not argv:
        return False

    program = argv[0].replace("\\", "/").rpartition("/")[2].lower()
    if program.endswith(".exe"):
        # Windows models often spell the executable out (where.exe, git.exe);
        # the suffix carries no semantic, so judge the bare program name.
        program = program[: -len(".exe")]
    args = argv[1:]
    if program in UNCONDITIONAL_READONLY_COMMANDS:
        return _inspection_readonly(program, args)
    if program == "find":
        return _find_readonly(args)
    if program == "git":
        return _git_readonly(args)
    if program in SELF_DESCRIBING_COMMANDS:
        return bool(args) and all(token in SELF_DESCRIBING_FLAGS for token in args)
    if program == "uv":
        return bool(args) and args[0] in UV_READONLY_SUBCOMMANDS
    if program in {"pip", "pip3"}:
        return bool(args) and args[0] in PIP_READONLY_SUBCOMMANDS
    return False


def _inspection_readonly(program: str, args: list[str]) -> bool:
    """Inspection commands, minus their known side-effecting spellings."""
    if program == "env":
        # A token that is neither a flag nor NAME=VALUE is the command to run.
        return all(token.startswith("-") or _ENV_ASSIGNMENT_HINT in token for token in args)
    if program == "rg":
        return not _has_flag(args, _RG_PREPROCESSOR_FLAGS)
    if program == "date":
        return not _has_flag(args, _DATE_SET_CLOCK_FLAGS)
    return True


def _find_readonly(args: list[str]) -> bool:
    return not any(
        token in FIND_WRITE_FLAGS or token.startswith(FIND_WRITE_FLAG_PREFIX) for token in args
    )


def _git_readonly(args: list[str]) -> bool:
    subcommand = None
    rest: list[str] = []
    for index, token in enumerate(args):
        if token in GIT_GLOBAL_VALUELESS_FLAGS:
            continue
        if token.startswith("-"):
            return False  # -c/-C/--git-dir/... global options: not judged
        subcommand = token
        rest = args[index + 1 :]
        break
    if subcommand is None:
        return False
    if subcommand in GIT_READONLY_SUBCOMMANDS:
        if _has_flag(rest, _GIT_OUTPUT_FLAGS):
            # --output=<file> / --output <file> write a file to disk (log,
            # show and diff support the flag; reflog show inherits it), so
            # any spelling of it disqualifies the command — same guard as
            # diff had from the start, now covering every whitelisted reader.
            return False
        if subcommand == "reflog":
            # expire/delete rewrite the reflog — every other form is read.
            return not any(token in _GIT_REFLOG_WRITE_SUBCOMMANDS for token in rest)
        return True
    if subcommand == "remote":
        return all(token in GIT_REMOTE_READONLY_TOKENS for token in rest)
    if subcommand == "branch":
        return all(_branch_flag_readonly(token) for token in rest)
    if subcommand == "tag":
        return all(token in GIT_TAG_READONLY_FLAGS for token in rest)
    if subcommand == "config":
        flags = [token for token in rest if token.startswith("-")]
        return bool(flags) and all(token in GIT_CONFIG_READONLY_FLAGS for token in flags)
    return False


def _branch_flag_readonly(token: str) -> bool:
    """A branch flag is read-only when valueless: listed, or a short cluster."""
    if token in GIT_BRANCH_READONLY_FLAGS:
        return True
    if len(token) <= 1 or not token.startswith("-") or token.startswith("--"):
        return False  # a positional (or unknown flag) would create a branch
    return token in GIT_BRANCH_READONLY_SHORT_FLAGS or all(
        f"-{char}" in GIT_BRANCH_READONLY_SHORT_FLAGS for char in token[1:]
    )


def _has_flag(args: list[str], flags: tuple[str, ...]) -> bool:
    """True when any argument is one of *flags*, bare or ``=``-attached."""
    return any(token == flag or token.startswith(f"{flag}=") for token in args for flag in flags)
