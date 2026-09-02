"""slash_commands.py — User-defined slash commands backed by markdown files.

A custom command is a ``*.md`` file in ``~/.nexuscli/commands/`` (user scope)
or ``<cwd>/.nexuscli/commands/`` (project scope, wins on name conflicts). The
file body is a prompt template; an optional YAML-ish frontmatter may set a
``description``. ``$ARGUMENTS`` in the body receives whatever the user typed
after the command name; when the placeholder is absent, arguments are appended
to the prompt. The expanded prompt is sent to the agent as a normal message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ARGUMENTS_PLACEHOLDER = "$ARGUMENTS"


@dataclass(frozen=True, slots=True)
class CustomCommand:
    name: str
    description: str
    body: str
    source: str  # "user" or "project"
    path: Path


def _is_valid_name(name: str) -> bool:
    if not name:
        return False
    compact = name.replace("-", "").replace("_", "")
    return compact.isascii() and compact.isalnum()


def _parse_command_file(path: Path, source: str) -> CustomCommand | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    name = path.stem.lower()
    if not _is_valid_name(name):
        return None
    description = ""
    body = text
    if text.startswith("---"):
        segments = text.split("---", 2)
        if len(segments) == 3:
            frontmatter, body = segments[1].strip(), segments[2].lstrip("\n")
            for line in frontmatter.splitlines():
                key, _, value = line.partition(":")
                if key.strip().lower() == "description":
                    description = value.strip()
    if not body.strip():
        return None
    return CustomCommand(
        name=name,
        description=description,
        body=body,
        source=source,
        path=path,
    )


def load_slash_commands(cwd: str, home: Path | None = None) -> dict[str, CustomCommand]:
    """Load custom commands; project scope overrides user scope per name."""
    base = home if home is not None else Path.home()
    scopes = [
        (Path(base) / ".nexuscli" / "commands", "user"),
        (Path(cwd) / ".nexuscli" / "commands", "project"),
    ]
    commands: dict[str, CustomCommand] = {}
    for directory, source in scopes:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            command = _parse_command_file(path, source)
            if command is not None:
                commands[command.name] = command
    return commands


def split_command_message(message: str) -> tuple[str, str] | None:
    """Split ``/name args`` into ``("/name", args)`` for a slash message."""
    stripped = message.strip()
    if not stripped.startswith("/"):
        return None
    head, _, tail = stripped.partition(" ")
    return head.lower(), tail.strip()


def expand_custom_command(command: CustomCommand, args: str) -> str:
    if ARGUMENTS_PLACEHOLDER in command.body:
        return command.body.replace(ARGUMENTS_PLACEHOLDER, args)
    if args:
        return f"{command.body.rstrip()}\n\n{args}"
    return command.body
