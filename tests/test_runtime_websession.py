"""工作台会话鉴权测试（spec 2026-10-25_update-workbench-auth，F14）。

覆盖 kitagent 模型的四件套：bootstrap 一次性语义、cookie 会话门、CSRF 强制
（api_key 通道豁免）、Origin 白名单。api_key 外部通道的回归由既有
test_runtime.py / test_runtime_approvals.py / test_runtime_sse.py 承担（零适配通过）。
"""

from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from nexuscli.config import load_config
from nexuscli.runtime.api import RuntimeApiServer
from nexuscli.runtime.websession import WebSession
from tests.test_runtime import _runtime_test_key

KEY = _runtime_test_key()


@pytest.fixture()
def ws_server(tmp_path):
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
    yield server, port
    server._stop.set()
    thread.join(timeout=5)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
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


def test_bootstrap_one_shot(ws_server) -> None:
    server, port = ws_server
    token = server.web_session.bootstrap_token

    # 错口令 → 403，且不消耗一次性标志
    status, _, _ = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": "wrong"})
    assert status == 403
    assert not server.web_session.bootstrapped

    # 正确口令 → 200 + Set-Cookie（HttpOnly/SameSite=strict）+ csrf
    status, headers, body = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": token})
    assert status == 200
    cookie_line = headers.get("set-cookie", "")
    assert cookie_line.startswith("nexuscli_workbench=")
    assert "HttpOnly" in cookie_line and "SameSite=strict" in cookie_line
    csrf = json.loads(body)["csrf"]
    assert csrf == server.web_session.csrf
    assert server.web_session.bootstrapped

    # 重放同口令 → 403（一次性）
    status, _, _ = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": token})
    assert status == 403


def test_cookie_channel_and_gate(ws_server) -> None:
    server, port = ws_server
    token = server.web_session.bootstrap_token
    _, headers, _ = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": token})
    cookie = headers["set-cookie"].split(";")[0]

    # cookie 通道 → 200
    status, _, _ = _request(port, "GET", "/v1/tasks", headers={"cookie": cookie})
    assert status == 200
    # 无 cookie 无 key → 401
    status, _, _ = _request(port, "GET", "/v1/tasks")
    assert status == 401
    # 畸形 cookie → 401
    status, _, _ = _request(
        port, "GET", "/v1/tasks", headers={"cookie": "nexuscli_workbench=wrong"}
    )
    assert status == 401


def test_cookie_channel_post_requires_csrf(ws_server) -> None:
    server, port = ws_server
    token = server.web_session.bootstrap_token
    _, headers, _ = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": token})
    cookie = headers["set-cookie"].split(";")[0]
    csrf = server.web_session.csrf

    # cookie 通道 POST 缺 csrf → 403
    status, _, _ = _request(port, "POST", "/v1/threads", headers={"cookie": cookie}, body={})
    assert status == 403
    # 带 csrf → 200
    status, _, body = _request(
        port,
        "POST",
        "/v1/threads",
        headers={"cookie": cookie, "x-csrf-token": csrf},
        body={},
    )
    assert status == 200
    # 错 csrf → 403
    status, _, _ = _request(
        port, "POST", "/v1/threads", headers={"cookie": cookie, "x-csrf-token": "nope"}, body={}
    )
    assert status == 403


def test_api_key_channel_exempt_from_csrf(ws_server) -> None:
    """外部自动化通道（api_key 头）无 cookie：POST 不要求 csrf（回归锁）。"""

    _server, port = ws_server
    status, _, _ = _request(port, "POST", "/v1/threads", headers={"x-api-key": KEY}, body={})
    assert status == 200


def test_origin_allowlist(ws_server) -> None:
    server, port = ws_server
    # 跨源 Origin → 403（先于凭证判断；即使带正确 key 也拒）
    status, _, _ = _request(
        port,
        "GET",
        "/v1/tasks",
        headers={"x-api-key": KEY, "Origin": "https://evil.example"},
    )
    assert status == 403
    # 本机来源 Origin（dev proxy 形态）+ 凭证 → 放行
    status, _, _ = _request(
        port,
        "GET",
        "/v1/tasks",
        headers={"x-api-key": KEY, "Origin": "http://127.0.0.1:5173"},
    )
    assert status == 200
    # 无 Origin（curl）+ 凭证 → 放行
    status, _, _ = _request(port, "GET", "/v1/tasks", headers={"x-api-key": KEY})
    assert status == 200


def test_bootstrap_endpoint_exempt_from_gate(ws_server) -> None:
    """bootstrap 端点在 cookie/key 门之外（一次性口令即凭证）：无凭证可达。"""

    _server, port = ws_server
    status, _, _ = _request(port, "POST", "/v1/workbench/bootstrap", body={"token": "whatever"})
    assert status in (200, 403)  # 可达；403 = 口令无效，不是 401 门


def test_web_session_unit() -> None:
    session = WebSession()
    assert not session.authorize(None)
    assert not session.authorize(session.cookie)  # 未 bootstrap 前恒 False
    assert session.bootstrap("wrong") is None
    csrf = session.bootstrap(session.bootstrap_token)
    assert csrf == session.csrf
    assert session.bootstrap(session.bootstrap_token) is None  # 一次性
    assert session.authorize(session.cookie)
    assert session.csrf_matches(session.csrf)
    assert not session.csrf_matches(None)
