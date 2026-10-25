"""Workbench SSE 事件流端点测试（spec 2026-10-25_add-workbench-mvp，F2）。

真端口 fixture 照 tests/test_mcp_http_security.py 的口径：RuntimeApiServer(port=0) +
_build_http_server。pytest 环境由 conftest 的 autouse fixture 重定向 HOME/USERPROFILE，
runtime.db / sessions 均落在临时目录。

A10（spec 2026-10-25_port-realtime-offline-e2e）新增：审批事件入流（approval_suspended
/ approval_resolved）、live token 帧按 50ms 窗口合帧、单连接积压 resync 帧
（Ported from kitagent (MIT, zed123214/kitagent) src/agent_runtime/web/session.py:13-266）。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import pytest

from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.approvals import ApprovalSuspension
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()


def _make_server(cwd: str) -> RuntimeApiServer:
    from nexuscli.config import load_config

    return RuntimeApiServer(
        cwd=cwd,
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
    )


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], bytes]:
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
    result_headers = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, result_headers, data


def _stream_lines(port: int, path: str, *, seconds: float, use_header: bool = True) -> list[str]:
    """有界读取一条 SSE 连接：看门狗线程在 seconds 后 shutdown socket 解除阻塞。

    getresponse() 之后 http.client 会把 conn.sock 置 None（close-delimited 响应），
    不能用 conn.sock.settimeout 做预算；shutdown 不受 makefile 引用计数影响，
    是唯一可靠的强制收口手段。默认带 x-api-key 头；api_key 查询参数只为浏览器
    EventSource 存在，由 use_header=False 的用例单独覆盖。
    """

    import socket as _socket

    # timeout=None：读完全阻塞，收口只由看门狗 shutdown 负责（5s 读超时会
    # 以 TimeoutError 形式被 except OSError 捕获，导致心跳用例提前断流）。
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=None)
    headers = {"x-api-key": KEY} if use_header else {}
    conn.request("GET", path, headers=headers)
    response = conn.getresponse()
    lines: list[str] = []

    def watchdog() -> None:
        try:
            raw = response.fp.raw  # SocketIO；_sock 是底层 socket
            raw._sock.shutdown(_socket.SHUT_RDWR)  # noqa: SLF001 - 测试专用的强制收口
        except (OSError, AttributeError):
            pass

    timer = threading.Timer(seconds, watchdog)
    timer.start()
    try:
        while True:
            try:
                line = response.fp.readline()
            except OSError:
                break  # shutdown 触发的读中断 = 预算到点
            if not line:
                break
            lines.append(line.decode("utf-8").rstrip("\n"))
    finally:
        timer.cancel()
        conn.close()
    return lines


def _create_thread(server: RuntimeApiServer, port: int) -> str:
    status, _, body = _request(port, "POST", "/v1/threads", headers={"x-api-key": KEY})
    assert status == 200
    thread_id = str(json.loads(body)["id"])
    return thread_id


def _stream_lines_live(port: int, path: str, *, seconds: float, sink: list[str]) -> None:
    """_stream_lines 的逐行实时投递版：行一到就 append 进 sink，不等到连接收口。

    _stream_lines 把行攒在局部列表里、watchdog 触发后才整批返回——适合「断流后
    断言」的既有用例；A10 用例需要在流存活期间断言帧序（挂起 → 决策 → 解决），
    必须逐行可见。收口机制相同：看门狗线程在 seconds 后 shutdown socket。
    """

    import socket as _socket

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=None)
    conn.request("GET", path, headers={"x-api-key": KEY})
    response = conn.getresponse()

    def watchdog() -> None:
        # 同 _stream_lines.watchdog：测试专用的强制收口（_sock 私有属性访问）。
        with suppress(OSError, AttributeError):
            response.fp.raw._sock.shutdown(_socket.SHUT_RDWR)  # noqa: SLF001

    timer = threading.Timer(seconds, watchdog)
    timer.start()
    try:
        while True:
            try:
                line = response.fp.readline()
            except OSError:
                break  # shutdown 触发的读中断 = 预算到点
            if not line:
                break
            sink.append(line.decode("utf-8").rstrip("\n"))
    finally:
        timer.cancel()
        conn.close()


def _frames(lines: list[str]) -> list[tuple[int | None, str, dict[str, Any] | None]]:
    """把 _stream_lines 的原始行解析成 (id, event, data) 帧。

    服务端所有 data 均为单行 JSON，解析失败置 None；审批/resync 帧不带 id: 行
    （id 为 None）；心跳注释行（": ping"）跳过。
    """

    frames: list[tuple[int | None, str, dict[str, Any] | None]] = []
    current_id: int | None = None
    current_event: str | None = None
    for line in lines:
        if line.startswith("id: "):
            current_id = int(line[4:])
        elif line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: ") and current_event is not None:
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                data = None
            frames.append((current_id, current_event, data if isinstance(data, dict) else None))
            current_id = None
            current_event = None
    return frames


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def sse_server(tmp_path):
    server = _make_server(str(tmp_path))
    thread = threading.Thread(target=server.serve_forever, name="nexuscli-sse-test", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server._server is not None
    port = int(server._server.server_address[1])
    time.sleep(0.1)
    yield server, port
    server._stop.set()
    thread.join(timeout=5)


def test_sse_replays_existing_events(sse_server) -> None:
    _server, port = sse_server
    thread_id = _create_thread(_server, port)
    lines = _stream_lines(port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=3)
    frames = [line for line in lines if line.startswith("event: ")]
    assert frames == ["event: thread.created"]
    id_lines = [line for line in lines if line.startswith("id: ")]
    assert len(id_lines) == 1 and id_lines[0][4:].isdigit()
    data_lines = [line for line in lines if line.startswith("data: ")]
    assert json.loads(data_lines[0][6:])["id"] == thread_id


def test_sse_pushes_live_events(sse_server) -> None:
    server, port = sse_server
    thread_id = _create_thread(server, port)
    collected: list[str] = []
    appended = threading.Event()

    # 读取线程先连上（等首帧到达），主线程再追加事件，SSE 应实时推送
    def reader() -> None:
        stream_path = f"/v1/threads/{thread_id}/events/stream?since=0"
        collected.extend(_stream_lines(port, stream_path, seconds=6))
        appended.set()

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    time.sleep(0.5)
    server._append_event(thread_id, "message.delta", {"text": "hello"})
    assert appended.wait(timeout=15)
    assert any("event: message.delta" in line for line in collected), collected


def test_sse_since_cursor_replay_only_later_events(sse_server) -> None:
    server, port = sse_server
    thread_id = _create_thread(server, port)
    first_id = server._events_since(thread_id, 0)[0][0]
    lines = _stream_lines(
        port, f"/v1/threads/{thread_id}/events/stream?since={first_id}", seconds=2
    )
    frames = [line for line in lines if line.startswith("event: ")]
    assert frames == [], f"since={first_id} 不应回放已有事件: {frames}"


def test_sse_heartbeat_on_idle(sse_server) -> None:
    _server, port = sse_server
    thread_id = _create_thread(_server, port)
    lines = _stream_lines(port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=25)
    assert ": ping" in lines, f"空闲 15s 内应收到 : ping 心跳: {lines[-5:]}"


def test_sse_auth_variants(sse_server) -> None:
    _server, port = sse_server
    thread_id = _create_thread(_server, port)

    # 无头无查询参数 → 401
    status, _, _ = _request(port, "GET", f"/v1/threads/{thread_id}/events/stream?since=0")
    assert status == 401
    # api_key 查询参数正确 → 200（SSE 流开始）
    lines = _stream_lines(
        port,
        f"/v1/threads/{thread_id}/events/stream?since=0&api_key={KEY}",
        seconds=3,
        use_header=False,
    )
    assert any(line.startswith("event: ") for line in lines)


def test_sse_query_param_auth_does_not_leak_to_other_endpoints(sse_server) -> None:
    _server, port = sse_server
    thread_id = _create_thread(_server, port)
    # api_key 查询参数对其他端点无效（鉴权扩展只对 stream 路径生效）
    status, _, _ = _request(port, "GET", f"/v1/tasks?api_key={KEY}")
    assert status == 401
    status, _, _ = _request(port, "POST", f"/v1/threads?api_key={KEY}", body={"x": 1})
    assert status == 401
    # 负向锁：其他 GET 带头鉴权仍正常
    status, _, _ = _request(port, "GET", "/v1/tasks", headers={"x-api-key": KEY})
    assert status == 200
    assert thread_id  # thread 存在，供语义完整性


def test_sse_cross_origin_rejected(sse_server) -> None:
    _server, port = sse_server
    thread_id = _create_thread(_server, port)
    status, _, _ = _request(
        port,
        "GET",
        f"/v1/threads/{thread_id}/events/stream?since=0&api_key={KEY}",
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403


def test_sse_unknown_thread_404(sse_server) -> None:
    _server, port = sse_server
    status, _, _ = _request(
        port,
        "GET",
        "/v1/threads/thread_nonexistent/events/stream?since=0",
        headers={"x-api-key": KEY},
    )
    assert status == 404


def test_sse_slot_limit(sse_server) -> None:
    server, port = sse_server
    thread_id = _create_thread(server, port)
    from nexuscli.runtime.api import MAX_SSE_CLIENTS

    conns: list[http.client.HTTPConnection] = []
    try:
        for _ in range(MAX_SSE_CLIENTS):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            conn.request(
                "GET",
                f"/v1/threads/{thread_id}/events/stream?since=0",
                headers={"x-api-key": KEY},
            )
            response = conn.getresponse()
            assert response.status == 200
            conns.append(conn)
        # 第 17 个连接 → 503
        status, headers, _ = _request(
            port,
            "GET",
            f"/v1/threads/{thread_id}/events/stream?since=0",
            headers={"x-api-key": KEY},
        )
        assert status == 503
        assert headers.get("retry-after") == "5"
    finally:
        for conn in conns:
            conn.close()


def test_sse_pushes_approval_suspend_and_resolve(sse_server) -> None:
    """A10 机制 1：审批登记表快照入流——挂起/解决经 SSE 可见（轮询对比）。

    挂起卡走 A9 挂起模式回调（登记即抛 ApprovalSuspension，卡片留登记表）；
    解决走决策端点（api.py 收口点登记台账并 notify SSE 等待者）。
    """

    server, port = sse_server
    thread_id = _create_thread(server, port)
    collected: list[str] = []

    def reader() -> None:
        _stream_lines_live(
            port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=5, sink=collected
        )

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    time.sleep(0.5)

    suspend_cb = server.approval_bridge.callback_for(thread_id=thread_id, suspend=True)
    approval_id: str | None = None
    try:
        suspend_cb({"tool_name": "bash", "description": "list files", "input": {"command": "ls"}})
    except ApprovalSuspension as exc:
        approval_id = exc.approval_id
    assert approval_id is not None

    assert _wait_for(
        lambda: any(event == "approval_suspended" for _fid, event, _data in _frames(collected))
    ), collected
    suspended = [data for _fid, event, data in _frames(collected) if event == "approval_suspended"]
    assert suspended[-1] == {
        "approval_id": approval_id,
        "tool_name": "bash",
        "description": "list files",
        "scope_type": "thread",
        "scope_id": thread_id,
        "reason": "approval_pending",
    }

    # 解决走真实决策端点：api.py 收口点登记台账并 notify SSE 等待者
    # （直接调 bridge.decide() 会绕过台账，落入 record_status 兜底帧）
    status, _, body = _request(
        port,
        "POST",
        f"/v1/approvals/{approval_id}/decision",
        headers={"x-api-key": KEY},
        body={"decision": "approve"},
    )
    assert status == 200
    assert json.loads(body)["decision"] == "approve"
    assert _wait_for(
        lambda: any(event == "approval_resolved" for _fid, event, _data in _frames(collected))
    ), collected
    resolved = [data for _fid, event, data in _frames(collected) if event == "approval_resolved"]
    assert resolved[-1] == {
        "id": approval_id,
        "approval_id": approval_id,
        "decision": "approve",
        "resolved_by": "web",
        "status": "resolved",
    }


def test_sse_coalesces_live_token_deltas_in_order(sse_server) -> None:
    """A10 机制 2：live token 帧按 50ms 窗口合帧——data.text 拼接、按序可拼、id 单调。

    回放段（thread.created）逐行原文（A7 契约由 tests/test_event_seq 锁定），
    合帧只发生在 live 段；非 delta 行即时直通并打断合帧窗口，帧序与 id 单调性
    不变（合帧帧 id = 段末行 id）。
    """

    server, port = sse_server
    thread_id = _create_thread(server, port)
    collected: list[str] = []

    def reader() -> None:
        _stream_lines_live(
            port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=5, sink=collected
        )

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    time.sleep(0.5)  # 连接建立并完成回放段（进入 live 段）

    server._append_event(thread_id, "tool_call", {"name": "bash", "input": {"command": "ls"}})
    for index in range(8):
        server._append_event(thread_id, "message.delta", {"text": f"t{index}"})
    server._append_event(thread_id, "tool_call", {"name": "bash", "result": "ok"})

    def both_tool_calls_seen() -> bool:
        return [event for _fid, event, _data in _frames(collected)].count("tool_call") == 2

    assert _wait_for(both_tool_calls_seen), collected
    frames = _frames(collected)
    events = [event for _fid, event, _data in frames]
    assert events[0] == "thread.created"  # 回放段逐行原文
    assert not any(event == "resync" for event in events)
    delta_texts = [str(data["text"]) for _fid, event, data in frames if event == "message.delta"]
    # 合帧确实发生（8 条 delta 不逐条成帧），且按帧序拼接与落库顺序一致
    assert 0 < len(delta_texts) < 8
    assert "".join(delta_texts) == "".join(f"t{index}" for index in range(8))
    # 非 delta 行即时直通（窗口在非 delta 行处打断）
    assert events.count("tool_call") == 2
    # id 单调：带 id 的帧严格递增（合帧帧 id = 段末行 id，重放游标不回退）
    ids = [fid for fid, _event, _data in frames if fid is not None]
    assert ids == sorted(set(ids))


def test_sse_resync_on_backlog_high_water(sse_server) -> None:
    """A10 机制 3：单连接积压防护——单次轮询非 delta 行超高水位 → resync 帧并整段跳过。

    data.since 指向客户端最后收到的行 id（此处连接后尚未送达任何行 → 0），
    客户端据其重拉快照；跳过段不在本流重发，但流本身继续工作。
    """

    server, port = sse_server
    thread_id = _create_thread(server, port)
    from nexuscli.runtime.api import SSE_BACKLOG_HIGH_WATER

    for index in range(SSE_BACKLOG_HIGH_WATER + 10):
        server._append_event(thread_id, "tool_call", {"index": index})
    collected: list[str] = []

    def reader() -> None:
        _stream_lines_live(
            port, f"/v1/threads/{thread_id}/events/stream?since=0", seconds=4, sink=collected
        )

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    assert _wait_for(lambda: any(event == "resync" for _fid, event, _data in _frames(collected))), (
        collected
    )
    frames = _frames(collected)
    resyncs = [data for _fid, event, data in frames if event == "resync"]
    assert len(resyncs) == 1, collected
    assert resyncs[0]["reason"] == "backlog"
    assert resyncs[0]["since"] == 0
    assert resyncs[0]["skipped_up_to"] > 0
    # 整段跳过：resync 是连接后的第一帧（thread.created 也在跳过段内）
    assert frames[0][1] == "resync"
    # 流未卡死：此后追加的 live 事件照常推送
    server._append_event(thread_id, "message.delta", {"text": "after-resync"})
    assert _wait_for(
        lambda: any(event == "message.delta" for _fid, event, _data in _frames(collected))
    ), collected
