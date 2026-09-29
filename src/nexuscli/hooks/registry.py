"""registry.py — Match configured hooks to lifecycle events and tool names.

The event keys used in config.json are camelCase (``PreToolUse``); the mapping
onto the snake_case ``HooksConfig`` fields lives in :mod:`nexuscli.config`.
A matcher is a regex applied to the tool name and only takes effect for tool
events (PreToolUse / PostToolUse / PermissionRequest / PostToolUseFailure);
a ``*`` or empty matcher matches everything, and a matcher that fails to
compile simply never matches instead of raising.
"""

from __future__ import annotations

import re

from nexuscli.config import HOOK_EVENT_FIELDS, HookCommandConfig, HooksConfig

_TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PermissionRequest", "PostToolUseFailure"})


def hooks_for(
    config: HooksConfig,
    event: str,
    tool_name: str | None,
) -> list[HookCommandConfig]:
    """Return every hook command configured for *event*, matcher-filtered."""
    field_name = HOOK_EVENT_FIELDS.get(event)
    if field_name is None:
        return []
    commands: list[HookCommandConfig] = []
    for matcher in getattr(config, field_name):
        if _matcher_matches(matcher.matcher, event, tool_name):
            commands.extend(matcher.hooks)
    return commands


def _matcher_matches(matcher: str, event: str, tool_name: str | None) -> bool:
    if event not in _TOOL_EVENTS or not matcher or matcher == "*":
        return True
    if not tool_name:
        return False
    try:
        pattern = re.compile(matcher)
    except re.error:
        return False
    return pattern.search(tool_name) is not None
