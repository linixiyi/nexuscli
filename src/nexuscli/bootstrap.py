from __future__ import annotations

from nexuscli.config import NexusCliConfig
from nexuscli.mcp import McpClientManager
from nexuscli.tools import ToolRegistry, get_builtin_tools


async def build_tool_registry(
    *,
    config: NexusCliConfig,
    cwd: str,
) -> tuple[ToolRegistry, McpClientManager | None]:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    manager: McpClientManager | None = None
    if config.features.mcp:
        manager = McpClientManager(cwd)
        registry.register_all(await manager.load_tools())
    return registry, manager
