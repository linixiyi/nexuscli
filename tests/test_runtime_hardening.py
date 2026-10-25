"""Runtime API 加固测试（lane A1 安全壳 + A2 幂等门，spec 2026-10-25 照搬对齐）。

A1 响应头与请求体门 Ported from kitagent (MIT, zed123214/kitagent)
src/agent_runtime/web/app.py:106-128；A2 幂等登记门 Ported from kitagent
src/agent_runtime/web/gateway.py:72-93（Gateway._record）。fixture 与请求助手
照 tests/test_runtime_websession.py 的口径；返回原始头列表以便断言不重复追加。
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from typing import Any

import pytest

from nexuscli.config import load_config
from nexuscli.runtime.api import IDEMPOTENCY_CAPACITY, RuntimeApiServer
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


@pytest.fixture()
def hardening_server(tmp_path):
    server = RuntimeApiServer(
        cwd=str(tmp_path), config=load_config(project_root=None), api_key=KEY, port=0
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while server._server is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server._server is not None
    port = int(server._server.server_address[1])  # type: ignore[index]
    time.sleep(0.1)
    yield server, port
    server._stop.set()
    thread.join(timeout=5)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    content_type: str | None = "application/json",
) -> tuple[int, list[tuple[str, str]], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    send_headers = dict(headers or {})
    if content_type is not None:
        send_headers["content-type"] = content_type
    conn.request(method, path, body=body, headers=send_headers)
    response = conn.getresponse()
    data = response.read()
    raw_headers = [(k.lower(), v) for k, v in response.getheaders()]
    conn.close()
    return response.status, raw_headers, data


def _json(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, list[tuple[str, str]], dict[str, Any]]:
    body = json.dumps(payload or {}).encode("utf-8")
    status, raw, data = _request(port, method, path, headers=headers, body=body)
    return status, raw, json.loads(data)


def _auth(**extra: str) -> dict[str, str]:
    return {"x-api-key": KEY, **extra}


# ---------------------------------------------------------------------------
# A1 安全壳：响应头统一追加
# ---------------------------------------------------------------------------


def test_security_headers_on_success_and_error(hardening_server) -> None:
    _server, port = hardening_server
    status, raw, _ = _json(port, "GET", "/v1/tasks", headers=_auth())
    assert status == 200
    sent = dict(raw)
    assert sent["content-security-policy"] == CSP
    assert sent["x-content-type-options"] == "nosniff"
    assert sent["referrer-policy"] == "no-referrer"
    assert sent["cache-control"] == "no-store"

    # 错误响应（401）同样带壳
    status, raw, _ = _request(port, "GET", "/v1/tasks")
    assert status == 401
    sent = dict(raw)
    assert sent["content-security-policy"] == CSP
    assert sent["x-content-type-options"] == "nosniff"
    assert sent["referrer-policy"] == "no-referrer"
    assert sent["cache-control"] == "no-store"


def test_no_duplicate_no_store_on_self_caching_responses(hardening_server) -> None:
    """自带 no-store 的响应（SSE 流）不重复追加 cache-control。"""

    _server, port = hardening_server
    status, _, payload = _json(port, "POST", "/v1/threads", headers=_auth())
    assert status == 200
    thread_id = payload["id"]

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", f"/v1/threads/{thread_id}/events/stream?api_key={KEY}")
        response = conn.getresponse()  # 响应头先行送达，读头即可返回
        cache = [v for k, v in response.getheaders() if k.lower() == "cache-control"]
        csp = [v for k, v in response.getheaders() if k.lower() == "content-security-policy"]
        assert cache == ["no-store"]  # SSE 自带 no-store，安全壳跳过而非重复
        assert csp == [CSP]
    finally:
        conn.close()


def test_413_on_oversized_body(hardening_server) -> None:
    _server, port = hardening_server
    status, raw, _ = _request(port, "POST", "/v1/threads", headers=_auth(), body=b"x" * (65536 + 1))
    assert status == 413
    assert dict(raw)["content-security-policy"] == CSP  # 413 响应也过安全壳

    # 边界：恰 64 KiB 不触发 413（照常进入建线程分支）
    status, _, _ = _request(port, "POST", "/v1/threads", headers=_auth(), body=b"x" * 65536)
    assert status == 200


def test_415_on_non_json_body(hardening_server) -> None:
    _server, port = hardening_server
    status, _, _ = _request(
        port,
        "POST",
        "/v1/threads",
        headers=_auth(),
        body=b"message=hi",
        content_type="application/x-www-form-urlencoded",
    )
    assert status == 415


def test_bootstrap_endpoint_also_gated(hardening_server) -> None:
    _server, port = hardening_server
    # 415：content-type 不对 → 门先于口令校验
    status, _, _ = _request(
        port,
        "POST",
        "/v1/workbench/bootstrap",
        body=b"token=x",
        content_type="application/x-www-form-urlencoded",
    )
    assert status == 415
    # 413：超大体
    status, _, _ = _request(
        port,
        "POST",
        "/v1/workbench/bootstrap",
        headers=_auth(),
        body=b"x" * (65536 + 1),
    )
    assert status == 413
    # 正常 JSON bootstrap 不受影响（错误口令 → 403，说明门放行了）
    status, _, _ = _json(port, "POST", "/v1/workbench/bootstrap", payload={"token": "wrong"})
    assert status == 403


def test_bodyless_post_still_allowed(hardening_server) -> None:
    """无 content-length / content-length: 0 的 POST 照旧放行（零适配回归锁）。"""

    _server, port = hardening_server
    status, _, _ = _request(
        port, "POST", "/v1/threads", headers=_auth(), body=None, content_type=None
    )
    assert status == 200


# ---------------------------------------------------------------------------
# A2 幂等门：同 ID 重放 / 冲突 / 容量 / 零适配
# ---------------------------------------------------------------------------


def test_idempotent_replay_returns_first_result(hardening_server) -> None:
    _server, port = hardening_server
    status1, raw1, payload1 = _json(
        port, "POST", "/v1/threads", headers=_auth(**{"x-request-id": "req-1"})
    )
    assert status1 == 200
    status2, raw2, payload2 = _json(
        port, "POST", "/v1/threads", headers=_auth(**{"x-request-id": "req-1"})
    )
    assert status2 == 200
    assert payload2["id"] == payload1["id"]  # 重放首次结果，不新建线程
    assert dict(raw2)["content-security-policy"] == CSP  # 重放响应也过安全壳
    assert dict(raw1)["cache-control"] == "no-store"


def test_idempotent_conflict_on_different_payload(hardening_server) -> None:
    _server, port = hardening_server
    status, _, _ = _json(port, "POST", "/v1/threads", headers=_auth(**{"x-request-id": "req-2"}))
    assert status == 200
    status, _, payload = _json(
        port,
        "POST",
        "/v1/threads",
        headers=_auth(**{"x-request-id": "req-2"}),
        payload={"mode": "react"},
    )
    assert status == 409
    assert "error" in payload


def test_conflict_when_same_id_hits_different_endpoint(hardening_server) -> None:
    """kitagent 摘要含操作（kind）语义：同 ID 换端点 = 异 payload → 409。"""

    _server, port = hardening_server
    status, _, _ = _json(port, "POST", "/v1/threads", headers=_auth(**{"x-request-id": "req-3"}))
    assert status == 200
    # 若无幂等门，POST /v1/tasks 空 payload 走 400（message is required）；409 说明
    # 是摘要冲突（端点参与摘要），不是业务校验。
    status, _, _ = _json(port, "POST", "/v1/tasks", headers=_auth(**{"x-request-id": "req-3"}))
    assert status == 409


def test_idempotency_registry_full_returns_429(hardening_server) -> None:
    server, port = hardening_server
    for index in range(IDEMPOTENCY_CAPACITY):
        server._idempotency[f"seed-{index}"] = {
            "digest": "seed",
            "done": True,
            "response": (200, {}),
        }
    status, _, payload = _json(
        port, "POST", "/v1/threads", headers=_auth(**{"x-request-id": "fresh"})
    )
    assert status == 429
    assert "error" in payload


def test_missing_request_id_is_zero_adaptation(hardening_server) -> None:
    """无 x-request-id 的 POST 照旧逐次执行；GET 带头也不进门。"""

    _server, port = hardening_server
    status1, _, payload1 = _json(port, "POST", "/v1/threads", headers=_auth())
    status2, _, payload2 = _json(port, "POST", "/v1/threads", headers=_auth())
    assert status1 == status2 == 200
    assert payload1["id"] != payload2["id"]  # 各建各的线程，未被幂等门拦截

    status, _, _ = _json(port, "GET", "/v1/tasks", headers=_auth(**{"x-request-id": "req-get"}))
    assert status == 200


def test_workbench_link_file_written_with_0600(tmp_path) -> None:
    """task_0006（2026-10-26_workbench-kitagent-ui）：一键链接落盘状态目录。

    服务启动（--workbench）后 ~/.nexuscli/workbench-link.txt 必须存在、内容是
    含当前端口与一次性口令的一键链接、权限 0600（文件内嵌一次性口令，等同凭证）。
    HOME/USERPROFILE 由 tests/conftest.py 的 autouse fixture 重定向到临时目录。
    """
    from pathlib import Path

    server = RuntimeApiServer(
        cwd=str(tmp_path),
        config=load_config(project_root=None),
        api_key=KEY,
        port=0,
        workbench=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        link_path = Path.home() / ".nexuscli" / "workbench-link.txt"
        deadline = time.monotonic() + 10
        while not link_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert link_path.exists(), "workbench 启动后必须落盘一键链接文件"
        content = link_path.read_text(encoding="utf-8").strip()
        port = int(server._server.server_address[1])  # type: ignore[index]
        assert content == (
            f"http://127.0.0.1:{port}/#bootstrap={server.web_session.bootstrap_token}"
        )
        assert link_path.stat().st_mode & 0o777 == 0o600
    finally:
        server._stop.set()
        thread.join(timeout=5)
