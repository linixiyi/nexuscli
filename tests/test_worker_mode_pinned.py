"""A user-pinned /team worker mode must not be silently overridden by the planner.

Round-1 feedback (spec 2026-10-18_verify-zero-and-yellow): with
``/team --mode plan …`` the planner emitted ``"mode": "react"`` for write
steps and the user's read-only request never took effect — the plan summary
even displayed ``mode: react``. Any non-default worker mode is treated as an
explicit pin: planner per-step modes are ignored, the default path is
unchanged.
"""

from __future__ import annotations

from nexuscli.agent.orchestrator import AgentOrchestrator
from nexuscli.config import load_config
from nexuscli.tools.registry import ToolRegistry

PLAN_JSON = (
    '{"summary":"demo","steps":['
    '{"id":"a","description":"Task A","type":"COMMAND","mode":"react"},'
    '{"id":"b","description":"Task B","type":"COMMAND"}'
    "]}"
)


def _orchestrator(tmp_path, worker_mode: str) -> AgentOrchestrator:
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    return AgentOrchestrator(
        llm_client=object(),
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(tmp_path),
        default_worker_mode=worker_mode,
    )


def test_pinned_plan_mode_ignores_planner_step_modes(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path, "plan")
    assert orchestrator.pinned_worker_mode is True
    steps = orchestrator.parse_plan(PLAN_JSON)
    assert [step.mode for step in steps] == ["plan", "plan"]


def test_unpinned_default_keeps_planner_step_modes(tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path, "react")
    assert orchestrator.pinned_worker_mode is False
    steps = orchestrator.parse_plan(PLAN_JSON)
    assert [step.mode for step in steps] == ["react", "react"]
