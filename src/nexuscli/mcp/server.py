from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from nexuscli import __version__
from nexuscli.config import load_config
from nexuscli.net_guards import is_loopback_origin as _is_loopback_origin
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext

# Protocol revision this server speaks. Clients negotiate against it during
# initialize; bump only alongside the message shapes that change.
MCP_PROTOCOL_VERSION = "2024-11-05"


def _build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


def _tool_list(registry: ToolRegistry) -> list[dict[str, Any]]:
    tools = []
    for definition in registry.definitions():
        fn = definition["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn["description"],
                "inputSchema": fn["parameters"],
            }
        )
    return tools


async def _handle_request(request: dict[str, Any], cwd: str) -> dict[str, Any]:
    registry = _build_registry()
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}
    if method in {"initialize", "notifications/initialized"}:
        # MCP clients validate this reply against InitializeResult, which
        # requires protocolVersion, capabilities and serverInfo.version. The
        # bare {"serverInfo": {"name": ...}} shape worked for hand-rolled
        # curl calls but every official SDK client rejected it at handshake
        # (3 pydantic validation errors), so the server was unreachable from
        # the ecosystem it exists to serve.
        if method == "notifications/initialized":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "nexuscli", "version": __version__},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": _tool_list(registry)}}
    if method == "tools/call":
        name = params.get("name")
        tool = registry.get(str(name))
        if not tool:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"message": f'Tool "{name}" not found'},
            }
        config = load_config(project_root=cwd)
        config.policy.hitl_mode = "never"
        result = await tool.execute(
            params.get("arguments") or {},
            ToolContext(cwd=cwd, config=config),
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": result.content}],
                "isError": result.is_error,
            },
        }
    return {"jsonrpc": "2.0", "id": request_id, "error": {"message": f"Unknown method: {method}"}}


async def serve_stdio(cwd: str | None = None) -> None:
    root = str(Path(cwd or ".").resolve())
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, input)
        try:
            request = json.loads(line)
            response = await _handle_request(request, root)
        except EOFError:
            break
        except Exception as exc:  # noqa: BLE001
            response = {"jsonrpc": "2.0", "error": {"message": str(exc)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


# 只开这两个入口：一个短路径，一个 MCP 惯例路径。其他路径一律 404，避免把
# 任何未知 URL 当成 JSON-RPC 端点（修复前 POST /random/path 也返回 200）。
_ALLOWED_PATHS = ("/", "/mcp")

# 浏览器发起的跨源请求必须带 Origin；判定逻辑提取至 net_guards 与 Runtime API 共用。

_SSE_UNSUPPORTED = (
    "This endpoint speaks JSON-RPC over POST only. "
    "SSE streaming (GET) is not supported by the NexusCLI MCP server.\n"
)


def _handler_class(root: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(body)))
            for name, value in (extra_headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            # HEAD 只回头部；写正文会让客户端把多余的字节当成下一个响应。
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_text(self, status: int, text: str, **headers: str) -> None:
            self._send(status, text.encode("utf-8"), "text/plain; charset=utf-8", headers)

        def _reject_method(self) -> None:
            # 路径先判：未知 URL 不存在，比"方法不对"更准确。
            if urlsplit(self.path).path not in _ALLOWED_PATHS:
                self._send_text(404, "Not found\n")
                return
            self._send_text(405, _SSE_UNSUPPORTED, allow="POST")

        do_GET = _reject_method  # noqa: N815 - stdlib naming
        do_HEAD = _reject_method  # noqa: N815
        do_PUT = _reject_method  # noqa: N815
        do_DELETE = _reject_method  # noqa: N815
        do_PATCH = _reject_method  # noqa: N815

        def do_POST(self) -> None:  # noqa: N802 - stdlib API
            if urlsplit(self.path).path not in _ALLOWED_PATHS:
                self._send_text(404, "Not found\n")
                return
            origin = self.headers.get("origin")
            if origin is not None and not _is_loopback_origin(origin):
                # 浏览器跨源请求：拒绝且不回 CORS 头，页面读不到响应也发不出工具调用。
                self._send_text(403, "Cross-origin requests are not allowed.\n")
                return
            # http.client 在缺省时把 content-type 报成 text/plain，缺头同样落到这里：
            # 浏览器能伪造的简单请求类型（text/plain、form）一律挡在解析之前。
            if self.headers.get_content_type() != "application/json":
                self._send_text(415, "Expected content-type: application/json\n")
                return
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length).decode("utf-8")
            try:
                request = json.loads(raw)
                response = asyncio.run(_handle_request(request, root))
            except Exception as exc:  # noqa: BLE001
                response = {"jsonrpc": "2.0", "error": {"message": str(exc)}}
            body = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json")

        def log_message(self, _format: str, *args: Any) -> None:
            return

    return Handler


def _make_server(port: int, root: str) -> ThreadingHTTPServer:
    """Build the loopback server without serving; port 0 picks a free port."""
    return ThreadingHTTPServer(("127.0.0.1", port), _handler_class(root))


def serve_http(port: int = 3000, cwd: str | None = None) -> None:
    root = str(Path(cwd or ".").resolve())
    server = _make_server(port, root)
    print(f"NexusCLI MCP server listening on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()
