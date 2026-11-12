"""项目层 MCP server 信任门与 stdio 环境清洗（task-0002）。

项目层 ``.nexuscli/mcp.json`` 的 enabled server 在无项目信任指纹时不得进入
有效列表（不 spawn），只保留用户层 server，并给一次可见 warning；交互式
确认后可记忆（复用 hooks/trust.py 的 canonical JSON + sha256 模式，独立存
``~/.nexuscli/mcp-trust.json``）；无 TTY（``-p``/serve 同路径）一律
fail-closed。stdio 子进程 env 不再是 ``{**os.environ, **spec.env}`` 全量继承，
而是 ``sanitize_environment(overrides=spec.env)``：密钥形态变量被剔除，
``spec.env`` 显式值保留。非 dict/坏配置行为不回归。

覆盖（tasks.md ①-⑤）:
- ① 无信任的项目 mcp.json 不 spawn；
- ② 信任后可加载（交互确认记忆、confirm seam、篡改重新武装、disabled-only 不设门）；
- ③ 用户层 server 不受影响；
- ④ 子进程 env 不含 *_API_KEY/token 形态，spec.env 显式值保留；
- ⑤ 非 dict/坏配置行为不回归。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscli.mcp import McpClientManager
from nexuscli.mcp import client as mcp_client
from nexuscli.mcp.config import (
    McpConfigError,
    McpTrustWarning,
    load_mcp_server_specs,
    read_mcp_config_entries,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTty(io.StringIO):
    """stdin double that reports isatty() True and carries canned keystrokes."""

    def isatty(self) -> bool:
        return True


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the fail-closed branch: stdin without a TTY (as under -p/serve)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO())


def _tty(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(sys, "stdin", _FakeTty(answer))


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user layer and the trust store at a temp home under *tmp_path*.

    Both HOME and USERPROFILE are set (win32 ``Path.home()`` prefers
    USERPROFILE; same stance as tests/test_project_trust.py and
    tests/test_hooks_trust.py).
    """
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _write_mcp_json(path: Path, servers: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _spawner_server(marker: Path) -> dict[str, object]:
    """An enabled stdio server that would leave a marker file if ever spawned."""
    return {
        "command": sys.executable,
        "args": ["-c", f"open({str(marker)!r}, 'w').close()"],
        "env": {"PROJ_FLAG": "from-project"},
    }


def _trust_warning_messages(recorded: object) -> list[str]:
    return [
        str(warning.message)
        for warning in recorded  # type: ignore[attr-defined]
        if issubclass(warning.category, McpTrustWarning)  # type: ignore[attr-defined]
    ]


class _FakeRemoteSession:
    """Stands in for the SDK session so no real child process is needed."""

    def __init__(self, read: object, write: object) -> None:
        _ = read, write

    async def initialize(self) -> None:
        return None

    async def list_tools(self) -> SimpleNamespace:
        return SimpleNamespace(tools=[])


def _capture_stdio_params(
    monkeypatch: pytest.MonkeyPatch, captured: dict[str, object]
) -> None:
    """Replace the SDK stdio transport with one that records StdioServerParameters."""

    @asynccontextmanager
    async def fake_stdio_client(params: object, errlog: object = None):
        captured["params"] = params
        _ = errlog
        yield (None, None)

    monkeypatch.setattr(mcp_client, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp_client, "ClientSession", _FakeRemoteSession)


# ---------------------------------------------------------------------------
# ① 无信任的项目 mcp.json 不 spawn
# ---------------------------------------------------------------------------


def test_untrusted_project_server_never_spawns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_tty(monkeypatch)
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    marker = tmp_path / "spawn-marker"
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"proj-srv": _spawner_server(marker)})

    with pytest.warns(McpTrustWarning) as recorded:
        specs = load_mcp_server_specs(project)
    # 项目层 server 不进入有效列表；一条可见 warning 点名被丢弃的 server。
    assert "proj-srv" not in specs
    assert specs == {}  # 无用户层时有效列表为空
    assert "proj-srv" in _trust_warning_messages(recorded)[0]
    assert not (Path.home() / ".nexuscli" / "mcp-trust.json").exists()  # 拒绝不记忆

    manager = McpClientManager(project)
    tools = asyncio.run(manager.load_tools())
    asyncio.run(manager.aclose())
    assert not any("proj-srv" in tool.name for tool in tools)
    assert manager.last_errors == {}  # 没有任何 spawn 尝试（有尝试会报错）
    assert not marker.exists()  # 子进程从未启动


# ---------------------------------------------------------------------------
# ② 信任后可加载
# ---------------------------------------------------------------------------


def test_interactive_confirm_trusts_and_is_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    servers: dict[str, object] = {"proj-srv": {"command": "python", "env": {"PROJ_FLAG": "1"}}}
    _write_mcp_json(project / ".nexuscli" / "mcp.json", servers)
    _tty(monkeypatch, "y\n")

    specs = load_mcp_server_specs(project)
    assert "proj-srv" in specs
    assert (home / ".nexuscli" / "mcp-trust.json").exists()  # 确认后记忆

    # 记忆后：无 TTY 的后续加载直接生效，且不再告警。
    _no_tty(monkeypatch)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        reloaded = load_mcp_server_specs(project)
    assert "proj-srv" in reloaded
    assert not _trust_warning_messages(caught)


def test_confirm_seam_true_merges_false_drops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"proj-srv": {"command": "python"}})

    specs = load_mcp_server_specs(project, confirm=lambda description: True)
    assert "proj-srv" in specs

    other = tmp_path / "other"
    _write_mcp_json(other / ".nexuscli" / "mcp.json", {"proj-srv": {"command": "python"}})
    with pytest.warns(McpTrustWarning):
        refused = load_mcp_server_specs(other, confirm=lambda description: False)
    assert "proj-srv" not in refused


def test_tampered_project_servers_require_reconfirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"proj-srv": {"command": "python"}})
    _tty(monkeypatch, "y\n")
    assert "proj-srv" in load_mcp_server_specs(project)

    # 篡改项目层内容 → 新指纹未信任 → 继续拦截。
    _no_tty(monkeypatch)
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"evil": {"command": sys.executable}})
    with pytest.warns(McpTrustWarning):
        specs = load_mcp_server_specs(project)
    assert "evil" not in specs


def test_disabled_only_project_layer_needs_no_trust(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """enabled server 才设门：只有 disabled server 的项目层照常进入列表（不 spawn）。"""
    _no_tty(monkeypatch)
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _write_mcp_json(
        project / ".nexuscli" / "mcp.json",
        {"off-srv": {"command": "python", "enabled": False}},
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        specs = load_mcp_server_specs(project)
    assert specs["off-srv"].enabled is False
    assert not _trust_warning_messages(caught)


def test_trusted_project_server_loads_through_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """信任后同一 server 经 McpClientManager 注册（fake session，无真实子进程）。"""
    _no_tty(monkeypatch)
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"proj-srv": {"command": "python"}})
    specs = load_mcp_server_specs(project, confirm=lambda description: True)
    assert "proj-srv" in specs

    manager = McpClientManager(project)

    @asynccontextmanager
    async def fake_session(spec: object):
        yield _FakeRemoteSession(None, None)

    monkeypatch.setattr(manager, "_session", fake_session)
    tools = asyncio.run(manager.load_tools())
    asyncio.run(manager.aclose())
    names = [tool.name for tool in tools]
    assert "mcp__proj-srv__list_resources" in names
    assert manager.last_errors == {}


# ---------------------------------------------------------------------------
# ③ 用户层 server 不受影响
# ---------------------------------------------------------------------------


def test_user_layer_servers_unaffected_by_untrusted_project_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_tty(monkeypatch)
    home = _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    marker = tmp_path / "spawn-marker"
    _write_mcp_json(
        home / ".nexuscli" / "mcp.json",
        {"user-srv": {"command": "python", "env": {"USER_FLAG": "1"}}},
    )
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"evil": _spawner_server(marker)})

    with pytest.warns(McpTrustWarning):
        specs = load_mcp_server_specs(project)
    assert set(specs) == {"user-srv"}  # 未信任时只保留用户层
    assert specs["user-srv"].env == {"USER_FLAG": "1"}

    manager = McpClientManager(project)

    @asynccontextmanager
    async def fake_session(spec: object):
        yield _FakeRemoteSession(None, None)

    monkeypatch.setattr(manager, "_session", fake_session)
    tools = asyncio.run(manager.load_tools())
    asyncio.run(manager.aclose())
    names = [tool.name for tool in tools]
    assert "mcp__user-srv__list_resources" in names  # 用户层照常注册
    assert not any("evil" in name for name in names)
    assert not marker.exists()


def test_untrusted_project_cannot_shadow_user_layer_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同名 server：未信任项目层不得覆盖用户层定义。"""
    _no_tty(monkeypatch)
    home = _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    marker = tmp_path / "spawn-marker"
    _write_mcp_json(home / ".nexuscli" / "mcp.json", {"shared": {"command": "python"}})
    _write_mcp_json(project / ".nexuscli" / "mcp.json", {"shared": _spawner_server(marker)})

    with pytest.warns(McpTrustWarning):
        specs = load_mcp_server_specs(project)
    assert set(specs) == {"shared"}
    assert specs["shared"].env == {}  # 用户层定义（无 env），项目层未混入
    assert not marker.exists()


# ---------------------------------------------------------------------------
# ④ 子进程 env 不含 *_API_KEY/token 形态
# ---------------------------------------------------------------------------


def test_stdio_child_env_is_sanitized_and_keeps_explicit_spec_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_tty(monkeypatch)
    home = _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _write_mcp_json(
        home / ".nexuscli" / "mcp.json",
        {"user-srv": {"command": "python", "env": {"PROJ_FLAG": "keep-me"}}},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-secret-value")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "fake-token-value")
    monkeypatch.setenv("NEXUSCLI_BASE_URL", "https://internal.example")
    monkeypatch.setenv("MONKEY_SECRET", "fake-secret-value")

    captured: dict[str, object] = {}
    _capture_stdio_params(monkeypatch, captured)
    manager = McpClientManager(project)
    asyncio.run(manager.load_tools())
    asyncio.run(manager.aclose())

    params = captured["params"]
    env = params.env  # type: ignore[attr-defined]
    # 密钥形态与内部变量不再全量继承
    assert "OPENAI_API_KEY" not in env
    assert "MY_SERVICE_TOKEN" not in env
    assert "NEXUSCLI_BASE_URL" not in env
    assert "MONKEY_SECRET" not in env
    # spec.env 显式值保留；必留键保留
    assert env["PROJ_FLAG"] == "keep-me"
    assert env["PATH"] == os.environ["PATH"]
    assert env["HOME"] == str(home)


# ---------------------------------------------------------------------------
# ⑤ 非 dict/坏配置行为不回归
# ---------------------------------------------------------------------------


def test_broken_configs_raise_or_skip_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_tty(monkeypatch)
    _isolate_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    config_path = project / ".nexuscli" / "mcp.json"
    config_path.parent.mkdir(parents=True)

    # 坏 JSON：strict 点名文件抛错；宽松路径返回空 dict。
    config_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(McpConfigError) as err:
        read_mcp_config_entries(project)
    assert "mcp.json" in str(err.value)
    assert load_mcp_server_specs(project) == {}

    # 顶层不是 JSON object：strict 抛错；宽松路径容忍为空。
    config_path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(McpConfigError):
        read_mcp_config_entries(project)
    assert load_mcp_server_specs(project) == {}

    # mcpServers 不是 object：strict 抛错。
    config_path.write_text(json.dumps({"mcpServers": [1]}), encoding="utf-8")
    with pytest.raises(McpConfigError):
        read_mcp_config_entries(project)

    # 非 dict 条目：不合并、进 skipped，坏条目不设门也不告警。
    config_path.write_text(
        json.dumps({"mcpServers": {"srv": "not-an-object"}}), encoding="utf-8"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        specs, skipped = read_mcp_config_entries(project)
    assert specs == {}
    assert skipped == ["srv: not an object"]
    assert not _trust_warning_messages(caught)
    assert load_mcp_server_specs(project) == {}

    # 缺文件：正常的"无配置"。
    missing = tmp_path / "empty-proj"
    specs, skipped = read_mcp_config_entries(missing)
    assert specs == {} and skipped == []
