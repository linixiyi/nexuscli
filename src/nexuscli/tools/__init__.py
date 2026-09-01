# Export the file_ops module so other code can reuse the pure logic.
from nexuscli.tools import file_ops  # noqa: F401
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.registry import ToolRegistry

__all__ = ["ToolRegistry", "get_builtin_tools", "file_ops"]
