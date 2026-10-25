"""审批五态与会话权限模式测试（spec 2026-10-25_port-approval-semantics，A5/A6）。

A5（Ported from kitagent (MIT, zed123214/kitagent) web/session.py:183-232 decide
幂等与校验 + projection.py:207 can_allow）：五态状态机（pending → submitting →
resolved | expired | unknown）、decide 幂等（重复决策同 id 返回首次结果）、非
pending 拒绝变更、参数截断禁 allow（deny 不受限）。

A6（Ported from kitagent (MIT, zed123214/kitagent) core/permissions/manager.py:66-108
configure_session/_recheck_pending + 389-404 断连清空）：SessionPermissions 乐观锁、
mode 切换清空授权、revoke_tool 单项撤销、权限变更后即时重判挂起审批、会话模式与
审批回调的接线、断连清空的等价语义（单用户无断连面：登记表仅内存、随进程消亡、
无落盘路径——本文件锁之）。

回归锚：F6 既有 9 测试（test_runtime_approvals.py）的 approval_callback 合同
（request → ToolDecision）不变；本文件只新增语义，不改既有合同。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.runtime import approvals as approvals_mod
from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.approvals import (
    APPROVAL_INPUT_MAX_BYTES,
    ApprovalBridge,
    ApprovalConflictError,
    ApprovalInputTooLargeError,
    UnknownApprovalError,
    _input_bytes,
    input_allowable,
)
from nexuscli.runtime.websession import RevisionConflict, SessionPermissions
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()


@pytest.fixture()
def approval_server(tmp_path):
    server = RuntimeApiServer(
        cwd=str(tmp_path), config=load_config(project_root=None), api_key=KEY, port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    port = int(server._server.server_address[1])  # type: ignore[index]
    time.sleep(0.1)
    yield server, port, tmp_path
    server._stop.set()
    thread.join(timeout=5)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(
        method,
        path,
        body=payload,
        headers={"content-type": "application/json", **(headers or {})},
    )
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response.status, data


def _bootstrap_cookie(port: int, token: str) -> tuple[str, str]:
    """换发一次性口令，返回 (cookie 头片段, csrf)。"""

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST",
        "/v1/workbench/bootstrap",
        body=json.dumps({"token": token}).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    response = conn.getresponse()
    set_cookie = response.getheader("set-cookie") or ""
    csrf = str(json.loads(response.read())["csrf"])
    conn.close()
    return set_cookie.split(";")[0], csrf


def _register_card(
    bridge: ApprovalBridge, request: dict[str, object]
) -> tuple[threading.Event, list[str]]:
    """后台线程走 callback（与执行器同形），返回 (完成信号, 决策收集位)。"""

    done = threading.Event()
    decisions: list[str] = []

    def waiter() -> None:
        callback = bridge.callback_for(task_id="task_sem", permissions=None)
        decisions.append(str(callback(request)))
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)
    return done, decisions


# ---------------------------------------------------------------- A5 桥单元语义


def test_five_states_pending_to_resolved_with_idempotent_replay() -> None:
    bridge = ApprovalBridge()
    done, decisions = _register_card(
        bridge,
        {
            "tool_name": "write_file",
            "input": {"path": "a"},
            "danger_level": "high",
            "description": "写",
        },
    )
    card = bridge.list_pending()[0]
    assert card["status"] == "pending"
    assert card["can_allow"] is True

    resolved = bridge.decide(card["id"], "approve")
    assert done.wait(timeout=5)
    assert decisions == ["approve"]
    assert resolved.decision == "approve"
    assert resolved.resolved_by == "web"
    assert resolved.status == "resolved"
    assert bridge.list_pending() == []
    assert bridge.record_status(card["id"]) == "resolved"

    # 幂等：重复决策同 id 同动词 → 首次结果，不报错
    replay = bridge.decide(card["id"], "approve")
    assert replay.id == resolved.id and replay.decision == "approve"

    # 非 pending 拒绝变更：异动词翻转 → 类型化错误
    with pytest.raises(ApprovalConflictError):
        bridge.decide(card["id"], "deny")


def test_submitting_transition_happens_before_resolution() -> None:
    bridge = ApprovalBridge()
    done, _ = _register_card(
        bridge,
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"},
    )
    approval_id = bridge.list_pending()[0]["id"]
    seen: dict[str, str] = {}
    original = bridge._resolve_locked

    def spy(approval, decision, resolved_by):
        seen["status_at_resolve"] = approval.status
        return original(approval, decision, resolved_by)

    bridge._resolve_locked = spy  # type: ignore[method-assign]
    try:
        bridge.decide(approval_id, "deny")
    finally:
        bridge._resolve_locked = original  # type: ignore[method-assign]
    assert seen["status_at_resolve"] == "submitting"
    assert done.wait(timeout=5)
    assert bridge.record_status(approval_id) == "resolved"


def test_timeout_marks_expired_and_refuses_late_decide(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(approvals_mod, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    bridge = ApprovalBridge()
    decision = bridge.callback_for(thread_id="thread_t")(
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"}
    )
    assert decision == "deny"  # 回调合同不变：超时 fail-closed
    approval_id = next(iter(bridge._finished))
    assert bridge.record_status(approval_id) == "expired"
    # expired 卡没有形成过 Web 决策：任何再决策都是「非 pending 拒绝变更」
    with pytest.raises(ApprovalConflictError):
        bridge.decide(approval_id, "deny")
    with pytest.raises(ApprovalConflictError):
        bridge.decide(approval_id, "approve")


def test_unknown_state_on_decision_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """kitagent session.py:221-224 的防御位：决策过程异常且未收口 → unknown。"""

    bridge = ApprovalBridge()
    done, decisions = _register_card(
        bridge,
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"},
    )
    approval_id = bridge.list_pending()[0]["id"]

    def boom(approval, decision, resolved_by):
        raise RuntimeError("simulated decision failure")

    monkeypatch.setattr(bridge, "_resolve_locked", boom)
    with pytest.raises(RuntimeError):
        bridge.decide(approval_id, "approve")
    assert bridge.record_status(approval_id) == "unknown"
    # fail-closed 唤醒等待者（decision 为 None → 回调 deny）
    assert done.wait(timeout=5)
    assert decisions == ["deny"]
    # unknown 卡：不可再决策
    with pytest.raises(ApprovalConflictError):
        bridge.decide(approval_id, "deny")


def test_input_too_large_forbids_approve_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """参数截断禁 allow：审的不是完整参数 → approve 拒绝且卡片不消耗；deny 不受限。"""

    bridge = ApprovalBridge()
    big_request = {
        "tool_name": "write_file",
        "input": {"content": "x" * (APPROVAL_INPUT_MAX_BYTES + 1)},
        "danger_level": "high",
        "description": "大参数写入",
    }
    done, decisions = _register_card(bridge, big_request)
    card = bridge.list_pending()[0]
    assert card["can_allow"] is False

    with pytest.raises(ApprovalInputTooLargeError) as excinfo:
        bridge.decide(card["id"], "approve")
    assert excinfo.value.size > APPROVAL_INPUT_MAX_BYTES
    # 卡片未被消耗：仍 pending、仍可 deny
    assert bridge.record_status(card["id"]) == "pending"
    assert bridge.list_pending() != []

    assert bridge.decide(card["id"], "deny").decision == "deny"
    assert done.wait(timeout=5)
    assert decisions == ["deny"]

    # 阈值边界：恰好 4KiB 可 approve（can_allow 与 decide 门共用同一把尺）
    base = {"input": {"content": ""}}
    pad = APPROVAL_INPUT_MAX_BYTES - _input_bytes(base)
    exact = {"input": {"content": "x" * pad}}
    assert _input_bytes(exact) == APPROVAL_INPUT_MAX_BYTES
    assert input_allowable(exact)
    assert not input_allowable({"input": {"content": "x" * (pad + 1)}})


def test_decide_unknown_id_raises() -> None:
    bridge = ApprovalBridge()
    with pytest.raises(UnknownApprovalError):
        bridge.decide("approval_nope", "deny")


# ---------------------------------------------------------------- A6 会话权限单元


def test_configure_revision_optimistic_lock() -> None:
    perms = SessionPermissions()
    assert perms.snapshot() == {"mode": "ask", "revoked_tools": [], "revision": 0}

    state = perms.configure(expected_revision=0, mode="auto")
    assert state == {"mode": "auto", "revoked_tools": [], "revision": 1}

    # 乐观锁：stale revision → 409 口径的 RevisionConflict
    with pytest.raises(RevisionConflict) as excinfo:
        perms.configure(expected_revision=0, mode="ask")
    assert excinfo.value.current_revision == 1

    with pytest.raises(ValueError):
        perms.configure(expected_revision=1, mode="yolo")  # type: ignore[arg-type]

    # mode 不变 + 单项撤销也推进 revision
    state = perms.configure(expected_revision=1, revoke_tool="bash")
    assert state["mode"] == "auto" and state["revoked_tools"] == ["bash"]
    assert state["revision"] == 2


def test_mode_switch_clears_revocations() -> None:
    perms = SessionPermissions()
    perms.configure(expected_revision=0, revoke_tool="bash")
    perms.configure(expected_revision=1, revoke_tool="write_file")
    assert perms.snapshot()["revoked_tools"] == ["bash", "write_file"]

    # 切 read_only / auto → 清空授权（换模式即全新起点）
    state = perms.configure(expected_revision=2, mode="read_only")
    assert state["revoked_tools"] == []
    perms.configure(expected_revision=3, revoke_tool="bash")
    state = perms.configure(expected_revision=4, mode="auto")
    assert state["revoked_tools"] == []
    # 切 ask 不清空（ask 是询问模式本身，撤销面继续生效）
    perms.configure(expected_revision=5, revoke_tool="bash")
    state = perms.configure(expected_revision=6, mode="ask")
    assert state["revoked_tools"] == ["bash"]


def test_static_gate_matrix() -> None:
    perms = SessionPermissions()
    assert perms.static_gate("bash") is None  # ask：交回既有门链/人审

    perms.configure(expected_revision=0, mode="auto")
    assert perms.static_gate("write_file") == "approve"

    perms.configure(expected_revision=1, mode="read_only")
    assert perms.static_gate("write_file") == "deny"

    # revoked 先于模式：auto 也不能放行被单项撤销的工具
    perms.configure(expected_revision=2, mode="auto", revoke_tool="bash")
    assert perms.static_gate("bash") == "deny"
    assert perms.static_gate("write_file") == "approve"
    # ask + revoked：不问，直接 deny
    perms.configure(expected_revision=3, mode="ask")
    assert perms.static_gate("bash") == "deny"
    assert perms.static_gate("write_file") is None


def test_registry_is_memory_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """断连清空的等价语义（单用户无断连面）：登记表与权限仅存内存、随进程消亡。

    kitagent manager.py:389-404 cancel_session 在断连时清空挂起与会话权限；本仓
    单用户 CLI 没有断连面，等价语义 = 无任何落盘路径（进程退出即消亡）。测试锁：
    全套桥/权限操作前后 HOME 目录逐字节同名集合不变，且新实例从空态起步。
    """

    monkeypatch.chdir(tmp_path)
    home = Path.home()
    before = sorted(str(p.relative_to(home)) for p in home.rglob("*"))

    bridge = ApprovalBridge()
    perms = SessionPermissions()
    done, decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"},
    )
    bridge.decide(bridge.list_pending()[0]["id"], "deny")
    assert done.wait(timeout=5) and decisions == ["deny"]
    perms.configure(expected_revision=0, mode="auto")
    bridge.recheck_pending(perms)

    after = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    assert before == after  # 无落盘：进程退出即随进程消亡

    fresh_bridge = ApprovalBridge()
    assert fresh_bridge.list_pending() == []
    assert fresh_bridge.record_status("whatever") is None
    assert SessionPermissions().snapshot() == {
        "mode": "ask",
        "revoked_tools": [],
        "revision": 0,
    }


# ---------------------------------------------------------------- A6 即时重判


def test_recheck_denies_pending_on_read_only() -> None:
    bridge = ApprovalBridge()
    perms = SessionPermissions()
    done, decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"},
    )
    approval_id = bridge.list_pending()[0]["id"]

    perms.configure(expected_revision=0, mode="read_only")
    rechecked = bridge.recheck_pending(perms)
    assert [item["decision"] for item in rechecked] == ["deny"]
    assert done.wait(timeout=5)
    assert decisions == ["deny"]  # 挂起审批以 deny 收口
    assert bridge.list_pending() == []  # 并从登记表移除
    assert bridge.record_status(approval_id) == "resolved"


def test_recheck_approves_pending_on_auto() -> None:
    """kitagent 双向重判：auto 对等待中的调用放行（PermissionControl.tsx:133-134）。"""

    bridge = ApprovalBridge()
    perms = SessionPermissions()
    done, decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"},
    )
    bridge.list_pending()

    perms.configure(expected_revision=0, mode="auto")
    rechecked = bridge.recheck_pending(perms)
    assert [item["decision"] for item in rechecked] == ["approve"]
    assert done.wait(timeout=5)
    assert decisions == ["approve"]


def test_recheck_noop_in_ask_mode() -> None:
    bridge = ApprovalBridge()
    perms = SessionPermissions()
    done, _decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"},
    )
    perms.configure(expected_revision=0, mode="ask")  # revision 推进但模式不变
    assert bridge.recheck_pending(perms) == []
    assert bridge.list_pending() != []  # ask 不动等待中的卡

    bridge.resolve(bridge.list_pending()[0]["id"], "deny")  # 清理等待线程
    assert done.wait(timeout=5)


def test_recheck_revoked_overrides_auto() -> None:
    bridge = ApprovalBridge()
    perms = SessionPermissions()
    bash_done, bash_decisions = _register_card(
        bridge,
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "跑命令"},
    )
    write_done, write_decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "写"},
    )
    assert len(bridge.list_pending()) == 2

    perms.configure(expected_revision=0, mode="auto", revoke_tool="bash")
    rechecked = {item["tool_name"]: item["decision"] for item in bridge.recheck_pending(perms)}
    assert rechecked == {"bash": "deny", "write_file": "approve"}
    assert bash_done.wait(timeout=5) and bash_decisions == ["deny"]
    assert write_done.wait(timeout=5) and write_decisions == ["approve"]


# ---------------------------------------------------------------- A6 端点语义


def test_permissions_endpoint_patch_flow(approval_server) -> None:
    server, port, _tmp = approval_server

    status, body = _request(port, "GET", "/v1/workbench/permissions", headers={"x-api-key": KEY})
    assert status == 200
    assert json.loads(body) == {"mode": "ask", "revoked_tools": [], "revision": 0}

    # 正常变更：expected_revision 命中 → 200 + 新 revision
    status, body = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "auto", "expected_revision": 0},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert json.loads(body) == {
        "mode": "auto",
        "revoked_tools": [],
        "revision": 1,
        "rechecked": [],
    }

    # 乐观锁：stale expected_revision → 409 + 当前 revision（客户端刷新重试）
    status, body = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "read_only", "expected_revision": 0},
        headers={"x-api-key": KEY},
    )
    assert status == 409
    assert json.loads(body)["current_revision"] == 1

    # 非法 mode / 缺 expected_revision → 400
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "yolo", "expected_revision": 1},
        headers={"x-api-key": KEY},
    )
    assert status == 400
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "ask"},
        headers={"x-api-key": KEY},
    )
    assert status == 400

    # 单项撤销：revoke_tool 进撤销集
    status, body = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"expected_revision": 1, "revoke_tool": "bash"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["revoked_tools"] == ["bash"] and payload["revision"] == 2

    status, body = _request(port, "GET", "/v1/workbench/permissions", headers={"x-api-key": KEY})
    assert json.loads(body)["revoked_tools"] == ["bash"]


def test_permissions_endpoint_auth_and_csrf(approval_server) -> None:
    server, port, _tmp = approval_server
    token = server.web_session.bootstrap_token
    cookie, csrf = _bootstrap_cookie(port, token)
    change = {"mode": "read_only", "expected_revision": 0}

    # 无鉴权 → 401
    status, _ = _request(port, "PATCH", "/v1/workbench/permissions", body=change)
    assert status == 401
    # cookie 通道：PATCH 是变更请求，缺 csrf → 403（与 POST 同刀）
    status, _ = _request(
        port, "PATCH", "/v1/workbench/permissions", body=change, headers={"cookie": cookie}
    )
    assert status == 403
    # 带 csrf → 200
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body=change,
        headers={"cookie": cookie, "x-csrf-token": csrf},
    )
    assert status == 200
    # api_key 通道豁免 csrf（凭证本身即凭据）
    server.session_permissions.configure(expected_revision=1, mode="ask")
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"expected_revision": 2, "revoke_tool": "bash"},
        headers={"x-api-key": KEY},
    )
    assert status == 200


def test_decision_endpoint_replay_conflict_and_too_large(approval_server) -> None:
    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    done, decisions = _register_card(
        bridge,
        {"tool_name": "edit_file", "input": {}, "danger_level": "medium", "description": "编辑"},
    )
    approval_id = bridge.list_pending()[0]["id"]

    # 首次决策 → 200
    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    first = json.loads(body)
    assert first["resolved_by"] == "web" and first["status"] == "resolved"
    assert done.wait(timeout=5) and decisions == ["approve"]

    # 重复决策同 id 同动词 → 200 幂等重放首次结果（不报错）
    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert json.loads(body)["decision"] == "approve"

    # 异动词 → 404（非 pending 拒绝变更，与既有「不区分从未存在与已解决」口径一致）
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY},
    )
    assert status == 404
    # 未知 id → 404
    status, _ = _request(
        port,
        "POST",
        "/v1/approvals/approval_nope/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY},
    )
    assert status == 404

    # 参数截断禁 allow：approve → 413 类型化错误，卡片不消耗；deny → 200
    big_done, big_decisions = _register_card(
        bridge,
        {
            "tool_name": "write_file",
            "input": {"content": "x" * (APPROVAL_INPUT_MAX_BYTES + 1)},
            "danger_level": "high",
            "description": "大参数",
        },
    )
    big_id = bridge.list_pending()[0]["id"]
    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{big_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 413
    payload = json.loads(body)
    assert payload["code"] == "approval_input_too_large"
    assert payload["max_bytes"] == APPROVAL_INPUT_MAX_BYTES
    assert bridge.list_pending() != []  # 卡片未被消耗

    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{big_id}/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert big_done.wait(timeout=5) and big_decisions == ["deny"]


def test_server_wiring_session_permissions_gate(approval_server) -> None:
    """接线锁：callback_for(permissions=session_permissions) 的回调先查会话模式。

    ask 模式照常登记等人（既有合同）；PATCH read_only/auto 后即时重判挂起卡；
    回调预判不登记卡片。执行器显式 allow 的项在门链内放行、到不了回调——
    AGENTS.md「不重复问」语义不受会话面影响。
    """

    server, port, tmp = approval_server
    bridge = server.approval_bridge
    assert server.session_permissions is not None

    # ask 模式（默认）：照常登记
    done, decisions = _register_card(
        bridge,
        {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"},
    )
    approval_id = bridge.list_pending()[0]["id"]

    # PATCH read_only → 即时重判：挂起卡 deny 收口
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "read_only", "expected_revision": 0},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert done.wait(timeout=5)
    assert decisions == ["deny"]
    assert bridge.record_status(approval_id) == "resolved"

    # read_only 下回调预判直接 deny，不再登记卡片
    decision = bridge.callback_for(task_id="task_gate", permissions=server.session_permissions)(
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"}
    )
    assert decision == "deny"
    assert bridge.list_pending() == []

    # PATCH auto → 回调预判直接放行（revoked 工具除外）
    server.session_permissions.configure(expected_revision=1, mode="ask")  # 复位 revision 对齐
    status, _ = _request(
        port,
        "PATCH",
        "/v1/workbench/permissions",
        body={"mode": "auto", "expected_revision": 2},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    decision = bridge.callback_for(task_id="task_gate2", permissions=server.session_permissions)(
        {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"}
    )
    assert decision == "approve"
    assert bridge.list_pending() == []


def test_executor_allow_rule_never_reaches_session_gate(tmp_path: Path) -> None:
    """AGENTS.md allow 语义：显式 allow 是长期信任——会话权限面（ask 模式）不得
    让这类调用重复问；门链内放行的调用到不了审批回调。"""

    import asyncio

    from nexuscli.tools import ToolRegistry, get_builtin_tools
    from nexuscli.tools.base import ToolContext
    from nexuscli.tools.executor import ToolExecutor

    calls: list[dict[str, object]] = []

    def spy_callback(request: dict[str, object]) -> str:
        calls.append(request)
        return "deny"

    config = load_config(project_root=None)
    config.permissions.allow = ["write_file"]
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    context = ToolContext(
        cwd=str(tmp_path),
        config=config,
        approval_callback=spy_callback,
    )
    executor = ToolExecutor(registry)
    call = {
        "id": "call_allow",
        "name": "write_file",
        "arguments": {"path": "a.txt", "content": "x"},
    }
    results = asyncio.run(executor.execute_all([call], context))
    assert not results[0].is_error
    assert (tmp_path / "a.txt").is_file()
    assert calls == []  # 门链内放行：审批回调（含会话预判）未被触达
