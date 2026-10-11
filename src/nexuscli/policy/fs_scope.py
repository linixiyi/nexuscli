"""fs_scope.py — Static filesystem write-scope analysis for bash commands.

:class:`FSScopeGuard` decides, from the command string alone, whether a
``bash`` / ``execute_command`` call only intends to write inside the current
workspace (plus the system temp directory, ``tempfile.gettempdir()``). It
extracts write targets — redirection destinations (``>`` ``>>`` ``2>`` ``&>``
and siblings) and the path arguments of write-shaped commands (``rm``, ``mv``,
``cp``, ``tee``, ``mkdir``, ``git clean``, ... — see :data:`WRITE_COMMANDS`) —
and classifies them. The bash handler in ``tools/builtins.py`` runs it right
after CommandGuard and, on an ``"outside"`` verdict, routes the call through
the same HITL approval callback the tool executor uses.

Honest boundaries:

- Static heuristic, not a syscall-level interception. It sees the command
  string, not what the child process actually does: indirect writes are
  invisible (interpreters like ``sh -c`` / ``python -c``, scripts, compilers,
  network downloads, editors, build tools — none are analysed).
- Tokenization is best-effort ``shlex`` plus a curated command table. Command
  substitution anywhere, or a variable / glob / tilde / ``%VAR%`` in a *write
  position*, is reported as unresolvable and classified ``"outside"`` (fail
  closed); ``--flag=value`` spellings lose their values, and a path hidden
  behind an interpreter or an unmodelled construct can still shrink the
  verdict to ``"workspace"``.
- Multi-line commands are cut at newlines the way bash cuts at ``;``, but
  backslash-newline continuations are not joined: a write whose command word
  ends one line and whose argument starts the next is invisible to the
  analysis. ``/X``-shaped tokens count as cmd.exe switches only under the
  cmd.exe builtins that take them (:data:`_CMD_SWITCH_COMMANDS`); elsewhere
  they are treated as paths (``rm /etc`` is caught).
- A parallel layer, not a replacement: CommandGuard (destructive-command
  blacklist), bash_readonly (read-only fast path) and the executor's
  permission/HITL chain all stay independent; this guard only adds an
  approval prompt for detected out-of-workspace writes.
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from collections.abc import Iterator
from pathlib import Path

__all__ = ["WRITE_COMMANDS", "FSScopeGuard"]

WORKSPACE = "workspace"
OUTSIDE = "outside"

# Sentinel targets for shapes the analyzer refuses to guess about. Every
# sentinel contains "\x00", which no real path does, so :meth:`FSScopeGuard.
# classify` flags them outside.
_UNPARSEABLE = "\x00unparseable"
_SUBSTITUTION = "\x00command-substitution"
_PROCESS_SUBSTITUTION = "\x00process-substitution"

# Simple commands whose positional arguments are filesystem paths being
# created, replaced or destroyed. ``dd`` is handled apart (its target is
# spelled ``of=PATH``) and ``git`` subcommands in :func:`_scan_git`. The
# cmd.exe spellings are listed because the bash tool's shell on Windows is
# cmd.exe, where these are the everyday write commands.
WRITE_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "mkdir",
        "md",
        "mv",
        "move",
        "cp",
        "copy",
        "tee",
        "touch",
        "truncate",
        "mktemp",
        "shred",
        "install",
        "ln",
        "del",
        "erase",
        "rd",
        "ren",
        "rename",
    }
)

# git subcommands whose arguments rewrite working-tree files; ``checkout``
# only in its path-limited form (everything after ``--``).
GIT_WRITE_SUBCOMMANDS: frozenset[str] = frozenset({"clean", "rm", "mv", "restore", "checkout"})

# Redirect operators whose following token is a written-to path. ``>&`` is
# handled apart: followed by a digit it duplicates an fd (``2>&1`` — no file
# target), followed by a word it is bash's both-streams-to-file spelling.
# ``>(`` opens a process substitution and is never a path.
REDIRECT_TARGET_OPS: frozenset[str] = frozenset({">", ">>", "&>", "&>>", "<>"})

# Tokens that look like cmd.exe switches (``copy /Y a b``); skipping them
# keeps their values from becoming bogus "/Y" absolute-path targets. The
# skip applies only to the cmd.exe commands that actually take slash
# switches (see :data:`_CMD_SWITCH_COMMANDS`): for every other command a
# ``/X``-shaped token is a path — under POSIX spellings (``rm /etc``) it is
# a single-segment absolute path, and swallowing it would silently
# downgrade an outside write to ``workspace``.
_CMD_SWITCH = re.compile(r"^/[A-Za-z]{1,4}$")

# cmd.exe builtins with real ``/X`` switches (``copy /Y``, ``del /Q``,
# ``rd /S /Q``, ``move /Y``, ...). ``md``/``mkdir`` are deliberately absent:
# cmd.exe's mkdir takes no switches, so a ``/X`` token there is treated as
# a path (fail closed).
_CMD_SWITCH_COMMANDS: frozenset[str] = frozenset(
    {"copy", "del", "erase", "rd", "rmdir", "ren", "rename", "move"}
)

# Flags whose separate value is not a path (sizes, modes, suffixes, dates):
# without this table the value would leak into the target list as a bogus
# relative path. Flags whose value IS a write target (``cp -t DIR``) are
# deliberately absent — the value then falls through as an ordinary
# positional and is classified like any other target.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "mkdir": frozenset({"-m", "--mode"}),
    "md": frozenset({"-m", "--mode"}),
    "touch": frozenset({"-d", "--date", "-r", "--reference", "-t"}),
    "truncate": frozenset({"-s", "--size"}),
    "cp": frozenset({"-S", "--suffix"}),
    "copy": frozenset({"-S", "--suffix"}),
    "mv": frozenset({"-S", "--suffix"}),
    "move": frozenset({"-S", "--suffix"}),
    "ln": frozenset({"-S", "--suffix"}),
}

# Same idea for git's write subcommands (pathspec modifiers).
_GIT_VALUE_FLAGS: frozenset[str] = frozenset({"-e", "--exclude", "-s", "--source", "-O"})

# Leading NAME=value words are environment assignments, not arguments.
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

# Segment separators between independent commands. The newline is included
# because bash treats it exactly like ``;``: without it shlex would swallow
# it as plain whitespace and every write command after the first line of a
# multi-line command would be silently missed. punctuation_chars keeps
# ``&&`` / ``||`` / ``&>`` as single tokens (the newline must be passed to
# the shlex constructor for the same reason — it cannot be added after).
_SEGMENT_SEPARATORS = frozenset({"|", "||", "&", "&&", ";", "\n"})

# Characters that mark a raw target as unresolvable: expansion, globs, brace
# expansion, substitution leftovers and stray metacharacters. ``%`` covers
# cmd.exe's ``%VAR%`` expansion — the bash tool's shell on Windows is
# cmd.exe, so a write target spelled with ``%VAR%`` expands to whatever the
# environment says and must be treated as unresolvable (fail closed).
# Quoting has already been stripped by shlex, so none of these belong in a
# plain path.
_UNRESOLVABLE_CHARS = frozenset("$`%*?[{();|;&<>")


class FSScopeGuard:
    """Static write-scope analysis for one shell command string.

    Two steps: :meth:`extract_write_targets` pulls the raw write targets out
    of the command text, :meth:`classify` resolves them against the
    workspace and the system temp directory. The class is stateless; both
    methods take ``workspace_root`` explicitly.
    """

    def extract_write_targets(
        self, command: str, workspace_root: str | os.PathLike[str]
    ) -> list[str]:
        """Return the raw write targets *command* statically declares.

        Redirect destinations and the path arguments of
        :data:`WRITE_COMMANDS` / git's write subcommands, verbatim as they
        appear (``out.txt``, ``../x``, ``/etc/hosts``, ``$VAR/x``). Relative
        targets are resolved against *workspace_root* later, by
        :meth:`classify` — the parameter is kept here for API symmetry.

        Unresolvable shapes come back as sentinels so the verdict fails
        closed: command substitution anywhere in the string (it may write
        anywhere), a process substitution as a redirect destination, or
        quoting shlex cannot parse.
        """
        text = str(command or "")
        targets: list[str] = []
        if "$(" in text or "`" in text:
            targets.append(_SUBSTITUTION)
        try:
            tokens = _tokenize(text)
        except ValueError:
            targets.append(_UNPARSEABLE)
            return targets
        for segment in _segments(tokens):
            _scan_segment(segment, targets)
        return targets

    def classify(self, targets: list[str] | None, workspace_root: str | os.PathLike[str]) -> str:
        """Return :data:`WORKSPACE` or :data:`OUTSIDE` for the raw targets.

        Every target must resolve inside *workspace_root* or inside the
        system temp directory for ``"workspace"``; any absolute path,
        drive-letter path, ``..`` escape, tilde path, glob, or unresolvable
        sentinel makes it ``"outside"``. An empty target list is
        ``"workspace"`` — statically no writes are visible (see the module
        docstring's honest boundaries for what that does and does not mean).
        """
        root = Path(workspace_root).resolve()
        temp_root = Path(tempfile.gettempdir()).resolve()
        for raw in targets or []:
            if not _resolvable(raw):
                return OUTSIDE
            candidate = Path(raw)
            if candidate.is_absolute() or raw.startswith(("/", "\\")):
                # A POSIX-rooted path is absolute even on Windows, where
                # pathlib anchors it to the current drive instead.
                resolved = candidate.resolve()
            else:
                resolved = (root / candidate).resolve()
            if _contained(resolved, root) or _contained(resolved, temp_root):
                continue
            return OUTSIDE
        return WORKSPACE


def _tokenize(text: str) -> list[str]:
    """Split *text* into words and shell punctuation, honoring quotes.

    ``escape`` is cleared so backslashes stay literal: the bash tool's shell
    on Windows is cmd.exe, where ``C:\\dir\\file`` is an ordinary path, not
    an escape sequence. The cost is that ``\\"``-style escaped quotes inside
    quoted strings degrade to literal characters — accepted, because such
    commands tokenize badly enough to land on the conservative path anyway.

    The newline is a punctuation character (see :data:`_SEGMENT_SEPARATORS`):
    shlex would otherwise drop it as whitespace and hide the command
    sequence. shlex checks its whitespace set before the punctuation set, so
    the newline has to leave ``lex.whitespace`` too or the constructor
    setting alone is inert. Inside quotes the newline stays literal, so a
    quoted string spanning lines survives as one token.
    """
    lex = shlex.shlex(text, posix=True, punctuation_chars="();<>|&\n")
    lex.whitespace = " \t\r"  # newline: punctuation (segment cut), not whitespace
    lex.whitespace_split = True
    lex.escape = ""
    return list(lex)


def _segments(tokens: list[str]) -> Iterator[list[str]]:
    """Yield the command's token stream cut at sequencing separators."""
    current: list[str] = []
    for token in tokens:
        if token in _SEGMENT_SEPARATORS:
            yield current
            current = []
        else:
            current.append(token)
    yield current


def _scan_segment(tokens: list[str], targets: list[str]) -> None:
    """Collect redirect targets from one segment and queue its plain words."""
    words: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if token == ">(":
            targets.append(_PROCESS_SUBSTITUTION)
            index += 1
            continue
        if token in REDIRECT_TARGET_OPS:
            index += _take_redirect_target(nxt, targets)
            continue
        if token == ">&":
            if nxt is not None and nxt.isdigit():
                index += 2  # fd duplication (2>&1): no file target
            else:
                index += _take_redirect_target(nxt, targets)
            continue
        if token.startswith("<"):
            # Input redirection / heredoc / here-string: the operand is
            # read, never written — skip operator and operand wholesale.
            index += 2 if nxt is not None else 1
            continue
        words.append(token)
        index += 1
    _scan_words(words, targets)


def _take_redirect_target(nxt: str | None, targets: list[str]) -> int:
    """Append the redirect destination *nxt*; return the consumed step."""
    if nxt is None:
        return 1
    if nxt == "(" or nxt.startswith(">("):
        targets.append(_PROCESS_SUBSTITUTION)
    else:
        targets.append(nxt)
    return 2


def _scan_words(words: list[str], targets: list[str]) -> None:
    """Queue the path arguments when the segment's program writes files."""
    index = 0
    while index < len(words) and _ENV_ASSIGNMENT.match(words[index]):
        index += 1
    if index >= len(words):
        return
    program = _program_name(words[index])
    args = words[index + 1 :]
    if program == "git":
        _scan_git(args, targets)
        return
    if program == "dd":
        for arg in args:
            if arg.lower().startswith("of="):
                targets.append(arg[len("of=") :])
        return
    if program in WRITE_COMMANDS:
        _scan_paths(
            args,
            targets,
            _VALUE_FLAGS.get(program, frozenset()),
            skip_cmd_switches=program in _CMD_SWITCH_COMMANDS,
        )


def _scan_git(args: list[str], targets: list[str]) -> None:
    """Queue the working-tree paths a ``git`` write subcommand touches."""
    if not args or args[0] not in GIT_WRITE_SUBCOMMANDS:
        return
    subcommand, rest = args[0], args[1:]
    if subcommand == "checkout":
        # Only the path-limited form (``git checkout -- paths``) rewrites
        # files; a bare ``git checkout <branch>`` switches refs only.
        if "--" in rest:
            targets.extend(rest[rest.index("--") + 1 :])
        return
    # git's flag grammar is ``-x`` only; a ``/X`` token is a path.
    _scan_paths(rest, targets, _GIT_VALUE_FLAGS, skip_cmd_switches=False)


def _scan_paths(
    args: list[str],
    targets: list[str],
    value_flags: frozenset[str],
    *,
    skip_cmd_switches: bool = False,
) -> None:
    """Queue every positional path argument, skipping flags and their values.

    ``/X``-shaped tokens are skipped only when *skip_cmd_switches* says the
    command actually takes cmd.exe slash switches; otherwise they queue as
    paths (a POSIX absolute path must not be swallowed).
    """
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token == "--" or (skip_cmd_switches and _CMD_SWITCH.match(token)):
            continue
        if token.startswith("-") and token != "-":
            if "=" not in token and token in value_flags:
                skip_next = True
            continue
        targets.append(token)


def _program_name(word: str) -> str:
    """Basename of argv[0], lowercased, with Windows' .exe suffix stripped."""
    name = word.replace("\\", "/").rpartition("/")[2].lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name


def _resolvable(target: str) -> bool:
    """False for sentinels, expansions, globs and tilde paths (fail closed)."""
    if not target or "\x00" in target or target.startswith("~"):
        return False
    return not any(ch in _UNRESOLVABLE_CHARS for ch in target)


def _contained(path: Path, ancestor: Path) -> bool:
    """True when *path* is *ancestor* or lies underneath it."""
    try:
        path.relative_to(ancestor)
    except ValueError:
        return False
    return True
