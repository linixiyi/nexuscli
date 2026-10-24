"""ApprovalBridge: 把 Runtime 任务/turn 的审批挂起呈现给 Web 工作台（F6）。

执行器门链（permission deny 规则、hooks、写域守卫、只读直通）都在 approval_callback
之前评估——能走到回调的只剩「真的需要人点头」的调用，因此 Web 端只回答已经到达回调
的问题，物理上没有绕过面。登记表是内存态：进程生命周期即审批生命周期。

超时失败关闭（300s 无决策 → deny），与 AGENTS.md「无审批回调时按失败关闭」同取向，
保证 worker 槽位不被没人看的卡片占死。审批提示带 Reason 行：request["description"]
由前端渲染为 Reason 行，缺失视为服务端契约缺陷（测试锁住）。
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from nexuscli.tools.base import ToolDecision

APPROVAL_TIMEOUT_SECONDS = 300

DecisionVerb = Literal["approve", "deny"]


@dataclass(slots=True)
class PendingApproval:
    id: str
    request: dict[str, Any]
    scope_type: Literal["thread", "task"]
    scope_id: str
    created_at: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: ToolDecision | None = None
    resolved_by: str | None = None


def _new_approval_id() -> str:
    # 时间戳 + 随机后缀（tasks.py _new_id 同风格），避免同时钟粒度撞号
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"approval_{stamp}_{secrets.token_hex(3)}"


class ApprovalBridge:
    """内存审批登记表：callback_for 绑定 thread/task 上下文，Web 端查询与决策。"""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}

    def callback_for(self, *, thread_id: str | None = None, task_id: str | None = None) -> Any:
        """返回绑定上下文的审批回调闭包（执行器 approval_callback 合同）。"""

        scope_type: Literal["thread", "task"] = "thread" if thread_id is not None else "task"
        scope_id = thread_id if thread_id is not None else (task_id or "")

        def callback(request: dict[str, Any]) -> ToolDecision:
            approval = self._register(request, scope_type, scope_id)
            signaled = approval.event.wait(timeout=APPROVAL_TIMEOUT_SECONDS)
            with self._guard:
                self._pending.pop(approval.id, None)
            if not signaled:
                approval.resolved_by = "timeout"
                approval.decision = "deny"
                return "deny"
            return approval.decision or "deny"

        return callback

    def _register(self, request: dict[str, Any], scope_type: str, scope_id: str) -> PendingApproval:
        approval = PendingApproval(
            id=_new_approval_id(),
            request=dict(request),
            scope_type=scope_type,  # type: ignore[arg-type]
            scope_id=scope_id,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._guard:
            self._pending[approval.id] = approval
        return approval

    def list_pending(self) -> list[dict[str, Any]]:
        with self._guard:
            pending = list(self._pending.values())
        return [
            {
                "id": approval.id,
                "tool_name": approval.request.get("tool_name"),
                "input": approval.request.get("input"),
                "danger_level": approval.request.get("danger_level"),
                "description": approval.request.get("description"),
                "scope_type": approval.scope_type,
                "scope_id": approval.scope_id,
                "created_at": approval.created_at,
            }
            for approval in pending
        ]

    def resolve(self, approval_id: str, decision: DecisionVerb) -> PendingApproval | None:
        """决策落定并唤醒回调；未知/已解决的 id 返回 None（不区分两种缺失，避免二次决策歧义）。"""

        with self._guard:
            approval = self._pending.pop(approval_id, None)
        if approval is None:
            return None
        approval.decision = decision
        approval.resolved_by = "web"
        approval.event.set()
        return approval
