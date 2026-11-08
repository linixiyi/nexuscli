"""The MCP server must satisfy the protocol, not just answer curl.

Every official SDK client validates the initialize reply against
InitializeResult, which requires protocolVersion, capabilities and
serverInfo.version. The server used to return {"serverInfo": {"name": ...}}
only: hand-written tools/list probes passed while real clients failed at
handshake with three validation errors, so the "NexusCLI as MCP server"
feature was unusable from the ecosystem it targets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.config import load_config
from nexuscli.mcp.server import MCP_PROTOCOL_VERSION, _handle_request
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema


def _initialize() -> dict:
    return asyncio.run(
        _handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, ".")
    )


def test_initialize_declares_protocol_version() -> None:
    result = _initialize()["result"]
    assert result["protocolVersion"] == MCP_PROTOCOL_VERSION


def test_initialize_declares_capabilities() -> None:
    result = _initialize()["result"]
    assert "tools" in result["capabilities"]


def test_initialize_reports_server_version() -> None:
    from nexuscli import __version__

    result = _initialize()["result"]
    assert result["serverInfo"]["name"] == "nexuscli"
    assert result["serverInfo"]["version"] == __version__


def test_initialized_notification_gets_an_empty_result() -> None:
    reply = asyncio.run(
        _handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized", "params": {}}, "."
        )
    )
    assert reply["result"] == {}


def test_tools_list_still_returns_the_builtin_tools() -> None:
    from nexuscli.tools.builtins import get_builtin_tools

    reply = asyncio.run(
        _handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}, ".")
    )
    assert len(reply["result"]["tools"]) == len(get_builtin_tools())


def test_tools_call_forces_never_on_a_copy_and_leaves_shared_config_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tools/call must not write its unattended hitl_mode into a shared config.

    The unattended server forces hitl_mode="never" before dispatching through
    ToolExecutor, and it used to do that in place on the very object
    load_config() returned: whoever else held that config would silently
    inherit unattended semantics. The rewrite now lands on a deepcopy, so the
    config load_config handed out keeps its original hitl_mode while the
    executor still sees "never".
    """
    import nexuscli.mcp.server as server_module

    shared = load_config(project_root=str(tmp_path), env={})
    shared.policy.hitl_mode = "auto"
    seen: list[ToolContext] = []

    async def _record_context(_payload: dict[str, Any], context: ToolContext) -> ToolResult:
        seen.append(context)
        return ToolResult(content="ok")

    stub = Tool(
        name="record_context",
        description="Records the config its execution context carried.",
        parameters=object_schema({}, []),
        handler=_record_context,
    )
    monkeypatch.setattr(server_module, "load_config", lambda project_root: shared)
    monkeypatch.setattr(server_module, "get_builtin_tools", lambda: [stub])

    reply = asyncio.run(
        _handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "record_context", "arguments": {}},
            },
            str(tmp_path),
        )
    )

    assert reply["result"]["isError"] is False
    # Server 内部仍为 never，且落在 deepcopy 的副本上——不是共享的那个对象。
    assert seen[0].config is not shared
    assert seen[0].config.policy.hitl_mode == "never"
    # load_config() 返回的原对象未被改写。
    assert shared.policy.hitl_mode == "auto"
