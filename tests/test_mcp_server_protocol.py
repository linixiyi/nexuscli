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

from nexuscli.mcp.server import MCP_PROTOCOL_VERSION, _handle_request


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
