"""Tests for plugin *packages*: user-level plugin directories, the
``<plugin>.<component>`` namespace, tool components (name/description/
parameters/handler) going through the same ToolExecutor approval chain, and
the "discovery reads manifests only, never executes code" guarantee.

Style mirrors tests/test_plugins.py (fresh plugin cache per test, HOME +
USERPROFILE + Path.home all redirected).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexuscli.agent.subagent import load_subagents
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig, PermissionsConfig
from nexuscli.plugins import (
    PluginToolDecl,
    component_ref,
    load_plugins,
    register_plugin_tools,
    reset_plugins_cache,
)
from nexuscli.tools import get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _fresh_plugin_cache():
    """Clear the module-level plugin cache around every test.

    load_plugins caches per (resolved cwd, resolved home) in a module-level
    dict; a stale entry from one test would leak into the next (same pattern
    as tests/test_plugins.py).
    """
    reset_plugins_cache()
    yield
    reset_plugins_cache()


@pytest.fixture
def plugin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the user-level home into a temp directory.

    HOME and USERPROFILE are both redirected (win32 Path.home() prefers
    USERPROFILE over HOME) and Path.home itself is patched, mirroring
    tests/test_plugins.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _make_plugin(base: Path, name: str = "acme", description: str | None = None) -> Path:
    """Create ``<base>/.nexuscli/plugins/<name>/`` with a valid manifest."""
    root = base / ".nexuscli" / "plugins" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps(
            {"name": name, "description": description or f"{name} 插件包", "version": "1.0.0"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def _write_tool_decl(
    plugin: Path,
    name: str,
    description: str = "插件工具",
    parameters: dict | None = None,
    handler: str = "handler.py:run",
    filename: str | None = None,
) -> None:
    tools_dir = plugin / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    decl: dict = {
        "name": name,
        "description": description,
        "parameters": parameters
        if parameters is not None
        else {"type": "object", "properties": {}},
    }
    if handler is not None:
        decl["handler"] = handler
    (tools_dir / f"{filename or name}.json").write_text(
        json.dumps(decl, ensure_ascii=False), encoding="utf-8"
    )


_ECHO_HANDLER = """\
import pathlib

# 模块体副作用：仅应在注册（register_plugin_tools）时执行，发现阶段绝不能触发。
pathlib.Path({import_marker!r}).write_text("imported", encoding="utf-8")


def run(payload, context):
    text = str(payload.get("text", ""))
    pathlib.Path({call_marker!r}).write_text(text, encoding="utf-8")
    return "echo:" + text
"""


def _write_echo_handler(
    plugin: Path, import_marker: Path, call_marker: Path, filename: str = "handler.py"
) -> None:
    (plugin / filename).write_text(
        _ECHO_HANDLER.format(import_marker=str(import_marker), call_marker=str(call_marker)),
        encoding="utf-8",
    )


def _echo_call(name: str, text: str = "hi") -> dict:
    return {
        "id": "call-1",
        "function": {"name": name, "arguments": json.dumps({"text": text})},
    }


def _context(
    proj: Path,
    callback=None,
    permissions: PermissionsConfig | None = None,
) -> ToolContext:
    config = NexusCliConfig()
    if permissions is not None:
        config.permissions = permissions
    return ToolContext(cwd=str(proj), config=config, approval_callback=callback)


# ---------------------------------------------------------------------------
# 用户级插件目录（~/.nexuscli/plugins/<name>）
# ---------------------------------------------------------------------------


def test_user_level_plugins_are_discovered(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    user_plugin = _make_plugin(plugin_home, "userplug", description="用户级插件包")
    (user_plugin / "agents").mkdir()
    (user_plugin / "agents" / "usr-agent.md").write_text(
        "---\nname: usr-agent\ndescription: 用户级代理\n---\n用户级提示词",
        encoding="utf-8",
    )
    _make_plugin(proj, "acme")

    plugins = load_plugins(str(proj), home=plugin_home)

    # 两个级别都被发现，按名字排序返回；用户级 root 指向 home
    assert [item.name for item in plugins] == ["acme", "userplug"]
    assert plugins[1].root == user_plugin
    assert plugins[1].manifest.description == "用户级插件包"

    # home=None 走 Path.home()（被 patch 指向同一目录）：同一缓存条目
    assert load_plugins(str(proj)) is plugins

    # 既有消费者不改签名即可拿到用户级插件组件（load_plugins 内部解析 home）
    definitions = load_subagents(str(proj))
    assert definitions["usr-agent"].prompt == "用户级提示词"


def test_project_plugin_wins_same_name_conflict(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    user_plugin = _make_plugin(plugin_home, "acme", description="用户版")
    _write_tool_decl(user_plugin, "echo", description="用户版工具")
    proj_plugin = _make_plugin(proj, "acme", description="项目版")
    import_marker = tmp_path / "import-marker"
    call_marker = tmp_path / "call-marker"
    _write_echo_handler(proj_plugin, import_marker, call_marker)
    _write_tool_decl(proj_plugin, "echo", description="项目版工具")

    # 同名插件：项目级目录胜出，列表里只保留一份
    plugins = load_plugins(str(proj), home=plugin_home)
    assert [item.name for item in plugins] == ["acme"]
    assert plugins[0].root == proj_plugin
    assert plugins[0].manifest.description == "项目版"
    assert [decl.description for decl in plugins[0].tools] == ["项目版工具"]

    # 命名空间冲突落到注册层也一样：acme.echo 只注册一次，跑的是项目级 handler
    registry = ToolRegistry()
    assert register_plugin_tools(registry, str(proj), home=plugin_home) == ["acme.echo"]
    result = asyncio.run(registry.get("acme.echo").execute({"text": "x"}, _context(proj)))
    assert result.content == "echo:x"
    assert call_marker.read_text(encoding="utf-8") == "x"


# ---------------------------------------------------------------------------
# <plugin>.<component> 命名空间
# ---------------------------------------------------------------------------


def test_component_ref_namespacing():
    assert component_ref("acme", "echo") == "acme.echo"
    assert component_ref("acme", "read_file") == "acme.read_file"


def test_tool_components_register_namespaced_without_shadowing(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    acme = _make_plugin(proj, "acme")
    _write_tool_decl(acme, "read_file")  # 故意撞内置工具名
    _write_echo_handler(acme, tmp_path / "m1", tmp_path / "c1")
    beta = _make_plugin(proj, "beta")
    _write_tool_decl(beta, "echo")
    (beta / "handler.py").write_text(
        "def run(payload, context):\n    return 'beta-echo'\n", encoding="utf-8"
    )

    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    builtin_read_file = registry.get("read_file")

    registered = register_plugin_tools(registry, str(proj), home=plugin_home)

    # 两个插件的同名工具互不冲突，内置 read_file 也没有被遮蔽
    assert registered == ["acme.read_file", "beta.echo"]
    assert registry.get("read_file") is builtin_read_file
    assert registry.get("acme.read_file").name == "acme.read_file"
    assert registry.get("beta.echo").name == "beta.echo"


# ---------------------------------------------------------------------------
# tool 组件：同一 ToolExecutor 审批链，项目级插件默认需要审批
# ---------------------------------------------------------------------------


def _make_tool_plugin(tmp_path: Path) -> tuple[Path, Path, Path]:
    proj = tmp_path / "proj"
    proj.mkdir()
    plugin = _make_plugin(proj, "acme")
    import_marker = tmp_path / "import-marker"
    call_marker = tmp_path / "call-marker"
    _write_echo_handler(plugin, import_marker, call_marker)
    _write_tool_decl(
        plugin,
        "echo",
        description="把输入原样返回",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    )
    return proj, import_marker, call_marker


def test_plugin_tool_defaults_fail_closed(tmp_path, plugin_home):
    proj, _, _ = _make_tool_plugin(tmp_path)
    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)

    tool = registry.get("acme.echo")
    # 副作用未知 → 非只读、非并发安全、medium；项目级插件默认需要审批
    assert tool.requires_approval is True
    assert tool.is_read_only is False
    assert tool.is_concurrency_safe is False
    assert tool.danger_level == "medium"
    assert tool.required_keys == ["text"]
    definition = tool.definition()["function"]
    assert definition["name"] == "acme.echo"
    assert definition["description"] == "把输入原样返回"
    assert definition["parameters"]["required"] == ["text"]


def test_plugin_tool_denied_without_approval(tmp_path, plugin_home):
    proj, import_marker, call_marker = _make_tool_plugin(tmp_path)
    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)
    executor = ToolExecutor(registry)

    prompts: list[dict] = []

    def deny_callback(payload: dict):
        prompts.append(payload)
        return "deny"

    results = asyncio.run(
        executor.execute_all([_echo_call("acme.echo")], _context(proj, deny_callback))
    )

    # 审批链先于执行：callback 被询问（带工具名/危险级/描述），拒绝后 handler 未跑
    assert len(prompts) == 1
    assert prompts[0]["tool_name"] == "acme.echo"
    assert prompts[0]["danger_level"] == "medium"
    assert prompts[0]["description"] == "把输入原样返回"
    assert results[0].is_error is True
    assert "denied by approval policy" in results[0].content
    assert not call_marker.exists()


def test_plugin_tool_runs_after_approval(tmp_path, plugin_home):
    proj, import_marker, call_marker = _make_tool_plugin(tmp_path)
    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)
    executor = ToolExecutor(registry)

    results = asyncio.run(
        executor.execute_all(
            [_echo_call("acme.echo", text="hello")], _context(proj, lambda payload: "approve")
        )
    )

    assert results[0].is_error is False
    assert results[0].content == "echo:hello"
    assert call_marker.read_text(encoding="utf-8") == "hello"


def test_plugin_tool_denied_without_interactive_approver(tmp_path, plugin_home):
    proj, _, call_marker = _make_tool_plugin(tmp_path)
    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)
    executor = ToolExecutor(registry)

    # 单条 -p 语义：没有审批回调时 fail closed，绝不静默放行
    results = asyncio.run(executor.execute_all([_echo_call("acme.echo")], _context(proj)))

    assert results[0].is_error is True
    assert "no interactive approver" in results[0].content
    assert not call_marker.exists()


def test_plugin_tool_allow_rule_is_the_explicit_exemption(tmp_path, plugin_home):
    proj, _, call_marker = _make_tool_plugin(tmp_path)
    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)
    executor = ToolExecutor(registry)

    # 显式 allow 规则是用户的长期信任决定：不再弹审批，直接放行（同一审批链内）
    prompts: list[dict] = []

    def callback(payload: dict):
        prompts.append(payload)
        return "deny"

    context = _context(proj, callback, permissions=PermissionsConfig(allow=["acme.echo"]))
    results = asyncio.run(executor.execute_all([_echo_call("acme.echo")], context))

    assert results[0].is_error is False
    assert results[0].content == "echo:hi"
    assert prompts == []  # allow 规则跳过 HITL 提示
    assert call_marker.read_text(encoding="utf-8") == "hi"


# ---------------------------------------------------------------------------
# 发现阶段只读 manifest，绝不执行代码
# ---------------------------------------------------------------------------


def test_discovery_only_reads_manifests_and_never_executes_code(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    acme = _make_plugin(proj, "acme")
    import_marker = tmp_path / "import-marker"
    call_marker = tmp_path / "call-marker"
    _write_echo_handler(acme, import_marker, call_marker)
    _write_tool_decl(acme, "echo")
    broken = _make_plugin(proj, "broken")
    _write_tool_decl(broken, "bad")
    (broken / "handler.py").write_text('raise RuntimeError("boom")\n', encoding="utf-8")

    # 发现阶段：handler 模块体（含 broken 的 import 即炸）完全不执行，也不发警告
    import warnings as warnings_mod

    with warnings_mod.catch_warnings(record=True) as record:
        warnings_mod.simplefilter("always")
        plugins = load_plugins(str(proj), home=plugin_home)
    assert [item.name for item in plugins] == ["acme", "broken"]
    assert record == []
    assert not import_marker.exists()
    assert not call_marker.exists()

    # 注册阶段才导入 handler：broken 只跳过自己的工具并告警，acme 照常注册
    registry = ToolRegistry()
    with pytest.warns(UserWarning, match="nexuscli:plugins"):
        registered = register_plugin_tools(registry, str(proj), home=plugin_home)
    assert registered == ["acme.echo"]
    assert import_marker.read_text(encoding="utf-8") == "imported"
    assert not call_marker.exists()  # 注册 ≠ 调用


def test_plugin_tool_handler_may_be_async_and_return_tool_result(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    plugin = _make_plugin(proj, "acme")
    tools_dir = plugin / "tools"
    tools_dir.mkdir()
    (tools_dir / "async-echo.json").write_text(
        json.dumps(
            {
                "name": "async-echo",
                "description": "异步插件工具",
                "parameters": {"type": "object", "properties": {}},
                "handler": "handler.py:run",
            }
        ),
        encoding="utf-8",
    )
    (plugin / "handler.py").write_text(
        "from nexuscli.tools.base import ToolResult\n"
        "\n"
        "async def run(payload, context):\n"
        "    return ToolResult(content='async-tool-result', is_error=False)\n",
        encoding="utf-8",
    )

    registry = ToolRegistry()
    register_plugin_tools(registry, str(proj), home=plugin_home)
    result = asyncio.run(registry.get("acme.async-echo").execute({}, _context(proj)))
    assert result.content == "async-tool-result"
    assert result.is_error is False


# ---------------------------------------------------------------------------
# 坏声明与 handler 越界：warn-and-skip，不拖垮其余插件/工具
# ---------------------------------------------------------------------------


def test_bad_tool_declarations_warn_and_skip(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    plugin = _make_plugin(proj, "acme")
    tools_dir = plugin / "tools"
    tools_dir.mkdir()
    (tools_dir / "not-json.json").write_text("{not json", encoding="utf-8")
    (tools_dir / "not-object.json").write_text("[]", encoding="utf-8")
    (tools_dir / "no-name.json").write_text(
        json.dumps({"description": "d", "parameters": {}, "handler": "handler.py:run"}),
        encoding="utf-8",
    )
    (tools_dir / "bad-name.json").write_text(
        json.dumps(
            {"name": "bad name!", "description": "d", "parameters": {}, "handler": "handler.py:run"}
        ),
        encoding="utf-8",
    )
    (tools_dir / "no-params.json").write_text(
        json.dumps(
            {"name": "np", "description": "d", "parameters": "x", "handler": "handler.py:run"}
        ),
        encoding="utf-8",
    )
    (tools_dir / "bad-handler.json").write_text(
        json.dumps({"name": "bh", "description": "d", "parameters": {}, "handler": "run"}),
        encoding="utf-8",
    )
    (tools_dir / "dup.json").write_text(
        json.dumps(
            {"name": "dup", "description": "第一份", "parameters": {}, "handler": "handler.py:run"}
        ),
        encoding="utf-8",
    )
    (tools_dir / "dup2.json").write_text(
        json.dumps(
            {"name": "dup", "description": "第二份", "parameters": {}, "handler": "handler.py:run"}
        ),
        encoding="utf-8",
    )
    (plugin / "handler.py").write_text(
        "def run(payload, context):\n    return 'ok'\n", encoding="utf-8"
    )
    _write_tool_decl(plugin, "good")

    with pytest.warns(UserWarning, match="nexuscli:plugins") as record:
        plugins = load_plugins(str(proj), home=plugin_home)

    # 每份坏声明各自告警；合法声明不受影响
    assert len(record) >= 7
    assert [decl.name for decl in plugins[0].tools] == ["dup", "good"]

    registry = ToolRegistry()
    registered = register_plugin_tools(registry, str(proj), home=plugin_home)
    assert registered == ["acme.dup", "acme.good"]


def test_tool_handler_cannot_escape_plugin_root(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    proj.mkdir()
    plugin = _make_plugin(proj, "acme")
    _write_tool_decl(plugin, "evil", handler="../outside.py:run")
    outside = proj / ".nexuscli" / "plugins" / "outside.py"
    outside.write_text("def run(payload, context):\n    return 'escaped'\n", encoding="utf-8")

    plugins = load_plugins(str(proj), home=plugin_home)
    assert isinstance(plugins[0].tools[0], PluginToolDecl)  # 发现阶段只看声明，不看文件

    registry = ToolRegistry()
    with pytest.warns(UserWarning, match="escapes the plugin root"):
        assert register_plugin_tools(registry, str(proj), home=plugin_home) == []
    assert registry.get("acme.evil") is None


# ---------------------------------------------------------------------------
# 生产装配路径：build_tool_registry 把插件工具组件并入工具表（0030 接线）
# ---------------------------------------------------------------------------


def _production_config() -> NexusCliConfig:
    config = NexusCliConfig()
    config.features.mcp = False  # 与本测试无关；不拉起 MCP 子进程
    return config


def test_build_tool_registry_includes_plugin_tools_with_approval_chain(tmp_path, plugin_home):
    """生产装配（REPL / -p / runtime 共用路径）包含 <plugin>.<tool> 且走审批链。"""
    proj, import_marker, call_marker = _make_tool_plugin(tmp_path)

    registry, manager = asyncio.run(build_tool_registry(config=_production_config(), cwd=str(proj)))

    # 注册进了生产工具表：命名空间名、与内置工具并存、requires_approval 保持
    assert "acme.echo" in registry.list_names()
    assert "read_file" in registry.list_names()  # 内置工具不受影响
    tool = registry.get("acme.echo")
    assert tool.requires_approval is True
    # 注册即导入 handler（模块体副作用落盘），但从未调用（call marker 不存在）
    assert import_marker.read_text(encoding="utf-8") == "imported"
    assert not call_marker.exists()
    # 与内置工具走同一 ToolExecutor 审批链：无审批回调 → fail closed 拒绝
    executor = ToolExecutor(registry)
    results = asyncio.run(executor.execute_all([_echo_call("acme.echo")], _context(proj)))
    assert results[0].is_error is True
    assert "no interactive approver" in results[0].content
    assert not call_marker.exists()
    assert manager is None  # features.mcp 关闭时的既有契约


def test_build_tool_registry_warns_and_survives_broken_plugin_tool_handler(tmp_path, plugin_home):
    """坏 handler 只告警并跳过自己的工具，其余插件与启动流程不受影响。"""
    proj = tmp_path / "proj"
    proj.mkdir()
    acme = _make_plugin(proj, "acme")
    _write_echo_handler(acme, tmp_path / "import-marker", tmp_path / "call-marker")
    _write_tool_decl(acme, "echo", description="把输入原样返回")
    broken = _make_plugin(proj, "broken")
    _write_tool_decl(broken, "bad")
    (broken / "handler.py").write_text('raise RuntimeError("boom")\n', encoding="utf-8")

    with pytest.warns(UserWarning, match="nexuscli:plugins"):
        registry, _manager = asyncio.run(
            build_tool_registry(config=_production_config(), cwd=str(proj))
        )

    assert "acme.echo" in registry.list_names()
    assert registry.get("broken.bad") is None
    assert "read_file" in registry.list_names()  # 启动没有被坏插件拖垮
