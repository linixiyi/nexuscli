"""MCP server 的 HTTP 面对整个浏览器与本机进程开放。

历史上实测过：GET / 返回 501，POST /random/path 返回 200，text/plain 也返回 200，
带 Origin: https://evil.example 的请求还能执行 bash——本机任意网页一句 fetch 就能在
127.0.0.1 上跑命令。锁住的边界：路径、content-type、Origin、GET 405，以及
「跨源真的执行不到工具」。2026-10-27 夜间①验证（P0-1）后新增两条硬边界：
未配置 NEXUSCLI_MCP_API_KEY 拒绝启动；缺/错 key 的请求一律 401，
tools/call 改经 ToolExecutor 全链（审计/hooks/规则/写域守卫生效）。
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexuscli.mcp.server import MCP_HTTP_API_KEY_ENV, _make_server, serve_http

API_KEY = "test-key"


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, int]]:
    httpd = _make_server(0, str(tmp_path), API_KEY)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield "127.0.0.1", httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _post(
    server: tuple[str, int],
    path: str,
    body: str,
    content_type: str | None = "application/json",
    origin: str | None = None,
    key: str | None = API_KEY,
) -> tuple[int, str, dict[str, str]]:
    host, port = server
    headers: dict[str, str] = {}
    if key is not None:
        headers["authorization"] = f"Bearer {key}"
    if content_type is not None:
        headers["content-type"] = content_type
    if origin is not None:
        headers["origin"] = origin
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8"), dict(response.getheaders())
    finally:
        conn.close()


def _get(server: tuple[str, int], path: str) -> tuple[int, str, dict[str, str]]:
    host, port = server
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8"), dict(response.getheaders())
    finally:
        conn.close()


def _initialize() -> str:
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})


def _bash_call(command: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "bash", "arguments": {"command": command}},
        }
    )


def test_get_is_rejected_with_allow_post(server: tuple[str, int]) -> None:
    status, body, headers = _get(server, "/")
    assert status == 405
    assert headers.get("allow") == "POST"
    assert "SSE" in body


def test_unknown_path_is_not_an_endpoint(server: tuple[str, int]) -> None:
    status, _, _ = _post(server, "/random/path", _initialize())
    assert status == 404


def test_get_on_unknown_path_is_404(server: tuple[str, int]) -> None:
    status, _, _ = _get(server, "/random/path")
    assert status == 404


def test_non_json_content_type_is_415(server: tuple[str, int]) -> None:
    status, _, _ = _post(server, "/", _initialize(), content_type="text/plain")
    assert status == 415


def test_missing_content_type_is_415(server: tuple[str, int]) -> None:
    status, _, _ = _post(server, "/", _initialize(), content_type=None)
    assert status == 415


def test_cross_origin_post_is_403(server: tuple[str, int]) -> None:
    status, _, _ = _post(server, "/", _initialize(), origin="https://evil.example")
    assert status == 403


def test_cross_origin_request_cannot_execute_bash(server: tuple[str, int], tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    status, _, _ = _post(server, "/", _bash_call(f"touch {marker}"), origin="https://evil.example")
    assert status == 403
    assert not marker.exists()


def test_missing_api_key_is_401(server: tuple[str, int], tmp_path: Path) -> None:
    marker = tmp_path / "pwned-no-key"
    status, body, _ = _post(server, "/", _bash_call(f"touch {marker}"), key=None)
    assert status == 401
    assert "API key" in body
    assert not marker.exists()


def test_wrong_api_key_is_401(server: tuple[str, int]) -> None:
    status, _, _ = _post(server, "/", _initialize(), key="wrong-key")
    assert status == 401


def test_x_api_key_header_is_accepted(server: tuple[str, int]) -> None:
    host, port = server
    conn = http.client.HTTPConnection(host, port, timeout=10)
    try:
        conn.request(
            "POST",
            "/",
            body=_initialize(),
            headers={"x-api-key": API_KEY, "content-type": "application/json"},
        )
        response = conn.getresponse()
        status = response.status
        body = response.read().decode("utf-8")
    finally:
        conn.close()
    assert status == 200
    assert json.loads(body)["result"]["protocolVersion"]


def test_client_without_origin_is_allowed(server: tuple[str, int]) -> None:
    status, body, _ = _post(server, "/", _initialize())
    assert status == 200
    assert json.loads(body)["result"]["protocolVersion"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://[::1]:3000",
    ],
)
def test_loopback_origin_is_allowed(server: tuple[str, int], origin: str) -> None:
    status, _, _ = _post(server, "/", _initialize(), origin=origin)
    assert status == 200


def test_mcp_path_accepts_json_rpc(server: tuple[str, int]) -> None:
    status, body, _ = _post(server, "/mcp", _initialize())
    assert status == 200
    assert json.loads(body)["result"]["protocolVersion"]


def test_authenticated_bash_call_executes_and_is_audited(
    server: tuple[str, int], tmp_path: Path
) -> None:
    """P1-1：tools/call 必须经 ToolExecutor——工具真跑且审计落盘。"""
    marker = tmp_path / "audited-exec"
    status, body, _ = _post(server, "/", _bash_call(f"touch {marker}"))
    assert status == 200
    payload = json.loads(body)
    assert payload["result"]["isError"] is False
    assert marker.exists()
    audit = Path.home() / ".nexuscli" / "audit.jsonl"
    assert audit.exists(), "executor 链必须产生审计日志"
    assert any('"bash"' in line for line in audit.read_text(encoding="utf-8").splitlines())


def test_serve_http_refuses_to_start_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P0-1：无凭证的 HTTP 端点必须在绑定端口前拒绝启动（fail closed）。"""
    monkeypatch.delenv(MCP_HTTP_API_KEY_ENV, raising=False)
    with pytest.raises(ValueError, match="API key"):
        serve_http(port=0)
