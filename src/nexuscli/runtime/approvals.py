"""ApprovalBridge: 把 Runtime 任务/turn 的审批挂起呈现给 Web 工作台（F6）。

执行器门链（permission deny 规则、hooks、写域守卫、只读直通）都在 approval_callback
之前评估——能走到回调的只剩「真的需要人点头」的调用，因此 Web 端只回答已经到达回调
的问题，物理上没有绕过面。登记表是内存态：进程生命周期即审批生命周期。

超时失败关闭（300s 无决策 → deny），与 AGENTS.md「无审批回调时按失败关闭」同取向，
保证 worker 槽位不被没人看的卡片占死。审批提示带 Reason 行：request["description"]
由前端渲染为 Reason 行，缺失视为服务端契约缺陷（测试锁住）。

A5 审批五态（Ported from kitagent (MIT, zed123214/kitagent)
src/agent_runtime/web/session.py:183-232 的 decide 幂等与校验 +
src/agent_runtime/web/projection.py:207 的 can_allow）：

- 状态机：pending → submitting → resolved（approve/deny）| expired（300s 超时）
  | unknown（决策过程异常的防御位，session.py:221-224 同款）。
- decide 幂等：重复决策同 id 且同动词 → 返回首次结果（不报错、不二次执行）；
  非 pending 状态拒绝变更（异动词翻转、expired/unknown 卡的再决策 → 类型化错误，
  session.py:185 同款）。
- 参数截断禁 allow：request.input 序列化超过 APPROVAL_INPUT_MAX_BYTES 时 approve
  返回类型化错误（防未审先批——卡片审的不是完整参数）；deny 不受限。

A6 会话权限预判与即时重判（Ported from kitagent (MIT, zed123214/kitagent)
core/permissions/manager.py:66-108 的 configure_session/_recheck_pending）：

- callback_for 可绑定 SessionPermissions（runtime/websession.py）：回调先查会话
  模式（revoked → deny、read_only → deny、auto → approve），ask 之外不登记卡片；
  回调位于既有门链出口——显式 allow 规则与 run-scope 授权在门链内已放行，根本
  不会到达回调，因此不存在重复问（AGENTS.md allow 语义）。
- recheck_pending：权限变更后即时重判全部挂起卡，不再满足当前模式的直接收口并
  从登记表移除（manager.py:99-108 同款，双向：auto 对等待中的调用放行，
  PermissionControl.tsx:133-134「将自动允许当前等待及后续的工具调用」）。

断连清空的等价语义：kitagent manager.py:389-404 在客户端断连时清空挂起；本桥是
单用户 CLI 进程内对象，无断连面——登记表（含已收口记录）仅存内存，随进程消亡
（测试锁：无任何落盘路径）。
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from nexuscli.tools.base import ToolDecision

APPROVAL_TIMEOUT_SECONDS = 300
# A5 参数截断禁 allow 阈值（ask 要求 4KiB；kitagent projection.py:204-207 的
# bounded(16384) + can_allow 同款语义，阈值按本仓口径收紧）。
APPROVAL_INPUT_MAX_BYTES = 4096

DecisionVerb = Literal["approve", "deny"]
ApprovalStatus = Literal["pending", "submitting", "resolved", "expired", "unknown"]


class ApprovalDecisionError(Exception):
    """A5 decide 的类型化错误基类（携带 approval_id；端点层映射状态码）。"""

    def __init__(self, approval_id: str, message: str | None = None) -> None:
        self.approval_id = approval_id
        super().__init__(message or self.__doc__ or "approval decision refused")


class UnknownApprovalError(ApprovalDecisionError):
    """决策目标从未登记。端点层映射 404。"""


class ApprovalConflictError(ApprovalDecisionError):
    """非 pending 状态拒绝变更（kitagent web/session.py:185 同款）：已收口的卡
    不能被二次决策翻转；expired/unknown 卡没有可重放的首次决策。端点层映射 404
    （与 F6「不区分从未存在与已解决」口径一致）。"""


class ApprovalInputTooLargeError(ApprovalDecisionError):
    """参数截断禁 allow：input 序列化超过 APPROVAL_INPUT_MAX_BYTES，approve 视为
    未审先批。端点层映射 413。deny 不受限。"""

    def __init__(self, approval_id: str, size: int) -> None:
        self.size = size
        super().__init__(
            approval_id,
            f"approval input serializes to {size} bytes "
            f"(>{APPROVAL_INPUT_MAX_BYTES}); the card cannot show the full "
            "parameters, so approve is refused",
        )


@dataclass(slots=True)
class PendingApproval:
    id: str
    request: dict[str, Any]
    scope_type: Literal["thread", "task"]
    scope_id: str
    created_at: str
    status: ApprovalStatus = "pending"
    event: threading.Event = field(default_factory=threading.Event)
    decision: ToolDecision | None = None
    resolved_by: str | None = None
    finished_at: str | None = None


def _new_approval_id() -> str:
    # 时间戳 + 随机后缀（tasks.py _new_id 同风格），避免同时钟粒度撞号
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    return f"approval_{stamp}_{secrets.token_hex(3)}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _input_bytes(request: dict[str, Any]) -> int:
    """request.input 的规范化序列化字节数（can_allow 与 approve 门共用同一把尺）。"""

    try:
        payload = json.dumps(request.get("input"), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        payload = repr(request.get("input"))
    return len(payload.encode("utf-8"))


def input_allowable(request: dict[str, Any]) -> bool:
    """can_allow（kitagent projection.py:207 同款）：参数完整可见才可 approve。"""

    return _input_bytes(request) <= APPROVAL_INPUT_MAX_BYTES


class ApprovalBridge:
    """内存审批登记表：callback_for 绑定 thread/task 上下文，Web 端查询与决策。"""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}
        # A5 已收口登记：decide 幂等重放的依据；随进程消亡，无落盘。
        self._finished: dict[str, PendingApproval] = {}

    def callback_for(
        self,
        *,
        thread_id: str | None = None,
        task_id: str | None = None,
        permissions: Any | None = None,
    ) -> Any:
        """返回绑定上下文的审批回调闭包（执行器 approval_callback 合同）。

        permissions（A6 可选）：SessionPermissions 实例。提供时回调先做会话权限
        静态预判——revoked/read_only → deny、auto → approve 均不登记卡片；返回
        None（ask 模式）才落回既有流程：登记卡片并等待 Web 决策。
        """

        scope_type: Literal["thread", "task"] = "thread" if thread_id is not None else "task"
        scope_id = thread_id if thread_id is not None else (task_id or "")

        def callback(request: dict[str, Any]) -> ToolDecision:
            # A6 会话权限预判（kitagent manager._evaluate_without_prompt 的
            # session 权限层同位）。既有门链在本回调之前已评估完毕：显式 allow
            # 规则（长期信任）、run-scope 授权、只读直通都在门链内放行，到不了
            # 这里——预判只对「门链仍要点头」的调用收紧或放行，不重复问。
            if permissions is not None:
                preset = permissions.static_gate(str(request.get("tool_name") or ""))
                if preset is not None:
                    return preset
            approval = self._register(request, scope_type, scope_id)
            # 等待结果以登记表现状为准（still_open）：决策收口（pop+赋值同临界区）
            # 先于 event.set，超时竞态不会覆写已落定的决策。
            approval.event.wait(timeout=APPROVAL_TIMEOUT_SECONDS)
            with self._guard:
                still_open = self._pending.pop(approval.id, None) is not None
            if still_open:
                # 无人决策 → expired（fail-closed 返回 deny，合同不变）。
                # 决策位保持 None：expired 卡没有形成过 Web 决策（kitagent
                # session.py:216-217 同款），不可作为幂等重放依据。
                approval.status = "expired"
                approval.resolved_by = "timeout"
                approval.finished_at = _now_iso()
                with self._guard:
                    self._finished[approval.id] = approval
                return "deny"
            return approval.decision or "deny"

        return callback

    def _register(self, request: dict[str, Any], scope_type: str, scope_id: str) -> PendingApproval:
        approval = PendingApproval(
            id=_new_approval_id(),
            request=dict(request),
            scope_type=scope_type,  # type: ignore[arg-type]
            scope_id=scope_id,
            created_at=_now_iso(),
        )
        with self._guard:
            self._pending[approval.id] = approval
        return approval

    def _resolve_locked(
        self,
        approval: PendingApproval,
        decision: ToolDecision,
        resolved_by: str,
    ) -> None:
        """收口一张卡（调用方持有 _guard）：pop 与决策赋值同一临界区，回调侧的
        超时路径不会覆写已落定的决策。"""

        approval.status = "resolved"
        approval.decision = decision
        approval.resolved_by = resolved_by
        approval.finished_at = _now_iso()
        self._finished[approval.id] = approval

    def resolve(self, approval_id: str, decision: DecisionVerb) -> PendingApproval | None:
        """决策落定并唤醒回调；未知/已解决的 id 返回 None（不区分两种缺失，避免二次决策歧义）。

        F6 既有原语（回归锚）：仅 pending 可收口。A5 语义（幂等重放、类型化错误）
        走 decide()；端点层自 A5 起使用 decide()。
        """

        with self._guard:
            approval = self._pending.pop(approval_id, None)
            if approval is None:
                return None
            self._resolve_locked(approval, decision, "web")
        approval.event.set()
        return approval

    def decide(self, approval_id: str, decision: DecisionVerb) -> PendingApproval:
        """A5 决策入口（kitagent web/session.py:183-232 decide 幂等与校验的移植）。

        - pending：approve/deny 落定并唤醒回调（approve 受参数截断禁 allow 门）；
        - 已收口 + 同动词：幂等重放首次结果（不报错、不二次执行）；
        - 已收口 + 异动词 / expired / unknown：ApprovalConflictError（非 pending
          状态拒绝变更）；
        - 从未登记：UnknownApprovalError；
        - approve 且 input 超限：ApprovalInputTooLargeError，卡片保持 pending
          （未消耗，仍可 deny）。
        """

        with self._guard:
            approval = self._pending.get(approval_id)
            if approval is None:
                finished = self._finished.get(approval_id)
                if finished is None:
                    raise UnknownApprovalError(approval_id)
                if finished.decision != decision:
                    raise ApprovalConflictError(approval_id)
                return finished
            if decision == "approve" and not input_allowable(approval.request):
                raise ApprovalInputTooLargeError(approval_id, _input_bytes(approval.request))
            # pending → submitting：kitagent session.py:191 同款中间态（决策已
            # 受理、尚未落定；同步桥里该状态只存在于下方两次临界区之间）。
            approval.status = "submitting"
        try:
            with self._guard:
                if self._pending.pop(approval_id, None) is approval:
                    self._resolve_locked(approval, decision, "web")
                    applied = True
                else:
                    # 并发窗口内被 recheck_pending/超时收口：非 pending 拒绝变更。
                    applied = False
        except Exception:
            # kitagent session.py:221-224 同款防御位：决策过程异常且未收口 →
            # unknown，fail-closed 唤醒等待者（decision 为 None → 回调返回 deny）。
            # 注意此时卡已不在 _pending（上方 pop 先于 _resolve_locked），只能以
            # _finished 反查是否已被并发收口，否则卡会悬在两个登记表之外。
            with self._guard:
                if self._finished.get(approval.id) is not approval:
                    self._pending.pop(approval.id, None)
                    approval.status = "unknown"
                    approval.resolved_by = None
                    approval.finished_at = _now_iso()
                    self._finished[approval.id] = approval
                    approval.event.set()
            raise
        approval.event.set()
        if not applied:
            raise ApprovalConflictError(approval_id)
        return approval

    def recheck_pending(self, permissions: Any) -> list[dict[str, Any]]:
        """A6：权限变更后即时重判全部挂起审批（kitagent manager.py:99-108 同款）。

        挂起中不再满足当前模式的卡直接收口并从登记表移除，等待者立即唤醒：
        revoked/read_only → deny；auto → approve（kitagent 双向——PermissionControl
        「将自动允许当前等待及后续的工具调用」）；ask 不动等待中的卡。
        """

        settled: list[PendingApproval] = []
        with self._guard:
            for approval in list(self._pending.values()):
                verdict = permissions.static_gate(str(approval.request.get("tool_name") or ""))
                if verdict is None:
                    continue
                self._pending.pop(approval.id, None)
                self._resolve_locked(approval, verdict, "session_permissions")
                settled.append(approval)
        for approval in settled:
            approval.event.set()
        return [
            {
                "id": approval.id,
                "tool_name": approval.request.get("tool_name"),
                "decision": approval.decision,
                "resolved_by": approval.resolved_by,
            }
            for approval in settled
        ]

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
                "status": approval.status,
                "can_allow": input_allowable(approval.request),
            }
            for approval in pending
        ]

    def record_status(self, approval_id: str) -> ApprovalStatus | None:
        """查询任意登记过 id 的当前状态（含已收口；未登记 → None）。"""

        with self._guard:
            approval = self._pending.get(approval_id)
            if approval is None:
                approval = self._finished.get(approval_id)
        return approval.status if approval is not None else None
