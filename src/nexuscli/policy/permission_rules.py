"""permission_rules.py — Pattern-based permission rules evaluated before HITL.

Inspired by the allow/deny/ask rule model used by Claude Code and OpenCode.
Rules live under ``permissions.allow`` / ``permissions.deny`` / ``permissions.ask``
in config.json and use one syntax::

    tool                     # every call of that tool
    bash(git diff:*)         # command rules: ``prefix:*`` matches commands starting with prefix
    bash(npm run *)          # other command specs are fnmatch globs on the command string
    write_file(src/**)       # path rules: fnmatch glob on the payload path
    web_fetch(domain:github.com)  # domain rules: exact host or subdomain match
    mcp__github__*           # tool names support fnmatch globs when they contain glob chars

Evaluation precedence is deny > ask > allow: a deny rule always wins over an
allow rule, and ``ask`` wins over ``allow`` so an explicit ask cannot be
shadowed by a broader allow entry. Within one list the first matching rule
determines the reported rule text.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

ACTION_PRECEDENCE = ("deny", "ask", "allow")

_COMMAND_TOOLS = {"bash", "execute_command"}
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


def subject_for(tool_name: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(kind, value)`` the rule spec is matched against for this call.

    kind is one of ``command`` / ``path`` / ``domain`` / ``any``. Rules with a
    spec only match kinds they can speak about; a bare or ``*`` spec matches
    any kind.
    """
    if tool_name in _COMMAND_TOOLS:
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
            return subject.startswith(rule.spec[:-2])
        return fnmatch.fnmatchcase(subject, rule.spec)
    if subject_kind == "path":
        return fnmatch.fnmatchcase(subject, rule.spec)
    if subject_kind == "domain":
        if rule.spec.startswith("domain:"):
            expected = rule.spec[len("domain:") :].lower().strip("/")
            return subject == expected or subject.endswith(f".{expected}")
        return fnmatch.fnmatchcase(subject, rule.spec)
    # "any": a spec'd rule cannot match a tool without a matchable subject.
    return False


def _tool_name_matches(pattern: str, tool_name: str) -> bool:
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(tool_name, pattern)
    return pattern == tool_name


def evaluate_permissions(
    permissions: Any, tool_name: str, payload: dict[str, Any]
) -> PermissionDecision | None:
    """First match under deny > ask > allow precedence; None when no rule hits."""
    subject_kind, subject = subject_for(tool_name, payload)
    for action in ACTION_PRECEDENCE:
        for raw in getattr(permissions, action, None) or []:
            rule = parse_rule(raw)
            if rule is None:
                continue
            if rule_matches(rule, tool_name, subject_kind, subject):
                return PermissionDecision(action=action, rule=rule.raw)
    return None
