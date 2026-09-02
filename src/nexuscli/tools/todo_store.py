"""todo_store.py — Persistent task checklist shared with the agent.

The agent tracks multi-step work through the ``todo_write`` tool. The list is
stored as JSON under ``<cwd>/.nexuscli/todo.json`` so it survives restarts and
resumed sessions, and the formatted checklist doubles as the tool result so
the model always sees the current state after an update.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATUSES = ("pending", "in_progress", "completed")
PRIORITIES = ("high", "medium", "low")
MAX_TODOS = 50
MAX_CONTENT_CHARS = 500

_STATUS_ICONS = {"pending": "☐", "in_progress": "▸", "completed": "☑"}


def todo_path(cwd: str) -> Path:
    return Path(cwd) / ".nexuscli" / "todo.json"


def _normalize_list(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        raise ValueError("todos must be a list")
    if len(raw) > MAX_TODOS:
        raise ValueError(f"todo list is limited to {MAX_TODOS} items")
    normalized: list[dict[str, str]] = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"todo #{index} must be an object")
        content = str(item.get("content") or "").strip()
        if not content:
            raise ValueError(f"todo #{index} is missing content")
        status = str(item.get("status") or "pending").lower()
        if status not in STATUSES:
            raise ValueError(f"todo #{index} status must be one of: {', '.join(STATUSES)}")
        priority = str(item.get("priority") or "medium").lower()
        if priority not in PRIORITIES:
            raise ValueError(f"todo #{index} priority must be one of: {', '.join(PRIORITIES)}")
        normalized.append(
            {
                "content": content[:MAX_CONTENT_CHARS],
                "status": status,
                "priority": priority,
            }
        )
    return normalized


def format_todos(todos: list[dict[str, str]]) -> str:
    if not todos:
        return "(no todos)"
    lines = []
    for index, item in enumerate(todos, 1):
        line = f"{index}. {_STATUS_ICONS[item['status']]} [{item['status']}] {item['content']}"
        if item["priority"] != "medium":
            line += f" ({item['priority']})"
        lines.append(line)
    return "\n".join(lines)


class TodoStore:
    """Read and replace the project todo list atomically."""

    def __init__(self, cwd: str) -> None:
        self.path = todo_path(cwd)

    def set(self, raw: Any) -> list[dict[str, str]]:
        todos = _normalize_list(raw)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(todos, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return todos

    def get(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        try:
            return _normalize_list(data)
        except ValueError:
            return []

    def clear(self) -> list[dict[str, str]]:
        self.set([])
        return []
