"""snapshot_cmds.py — /snapshot save/list/clean.

``save`` exists because the manual-snapshot story needs a creator: with
``features.auto_snapshot`` off (the default) a fresh project has no restore
points at all, so bare ``/snapshot`` (a listing) could only ever print
"(no snapshots)" and ``/restore`` had nothing to resolve. The label becomes
the snapshot phase (visible in ``/snapshot`` listings via the record id); it
is slugified here, at the user-input boundary, because ``SnapshotService.create``
turns the phase into a directory name under ``~/.nexuscli/snapshots/``.
"""

from __future__ import annotations

import re

from rich.console import Console

from nexuscli.snapshot import SnapshotService

# Snapshot ids become directory names; anything outside this class (path
# separators, drive colons, whitespace, control chars) is replaced with "-".
_LABEL_ALLOWED_RE = re.compile(r"[^\w.-]", re.UNICODE)
_LABEL_MAX_CHARS = 40


def _snapshot_command(arg: str, console: Console, cwd: str) -> None:
    service = SnapshotService(cwd)
    verb, _, label = arg.partition(" ")
    verb = verb.strip().lower()
    if verb == "clean":
        console.print(f"Cleaned {service.clean()} snapshots.")
        return
    if verb == "save":
        phase = _slugify_label(label) or "manual"
        try:
            record = service.create(phase)
        except OSError as exc:
            console.print(f"[red]Snapshot failed:[/red] {exc}")
            return
        console.print(f"[green]Snapshot saved:[/green] {record.id}")
        if record.skipped:
            preview = ", ".join(record.skipped[:5])
            more = f" … +{len(record.skipped) - 5} more" if len(record.skipped) > 5 else ""
            console.print(
                f"[yellow]Skipped {len(record.skipped)} unreadable path(s): "
                f"{preview}{more}[/yellow]"
            )
        return
    rows = service.list(limit=20)
    output = "\n".join(
        f"{index}. {row.id} {row.phase} {row.created_at}" for index, row in enumerate(rows, 1)
    )
    console.print(output or "(no snapshots — create one with: /snapshot save \\[label])")


def _slugify_label(label: str) -> str:
    """Reduce a user label to a safe directory-name fragment (no separators)."""
    slug = _LABEL_ALLOWED_RE.sub("-", label).strip(".-")[:_LABEL_MAX_CHARS]
    return slug.strip("-")
