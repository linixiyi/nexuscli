"""Web 审批卡片测试（spec 2026-10-25_add-workbench-interaction，F6）。

覆盖 01 契约的全部语义：桥单元（登记/查询/决策/二次决策 404/非法动词 400/超时 deny）、
决策端点 Origin 口径（同 MCP A3）、执行器集成（write_file 挂起→approve 落盘 + 审计
approver=hitl；deny 规则下登记表恒空——门序在回调之前，Web 无绕过面）、Reason 契约
（description 键存在且非空）。conftest 的 HOME 重定向让审计日志落在临时目录。

0021（spec 2026-11-07_fix-nexuscli-release-hardening）新增：扩展动词 approve_run
（本次 run 内放行：卡按 approve 收口 + run-scope 授权表，回调包装层翻译回
approve_run）、approve_auto（会话切 auto + recheck_pending 即时重判）、审批超时可配
（构造参数 / NEXUSCLI_APPROVAL_TIMEOUT_SECONDS / 看护线程统一收口）。
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import nexuscli.runtime.approvals as approvals_module
from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.approvals import (
    APPROVAL_TIMEOUT_SECONDS,
    ApprovalBridge,
    ApprovalSuspension,
)
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor


def _runtime_test_key() -> str:
    # 与 tests/test_runtime.py 的同名 helper 同语义。本文件不 `from tests.…`
    # 导入：任务 verify 用裸 `pytest`（非 `python -m pytest`）跑单文件时，
    # 仓库根不在 sys.path，`tests` 命名空间包不可导入（test_workbench_static
    # 同款先例）。
    return os.environ.get("NEXUSCLI_UNITTEST_RUNTIME_CREDENTIAL") or "unit-test-value"


KEY = _runtime_test_key()

_APPROVAL_REQUEST = {
    "tool_name": "write_file",
    "input": {"path": "a"},
    "danger_level": "high",
    "description": "d",
}


def _wait_for_card(bridge: ApprovalBridge, *, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = bridge.list_pending()
        if pending:
            return str(pending[0]["id"])
        time.sleep(0.02)
    raise AssertionError("approval card not registered in time")


def _register_blocking(bridge: ApprovalBridge, callback, request: dict | None = None):
    """后台线程经回调登记阻塞卡；返回 (done, decisions) 供决策侧断言。"""

    done = threading.Event()
    decisions: list[str] = []

    def waiter() -> None:
        decisions.append(str(callback(dict(request or _APPROVAL_REQUEST))))
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    return done, decisions


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


# ---------------------------------------------------------------- 桥单元语义


def test_bridge_register_list_resolve() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_1")
    done = threading.Event()
    decisions: list[str] = []

    def waiter() -> None:
        decisions.append(
            str(
                callback(
                    {
                        "tool_name": "write_file",
                        "input": {"path": "a"},
                        "danger_level": "high",
                        "description": "写文件",
                    }
                )
            )
        )
        done.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)

    pending = bridge.list_pending()
    assert len(pending) == 1
    item = pending[0]
    assert item["tool_name"] == "write_file"
    assert item["scope_type"] == "task"
    assert item["scope_id"] == "task_1"
    assert item["description"] == "写文件"  # Reason 契约：description 在请求里

    assert bridge.resolve(item["id"], "deny") is not None
    assert done.wait(timeout=5)
    assert decisions == ["deny"]
    assert bridge.list_pending() == []  # 已解决的不再出现
    # 二次决策同 id → None（端点层映射 404）
    assert bridge.resolve(item["id"], "approve") is None


def test_bridge_timeout_fails_closed() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(thread_id="thread_1")
    # 短超时注入： monkeypatch 常量
    import nexuscli.runtime.approvals as approvals_mod

    original = approvals_mod.APPROVAL_TIMEOUT_SECONDS
    approvals_mod.APPROVAL_TIMEOUT_SECONDS = 0.2
    try:
        start = time.monotonic()
        decision = callback(
            {"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"}
        )
        elapsed = time.monotonic() - start
    finally:
        approvals_mod.APPROVAL_TIMEOUT_SECONDS = original
    assert decision == "deny"
    assert elapsed < 5
    assert bridge.list_pending() == []


# ---------------------------------------------------------------- 端点语义


def test_decision_endpoint_semantics(approval_server) -> None:
    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    done = threading.Event()

    def waiter() -> None:
        callback = bridge.callback_for(task_id="task_x")
        callback(
            {"tool_name": "edit_file", "input": {}, "danger_level": "medium", "description": "编辑"}
        )
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)
    approval_id = bridge.list_pending()[0]["id"]

    # 非法动词 → 400
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "allow"},
        headers={"x-api-key": KEY},
    )
    assert status == 400
    # 非法动词不消耗登记项
    assert bridge.list_pending() != []

    # 无 Origin（curl 形态）→ 放行并决策
    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["resolved_by"] == "web"
    assert done.wait(timeout=5)

    # 二次决策同 id → 404（不区分从未存在与已解决）
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


def test_decision_endpoint_origin_and_auth(approval_server) -> None:
    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    done = threading.Event()

    def waiter() -> None:
        bridge.callback_for(task_id="task_o")(
            {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"}
        )
        done.set()

    threading.Thread(target=waiter, daemon=True).start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)
    approval_id = bridge.list_pending()[0]["id"]

    # 非本机 Origin → 403，登记表不变（A3 口径：带 Origin 且非 loopback 拒绝）
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY, "Origin": "https://evil.example"},
    )
    assert status == 403
    assert bridge.list_pending() != []

    # loopback Origin → 放行
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY, "Origin": "http://127.0.0.1:5173"},
    )
    assert status == 200
    assert done.wait(timeout=5)

    # 无鉴权 → 401
    status, _ = _request(port, "GET", "/v1/approvals")
    assert status == 401


# ---------------------------------------------------------------- 执行器集成


def _approval_executor(tmp_path: Path, callback) -> tuple[ToolExecutor, ToolContext]:
    from nexuscli.tools import ToolRegistry, get_builtin_tools

    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    context = ToolContext(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        approval_callback=callback,
    )
    return ToolExecutor(registry), context


def test_executor_integration_approve_and_audit(approval_server) -> None:
    server, port, tmp = approval_server
    bridge = server.approval_bridge
    executor, executor_context = _approval_executor(tmp, bridge.callback_for(task_id="task_exec"))
    target = tmp / "approved.txt"

    def run_tool() -> None:
        import asyncio

        call = {
            "id": "call_1",
            "name": "write_file",
            "arguments": {"path": "approved.txt", "content": "hi"},
        }
        asyncio.run(executor.execute_all([call], executor_context))

    thread = threading.Thread(target=run_tool, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)
    approval_id = bridge.list_pending()[0]["id"]
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    thread.join(timeout=10)

    assert target.is_file()  # 批准后真实落盘
    audit_path = Path.home() / ".nexuscli" / "audit.jsonl"
    audit_lines = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_audit = [row for row in audit_lines if row.get("tool_name") == "write_file"]
    assert write_audit, audit_lines
    assert write_audit[-1]["approver"] == "hitl"  # Web 决策沿执行器落盘为 hitl
    assert write_audit[-1]["outcome"] == "allow"


def test_executor_integration_deny(approval_server) -> None:
    server, port, tmp = approval_server
    bridge = server.approval_bridge
    executor, executor_context = _approval_executor(tmp, bridge.callback_for(task_id="task_deny"))
    target = tmp / "denied.txt"

    def run_tool() -> None:
        import asyncio

        call = {
            "id": "call_2",
            "name": "write_file",
            "arguments": {"path": "denied.txt", "content": "no"},
        }
        asyncio.run(executor.execute_all([call], executor_context))

    thread = threading.Thread(target=run_tool, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not bridge.list_pending() and time.monotonic() < deadline:
        time.sleep(0.02)
    approval_id = bridge.list_pending()[0]["id"]
    _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY},
    )
    thread.join(timeout=10)

    assert not target.exists()  # 拒绝后不落盘


def test_deny_rule_never_reaches_bridge(approval_server) -> None:
    """deny 权限规则在回调之前生效：Web 登记表恒空——锁住「无绕过面」。"""

    from nexuscli.tools import ToolRegistry, get_builtin_tools

    server, port, tmp = approval_server
    config = load_config(project_root=None)
    config.permissions.deny = ["write_file"]
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    context = ToolContext(
        cwd=str(tmp),
        config=config,
        approval_callback=server.approval_bridge.callback_for(task_id="task_deny"),
    )
    executor = ToolExecutor(registry)

    import asyncio

    call = {
        "id": "call_3",
        "name": "write_file",
        "arguments": {"path": "blocked.txt", "content": "x"},
    }
    result = asyncio.run(executor.execute_all([call], context))[0]
    assert result.is_error
    assert server.approval_bridge.list_pending() == []


def test_reason_contract_on_both_call_sites() -> None:
    """executor.py:378-385 与 builtins.py:668-675 两个调用点的请求都带非空 description。"""

    from nexuscli.tools import ToolRegistry, get_builtin_tools

    seen: list[dict[str, object]] = []

    def spy_callback(request: dict[str, object]) -> str:
        seen.append(request)
        return "deny"

    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    context = ToolContext(
        cwd=".",
        config=load_config(project_root=None),
        approval_callback=spy_callback,
    )
    executor = ToolExecutor(registry)
    import asyncio

    call = {"id": "call_4", "name": "write_file", "arguments": {"path": "x.txt", "content": "y"}}
    asyncio.run(executor.execute_all([call], context))
    assert seen, "write_file 应触发审批回调"
    for request in seen:
        assert isinstance(request.get("description"), str)
        assert str(request["description"]) != ""


def test_timeout_constant_is_module_level() -> None:
    assert APPROVAL_TIMEOUT_SECONDS == 300


# ---------------------------------------------------------------- 0021 扩展动词


def test_approve_run_endpoint_grants_run_scope(approval_server) -> None:
    """approve_run：卡按 approve 收口 + 工具入 run-scope 授权表，等待方经回调
    包装层收到 approve_run（REPL "r" 的 Web 同位）。"""

    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    wired = server._approval_callback_for(
        task_id="task_run", permissions=server.session_permissions
    )
    done, decisions = _register_blocking(bridge, wired)
    approval_id = _wait_for_card(bridge)

    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_run"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["decision"] == "approve_run"
    assert payload["resolved_by"] == "web"
    assert payload["status"] == "resolved"
    assert done.wait(timeout=5)
    assert decisions == ["approve_run"]
    # 授权表登记；会话模式不被 approve_run 改动（那是 approve_auto 的语义）
    assert server._run_scope_allows("task_run", "write_file") is True
    assert server.session_permissions.snapshot()["mode"] == "ask"
    # 收口台账记应用结果（SSE approval_resolved 帧的数据源）
    entry = server._approval_resolution_for(approval_id)
    assert entry is not None
    assert entry["decision"] == "approve_run"
    assert entry["status"] == "resolved"


def test_approve_run_covers_rest_of_run_via_executor(approval_server) -> None:
    """同一 ToolContext（同一 run）内的后续同工具调用走执行器 run_scope_bypass
    免提示，不再登记卡；审计归属第一行 hitl、后续行 run-scope。"""

    server, port, tmp = approval_server
    bridge = server.approval_bridge
    wired = server._approval_callback_for(
        task_id="task_scope", permissions=server.session_permissions
    )
    executor, context = _approval_executor(tmp, wired)
    results_box: list[list] = []

    def run_tools() -> None:
        import asyncio

        calls = [
            {
                "id": "call_r1",
                "name": "write_file",
                "arguments": {"path": "run_a.txt", "content": "A"},
            },
            {
                "id": "call_r2",
                "name": "write_file",
                "arguments": {"path": "run_b.txt", "content": "B"},
            },
        ]
        results_box.append(asyncio.run(executor.execute_all(calls, context)))

    thread = threading.Thread(target=run_tools, daemon=True)
    thread.start()
    approval_id = _wait_for_card(bridge)
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_run"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    thread.join(timeout=10)

    assert (tmp / "run_a.txt").read_text(encoding="utf-8") == "A"
    assert (tmp / "run_b.txt").read_text(encoding="utf-8") == "B"
    assert bridge.list_pending() == []  # 第二次调用没有再登记卡
    audit_path = Path.home() / ".nexuscli" / "audit.jsonl"
    rows = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    write_rows = [
        row
        for row in rows
        if row.get("tool_name") == "write_file" and row.get("outcome") == "allow"
    ]
    assert [row["approver"] for row in write_rows[-2:]] == ["hitl", "run-scope"]


def test_approve_run_input_too_large_rolls_back_grant(approval_server) -> None:
    """参数截断禁 allow 对 approve_run 同样生效：413、卡不消耗、授权回滚。"""

    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    done, _decisions = _register_blocking(
        bridge,
        bridge.callback_for(task_id="task_big"),
        {
            "tool_name": "write_file",
            "input": {"path": "big", "content": "x" * 8192},
            "danger_level": "high",
            "description": "d",
        },
    )
    approval_id = _wait_for_card(bridge)

    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_run"},
        headers={"x-api-key": KEY},
    )
    assert status == 413
    assert json.loads(body)["code"] == "approval_input_too_large"
    assert bridge.list_pending() != []  # 卡未消耗，仍可 deny
    assert server._run_scope_allows("task_big", "write_file") is False
    bridge.decide(approval_id, "deny")
    assert done.wait(timeout=5)


def test_approve_auto_switches_session_and_settles_waiting_card(approval_server) -> None:
    """approve_auto：会话切 auto（full access）+ recheck 即时重判——等待中的卡被
    放行（resolved_by=session_permissions），后续调用不再登记卡。"""

    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    wired = server._approval_callback_for(
        task_id="task_auto", permissions=server.session_permissions
    )
    done, decisions = _register_blocking(bridge, wired)
    approval_id = _wait_for_card(bridge)

    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_auto"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["decision"] == "approve_auto"
    assert payload["resolved_by"] == "session_permissions"
    assert payload["status"] == "resolved"
    assert done.wait(timeout=5)
    # 等待方拿到 recheck 的放行结果；无 run-scope 授权 → 不翻译成 approve_run
    assert decisions == ["approve"]
    assert server.session_permissions.snapshot()["mode"] == "auto"
    assert bridge.list_pending() == []
    # auto 模式下后续调用静态放行，不登记卡、也不经 Web 决策
    followup = wired({"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"})
    assert followup == "approve"
    assert bridge.list_pending() == []


def test_approve_auto_clears_revocations_and_approves_waiting_card(approval_server) -> None:
    """approve_auto 的换模式语义（A6 configure）：切到 auto 即清空单项撤销
    （「换模式即全新起点」）——先撤销 write_file，approve_auto 后撤销集清空，
    等待中的 write_file 卡被放行。"""

    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    wired = server._approval_callback_for(
        task_id="task_revoked", permissions=server.session_permissions
    )
    # 卡在 ask 模式下登记（static_gate 放行到人审）；随后挂起期内单项撤销，
    # approve_auto 换模式清空撤销集并重判放行。
    done, decisions = _register_blocking(bridge, wired)
    approval_id = _wait_for_card(bridge)
    snapshot = server.session_permissions.snapshot()
    server.session_permissions.configure(
        expected_revision=int(snapshot["revision"]), revoke_tool="write_file"
    )

    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_auto"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert done.wait(timeout=5)
    assert decisions == ["approve"]
    assert server.session_permissions.snapshot()["mode"] == "auto"
    assert server.session_permissions.snapshot()["revoked_tools"] == []


def test_extended_decision_verbs_error_mapping(approval_server) -> None:
    """扩展动词的权限与错误映射：401 无鉴权、403 跨源、400 非法动词、404 未知/
    已收口（且 approve_auto 对陈旧卡不产生会话模式副作用）。"""

    server, port, _tmp = approval_server
    bridge = server.approval_bridge
    # 无鉴权 → 401
    status, _ = _request(
        port,
        "POST",
        "/v1/approvals/approval_nope/decision",
        body={"decision": "approve_run"},
    )
    assert status == 401
    # 非 loopback Origin → 403（先于决策面）
    status, _ = _request(
        port,
        "POST",
        "/v1/approvals/approval_nope/decision",
        body={"decision": "approve_run"},
        headers={"x-api-key": KEY, "Origin": "https://evil.example"},
    )
    assert status == 403
    # 非法动词 → 400
    status, _ = _request(
        port,
        "POST",
        "/v1/approvals/approval_nope/decision",
        body={"decision": "approve_run_x"},
        headers={"x-api-key": KEY},
    )
    assert status == 400
    # 未知 id：approve_run / approve_auto 都 404
    for verb in ("approve_run", "approve_auto"):
        status, _ = _request(
            port,
            "POST",
            "/v1/approvals/approval_nope/decision",
            body={"decision": verb},
            headers={"x-api-key": KEY},
        )
        assert status == 404
    # 已收口的卡再 approve_auto → 404，且会话模式不被改动
    done, _decisions = _register_blocking(bridge, bridge.callback_for(task_id="task_stale"))
    approval_id = _wait_for_card(bridge)
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    assert done.wait(timeout=5)
    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve_auto"},
        headers={"x-api-key": KEY},
    )
    assert status == 404
    assert server.session_permissions.snapshot()["mode"] == "ask"


class _ToolCallThenDoneClient:
    """有状态假 LLM（与 tests/test_approval_async.py 的同名类同语义，本文件内联：
    0021 verify 用裸 pytest 跑单文件，`tests` 命名空间包不可导入）。首轮发起工具
    调用；看到 tool 结果消息即收口（恢复段共用）。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100000

    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        self.calls = calls

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if messages and getattr(messages[-1], "role", "") == "tool":
            yield {"type": "text_delta", "text": "resumed and finished"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        for index, (call_id, name, arguments) in enumerate(self.calls):
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": index,
                    "id": call_id,
                    "function": {"name": name, "arguments": arguments},
                },
            }
        yield {"type": "message_end", "stop_reason": "tool_use"}


def test_approve_run_resumes_suspended_react_task(approval_server, monkeypatch) -> None:
    """全栈：挂起 react 任务收到 approve_run → 桥层 approve 收口让 worker 恢复，
    执行器经翻译记 run scope，任务真实完成且文件落盘。"""

    server, port, tmp = approval_server
    server.config.llm.api_key = "test-key"  # 假链路不校验，仅为过 _ensure_llm_key
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "resumed.txt", "content": "hi"}))]
    )
    monkeypatch.setattr("nexuscli.runtime.api.create_llm_client", lambda *args, **kwargs: client)
    status, body = _request(
        port, "POST", "/v1/tasks", body={"message": "write it"}, headers={"x-api-key": KEY}
    )
    assert status == 200
    task_id = str(json.loads(body)["id"])

    deadline = time.monotonic() + 15
    card = None
    while time.monotonic() < deadline:
        card = next(
            (
                item
                for item in server.approval_bridge.list_pending()
                if item["scope_type"] == "task" and item["scope_id"] == task_id
            ),
            None,
        )
        if card is not None:
            break
        time.sleep(0.05)
    assert card is not None

    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{card['id']}/decision",
        body={"decision": "approve_run"},
        headers={"x-api-key": KEY},
    )
    assert status == 200

    deadline = time.monotonic() + 20
    record = None
    while time.monotonic() < deadline:
        record = server.task_manager.get(task_id)
        if record is not None and record.status == "completed":
            break
        time.sleep(0.05)
    assert record is not None and record.status == "completed"
    assert (tmp / "resumed.txt").read_text(encoding="utf-8") == "hi"


# ---------------------------------------------------------------- 0021 可配超时


def test_run_scope_grants_cleared_per_run(approval_server) -> None:
    """run-scope 授权按 scope 存取，run 开始即清空（不外溢到下一个 run）。"""

    server, _port, _tmp = approval_server
    server._grant_run_scope("thread_x", "write_file")
    assert server._run_scope_allows("thread_x", "write_file")
    assert server._run_scope_allows("thread_y", "write_file") is False
    server._clear_run_scope("thread_x")
    assert server._run_scope_allows("thread_x", "write_file") is False


def test_approval_timeout_configurable_suspend_path(tmp_path) -> None:
    """挂起路径：_await_suspension_settlement 按可配超时收口——超时仍 deny。"""

    server = RuntimeApiServer(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        approval_timeout_seconds=0.3,
    )
    callback = server._approval_callback_for(
        task_id="task_t", permissions=server.session_permissions, suspend=True
    )
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback(dict(_APPROVAL_REQUEST))
    suspension = exc_info.value
    start = time.monotonic()
    decision, approval = server._await_suspension_settlement(
        SimpleNamespace(id="task_t"), suspension
    )
    elapsed = time.monotonic() - start
    assert decision == "deny"
    assert elapsed < 5
    assert approval.status == "expired"
    assert approval.resolved_by == "timeout"
    entry = server._approval_resolution_for(approval.id)
    assert entry is not None
    assert entry["decision"] is None
    assert entry["resolved_by"] == "timeout"
    server._stop.set()


def test_approval_timeout_sweeper_expires_sync_bridge(tmp_path) -> None:
    """同步桥路径（F6 回调在 approvals.py 闭包内等待）：看护线程按可配超时
    expire——等待方拿到 deny，台账记 timeout（SSE 侧 approval_resolved 可见）。"""

    server = RuntimeApiServer(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        approval_timeout_seconds=0.3,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        bridge = server.approval_bridge
        done, decisions = _register_blocking(bridge, bridge.callback_for(task_id="task_sweep"))
        approval_id = _wait_for_card(bridge)
        assert done.wait(timeout=5)
        assert decisions == ["deny"]
        assert bridge.record_status(approval_id) == "expired"
        assert bridge.list_pending() == []
        entry = server._approval_resolution_for(approval_id)
        assert entry is not None
        assert entry["resolved_by"] == "timeout"
        assert entry["status"] == "expired"
    finally:
        server._stop.set()
        thread.join(timeout=5)


def test_approval_timeout_resolution_order(tmp_path, monkeypatch) -> None:
    """超时取值口径：构造参数 > env（NEXUSCLI_APPROVAL_TIMEOUT_SECONDS）>
    动态模块常量（既有 monkeypatch 注入缝保持可用）；非法值快速失败。"""

    base = dict(cwd=str(tmp_path), config=load_config(project_root=None), api_key=KEY, port=0)
    default_server = RuntimeApiServer(**base)
    assert default_server.approval_timeout_seconds is None
    assert default_server._approval_timeout() == 300
    monkeypatch.setattr(approvals_module, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    assert default_server._approval_timeout() == 0.2

    monkeypatch.setenv("NEXUSCLI_APPROVAL_TIMEOUT_SECONDS", "1.5")
    env_server = RuntimeApiServer(**base)
    assert env_server.approval_timeout_seconds == 1.5
    assert env_server._approval_timeout() == 1.5

    explicit_server = RuntimeApiServer(**base, approval_timeout_seconds=0.3)
    assert explicit_server._approval_timeout() == 0.3

    monkeypatch.setenv("NEXUSCLI_APPROVAL_TIMEOUT_SECONDS", "abc")
    with pytest.raises(ValueError):
        RuntimeApiServer(**base)
    monkeypatch.setenv("NEXUSCLI_APPROVAL_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError):
        RuntimeApiServer(**base)
    with pytest.raises(ValueError):
        RuntimeApiServer(**base, approval_timeout_seconds=-1)
