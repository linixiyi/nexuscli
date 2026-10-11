"""vfs/tools.py — Read-only snapshot tools for the main agent.

``vfs_ls`` / ``vfs_read`` expose a workspace snapshot as a read-only virtual
filesystem so the agent can inspect how files looked before its edits without
restoring (``revert_turn`` wipes the workspace). Snapshot references accept
the same id-or-1-based-index semantics as ``SnapshotService.restore`` via the
shared ``SnapshotService.resolve_ref`` lookup — which never writes, unlike
``restore`` (it snapshots ``pre-restore`` before touching the workspace).

Coverage note: only the main-agent registry registers these tools
(bootstrap.build_tool_registry). Subagents build their own registry
(subagent.py:288-289), and sdk.py / mcp/server.py assemble from
``get_builtin_tools()`` directly, so they do not see vfs tools yet; wiring
those paths is deferred, not done.
"""

from __future__ import annotations

from typing import Any

from nexuscli.snapshot import SnapshotService
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.vfs.view import SnapshotView


def vfs_tools() -> list[Tool]:
    """Return the read-only snapshot-view tools (vfs_ls, vfs_read)."""
    return [_vfs_ls_tool(), _vfs_read_tool()]


def _vfs_ls_tool() -> Tool:
    return Tool(
        name="vfs_ls",
        description=(
            "List a directory inside a previous workspace snapshot's read-only view. "
            "Use it to compare against the pre-change state without restoring. "
            "The virtual path '/' is the snapshot root."
        ),
        parameters=object_schema(
            {
                "snapshot": {
                    "type": "string",
                    "description": "Snapshot id or 1-based index (same semantics as revert_turn)",
                },
                "path": {
                    "type": "string",
                    "description": "Virtual path inside the snapshot (default '/')",
                },
            },
            ["snapshot"],
        ),
        required_keys=["snapshot"],
        handler=_vfs_ls,
    )


def _vfs_read_tool() -> Tool:
    return Tool(
        name="vfs_read",
        description=(
            "Read a text file from a previous workspace snapshot's read-only view, "
            "without restoring it. Useful to diff the current file against its "
            "pre-change content."
        ),
        parameters=object_schema(
            {
                "snapshot": {
                    "type": "string",
                    "description": "Snapshot id or 1-based index (same semantics as revert_turn)",
                },
                "path": {"type": "string", "description": "Virtual path inside the snapshot"},
                "offset": {"type": "number", "description": "Start line, 1-based"},
                "limit": {"type": "number", "description": "Maximum number of lines"},
            },
            ["snapshot", "path"],
        ),
        required_keys=["snapshot", "path"],
        handler=_vfs_read,
    )


def _snapshot_view(context: ToolContext, ref: str) -> tuple[SnapshotView, str]:
    """Build the view for *ref*, or raise ValueError with an agent-facing message."""
    record = SnapshotService(context.cwd).resolve_ref(ref)
    if not record.path.is_dir():
        raise ValueError(f"snapshot directory is missing on disk: {record.path}")
    return SnapshotView(record.path), record.id


async def _vfs_ls(payload: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        view, record_id = _snapshot_view(context, str(payload["snapshot"]))
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    result = view.list_dir(str(payload.get("path") or "/"))
    return ToolResult(
        result.content,
        is_error=result.is_error,
        display_summary=result.display_summary or f"Listed snapshot {record_id}",
    )


async def _vfs_read(payload: dict[str, Any], context: ToolContext) -> ToolResult:
    try:
        view, _record_id = _snapshot_view(context, str(payload["snapshot"]))
    except ValueError as exc:
        return ToolResult(str(exc), is_error=True)
    result = view.read(
        str(payload["path"]),
        offset=int(payload.get("offset") or 1),
        limit=int(payload.get("limit") or 500),
    )
    return ToolResult(
        result.content,
        is_error=result.is_error,
        display_summary=result.display_summary,
    )
