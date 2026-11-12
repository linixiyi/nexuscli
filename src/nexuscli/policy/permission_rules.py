"""permission_rules.py — Pattern-based permission rules evaluated before HITL.

Inspired by the allow/deny/ask rule model used by Claude Code and OpenCode.
Rules live under ``permissions.allow`` / ``permissions.deny`` / ``permissions.ask``
in config.json and use one syntax::

    tool                     # every call of that tool
    bash(git diff:*)         # command rules: ``prefix:*`` matches the prefix at a token
                             # boundary on a single un-chained command (no shell operators)
    bash(npm run *)          # other command specs are fnmatch globs on the command string
    write_file(src/**)       # path rules: fnmatch glob on the payload path,
                             # matched against the raw and POSIX-normalized
                             # spellings (either hitting counts as a hit)
    web_fetch(domain:github.com)  # domain rules: exact host or subdomain match
    mcp__github__*           # tool names support fnmatch globs when they contain glob chars

Evaluation precedence is deny > ask > allow: a deny rule always wins over an
allow rule, and ``ask`` wins over ``allow`` so an explicit ask cannot be
shadowed by a broader allow entry. Within one list the first matching rule
determines the reported rule text.
"""

from __future__ import annotations

import fnmatch
import posixpath
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

ACTION_PRECEDENCE = ("deny", "ask", "allow")

_COMMAND_TOOLS = {"bash", "execute_command"}

# ``!`cmd` `` fragments inside custom slash command bodies enter the registry
# under this internal tool name (see nexuscli.entrypoints.slash_commands), but
# the call IS a shell command: rules and hooks written for the shell tools
# must evaluate it (spec 2026-11-13_fix-project-trust-boundary task-0004 /
# F3 — keep the internal name, map the policy subject).
SLASH_INJECTION_TOOL = "slash_injection"
# The shell tools an injection fragment is policy-equivalent to. Both are
# ``_COMMAND_TOOLS`` members, so command rules (``bash(git push:*)``) speak
# the fragment's language either way.
INJECTION_SHELL_TOOLS = ("bash", "execute_command")
# The subject PreToolUse hooks see for an injection fragment: the canonical
# shell tool name, so a hook matcher written for ``bash`` matches fragments.
INJECTION_POLICY_SUBJECT = "bash"

_PATH_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "list_dir",
    "get_file_info",
    "glob",
    "glob_files",
    "grep",
    "grep_code",
    "directory_tree",
    "search_code",
}


@dataclass(frozen=True, slots=True)
class PermissionRule:
    raw: str
    tool: str
    spec: str


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    action: str  # "allow" | "deny" | "ask"
    rule: str  # raw rule text that matched


def parse_rule(raw: str) -> PermissionRule | None:
    """Parse ``tool(spec)`` or a bare ``tool`` rule; None when unparseable."""
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith(")") and "(" in text:
        name, _, spec = text[:-1].partition("(")
        name = name.strip()
        spec = spec.strip()
        if not name or "(" in spec or ")" in spec:
            return None
        return PermissionRule(raw=text, tool=name, spec=spec)
    if "(" in text or ")" in text:
        return None
    return PermissionRule(raw=text, tool=text, spec="")


def policy_tool_names(tool_name: str) -> tuple[str, ...]:
    """Tool names a call's permission rules are matched against.

    Most calls evaluate under their own registry name. A ``slash_injection``
    fragment additionally evaluates under the shell tools it actually is
    (``bash`` / ``execute_command``) — and keeps matching rules written for
    the internal name, so an existing ``deny: ["slash_injection"]`` retains
    its force. The candidates only widen what a rule can hit; the deny > ask
    > allow precedence below is applied over the same rule lists as before.
    """
    if tool_name == SLASH_INJECTION_TOOL:
        return (*INJECTION_SHELL_TOOLS, SLASH_INJECTION_TOOL)
    return (tool_name,)


def subject_for(tool_name: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(kind, value)`` the rule spec is matched against for this call.

    kind is one of ``command`` / ``path`` / ``domain`` / ``any``. Rules with a
    spec only match kinds they can speak about; a bare or ``*`` spec matches
    any kind.
    """
    if tool_name in _COMMAND_TOOLS or tool_name == SLASH_INJECTION_TOOL:
        return "command", str(payload.get("command") or "")
    if tool_name in _PATH_TOOLS:
        raw_path = str(payload.get("path") or "")
        if not raw_path:
            return "path", ""
        return "path", raw_path.replace("\\", "/")
    if tool_name in {"web_fetch", "web_search"}:
        # Only a real URL participates in domain matching. Query text (e.g.
        # web_search's ``query``) must never be mistaken for a hostname: it
        # yields an empty subject that no domain rule can match, so deny
        # rules cannot be hit (or dodged) by crafted query strings.
        url = str(payload.get("url") or "")
        if not url:
            return "domain", ""
        host = urlsplit(url if "//" in url else f"https://{url}").hostname
        return "domain", (host or "").lower()
    return "any", ""


def rule_matches(rule: PermissionRule, tool_name: str, subject_kind: str, subject: str) -> bool:
    if not _tool_name_matches(rule.tool, tool_name):
        return False
    if not rule.spec or rule.spec == "*":
        return True
    if subject_kind == "command":
        if rule.spec.endswith(":*"):
            return _command_prefix_matches(subject, rule.spec[:-2])
        return fnmatch.fnmatchcase(subject, rule.spec)
    if subject_kind == "path":
        return any(
            fnmatch.fnmatchcase(path_form, spec_form)
            for path_form in _path_variants(subject)
            for spec_form in _path_variants(rule.spec)
        )
    if subject_kind == "domain":
        if rule.spec.startswith("domain:"):
            expected = rule.spec[len("domain:") :].lower().strip("/")
            return subject == expected or subject.endswith(f".{expected}")
        return fnmatch.fnmatchcase(subject, rule.spec)
    # "any": a spec'd rule cannot match a tool without a matchable subject.
    return False


def _path_variants(path: str) -> tuple[str, ...]:
    """Spellings of a path-matching operand (subject or rule spec).

    Path rules used to fnmatch the raw string only, so ``./.env``,
    ``a/../.env``, ``.//AGENTS.md`` and ``src/./x.py`` dodged deny rules
    written for the clean spellings. Matching now considers every form:

    - the raw spelling first — existing rules keep their exact semantics
      (spec 2026-11-13_fix-project-trust-boundary task-0005 keeps the raw
      form for backward compatibility), and
    - the POSIX-normalized spelling (``posixpath.normpath``): collapses
      ``./`` segments, ``a/../`` steps and repeated slashes — for a
      relative path this *is* its resolved relative form.

    The category never changes: a relative path stays relative and an
    absolute path stays absolute (``normpath`` preserves the leading
    ``/``), so an absolute payload can never match a relative rule or vice
    versa. No filesystem access happens here: symlinks are not resolved
    (that is path_guard/FSScopeGuard's job on the real path), so rule
    matching stays lexical.
    """
    if not path:
        return (path,)
    variants = [path]
    normalized = posixpath.normpath(path)
    if normalized != path:
        variants.append(normalized)
    return tuple(variants)


# Shell metacharacters that turn the text after a matched prefix into another
# command or command substitution. ``$(``, subshells and process substitution
# are all covered by the bare parens; ``&`` covers ``&&`` and backgrounding
# (redirection spellings like ``2>&1`` are gated too — conservative, and the
# tool runs under /bin/sh where ``&>`` is not a redirect anyway).
_COMPOUND_SHELL_CHARS = frozenset(";|&`()\r\n")


def _command_prefix_matches(subject: str, prefix: str) -> bool:
    """True when ``subject`` starts with ``prefix`` at a shell token boundary
    and nothing after the prefix chains another command.

    ``git diff:*`` must allow ``git diff`` and ``git diff --stat`` but never
    ``git difftool``: a bare ``startswith`` lets a longer command word ride
    the prefix. The character right after the prefix therefore has to end
    the word. A space ends the word, but the tail may still chain commands
    the rule never named (``git diff --stat && git push --force``, ``git
    diff --stat; touch /tmp/x``): the rule only speaks for its own prefix,
    so any shell operator or command substitution after it keeps the rule
    silent and the call falls through to the ordinary approval chain —
    including for deny rules, so a compound can neither dodge deny nor ride
    an allow rule that only named the first segment.
    """
    if not subject.startswith(prefix):
        return False
    rest = subject[len(prefix) :]
    if not rest:
        return True
    if rest[0] not in " \t":
        return False
    return not any(ch in _COMPOUND_SHELL_CHARS for ch in rest)


def _tool_name_matches(pattern: str, tool_name: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(tool_name, pattern)
    return pattern == tool_name


def evaluate_permissions(
    permissions: Any, tool_name: str, payload: dict[str, Any]
) -> PermissionDecision | None:
    """First match under deny > ask > allow precedence; None when no rule hits.

    A rule matches when its tool pattern hits any of the call's policy names
    (see :func:`policy_tool_names`) and its spec matches the subject — so an
    injection fragment is denied/asked/allowed by the same ``bash`` rules
    that would govern an ordinary shell call.
    """
    subject_kind, subject = subject_for(tool_name, payload)
    names = policy_tool_names(tool_name)
    for action in ACTION_PRECEDENCE:
        for raw in getattr(permissions, action, None) or []:
            rule = parse_rule(raw)
            if rule is None:
                continue
            if any(rule_matches(rule, name, subject_kind, subject) for name in names):
                return PermissionDecision(action=action, rule=rule.raw)
    return None
