"""memory_cmds.py — /memory subcommand handler (long-term memory management)."""

from __future__ import annotations

import json

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.memory import MemoryManager


async def _memory_command(arg: str, console: Console, cwd: str, config: NexusCliConfig) -> None:
    manager = MemoryManager(
        config.memory.long_term_db_path,
        scope=cwd,
        max_entries=config.memory.max_long_term_entries,
        max_content_length=config.memory.max_memory_chars,
    )
    sub, _, rest = arg.partition(" ")
    if sub == "clear":
        count = manager.clear()
        console.print(f"Cleared {count} memories.")
    elif sub == "search":
        rows = manager.search(rest)
        console.print("\n".join(f"#{row.id} {row.content}" for row in rows) or "(no matches)")
    elif sub == "stats":
        console.print_json(json.dumps(manager.stats(), ensure_ascii=False))
    elif sub == "delete" and rest.strip().isdigit():
        console.print(f"Deleted: {manager.delete(int(rest.strip()))}")
    else:
        rows = manager.list()
        console.print("\n".join(f"#{row.id} {row.content}" for row in rows) or "(no memories)")
