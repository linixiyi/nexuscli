from __future__ import annotations

from nexuscli.tools.base import Tool

# Task 0038: ``agent.prompt_profile == "lean"`` keeps only these core tool
# schemas in the request's tool list, so the fixed per-request tool overhead
# shrinks on registries that carry many optional tools. Names missing from a
# registry simply do not match (e.g. update_plan rides as todo_write today),
# so the filter degrades gracefully instead of erroring; anything but the
# exact string "lean" advertises the whole registry (unknown profiles ≡ full).
LEAN_CORE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "bash",
        "list_dir",
        "grep",
        "glob",
        "update_plan",
    }
)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_all(self, tools: list[Tool]) -> None:
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self, profile: str = "full") -> list[dict]:
        """OpenAI-style tool schemas; ``profile="lean"`` keeps LEAN_CORE_TOOLS only."""
        names = self.list_names()
        if profile == "lean":
            names = [name for name in names if name in LEAN_CORE_TOOLS]
        return [self._tools[name].definition() for name in names]
