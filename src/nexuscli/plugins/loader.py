"""plugins/loader.py — Local-directory plugin discovery + tool components.

A plugin is a directory under ``<cwd>/.nexuscli/plugins/<name>/`` (project
level) or ``~/.nexuscli/plugins/<name>/`` (user level) holding a
``plugin.json`` manifest plus up to four component subdirectories: ``agents/``
(markdown subagent definitions), ``skills/`` (``<name>/SKILL.md`` packs),
``commands/`` (markdown slash commands) and ``tools/`` (tool component
declarations). Discovery is pure file reads — no network, no code execution;
merging the three prompt-style component kinds into the existing registries
happens in the consumer functions (:func:`nexuscli.agent.subagent.load_subagents`,
``SkillRegistry._load_all``, :func:`nexuscli.entrypoints.slash_commands.
load_slash_commands`).

Namespace: plugin components are referenced as ``<plugin>.<component>``
(:func:`component_ref`). Tool components are registered under their namespaced
name, so two plugins can ship the same tool name and neither shadows a builtin.
The project level wins when the same plugin name exists at both levels — the
same "project overrides user" direction the other scopes follow.

Tool components (``tools/<name>.json``) declare ``name`` / ``description`` /
``parameters`` / ``handler``; the handler is a ``"file.py:attr"`` reference
into the plugin directory. The referenced module is imported only when
:func:`register_plugin_tools` runs — never during discovery. Registered tools
go through the same :class:`nexuscli.tools.executor.ToolExecutor` gate chain
as builtins and default to ``requires_approval=True`` (project-level plugins
ship with whatever repository was cloned, so their tools prompt for approval;
an explicit ``permissions.allow`` rule stays the way to pre-approve one).

Robustness contract: a missing or broken manifest, or a bad required field,
warns through :func:`warnings.warn` with a ``[nexuscli:plugins]`` prefix and
skips that plugin only — startup must never be taken down by one bad plugin.
The same warn-and-skip applies per tool declaration and per handler import at
registration time. Optional manifest fields (``description``/``version``) are
lenient: a non-string value falls back to the default instead of rejecting the
plugin. Unknown manifest keys are silently ignored.

Windows compatibility: every path is built with :mod:`pathlib` and matched
with ``Path.glob()`` — never string concatenation with path separators (the
same convention as the other asset loaders, e.g. ``agent/subagent.py``).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 仅类型标注；运行期在函数体内惰性导入（见下）
    from nexuscli.tools.base import Tool, ToolContext, ToolResult
    from nexuscli.tools.registry import ToolRegistry

__all__ = [
    "Plugin",
    "PluginManifest",
    "PluginToolDecl",
    "component_ref",
    "load_plugins",
    "plugin_root",
    "register_plugin_tools",
    "reset_plugins_cache",
    "user_plugin_root",
]

# Module-level cache keyed by (resolved cwd, resolved home). ``Path.resolve()``
# normalizes Windows drive-letter case and ``/`` vs ``\`` separator
# differences, so the same directory reached through different spellings
# shares one cache entry. A cache hit returns the very same list object and
# never re-emits warnings, so a bad manifest warns exactly once per process
# (startup assembly warms the cache; later consumers stay silent).
_CACHE: dict[tuple[str, str], list[Plugin]] = {}

# Tool component names become ``<plugin>.<name>`` model-facing tool names, so
# the declared name is kept to the builtin-tool charset (no dots/spaces).
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HANDLER_RE = re.compile(r"^(?P<file>[^:]+\.py):(?P<attr>[A-Za-z_][A-Za-z0-9_]*)$")


@dataclass(slots=True)
class PluginManifest:
    """Parsed ``plugin.json``: ``name`` required, the rest optional."""

    name: str
    description: str = ""
    version: str = ""


@dataclass(slots=True)
class PluginToolDecl:
    """One tool component declaration from ``tools/<name>.json``.

    ``handler`` stays a string reference (``"file.py:attr"`` relative to the
    plugin root) until :func:`register_plugin_tools` resolves it — discovery
    never imports plugin code.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: str


@dataclass(slots=True)
class Plugin:
    """One discovered plugin: identity plus its present component directories.

    A component directory field is set only when the subdirectory exists and
    is a directory; otherwise it stays ``None`` and consumers skip it.
    ``tools`` holds the parsed tool declarations (empty without a ``tools/``
    directory or when every declaration was rejected).
    """

    name: str
    root: Path
    manifest: PluginManifest
    agents_dir: Path | None
    skills_dir: Path | None
    commands_dir: Path | None
    tools_dir: Path | None = None
    tools: tuple[PluginToolDecl, ...] = ()


def _warn(message: str) -> None:
    warnings.warn(f"[nexuscli:plugins] {message}", stacklevel=3)


def plugin_root(cwd: str) -> Path:
    """Project-level plugin directory for *cwd*."""
    return Path(cwd) / ".nexuscli" / "plugins"


def user_plugin_root(home: str | Path) -> Path:
    """User-level plugin directory under *home* (``~/.nexuscli/plugins``)."""
    return Path(home) / ".nexuscli" / "plugins"


def component_ref(plugin_name: str, component: str) -> str:
    """Namespaced component reference: ``<plugin>.<component>``."""
    return f"{plugin_name}.{component}"


def reset_plugins_cache() -> None:
    """Drop the discovery cache (test hook; the next load re-scans and re-warns)."""
    _CACHE.clear()


def _component_dir(root: Path, name: str) -> Path | None:
    candidate = root / name
    return candidate if candidate.is_dir() else None


def _parse_manifest(directory: Path) -> PluginManifest | None:
    """Parse ``<directory>/plugin.json``; ``None`` means "warned and skipped"."""
    manifest_path = directory / "plugin.json"
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError — a plugin.json
        # with invalid bytes must warn+skip, never escape.
        _warn(f"plugin {directory.name}: plugin.json unreadable ({exc}), skipped")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _warn(f"plugin {directory.name}: plugin.json is not valid JSON ({exc}), skipped")
        return None
    if not isinstance(data, dict):
        _warn(f"plugin {directory.name}: plugin.json must be a JSON object, skipped")
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        _warn(f"plugin {directory.name}: plugin.json needs a non-empty string 'name', skipped")
        return None
    # Optional fields are lenient: a non-string value falls back to the
    # default instead of rejecting the whole plugin. Unknown keys are
    # silently ignored.
    description = data.get("description")
    version = data.get("version")
    return PluginManifest(
        name=name.strip(),
        description=description if isinstance(description, str) else "",
        version=version if isinstance(version, str) else "",
    )


def _parse_tool_decls(plugin_name: str, directory: Path) -> tuple[PluginToolDecl, ...]:
    """Parse ``tools/*.json`` declarations; bad files warn and skip themselves.

    Pure data parsing: no handler module is imported here (code execution is
    registration-time, see :func:`register_plugin_tools`).
    """
    decls: list[PluginToolDecl] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        where = f"plugin {plugin_name}: tool declaration {path.name}"
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _warn(f"{where} unreadable ({exc}), skipped")
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            _warn(f"{where} is not valid JSON ({exc}), skipped")
            continue
        if not isinstance(data, dict):
            _warn(f"{where} must be a JSON object, skipped")
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            _warn(f"{where} needs a non-empty string 'name', skipped")
            continue
        if not _TOOL_NAME_RE.match(name):
            _warn(f"{where} name {name!r} may only use [A-Za-z0-9_-], skipped")
            continue
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            _warn(f"{where} needs a non-empty string 'description', skipped")
            continue
        parameters = data.get("parameters")
        if not isinstance(parameters, dict):
            _warn(f"{where} needs an object 'parameters' (JSON schema), skipped")
            continue
        handler = data.get("handler")
        if not isinstance(handler, str) or not _HANDLER_RE.match(handler.strip()):
            _warn(f"{where} needs a 'handler' like \"handler.py:run\", skipped")
            continue
        if name in seen:
            _warn(f"{where} duplicates tool name {name!r} in the same plugin, skipped")
            continue
        seen.add(name)
        decls.append(
            PluginToolDecl(
                name=name,
                description=description.strip(),
                parameters=parameters,
                handler=handler.strip(),
            )
        )
    return tuple(decls)


def _load_plugin(directory: Path) -> Plugin | None:
    manifest = _parse_manifest(directory)
    if manifest is None:
        return None
    tools_dir = _component_dir(directory, "tools")
    return Plugin(
        name=manifest.name,
        root=directory,
        manifest=manifest,
        agents_dir=_component_dir(directory, "agents"),
        skills_dir=_component_dir(directory, "skills"),
        commands_dir=_component_dir(directory, "commands"),
        tools_dir=tools_dir,
        tools=_parse_tool_decls(manifest.name, tools_dir) if tools_dir is not None else (),
    )


def load_plugins(cwd: str, home: str | Path | None = None) -> list[Plugin]:
    """Discover plugins under *cwd*'s project plugin directory and the user's.

    Both ``<cwd>/.nexuscli/plugins/`` and ``~/.nexuscli/plugins/`` (resolved
    through *home* when given, :meth:`pathlib.Path.home` otherwise) are
    enumerated in name order and each manifest parsed. A plugin name found at
    both levels resolves to the project-level directory — the project
    workspace is the closer scope and its copy wins, so the returned list
    holds at most one :class:`Plugin` per name. The first call scans; later
    calls with the same resolved cwd/home return the same list object without
    re-scanning or re-warning. Any single plugin failure only skips that
    plugin — callers always get a list (empty when neither directory exists),
    never an exception.
    """
    base = Path(home) if home is not None else Path.home()
    cache_key = (str(Path(cwd).resolve()), str(base.resolve()))
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    by_name: dict[str, Plugin] = {}
    for root in (user_plugin_root(base), plugin_root(cwd)):
        if not root.is_dir():
            continue
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            plugin = _load_plugin(directory)
            if plugin is not None:
                by_name[plugin.name] = plugin
    plugins = [by_name[name] for name in sorted(by_name)]
    _CACHE[cache_key] = plugins
    return plugins


def register_plugin_tools(
    registry: ToolRegistry,
    cwd: str,
    home: str | Path | None = None,
) -> list[str]:
    """Register every plugin tool component into *registry* (namespaced names).

    This is the only step that executes plugin code: each declaration's
    ``"file.py:attr"`` handler is imported here (once per process, keyed by
    the resolved file path), wrapped and registered as
    ``<plugin>.<tool>``. The namespace keeps plugins from shadowing builtins
    or each other. A handler that fails to import or resolve only warns and
    skips its own tool — the rest of the plugin set still registers.
    """
    registered: list[str] = []
    for plugin in load_plugins(cwd, home):
        for decl in plugin.tools:
            tool = _build_plugin_tool(plugin, decl)
            if tool is None:
                continue
            registry.register(tool)
            registered.append(tool.name)
    return registered


def _build_plugin_tool(plugin: Plugin, decl: PluginToolDecl) -> Tool | None:
    """Resolve one declaration's handler and build the Tool; ``None`` = warned."""
    # 惰性导入：tools.__init__ 会拉起 builtins → skill → plugins，本模块若在
    # 导入期（尤其 skill.registry 之上）引用 tools 就形成环。到这里运行时整包
    # 已初始化完毕，导入必然安全。
    from nexuscli.tools.base import Tool

    match = _HANDLER_RE.match(decl.handler)
    if match is None:  # 已在发现阶段校验过；防御性兜底
        return None
    handler_path = plugin.root / match.group("file")
    resolved = handler_path.resolve()
    # 插件自带代码只允许引用插件目录内的文件：handler 指向插件外（含绝对路径与
    # ../ 逃逸）一律拒绝，不给恶意 manifest 借注册环节读加载任意路径的机会。
    if not resolved.is_relative_to(plugin.root.resolve()):
        _warn(f"plugin {plugin.name}: tool {decl.name} handler escapes the plugin root, skipped")
        return None
    if not resolved.is_file():
        _warn(f"plugin {plugin.name}: tool {decl.name} handler {decl.handler} not found, skipped")
        return None
    handler = _import_handler(plugin, decl, resolved)
    if handler is None:
        return None
    required = decl.parameters.get("required")
    if not isinstance(required, list):
        required = []
    return Tool(
        name=component_ref(plugin.name, decl.name),
        description=decl.description,
        parameters=decl.parameters,
        handler=_wrap_handler(handler),
        # 插件工具的副作用未知，一律按最保守默认：非只读（plan 模式拦截）、
        # 非并发安全、medium 危险级；项目级插件默认需要审批——调用走同一个
        # ToolExecutor 审批链，permissions.allow 显式规则是预先豁免的通道。
        is_read_only=False,
        is_concurrency_safe=False,
        danger_level="medium",
        requires_approval=True,
        required_keys=[key for key in required if isinstance(key, str)],
    )


def _import_handler(plugin: Plugin, decl: PluginToolDecl, resolved: Path):
    """Import the handler module (registration-time code execution).

    The module is cached in ``sys.modules`` keyed by the resolved file path so
    calling :func:`register_plugin_tools` twice does not re-run plugin module
    bodies. Import failures warn and skip that tool only — one broken plugin
    must not take down startup (same contract as manifest parsing).
    """
    module_key = f"nexuscli_plugin:{resolved}"
    module = sys.modules.get(module_key)
    if module is None:
        spec = importlib.util.spec_from_file_location(module_key, resolved)
        if spec is None or spec.loader is None:
            _warn(f"plugin {plugin.name}: tool {decl.name} handler cannot be imported, skipped")
            return None
        module = importlib.util.module_from_spec(spec)
        try:
            sys.modules[module_key] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - 插件代码的任何失败都不拖垮注册
            sys.modules.pop(module_key, None)
            _warn(f"plugin {plugin.name}: tool {decl.name} handler import failed ({exc}), skipped")
            return None
    attr = getattr(module, decl.handler.rpartition(":")[2], None)
    if not callable(attr):
        _warn(
            f"plugin {plugin.name}: tool {decl.name} handler {decl.handler} "
            "is not a callable, skipped"
        )
        return None
    return attr


def _wrap_handler(handler):
    """Adapt a plugin handler to the builtin Tool handler contract.

    Plugin handlers may be sync or async and may return a
    :class:`~nexuscli.tools.base.ToolResult` or any value (stringified into
    the result content), which keeps third-party handlers free of nexusCLI
    imports for the common case.
    """

    async def _run(payload: dict[str, Any], context: ToolContext) -> ToolResult:
        from nexuscli.tools.base import ToolResult

        result = handler(payload, context)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result))

    return _run
