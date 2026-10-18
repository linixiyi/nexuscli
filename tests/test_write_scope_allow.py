"""Regression tests for the bash write-scope guard's allow-rule path.

Round-1 real-run feedback (spec 2026-10-18_verify-zero-and-yellow): with
``permissions.allow: ["bash"]`` a subagent's ``ls X 2>/dev/null && cat Y``
still hit the inner write-scope HITL prompt — the guard never consulted the
permission rules the executor already honours — and ``/dev/null`` counted as
an out-of-workspace write target, so every redirect-to-null command prompted.
"""

from __future__ import annotations

import asyncio

from nexuscli.config import NexusCliConfig, PermissionsConfig
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import _reject_outside_write_scope


def _context(tmp_path, *, allow=None, hitl="auto", approval=None) -> ToolContext:
    config = NexusCliConfig()
    config.policy.hitl_mode = hitl
    if allow is not None:
        config.permissions = PermissionsConfig(allow=allow)
    return ToolContext(cwd=str(tmp_path), config=config, approval_callback=approval)


def test_dev_null_redirect_does_not_prompt(tmp_path) -> None:
    context = _context(tmp_path)
    result = asyncio.run(_reject_outside_write_scope("ls tests 2>/dev/null", context))
    assert result is None


def test_allow_rule_skips_write_scope_prompt(tmp_path) -> None:
    context = _context(tmp_path, allow=["bash"])
    result = asyncio.run(
        _reject_outside_write_scope("cat /etc/hostname > /nexuscli-guard-outside.txt", context)
    )
    assert result is None


def test_allow_rule_with_hitl_always_still_prompts(tmp_path) -> None:
    prompted: list[dict] = []

    def approval(request):
        prompted.append(request)
        return "approve"

    context = _context(tmp_path, allow=["bash"], hitl="always", approval=approval)
    result = asyncio.run(
        _reject_outside_write_scope("cat /etc/hostname > /nexuscli-guard-outside.txt", context)
    )
    assert len(prompted) == 1
    assert result is None


def test_without_allow_rule_still_prompts_and_fail_closed(tmp_path) -> None:
    prompted: list[dict] = []

    def approval(request):
        prompted.append(request)
        return "deny"

    context = _context(tmp_path, approval=approval)
    result = asyncio.run(
        _reject_outside_write_scope("cat /etc/hostname > /nexuscli-guard-outside.txt", context)
    )
    assert len(prompted) == 1
    assert result is not None and result.is_error
