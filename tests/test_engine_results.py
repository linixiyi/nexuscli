"""EngineResult 合同测试（spec 2026-10-25_add-turn-resume-engine-contract，F11）。"""

from __future__ import annotations

import pytest

from nexuscli.agent.results import (
    EngineErrorDetail,
    PlanCheckpointMissing,
    RunOutcome,
    RunSuspension,
    classify_error,
    error_detail_from_event,
    fold_engine_result,
)
from nexuscli.tools.journal import ToolJournalConflictError


def _events(*items: dict):
    async def stream():
        for item in items:
            yield item

    return stream()


@pytest.mark.anyio
async def test_fold_success() -> None:
    result = await fold_engine_result(
        _events(
            {"type": "text_delta", "text": "hello "},
            {"type": "text_delta", "text": "world"},
            {"type": "done", "total_turns": 3, "total_tokens": 100},
        )
    )
    assert isinstance(result, RunOutcome)
    assert result.status == "success"
    assert result.result == "hello world"
    assert result.steps == 3
    assert result.error is None


@pytest.mark.anyio
async def test_fold_error_carries_typed_code() -> None:
    result = await fold_engine_result(
        _events(
            {"type": "text_delta", "text": "partial"},
            {"type": "error", "error": RuntimeError("boom"), "error_code": "llm_error"},
        )
    )
    assert isinstance(result, RunOutcome)
    assert result.status == "failed"
    assert result.result == "partial"
    assert result.error == EngineErrorDetail(code="llm_error", message="boom")


@pytest.mark.anyio
async def test_fold_plan_interrupted_is_suspension() -> None:
    result = await fold_engine_result(
        _events(
            {"type": "plan_interrupted", "plan_id": "plan_123"},
        )
    )
    assert isinstance(result, RunSuspension)
    assert result.reason == "collaborative_interrupt"
    assert result.checkpoint_ref == "plan_123"


@pytest.mark.anyio
async def test_fold_budget_warning_is_suspension() -> None:
    result = await fold_engine_result(
        _events(
            {"type": "text_delta", "text": "work in progress"},
            {
                "type": "warning",
                "kind": "budget_exhausted",
                "message": "Reached the agent.max_turns limit (200)",
            },
            {"type": "done", "total_turns": 200, "total_tokens": 5},
        )
    )
    assert isinstance(result, RunSuspension)
    assert result.reason == "budget_exhausted"
    assert "max_turns" in (result.detail or "")


@pytest.mark.anyio
async def test_fold_stream_ended_without_done() -> None:
    result = await fold_engine_result(_events({"type": "text_delta", "text": "x"}))
    assert isinstance(result, RunOutcome)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == "agent_failed"


def test_classify_error_codes() -> None:
    assert classify_error(PlanCheckpointMissing("x")) == "plan_no_checkpoint"
    assert classify_error(ToolJournalConflictError("x")) == "tool_invocation_conflict"
    assert classify_error(RuntimeError("x")) == "agent_failed"


def test_error_detail_from_event_prefers_emitter_code() -> None:
    detail = error_detail_from_event(
        {"error": PlanCheckpointMissing("missing"), "error_code": "plan_no_checkpoint"}
    )
    assert detail.code == "plan_no_checkpoint"
    # 无标注 → 按异常类型兜底
    detail = error_detail_from_event({"error": ToolJournalConflictError("mismatch")})
    assert detail.code == "tool_invocation_conflict"
    # 裸事件 → agent_failed
    detail = error_detail_from_event({"error": "string error"})
    assert detail.code == "agent_failed"
    assert detail.message == "string error"
