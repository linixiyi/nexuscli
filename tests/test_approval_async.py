"""A9 审批异步化测试（spec 2026-10-25_port-approval-async，task_0001/task_0002）。

覆盖两段任务：
- 挂起分支：react 任务遇审批 → 登记卡片后立即挂起（不等待）、回合 checkpoint (a)
  落盘（pending 调用带原 id）、任务保持 running（worker 租约内）、任务事件台账可见
  approval_suspended；
- 决策恢复与超时：approve → checkpoint 按原 id 重派（journal 分流）→ 任务 completed
  且文件落盘；deny → 任务 completed、文件不存在、结果含拒绝说明；300s 无决策 →
  expired → deny 收口（F6 口径）；
- 桥单元：挂起回调立即抛 ApprovalSuspension（BaseException，穿透执行器的
  except Exception）、持久决策查找（同参重放不二次挂起）、expire/cancel_pending；
- fold 合同：approval_suspended 事件 → RunSuspension(reason="approval_pending")。

同步回调路径（REPL/线程、F6 阻塞桥）的行为零变化由既有 tests/test_runtime_approvals.py
零修改回归锁住（本文件不重复其用例）。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest

from nexuscli.agent.results import RunSuspension, fold_engine_result
from nexuscli.agent.turn_checkpoint import TurnCheckpointStore, _checkpoint_id_for_run
from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.approvals import (
    ApprovalBridge,
    ApprovalConflictError,
    ApprovalSuspension,
    UnknownApprovalError,
)
from nexuscli.tools.journal import get_tool_journal, input_hash
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()


# ---------------------------------------------------------------- 假 LLM 与基建


class _ToolCallThenDoneClient:
    """有状态假 LLM：首轮发起工具调用；看到 tool 结果消息即收口（恢复段共用）。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 100000

    def __init__(self, calls: list[tuple[str, str, str]]) -> None:
        # calls: [(tool_use_id, tool_name, arguments_json)]
        self.calls = calls
        self.chat_calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.chat_calls += 1
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


def _wait_until(predicate, timeout: float, message: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out after {timeout}s: {message}")


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
) -> tuple[int, bytes]:
    import http.client

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


@pytest.fixture()
def async_server(tmp_path, monkeypatch):
    """全栈 Runtime 服务器（HTTP + worker 线程），LLM 由各测试注入假客户端。"""

    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    server = RuntimeApiServer(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        workers=2,
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


def _submit_task(port: int, message: str) -> str:
    status, body = _request(
        port, "POST", "/v1/tasks", body={"message": message}, headers={"x-api-key": KEY}
    )
    assert status == 200, body
    return str(json.loads(body)["id"])


def _task_record(server: RuntimeApiServer, task_id: str):
    record = server.task_manager.get(task_id)
    assert record is not None
    return record


def _card_for(server: RuntimeApiServer, task_id: str) -> dict[str, Any] | None:
    for item in server.approval_bridge.list_pending():
        if item["scope_type"] == "task" and item["scope_id"] == task_id:
            return item
    return None


def _set_fake_llm(monkeypatch, client: _ToolCallThenDoneClient) -> None:
    monkeypatch.setattr("nexuscli.runtime.api.create_llm_client", lambda *args, **kwargs: client)


# ---------------------------------------------------------------- 桥单元：挂起模式


def test_suspend_callback_registers_then_raises_immediately() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_s", suspend=True)
    request = {
        "tool_name": "write_file",
        "input": {"path": "a", "content": "x"},
        "danger_level": "high",
        "description": "写文件",
    }
    start = time.monotonic()
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback(request)
    assert time.monotonic() - start < 1  # 立即中断，不等待
    # BaseException 合同：穿透执行器的 except Exception，不被当成工具失败
    assert not isinstance(exc_info.value, Exception)
    assert exc_info.value.approval_id.startswith("approval_")
    # 同一登记表：卡片 pending、Reason 契约不变
    pending = bridge.list_pending()
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["description"] == "写文件"
    # RunSuspension(approval_pending) 等价事件形状
    event = exc_info.value.as_event()
    assert event["type"] == "approval_suspended"
    assert event["reason"] == "approval_pending"
    assert event["scope_type"] == "task"
    assert event["scope_id"] == "task_s"
    assert event["tool_name"] == "write_file"
    # 决策端点语义对挂起卡照常可用（A5 决策面零变化）
    bridge.decide(pending[0]["id"], "deny")
    assert bridge.record_status(pending[0]["id"]) == "resolved"


def test_default_callback_has_no_suspend_behavior() -> None:
    """不带 suspend 参数的 callback_for 保持 F6 阻塞桥语义（REPL 零变化锚）。"""

    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_f6")
    decided = threading.Event()
    decisions: list[str] = []

    def waiter() -> None:
        decisions.append(
            str(
                callback(
                    {
                        "tool_name": "write_file",
                        "input": {"path": "a"},
                        "danger_level": "high",
                        "description": "d",
                    }
                )
            )
        )
        decided.set()

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    card = _wait_until(lambda: (bridge.list_pending() or [None])[0], 5, "blocking card")
    assert card is not None
    bridge.resolve(str(card["id"]), "approve")
    assert decided.wait(timeout=5)
    assert decisions == ["approve"]  # 阻塞等待后返回决策，而不是抛挂起


def test_durable_decision_allows_same_input_replay_only() -> None:
    """持久决策查找（evaluate_durable 同位）：同参重放复用决策，异参新卡挂起。"""

    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_d", suspend=True)
    request = {
        "tool_name": "write_file",
        "input": {"path": "a", "content": "x"},
        "danger_level": "high",
        "description": "d",
    }
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback(request)
    bridge.decide(exc_info.value.approval_id, "approve")
    # 同参重放 → 持久 approve（恢复重放的钥匙），不再登记新卡
    assert callback(dict(request)) == "approve"
    # deny 同样持久：同参重放 fail-closed 地再拒绝
    with pytest.raises(ApprovalSuspension):
        callback({**request, "input": {"path": "other", "content": "x"}})
    pending = bridge.list_pending()
    assert len(pending) == 1  # 异参 → 新卡
    bridge.decide(pending[0]["id"], "deny")
    assert callback({**request, "input": {"path": "other", "content": "x"}}) == "deny"
    assert bridge.list_pending() == []


def test_durable_decision_keys_on_normalized_input() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_h", suspend=True)
    with pytest.raises(ApprovalSuspension):
        callback(
            {
                "tool_name": "write_file",
                "input": {"content": "x", "path": "a"},  # 键序不同，规范化后同参
                "danger_level": "high",
                "description": "d",
            }
        )
    # 键序无关的规范化身份（journal.input_hash）：不经决策不放行
    assert input_hash({"path": "a", "content": "x"}) == input_hash({"content": "x", "path": "a"})


def test_expire_fails_closed_and_blocks_redecision() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_t", suspend=True)
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback({"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"})
    approval = exc_info.value.approval
    assert bridge.expire(approval.id)
    assert not bridge.expire(approval.id)  # 已收口不再重复
    assert bridge.record_status(approval.id) == "expired"
    assert approval.decision is None  # F6 口径：expired 卡没有形成过决策
    with pytest.raises(ApprovalConflictError):
        bridge.decide(approval.id, "approve")


def test_cancel_pending_drops_card_without_finished_registration() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_c", suspend=True)
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback({"tool_name": "bash", "input": {}, "danger_level": "high", "description": "d"})
    approval = exc_info.value.approval
    assert bridge.cancel_pending(approval.id)
    assert bridge.record_status(approval.id) is None  # 不进已收口表
    with pytest.raises(UnknownApprovalError):
        bridge.decide(approval.id, "approve")


def test_suspension_record_lifecycle() -> None:
    bridge = ApprovalBridge()
    callback = bridge.callback_for(task_id="task_r", suspend=True)
    with pytest.raises(ApprovalSuspension) as exc_info:
        callback(
            {"tool_name": "write_file", "input": {}, "danger_level": "high", "description": "d"}
        )
    approval_id = exc_info.value.approval_id
    assert bridge.suspension_for(approval_id) is None  # 捕获点关联前为空
    bridge.attach_suspension(
        approval_id,
        task_id="task_r",
        checkpoint_id="turn_run1",
        permissions_revision=3,
    )
    record = bridge.suspension_for(approval_id)
    assert record is not None
    assert record["task_id"] == "task_r"
    assert record["checkpoint_id"] == "turn_run1"
    assert record["permissions_revision"] == 3
    assert bridge.list_suspensions() == [record]
    bridge.drop_suspension(approval_id)
    assert bridge.suspension_for(approval_id) is None


# ---------------------------------------------------------------- fold 合同


def test_fold_maps_approval_suspended_to_run_suspension() -> None:
    async def stream():
        yield {
            "type": "approval_suspended",
            "reason": "approval_pending",
            "approval_id": "approval_x",
            "tool_name": "write_file",
        }

    result = asyncio.run(fold_engine_result(stream()))
    assert isinstance(result, RunSuspension)
    assert result.reason == "approval_pending"
    assert result.checkpoint_ref == "approval_x"
    assert result.detail == "write_file"


# ---------------------------------------------------------------- 全栈：挂起与恢复


def test_task_suspends_with_checkpoint_and_stays_running(async_server, monkeypatch) -> None:
    server, port, tmp = async_server
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "async_target.txt", "content": "approved"}))]
    )
    _set_fake_llm(monkeypatch, client)

    task_id = _submit_task(port, "write the file")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card")
    assert card is not None
    record = _task_record(server, task_id)
    run_id = record.run_id

    # 任务保持 running（挂起不终态、worker 租约内），文件未落盘
    assert record.status == "running"
    assert record.worker_id
    assert not (tmp / "async_target.txt").exists()

    # 挂起态落盘：checkpoint (a) 带原 id 的 pending 调用与 assistant tool_use
    store = TurnCheckpointStore()
    checkpoint = store.load(_checkpoint_id_for_run(run_id))
    assert checkpoint is not None
    assert checkpoint.run_id == run_id
    assert [call["id"] for call in checkpoint.pending_tool_calls] == ["call_1"]
    assistant_calls = [
        call
        for message in checkpoint.messages
        if message.get("role") == "assistant"
        for call in (message.get("tool_calls") or [])
    ]
    assert [call["id"] for call in assistant_calls] == ["call_1"]

    # 挂起环境登记：任务 id、checkpoint 关联、会话权限 revision
    suspension = server.approval_bridge.suspension_for(str(card["id"]))
    assert suspension is not None
    assert suspension["task_id"] == task_id
    assert suspension["checkpoint_id"] == _checkpoint_id_for_run(run_id)
    assert suspension["permissions_revision"] == 0

    # 任务事件台账可见：approval_suspended（RunSuspension 等价信号）
    events = server.task_events(task_id)
    assert [event["type"] for event in events] == ["approval_suspended"]
    assert events[0]["reason"] == "approval_pending"
    assert events[0]["approval_id"] == card["id"]

    # 收口不留给终态任务：清理分支在 deny/取消路径验证，这里仅确认挂起期不删除
    assert store.load(_checkpoint_id_for_run(run_id)) is not None


def test_decision_endpoint_annotates_suspended_task(async_server, monkeypatch) -> None:
    server, port, tmp = async_server
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "annot.txt", "content": "x"}))]
    )
    _set_fake_llm(monkeypatch, client)
    task_id = _submit_task(port, "write it")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card")

    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{card['id']}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    payload = json.loads(body)
    assert payload["ok"] is True
    assert payload["suspended_task_id"] == task_id  # A9 增量标注（向后兼容加字段）
    assert payload["checkpoint_id"] == _checkpoint_id_for_run(_task_record(server, task_id).run_id)
    _wait_until(lambda: _task_record(server, task_id).status == "completed", 15, "task completion")
    assert (tmp / "annot.txt").read_text(encoding="utf-8") == "x"


def test_approve_resumes_from_checkpoint_and_completes(async_server, monkeypatch) -> None:
    server, port, tmp = async_server
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "approved.txt", "content": "hi"}))]
    )
    _set_fake_llm(monkeypatch, client)

    task_id = _submit_task(port, "write the file")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card")
    approval_id = str(card["id"])

    status, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200

    record = _wait_until(
        lambda: (
            _task_record(server, task_id)
            if _task_record(server, task_id).status == "completed"
            else None
        ),
        20,
        "task completion after approve",
    )
    # approve 恢复后文件真实落盘，结果含恢复段文本
    assert (tmp / "approved.txt").read_text(encoding="utf-8") == "hi"
    assert record is not None and "resumed and finished" in str(record.result)
    assert record.error is None

    # journal 分流留痕：重派的 call_1 走 无记录→执行→completed（scope = 服务器
    # 归一化 cwd，与执行器 context.cwd 同值）
    # 与服务器共用进程级单例：journal 路径在单例首次使用时的 HOME 落定，
    # 每测各建 ToolJournal() 会拿到不同 HOME 下的空库
    journal = get_tool_journal()
    claim = journal.claim_tool(
        scope=server.cwd,
        run_id=_task_record(server, task_id).run_id,
        tool_use_id="call_1",
        tool_name="write_file",
        tool_input={"path": "approved.txt", "content": "hi"},
    )
    assert claim.action == "reuse"  # 已 completed，幂等重放
    assert claim.serialized_result is not None
    assert claim.serialized_result["is_error"] is False  # 序列化的是工具自身结果

    # 事件台账完整：挂起 → 收口(approve)
    event_types = [event["type"] for event in server.task_events(task_id)]
    assert event_types == ["approval_suspended", "approval_settled"]
    settled = server.task_events(task_id)[-1]
    assert settled["decision"] == "approve"
    assert settled["resolved_by"] == "web"

    # 终态即清 checkpoint：无可恢复物
    assert TurnCheckpointStore().load(_checkpoint_id_for_run(record.run_id)) is None
    # 卡已收口且无残留挂起登记
    assert server.approval_bridge.record_status(approval_id) == "resolved"
    assert server.approval_bridge.list_suspensions() == []


def test_approve_resume_replays_batch_with_journal_routing(async_server, monkeypatch) -> None:
    """batch 重派：先执行的调用 journal 复用，挂起调用重放执行（按原 id）。"""

    server, port, tmp = async_server
    config = load_config(project_root=None)
    config.permissions.allow = ["write_file"]  # call_a 免审批直行（先于挂起调用执行）
    server.config = config
    client = _ToolCallThenDoneClient(
        [
            ("call_a", "write_file", json.dumps({"path": "batch_a.txt", "content": "A"})),
            ("call_b", "bash", json.dumps({"command": "echo B > batch_b.txt"})),
        ]
    )
    _set_fake_llm(monkeypatch, client)

    task_id = _submit_task(port, "write both files")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card on bash")
    assert card is not None
    run_id = _task_record(server, task_id).run_id
    # 挂起时 batch_a 已落盘（先执行的调用），batch_b 没有
    assert (tmp / "batch_a.txt").read_text(encoding="utf-8") == "A"
    assert not (tmp / "batch_b.txt").exists()

    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{card['id']}/decision",
        body={"decision": "approve"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    record = _wait_until(
        lambda: (
            _task_record(server, task_id)
            if _task_record(server, task_id).status == "completed"
            else None
        ),
        20,
        "task completion after batch approve",
    )
    assert record is not None
    # 挂起调用按原 id 重派执行；先执行的调用结果原样保留
    assert (tmp / "batch_b.txt").read_text(encoding="utf-8").strip() == "B"
    assert (tmp / "batch_a.txt").read_text(encoding="utf-8") == "A"
    # journal 分流留痕：两个调用都 completed（call_a 复用、call_b 执行）
    # 与服务器共用进程级单例：journal 路径在单例首次使用时的 HOME 落定，
    # 每测各建 ToolJournal() 会拿到不同 HOME 下的空库
    journal = get_tool_journal()
    for call_id, name, arguments in (
        ("call_a", "write_file", {"path": "batch_a.txt", "content": "A"}),
        ("call_b", "bash", {"command": "echo B > batch_b.txt"}),
    ):
        claim = journal.claim_tool(
            scope=server.cwd,
            run_id=run_id,
            tool_use_id=call_id,
            tool_name=name,
            tool_input=arguments,
        )
        assert claim.action == "reuse", call_id


def test_deny_completes_task_without_executing(async_server, monkeypatch) -> None:
    server, port, tmp = async_server
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "denied.txt", "content": "no"}))]
    )
    _set_fake_llm(monkeypatch, client)

    task_id = _submit_task(port, "write the file")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card")
    approval_id = str(card["id"])

    status, _ = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        body={"decision": "deny"},
        headers={"x-api-key": KEY},
    )
    assert status == 200
    record = _wait_until(
        lambda: (
            _task_record(server, task_id)
            if _task_record(server, task_id).status == "completed"
            else None
        ),
        15,
        "task completion after deny",
    )
    # 文件不存在；结果含拒绝说明（正常完成收口，不是 failed）
    assert not (tmp / "denied.txt").exists()
    assert record is not None
    assert "denied by approval policy" in str(record.result)
    assert "resolved_by=web" in str(record.result)
    # journal 无执行记录（挂起发生在 claim 之前，deny 不重放）
    # 与服务器共用进程级单例：journal 路径在单例首次使用时的 HOME 落定，
    # 每测各建 ToolJournal() 会拿到不同 HOME 下的空库
    journal = get_tool_journal()
    claim = journal.claim_tool(
        scope=server.cwd,
        run_id=record.run_id,
        tool_use_id="call_1",
        tool_name="write_file",
        tool_input={"path": "denied.txt", "content": "no"},
    )
    assert claim.action == "execute"  # 无记录 → 首次认领（从未执行过）
    # 收口事件与 checkpoint 清理
    settled = server.task_events(task_id)[-1]
    assert settled["type"] == "approval_settled"
    assert settled["decision"] == "deny"
    assert TurnCheckpointStore().load(_checkpoint_id_for_run(record.run_id)) is None


def test_timeout_expires_and_fails_closed(async_server, monkeypatch) -> None:
    server, port, tmp = async_server
    # 短超时注入：_await_suspension_settlement 读取模块常量（与 F6 桥同一把尺）
    monkeypatch.setattr("nexuscli.runtime.approvals.APPROVAL_TIMEOUT_SECONDS", 0.4)
    client = _ToolCallThenDoneClient(
        [("call_1", "write_file", json.dumps({"path": "expired.txt", "content": "late"}))]
    )
    _set_fake_llm(monkeypatch, client)

    task_id = _submit_task(port, "write the file")
    card = _wait_until(lambda: _card_for(server, task_id), 15, "approval card")
    approval_id = str(card["id"])

    record = _wait_until(
        lambda: (
            _task_record(server, task_id)
            if _task_record(server, task_id).status == "completed"
            else None
        ),
        15,
        "task completion after timeout",
    )
    # 300s（此处缩短）无决策 → expired → 合成 deny，任务正常收口
    assert not (tmp / "expired.txt").exists()
    assert record is not None
    assert "denied by approval policy" in str(record.result)
    assert "resolved_by=timeout" in str(record.result)
    assert server.approval_bridge.record_status(approval_id) == "expired"
    settled = server.task_events(task_id)[-1]
    assert settled["decision"] == "deny"
    assert settled["resolved_by"] == "timeout"
    assert TurnCheckpointStore().load(_checkpoint_id_for_run(record.run_id)) is None
