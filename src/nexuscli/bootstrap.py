from __future__ import annotations

from nexuscli.config import NexusCliConfig
from nexuscli.mcp import McpClientManager
from nexuscli.plugins import load_plugins
from nexuscli.tools import ToolRegistry, get_builtin_tools


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
    manager: McpClientManager | None = None
    if config.features.mcp:
        manager = McpClientManager(cwd)
        registry.register_all(await manager.load_tools())
    return registry, manager
