"""Round-2 closeout fixes: vision badge in the /model fetch list, and the
write-scope reason line in the approval prompt.

Feedback sources (spec 2026-10-18_verify-zero-and-yellow, round 2):
- ``_capability_badges`` rendered only reasoning-effort badges, so a gateway
  model with ``capabilities.vision: true`` showed no vision marker in the
  fetch list even though the capability was parsed and persisted.
- ``_approval_prompt`` never rendered ``request["description"]``, so the
  write-scope guard's reason was invisible and stacked approval prompts were
  indistinguishable apart from the danger level.
"""

from __future__ import annotations

import io
import sys

from rich.console import Console

from nexuscli.entrypoints.commands.approval import _approval_prompt
from nexuscli.entrypoints.model_selector import _capability_badges
from nexuscli.llm.openai_compatible import ModelListingProfile


def test_capability_badges_include_vision() -> None:
    profile = ModelListingProfile(model="cbcn/glm-5.3-flash", context_window=1, vision=True)
    assert "视觉✓" in _capability_badges(profile)


def test_capability_badges_stay_tri_state() -> None:
    undeclared = ModelListingProfile(model="m", context_window=1)
    assert _capability_badges(undeclared) == ""
    explicitly_off = ModelListingProfile(model="m", context_window=1, vision=False)
    assert "视觉" not in _capability_badges(explicitly_off)


class _FakeStdin(io.StringIO):
    def isatty(self) -> bool:
        return True


class _PermissionStub:
    def __init__(self) -> None:
        self.mode: str | None = None

    def set(self, mode: str) -> None:
        self.mode = mode


def _run_prompt(request: dict, monkeypatch) -> tuple[str, str]:
    buf = io.StringIO()
    console = Console(file=buf, width=160)
    monkeypatch.setattr(sys, "stdin", _FakeStdin("n\n"))
    decision = _approval_prompt(request, console, _PermissionStub())
    return decision, buf.getvalue()


def test_approval_prompt_renders_description_line(monkeypatch) -> None:
    request = {
        "tool_name": "bash",
        "danger_level": "high",
        "input": {"command": "cat /etc/hostname > /outside/probe.txt"},
        "description": (
            "Static analysis found a write target outside the workspace: /outside/probe.txt"
        ),
    }
    decision, out = _run_prompt(request, monkeypatch)
    assert decision == "deny"
    assert "Reason: Static analysis found a write target outside the workspace" in out


def test_approval_prompt_omits_description_line_when_absent(monkeypatch) -> None:
    request = {
        "tool_name": "bash",
        "danger_level": "medium",
        "input": {"command": "npm test"},
    }
    decision, out = _run_prompt(request, monkeypatch)
    assert decision == "deny"
    assert "Reason:" not in out
