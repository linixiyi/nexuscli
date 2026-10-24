"""Workbench SSE 事件流端点测试（spec 2026-10-25_add-workbench-mvp，F2）。

真端口 fixture 照 tests/test_mcp_http_security.py 的口径：RuntimeApiServer(port=0) +
_build_http_server。pytest 环境由 conftest 的 autouse fixture 重定向 HOME/USERPROFILE，
runtime.db / sessions 均落在临时目录。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from typing import Any

import pytest

from nexuscli.runtime.api import RuntimeApiServer
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
