"""REPL /mcp lists configured servers instead of only printing the serve hint.

Round-1 feedback (spec 2026-10-18_verify-zero-and-yellow): a real stdio server
was connected and its tools registered, but /mcp only printed "Use `nexuscli
mcp serve …`", so the user had no way to see what was attached from inside
the REPL.
"""

from __future__ import annotations

from rich.console import Console

from nexuscli.entrypoints.repl import _mcp_status_command
from nexuscli.mcp.config import McpServerSpec


class _ManagerStub:
    def __init__(self, specs, last_errors=None):
        self.specs = specs
        self.last_errors = last_errors or {}


class _RegistryStub:
    def __init__(self, names):
        self._names = names

    def list_names(self):
        return list(self._names)


def _output(manager, registry) -> str:
    console = Console(record=True, width=120)
    _mcp_status_command(console, manager, registry)
    return console.export_text()


def test_no_servers_prints_config_hint() -> None:
    text = _output(_ManagerStub({}), None)
    assert "No MCP servers configured" in text
    assert "mcp.json" in text


def test_feature_disabled_message() -> None:
    text = _output(None, None)
    assert "MCP support is disabled" in text


def test_lists_servers_with_tool_counts_errors_and_disabled() -> None:
    manager = _ManagerStub(
        {
            "fs": McpServerSpec(name="fs", type="stdio", command="npx", args=["-y", "server"]),
            "off": McpServerSpec(name="off", type="stdio", command="bin", enabled=False),
        },
        last_errors={"fs": "connection refused"},
    )
    registry = _RegistryStub(["mcp__fs__read", "mcp__fs__write", "read_file"])
    text = _output(manager, registry)
    assert "fs" in text and "2 tools" in text
    assert "disabled" in text
    assert "connection refused" in text
