from __future__ import annotations

from nexuscli.config import NexusCliConfig
from nexuscli.mcp import McpClientManager
from nexuscli.plugins import load_plugins, register_plugin_tools
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.vfs import vfs_tools


async def build_tool_registry(
    *,
    config: NexusCliConfig,
    cwd: str,
) -> tuple[ToolRegistry, McpClientManager | None]:
    # Eager plugin assembly, the first thing on startup: discover plugins and
    # warm the loader cache so a bad manifest warns when the REPL boots
    # (repl.py's build_tool_registry call), not on the first task delegation
    # or skill match. The three component registries merge inside their own
    # consumer functions (load_subagents / SkillRegistry._load_all /
    # load_slash_commands); bootstrap only primes the cache.
    load_plugins(cwd)
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    # Read-only snapshot views (vfs_ls / vfs_read): main-agent path only —
    # subagents, sdk.py and mcp/server.py build their registries elsewhere.
    registry.register_all(vfs_tools())
    # Task 0030 wiring: plugin tool components join the same registry so every
    # production consumer (REPL, -p, runtime) sees ``<plugin>.<tool>``. This
    # is the only step that executes plugin code — each handler is imported
    # here (discovery above stayed read-only); a broken handler warns through
    # the loader's ``[nexuscli:plugins]`` channel and skips its own tool only.
    # Tools keep the loader's fail-closed defaults (requires_approval=True,
    # non-read-only) and ride the same ToolExecutor approval chain as
    # builtins; an explicit permissions.allow rule stays the exemption.
    register_plugin_tools(registry, cwd)
    manager: McpClientManager | None = None
    if config.features.mcp:
        manager = McpClientManager(cwd)
        registry.register_all(await manager.load_tools())
    return registry, manager
