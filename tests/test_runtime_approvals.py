"""Web 审批卡片测试（spec 2026-10-25_add-workbench-interaction，F6）。

覆盖 01 契约的全部语义：桥单元（登记/查询/决策/二次决策 404/非法动词 400/超时 deny）、
决策端点 Origin 口径（同 MCP A3）、执行器集成（write_file 挂起→approve 落盘 + 审计
approver=hitl；deny 规则下登记表恒空——门序在回调之前，Web 无绕过面）、Reason 契约
（description 键存在且非空）。conftest 的 HOME 重定向让审计日志落在临时目录。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.approvals import APPROVAL_TIMEOUT_SECONDS, ApprovalBridge
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
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
