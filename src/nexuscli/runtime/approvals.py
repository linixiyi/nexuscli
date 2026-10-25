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

A9 审批异步化（Ported from kitagent (MIT, zed123214/kitagent)
core/graph/interrupts.py:20-113 的 durable permission interrupt +
core/permissions/manager.py evaluate_durable 的「既有决策先于 interrupt」语义）：

- 挂起模式回调（callback_for(suspend=True)，仅由 Runtime 任务路径 _run_task 传入）：
  审批请求到来 → 登记（同一登记表，状态 pending）→ 立即以 ApprovalSuspension
  异常中断本次执行（不等待）。挂起是 checkpoint 语义：中断点捕获方（_run_task）
  落回合 checkpoint 并保持任务 running，等待决策的线程是 worker 自己。
- ApprovalSuspension 继承 BaseException 而非 Exception：executor._execute_single
  的 ``except Exception`` 会把普通异常折叠为错误 tool_result（tools/executor.py:397），
  挂起必须穿透它直达 _run_task 的捕获点，不能被当成工具失败。
- 持久决策查找（evaluate_durable 同位）：同 (scope, tool_name, 规范化 input) 的已
  收口决策直接返回，不再登记新卡——这是恢复重放的钥匙：决策端点 approve 后，
  checkpoint 里的 pending 调用按原 id 重派时会再次走到回调，持「已 approve 的
  同参决策」放行，恢复才不会在原审批上二次挂起（kitagent check_durable_permission
  先 evaluate_durable 后 interrupt 的次序同款）。
- expire/cancel_pending：挂起模式下没有回调线程内联超时，等待方（worker）负责
  300s 超时收口（expire，F6 口径：expired 卡 decision 保持 None）与任务取消时
  撤销未决卡（cancel_pending，不进已收口表——再决策按未知 id 404）。
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from nexuscli.tools.base import ToolDecision
from nexuscli.tools.journal import input_hash

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


class ApprovalSuspension(BaseException):
    """A9 挂起信号：审批已登记（pending），调用方必须立即中断本次执行。

    携带整张 PendingApproval（含 event 与决策位）：等待方（Runtime worker）凭它
    等待 decide/recheck/expire 的收口事件，无需再查登记表内部状态。继承
    BaseException 的原因见模块 docstring——工具执行器的 ``except Exception``
    不得吞掉挂起。
    """

    def __init__(self, approval: PendingApproval) -> None:
        self.approval = approval
        super().__init__(
            f"approval {approval.id} registered for {approval.scope_type} "
            f"{approval.scope_id}; run suspended pending a web decision"
        )

    @property
    def approval_id(self) -> str:
        return self.approval.id

    def as_event(self) -> dict[str, Any]:
        """RunSuspension(reason="approval_pending") 的等价事件信号（A9 事件形状）。

        新增 type=approval_suspended 而非复用 error 事件 + error_code：挂起不是
        错误，error 事件在折叠层映射 RunOutcome(failed)，复用会把挂起伪装成失败；
        独立 type 让 fold_engine_result 可以精确映射回 RunSuspension，且既有消费方
        （按 type 分派、未知 type 忽略）向后兼容。
        """

        return {
            "type": "approval_suspended",
            "reason": "approval_pending",
            "approval_id": self.approval.id,
            "scope_type": self.approval.scope_type,
            "scope_id": self.approval.scope_id,
            "tool_name": self.approval.request.get("tool_name"),
        }


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
        # A9 挂起中的卡（approval_id → 挂起环境）：checkpoint 关联、任务 id、挂起
        # 时刻的会话权限 revision。进程内存态，与登记表同生命周期；决策端点据此
        # 标注「该审批属于挂起任务」，worker 收口后移除。
        self._suspensions: dict[str, dict[str, Any]] = {}

    def callback_for(
        self,
        *,
        thread_id: str | None = None,
        task_id: str | None = None,
        permissions: Any | None = None,
        suspend: bool = False,
    ) -> Any:
        """返回绑定上下文的审批回调闭包（执行器 approval_callback 合同）。

        permissions（A6 可选）：SessionPermissions 实例。提供时回调先做会话权限
        静态预判——revoked/read_only → deny、auto → approve 均不登记卡片；返回
        None（ask 模式）才落回既有流程：登记卡片并等待 Web 决策。

        suspend（A9，默认 False）：False 为 F6 同步阻塞桥（REPL 线程路径与
        plan/team 任务路径，行为零变化——登记后等 event，300s 超时 deny）；
        True 为挂起模式（仅 Runtime react 任务路径传入）：登记后立即抛
        ApprovalSuspension 中断执行，等待与恢复由调用方的捕获点负责。
        """

        scope_type: Literal["thread", "task"] = "thread" if thread_id is not None else "task"
        scope_id = thread_id if thread_id is not None else (task_id or "")

        if suspend:
            return self._suspend_callback(scope_type, scope_id, permissions)

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

    def _suspend_callback(
        self,
        scope_type: Literal["thread", "task"],
        scope_id: str,
        permissions: Any | None,
    ) -> Any:
        """A9 挂起模式回调：登记 → 立即抛 ApprovalSuspension（不等待）。

        门序与 F6 回调一致（会话权限预判 → 持久决策查找 → 登记）：既有门链
        （显式 allow、run-scope、只读直通）在执行器内先行放行，到不了这里。
        """

        def suspend_callback(request: dict[str, Any]) -> ToolDecision:
            if permissions is not None:
                preset = permissions.static_gate(str(request.get("tool_name") or ""))
                if preset is not None:
                    return preset
            durable = self._durable_decision(scope_type, scope_id, request)
            if durable is not None:
                return durable
            approval = self._register(request, scope_type, scope_id)
            raise ApprovalSuspension(approval)

        return suspend_callback

    def _durable_decision(
        self,
        scope_type: str,
        scope_id: str,
        request: dict[str, Any],
    ) -> ToolDecision | None:
        """持久决策查找（kitagent manager.evaluate_durable 同位）：同 scope、同
        工具、同规范化输入的已收口决策直接复用，不再登记新卡。

        这让 A9 恢复重放不二次挂起：决策端点 approve 之后，checkpoint 的 pending
        调用按原 id 重派会再次走到回调，此处以「已 approve 的同参决策」放行。
        deny 同样复用（重放已拒绝的同参调用 fail-closed 地再拒绝，避免恢复在
        原卡上循环挂起）。input 身份用 journal.input_hash（规范化 JSON + sha256），
        参数漂移即 miss，照常登记新卡。
        """

        tool_name = str(request.get("tool_name") or "")
        digest = input_hash(request.get("input"))
        with self._guard:
            for approval in self._finished.values():
                if approval.scope_type != scope_type or approval.scope_id != scope_id:
                    continue
                if str(approval.request.get("tool_name") or "") != tool_name:
                    continue
                if approval.status != "resolved" or approval.decision is None:
                    continue
                if input_hash(approval.request.get("input")) == digest:
                    return approval.decision
        return None

    def expire(self, approval_id: str) -> bool:
        """A9：挂起等待方（worker）的 300s 超时收口——F6 回调内联超时的等价物。

        pending → expired（decision 保持 None：expired 卡没有形成过 Web 决策，
        kitagent session.py:216-217 同款，不可作为幂等重放/持久决策依据）。
        非 pending 返回 False。已由 decide/recheck 先收口的卡不动。
        """

        with self._guard:
            approval = self._pending.pop(approval_id, None)
            if approval is None:
                return False
            approval.status = "expired"
            approval.resolved_by = "timeout"
            approval.finished_at = _now_iso()
            self._finished[approval.id] = approval
        approval.event.set()
        return True

    def cancel_pending(self, approval_id: str, *, reason: str = "task_canceled") -> bool:
        """A9：任务取消时撤销未决卡——从登记表移除且不进已收口表。

        再决策该 id → UnknownApprovalError（端点层 404），与「不区分从未存在与
        已解决」的既有口径一致；对象仅在内存里留 status/resolved_by 供诊断。
        """

        with self._guard:
            approval = self._pending.pop(approval_id, None)
            if approval is None:
                return False
            approval.status = "expired"
            approval.resolved_by = reason
            approval.finished_at = _now_iso()
        approval.event.set()
        return True

    def attach_suspension(
        self,
        approval_id: str,
        *,
        task_id: str,
        checkpoint_id: str | None,
        permissions_revision: int | None = None,
    ) -> None:
        """A9：捕获点（_run_task）把挂起卡与其回合 checkpoint 关联登记。"""

        with self._guard:
            self._suspensions[approval_id] = {
                "approval_id": approval_id,
                "task_id": task_id,
                "checkpoint_id": checkpoint_id,
                "permissions_revision": permissions_revision,
                "suspended_at": _now_iso(),
            }

    def drop_suspension(self, approval_id: str) -> None:
        with self._guard:
            self._suspensions.pop(approval_id, None)

    def suspension_for(self, approval_id: str) -> dict[str, Any] | None:
        with self._guard:
            record = self._suspensions.get(approval_id)
        return dict(record) if record is not None else None

    def list_suspensions(self) -> list[dict[str, Any]]:
        with self._guard:
            return [dict(record) for record in self._suspensions.values()]

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
