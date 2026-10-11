from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscli.mcp import McpClientManager
from nexuscli.mcp.config import _spec_from_raw, load_mcp_server_specs


@pytest.fixture
def filter_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home.

    A HOME-only patch leaks on Windows (Path.home() reads USERPROFILE there):
    load_mcp_server_specs would still merge the real user-level
    ``~/.nexuscli/mcp.json`` into the specs. Triple patch, mirroring
    tests/test_background.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


@dataclass
class _FakeTool:
    name: str
    inputSchema: dict = field(default_factory=lambda: {"type": "object", "properties": {}})
    description: str | None = None
    annotations: None = None


class _FakeSession:
    def __init__(self, tools: list[_FakeTool]):
        self._tools = tools

    async def list_tools(self):
        return SimpleNamespace(tools=self._tools)


def _manager_with_remote_tools(
    tmp_path, monkeypatch, raw: dict, tools: list[_FakeTool]
) -> McpClientManager:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (tmp_path / ".nexuscli").mkdir(exist_ok=True)
    (tmp_path / ".nexuscli" / "mcp.json").write_text(
        json.dumps({"mcpServers": raw}), encoding="utf-8"
    )
    manager = McpClientManager(tmp_path)

    @asynccontextmanager
    async def fake_session(spec):
        yield _FakeSession(tools)

    monkeypatch.setattr(manager, "_session", fake_session)
    return manager


def test_spec_from_raw_parses_tool_filters(tmp_path, filter_home):
    spec = _spec_from_raw(
        "srv",
        {"command": "python", "allowed_tools": ["echo", "search*"], "blocked_tools": ["admin*"]},
        tmp_path,
    )
    assert spec.allowed_tools == ["echo", "search*"]
    assert spec.blocked_tools == ["admin*"]


def test_spec_from_raw_defaults_to_empty_filters(tmp_path, filter_home):
    spec = _spec_from_raw("srv", {"command": "python"}, tmp_path)
    assert spec.allowed_tools == []
    assert spec.blocked_tools == []


def test_non_list_filter_values_degrade_to_no_filtering(tmp_path, monkeypatch):
    """A common typo — writing the list as a plain string — must not filter.

    Iterating the string would split ``"echo"`` into the whitelist
    ``["e", "c", "h", "o"]``, silently hiding every remote tool on the server
    with no error at all. Only real lists are accepted; anything else degrades
    to "not configured" (all remote tools registered).
    """
    tools = [_FakeTool("echo"), _FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "allowed_tools": "echo", "blocked_tools": "admin*"}},
        tools,
    )
    spec = manager.specs["fake"]
    assert spec.allowed_tools == []
    assert spec.blocked_tools == []
    wrapped = asyncio.run(manager._tools_for_server(spec))
    assert [tool.name for tool in wrapped] == [
        "mcp__fake__echo",
        "mcp__fake__admin_delete",
    ]


def test_load_mcp_server_specs_reads_filters(tmp_path, filter_home):
    (tmp_path / ".nexuscli").mkdir()
    (tmp_path / ".nexuscli" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fake": {
                        "command": "python",
                        "allowed_tools": ["echo"],
                        "blocked_tools": ["admin*"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    specs = load_mcp_server_specs(tmp_path)
    assert specs["fake"].allowed_tools == ["echo"]
    assert specs["fake"].blocked_tools == ["admin*"]


def test_allowed_tools_keeps_only_whitelisted(tmp_path, monkeypatch):
    tools = [_FakeTool("echo"), _FakeTool("search_web"), _FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "allowed_tools": ["echo", "search*"]}},
        tools,
    )
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    assert [tool.name for tool in wrapped] == ["mcp__fake__echo", "mcp__fake__search_web"]


def test_blocked_tools_excludes_matches(tmp_path, monkeypatch):
    tools = [_FakeTool("echo"), _FakeTool("search_web"), _FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "blocked_tools": ["admin*"]}},
        tools,
    )
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    assert [tool.name for tool in wrapped] == ["mcp__fake__echo", "mcp__fake__search_web"]


def test_wildcard_and_exact_matching(tmp_path, monkeypatch):
    tools = [_FakeTool("echo"), _FakeTool("echo2"), _FakeTool("search_web")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "allowed_tools": ["echo", "*_web"]}},
        tools,
    )
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    # exact name "echo" must not swallow "echo2"; wildcard "*_web" matches "search_web"
    assert [tool.name for tool in wrapped] == ["mcp__fake__echo", "mcp__fake__search_web"]


def test_allowed_takes_priority_over_blocked(tmp_path, monkeypatch):
    tools = [_FakeTool("echo"), _FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "allowed_tools": ["echo"], "blocked_tools": ["echo"]}},
        tools,
    )
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    assert [tool.name for tool in wrapped] == ["mcp__fake__echo"]


def test_all_remote_tools_filtered_keeps_virtual_tools(tmp_path, monkeypatch):
    tools = [_FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python", "blocked_tools": ["admin*"]}},
        tools,
    )
    loaded = asyncio.run(manager.load_tools())
    names = [tool.name for tool in loaded]
    assert not any("admin_delete" in name for name in names)
    assert "mcp__fake__list_resources" in names
    assert "mcp__fake__read_resource" in names
    assert "mcp__fake__list_prompts" in names
    assert "mcp__fake__get_prompt" in names
    assert manager.last_errors == {}


def test_unfiltered_spec_registers_all_remote_tools(tmp_path, monkeypatch):
    tools = [_FakeTool("echo"), _FakeTool("admin_delete")]
    manager = _manager_with_remote_tools(
        tmp_path,
        monkeypatch,
        {"fake": {"command": "python"}},
        tools,
    )
    wrapped = asyncio.run(manager._tools_for_server(manager.specs["fake"]))
    assert [tool.name for tool in wrapped] == ["mcp__fake__echo", "mcp__fake__admin_delete"]
