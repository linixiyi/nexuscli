"""引擎结果合同（F11）：EngineResult = RunOutcome | RunSuspension 的 sum type。

对标 kitagent engine/base.py 的形态（05-execution-durability.md F11 节定稿）：
挂起是一等返回值，不是异常、不是 None。SuspensionReason 三态中，本轮真实生产者只有
collaborative_interrupt（plan 协作中断）与 budget_exhausted（max_turns 用尽 / loop guard
中止）；approval_pending 是挂账——F6 审批桥为同步阻塞回调、无挂起出口，异步化审批模型
之前不接线（该值不出现在任何运行路径）。

错误码分类靠发射点标注 + 类型化异常，不做消息字符串匹配（码表只增不改语义）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

SuspensionReason = Literal["approval_pending", "collaborative_interrupt", "budget_exhausted"]

ErrorCode = Literal[
    "llm_error",
    "plan_no_checkpoint",
    "tool_invocation_conflict",
    "outcome_unknown",
    "agent_failed",
]


class PlanCheckpointMissing(ValueError):
    """/plan --resume 找不到计划 checkpoint（取代裸 ValueError 的类型化升级）。"""


@dataclass(frozen=True, slots=True)
class EngineErrorDetail:
    code: ErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """终态结果。"""

    status: Literal["success", "failed"]
    result: str
    steps: int = 0
    reason: str | None = None
    error: EngineErrorDetail | None = None


@dataclass(frozen=True, slots=True)
class RunSuspension:
    """非终态挂起：运行停在可恢复的点（一等返回值）。"""

    reason: SuspensionReason
    checkpoint_ref: str | None = None
    detail: str | None = None


EngineRunResult = RunOutcome | RunSuspension


def classify_error(exc: BaseException) -> ErrorCode:
    """按类型化异常分类（发射点标注优先于本函数；此处只兜底分发）。"""

    from nexuscli.tools.journal import ToolJournalConflictError

    if isinstance(exc, PlanCheckpointMissing):
        return "plan_no_checkpoint"
    if isinstance(exc, ToolJournalConflictError):
        return "tool_invocation_conflict"
    return "agent_failed"


def error_detail_from_event(event: dict[str, Any]) -> EngineErrorDetail:
    """从 error 事件构造类型化明细：发射点标注的 error_code 优先，其余 classify 兜底。"""

    code = event.get("error_code")
    exc = event.get("error")
    message = str(exc) if exc is not None else "unknown error"
    if isinstance(code, str) and code in {
        "llm_error",
        "plan_no_checkpoint",
        "tool_invocation_conflict",
        "outcome_unknown",
        "agent_failed",
    }:
        return EngineErrorDetail(code=code, message=message)  # type: ignore[arg-type]
    if isinstance(exc, BaseException):
        return EngineErrorDetail(code=classify_error(exc), message=message)
    return EngineErrorDetail(code="agent_failed", message=message)


async def fold_engine_result(events: Any) -> EngineRunResult:
    """把事件流折叠为 EngineResult（SDK `*_result_async` 变体的共用实现）。

    - error 事件 → RunOutcome(failed, EngineErrorDetail)
    - plan_interrupted 事件 → RunSuspension(collaborative_interrupt, plan_id)
    - kind=budget_exhausted 的 warning → RunSuspension(budget_exhausted)
    - done 事件 → RunOutcome(success, 累计 text_delta 为 result)
    """

    text_parts: list[str] = []
    steps = 0
    budget_detail: str | None = None
    async for event in events:
        kind = str(event.get("type") or "")
        if kind == "text_delta":
            text_parts.append(str(event.get("text") or ""))
        elif kind == "error":
            detail = error_detail_from_event(event)
            return RunOutcome(
                status="failed",
                result="".join(text_parts),
                steps=steps,
                error=detail,
            )
        elif kind == "plan_interrupted":
            return RunSuspension(
                reason="collaborative_interrupt",
                checkpoint_ref=str(event.get("plan_id") or "") or None,
            )
        elif kind == "warning" and event.get("kind") == "budget_exhausted":
            budget_detail = str(event.get("message") or "")
        elif kind == "done":
            steps = int(event.get("total_turns") or 0)
            if budget_detail is not None:
                return RunSuspension(reason="budget_exhausted", detail=budget_detail)
            return RunOutcome(status="success", result="".join(text_parts), steps=steps)
    return RunOutcome(
        status="failed",
        result="".join(text_parts),
        error=EngineErrorDetail(
            code="agent_failed", message="event stream ended without a done event"
        ),
    )
