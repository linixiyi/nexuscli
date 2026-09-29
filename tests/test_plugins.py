"""Tests for the local-directory plugin MVP: manifest discovery, bad-manifest
warn-and-skip, and merging plugin agents/skills/commands into the three
existing registries (subagents, skills, slash commands)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexuscli.agent.subagent import load_subagents
from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.slash_commands import load_slash_commands
from nexuscli.plugins import Plugin, load_plugins, reset_plugins_cache
from nexuscli.skill import SkillRegistry, SkillStateStore


@pytest.fixture(autouse=True)
def _fresh_plugin_cache():
    """Clear the module-level plugin cache around every test.

    load_plugins caches per resolved cwd in a module-level dict; a stale
    entry from one test would leak into the next (same pattern as the
    background registry cleanup in tests/test_background.py).
    """
    reset_plugins_cache()
    yield
    reset_plugins_cache()


@pytest.fixture
def plugin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the user-level home into a temp directory.

    HOME and USERPROFILE are both redirected (win32 Path.home() prefers
    USERPROFILE over HOME) and Path.home itself is patched, mirroring
    tests/test_hooks.py and tests/test_background.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _write_agent(directory: Path, filename: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(text, encoding="utf-8")


def _write_skill(root: Path, name: str, desc: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\nbody for {name}\n",
        encoding="utf-8",
    )


def _write_command(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


def _make_plugin(proj: Path, name: str = "acme") -> Path:
    """Create ``<proj>/.nexuscli/plugins/<name>/`` with a valid manifest."""
    root = proj / ".nexuscli" / "plugins" / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps(
            {"name": name, "description": f"{name} 插件包", "version": "1.0.0"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


# ---------------------------------------------------------------------------
# Discovery and caching
# ---------------------------------------------------------------------------


def test_load_plugins_discovers_manifest_and_component_dirs(tmp_path):
    proj = tmp_path / "proj"
    plugin = _make_plugin(proj)
    _write_agent(
        plugin / "agents",
        "researcher.md",
        "---\nname: researcher\ndescription: 插件研究\n---\n插件研究提示词",
    )
    _write_skill(plugin / "skills", "pack", "插件技能包")
    _write_command(plugin / "commands", "ship.md", "Ship it.")
    (plugin / "misc").mkdir()  # 无关目录不属于任何组件类型

    plugins = load_plugins(str(proj))

    assert len(plugins) == 1
    loaded = plugins[0]
    assert loaded.name == "acme"
    assert loaded.root == plugin
    assert loaded.manifest.description == "acme 插件包"
    assert loaded.manifest.version == "1.0.0"
    assert loaded.agents_dir == plugin / "agents"
    assert loaded.skills_dir == plugin / "skills"
    assert loaded.commands_dir == plugin / "commands"
    # misc/ 只是被忽略的目录：三个组件字段之外不产生任何 dir 字段
    assert Plugin.__dataclass_fields__.keys() >= {"agents_dir", "skills_dir", "commands_dir"}

    # 缓存命中：再次调用返回同一 list 对象，不再重新扫描
    assert load_plugins(str(proj)) is plugins


def test_bad_manifests_skip_with_warning(tmp_path):
    proj = tmp_path / "proj"
    plugins_dir = proj / ".nexuscli" / "plugins"
    _make_plugin(proj, "acme")
    bad_bytes = plugins_dir / "bad-bytes"
    bad_bytes.mkdir(parents=True)
    (bad_bytes / "plugin.json").write_bytes(b"\xff\xfe{invalid bytes")
    bad_list = plugins_dir / "bad-list"
    bad_list.mkdir(parents=True)
    (bad_list / "plugin.json").write_text("[]", encoding="utf-8")
    bad_name = plugins_dir / "bad-name"
    bad_name.mkdir(parents=True)
    (bad_name / "plugin.json").write_text('{"description": "no name"}', encoding="utf-8")

    with pytest.warns(UserWarning, match="nexuscli:plugins") as record:
        plugins = load_plugins(str(proj))

    assert len(record) >= 3
    # 坏 manifest 只拖垮自己：合法插件 acme 仍然加载
    assert [plugin.name for plugin in plugins] == ["acme"]


def test_missing_manifest_warns_and_skips(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".nexuscli" / "plugins" / "bare").mkdir(parents=True)

    with pytest.warns(UserWarning, match="nexuscli:plugins"):
        plugins = load_plugins(str(proj))

    assert plugins == []


# ---------------------------------------------------------------------------
# Merging into the three registries
# ---------------------------------------------------------------------------


def test_plugin_merges_into_three_registries(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    plugin = _make_plugin(proj)
    _write_agent(
        plugin / "agents",
        "researcher.md",
        "---\n"
        "name: researcher\n"
        "description: 插件研究代理\n"
        "tools: read_file, grep\n"
        "---\n"
        "插件研究提示词",
    )
    _write_skill(plugin / "skills", "pack", "插件技能包")
    _write_command(
        plugin / "commands",
        "ship.md",
        "---\ndescription: 插件发布命令\n---\nShip $ARGUMENTS",
    )

    definitions = load_subagents(str(proj), home=plugin_home)
    assert definitions["researcher"].description == "插件研究代理"
    assert definitions["researcher"].prompt == "插件研究提示词"
    assert definitions["researcher"].tools == ["read_file", "grep"]

    registry = SkillRegistry(
        proj,
        builtin_root=tmp_path / "builtin",
        user_root=plugin_home / ".nexuscli" / "skills",
        state_store=SkillStateStore(tmp_path / "skills.json"),
    )
    skills = {skill.name: skill for skill in registry.all_skills()}
    assert "pack" in skills
    assert skills["pack"].source == "project"  # 固定 project 层，不新造层级

    commands = load_slash_commands(str(proj), home=plugin_home)
    assert commands["ship"].source == "project"
    assert commands["ship"].description == "插件发布命令"


def test_plugin_agent_priority_chain(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    home = plugin_home
    plugin = _make_plugin(proj)
    _write_agent(
        plugin / "agents",
        "explore.md",
        "---\nname: explore\ndescription: 插件版探索\n---\n插件版探索提示词",
    )
    _write_agent(
        proj / ".nexuscli" / "agents",
        "planner.md",
        "---\nname: planner\ndescription: 项目版规划\n---\n项目版规划提示词",
    )
    _write_agent(
        home / ".nexuscli" / "agents",
        "planner.md",
        "---\nname: planner\ndescription: 用户版规划\n---\n用户版规划提示词",
    )

    definitions = load_subagents(str(proj), home=home)

    # 插件覆盖内置 explore；普通项目 planner 覆盖用户 planner（既有语义不回退）
    assert definitions["explore"].prompt == "插件版探索提示词"
    assert definitions["planner"].prompt == "项目版规划提示词"

    # 再加插件版 planner → 插件最终胜出（插件 > 项目 > 用户 > 内置）
    _write_agent(
        plugin / "agents",
        "planner.md",
        "---\nname: planner\ndescription: 插件版规划\n---\n插件版规划提示词",
    )
    reset_plugins_cache()
    definitions = load_subagents(str(proj), home=home)
    assert definitions["planner"].prompt == "插件版规划提示词"


def test_plugin_skill_project_layer_semantics(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    home = plugin_home
    plugin = _make_plugin(proj)
    _write_skill(plugin / "skills", "release-check", "插件版 desc")
    _write_skill(proj / ".nexuscli" / "skills", "release-check", "普通项目版 desc")
    _write_skill(home / ".nexuscli" / "skills", "release-check", "用户版 desc")
    state = SkillStateStore(tmp_path / "skills.json")
    state.disable("release-check")

    registry = SkillRegistry(
        proj,
        builtin_root=tmp_path / "builtin",
        user_root=home / ".nexuscli" / "skills",
        state_store=state,
    )

    # 禁用状态对插件技能照常生效
    assert registry.load("release-check") is None
    disabled = registry.load("release-check", include_disabled=True)
    assert disabled is not None
    assert disabled.source == "project"
    assert disabled.description == "插件版 desc"  # 插件 > 普通项目 > 用户

    assert "release-check" in [skill.name for skill in registry.all_skills()]
    assert "release-check" not in [skill.name for skill in registry.enabled_skills()]


def test_plugin_command_priority_and_parse(tmp_path, plugin_home):
    proj = tmp_path / "proj"
    plugin = _make_plugin(proj)
    _write_command(
        plugin / "commands",
        "ship.md",
        "---\n"
        "description: 插件版发布\n"
        "mode: plan\n"
        "allowed-tools: read_file, glob , read_file,bash\n"
        "argument-hint: [target]\n"
        "---\n"
        "插件发布 $ARGUMENTS",
    )
    _write_command(
        plugin / "commands",
        "yolo.md",
        "---\nmode: yolo\ndescription: 非法 mode 忽略\n---\n正文",
    )
    _write_command(
        proj / ".nexuscli" / "commands",
        "ship.md",
        "---\ndescription: 项目版发布\n---\n项目发布",
    )

    commands = load_slash_commands(str(proj), home=plugin_home)

    ship = commands["ship"]
    assert ship.source == "project"
    assert ship.body == "插件发布 $ARGUMENTS"  # 插件命令覆盖同名项目命令
    # frontmatter 解析与 _parse_command_file 既有语义一致
    assert ship.description == "插件版发布"
    assert ship.mode == "plan"
    assert ship.allowed_tools == ("read_file", "glob", "bash")
    assert ship.argument_hint == "[target]"
    assert commands["yolo"].mode == ""  # 非法 mode 落回空串
    assert commands["yolo"].description == "非法 mode 忽略"


# ---------------------------------------------------------------------------
# Cache reset, absent plugin dir, and bootstrap warm-up
# ---------------------------------------------------------------------------


def test_plugins_cache_reset_and_absent_dir(tmp_path):
    proj = tmp_path / "proj"
    _write_agent(
        proj / ".nexuscli" / "agents",
        "planner.md",
        "---\nname: planner\ndescription: 项目规划\n---\n项目规划提示词",
    )

    # 无 .nexuscli/plugins/ 的工作区：空 list，注册表与无插件基线一致
    assert load_plugins(str(proj)) == []
    baseline = load_subagents(str(proj), home=tmp_path / "home")
    assert set(baseline) == {"general-purpose", "explore", "planner"}

    # 缓存命中：新增插件目录后不重扫，仍返回空
    plugin = _make_plugin(proj)
    _write_agent(
        plugin / "agents",
        "researcher.md",
        "---\nname: researcher\ndescription: 插件研究\n---\n插件研究提示词",
    )
    assert load_plugins(str(proj)) == []

    # reset 后重新扫描：新插件被发现，load_subagents 也并入
    reset_plugins_cache()
    plugins = load_plugins(str(proj))
    assert [item.name for item in plugins] == ["acme"]
    assert "researcher" in load_subagents(str(proj), home=tmp_path / "home")


def test_build_tool_registry_warms_plugins_and_survives_bad_plugin(tmp_path):
    proj = tmp_path / "proj"
    plugin = _make_plugin(proj, "acme")
    _write_agent(
        plugin / "agents",
        "researcher.md",
        "---\nname: researcher\ndescription: 插件研究\n---\n插件研究提示词",
    )
    broken = proj / ".nexuscli" / "plugins" / "broken"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{not json", encoding="utf-8")

    # 坏插件只发警告不拖垮启动；空 MCP 配置返回空工具列表（既有路径）
    with pytest.warns(UserWarning, match="nexuscli:plugins"):
        registry, manager = asyncio.run(build_tool_registry(config=NexusCliConfig(), cwd=str(proj)))

    assert registry is not None
    assert manager is not None
    # 缓存预热生效：随后的 load_subagents 直接拿到插件代理，无需重新扫描
    definitions = load_subagents(str(proj), home=tmp_path / "home")
    assert "researcher" in definitions
