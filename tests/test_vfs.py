"""tests/test_vfs.py — Read-only snapshot views (SnapshotView + vfs_ls/vfs_read)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from nexuscli.bootstrap import build_tool_registry
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.snapshot import SnapshotService
from nexuscli.tools.base import Tool, ToolContext, ToolResult
from nexuscli.tools.registry import ToolRegistry
from nexuscli.vfs import SnapshotView, VfsPathError, vfs_tools


@pytest.fixture
def vfs_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp directory.

    Mirrors tests/test_background.py:52-54: on Windows Path.home() reads
    USERPROFILE (not HOME), so both environment variables are redirected and
    Path.home itself is patched, keeping snapshots out of the real ~/.nexuscli.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _make_project(tmp_path: Path, *, content: str = "line1\nline2\nline3\n") -> object:
    """Create a workspace with files and snapshot it; return the snapshot record."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text(content, encoding="utf-8")
    (project / "note.txt").write_text("before", encoding="utf-8")
    service = SnapshotService(project)
    return service.create("pre-turn")


def _make_context(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=str(tmp_path / "project"), config=NexusCliConfig())


def _call_tool(
    registry: ToolRegistry, name: str, payload: dict, context: ToolContext
) -> ToolResult:
    """Run one registered tool synchronously (mirrors tests/test_tools.py style)."""
    tool: Tool | None = registry.get(name)
    assert tool is not None, f"tool not registered: {name}"
    return asyncio.run(tool.execute(payload, context))


# ---------------------------------------------------------------------------
# ① SnapshotView returns snapshot content
# ---------------------------------------------------------------------------


def test_snapshot_view_reads_snapshot_contents(tmp_path, vfs_home):
    project = tmp_path / "project"
    record = _make_project(tmp_path)
    # Mutate the workspace after the snapshot: the view must still show the
    # pre-change state (that is its whole point — compare without restoring).
    (project / "src" / "app.py").write_text("mutated\n", encoding="utf-8")

    view = SnapshotView(record.path)
    assert view.exists("src/app.py")
    assert not view.exists("src/missing.py")

    read = view.read("src/app.py")
    assert not read.is_error
    assert "1: line1" in read.content
    assert "3: line3" in read.content
    assert "mutated" not in read.content

    listing = view.list_dir("src")
    assert not listing.is_error
    assert "app.py" in listing.content

    root_listing = view.list_dir("/")
    assert not root_listing.is_error
    assert "src/" in root_listing.content

    info = view.info("src/app.py")
    assert not info.is_error
    assert "Type: file" in info.content
    assert "src/app.py" in info.content


def test_snapshot_view_read_offset_and_limit(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    view = SnapshotView(record.path)
    read = view.read("src/app.py", offset=2, limit=1)
    assert not read.is_error
    assert read.content == "2: line2"


def test_snapshot_view_reads_oversized_files_like_read_file(tmp_path, vfs_home):
    """No snapshot-only size gate: parity with the workspace read_file.

    Comparing before/after matters most for large generated files, so a
    snapshot-only ``skip_file`` size limit (the workspace read tool has none)
    would break exactly the reads this view exists for. Snapshots already
    exclude build/dist/node_modules at creation time.
    """
    project = tmp_path / "project"
    project.mkdir()
    big = project / "generated.log"
    # Pad past file_ops.MAX_FILE_SIZE (1 MB) without bloating the test: filler
    # lines after the interesting head lines.
    filler = ("f" * 120 + "\n") * 9000  # ~1.08 MB
    big.write_text("header\nmarker-line\n" + filler + "footer\n", encoding="utf-8")
    assert big.stat().st_size > 1_000_000

    service = SnapshotService(project)
    record = service.create("pre-turn")
    view = SnapshotView(record.path)

    read = view.read("generated.log", limit=2)
    assert not read.is_error
    assert read.content.splitlines()[1] == "2: marker-line"


# ---------------------------------------------------------------------------
# ② Path escapes are rejected
# ---------------------------------------------------------------------------


def test_snapshot_view_rejects_path_escapes(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    view = SnapshotView(record.path)

    escapes = ["../outside.txt", "src/../../outside.txt", "a/b/../../../../../outside.txt"]
    for bad in escapes:
        with pytest.raises(VfsPathError):
            view.resolve(bad)
        assert view.read(bad).is_error
        assert view.list_dir(bad).is_error
        assert view.info(bad).is_error
        assert view.exists(bad) is False


@pytest.mark.skipif(sys.platform != "win32", reason="drive/UNC anchors are a Windows concept")
def test_snapshot_view_rejects_real_absolute_paths(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    view = SnapshotView(record.path)

    # A drive-anchored path is an address of the real filesystem, never of the
    # virtual tree: it must be rejected even though it may exist on disk.
    for bad in (str(tmp_path / "outside.txt"), "C:/Windows/system32", "\\\\server\\share\\x"):
        with pytest.raises(VfsPathError):
            view.resolve(bad)
        assert view.read(bad).is_error


def test_snapshot_view_leading_slash_is_mount_root(tmp_path, vfs_home):
    """A single leading '/' is the virtual mount point, not a real absolute path."""
    record = _make_project(tmp_path)
    view = SnapshotView(record.path)
    assert view.resolve("/src/app.py") == (record.path / "src" / "app.py").resolve()
    assert view.exists("/src/app.py")
    assert not view.list_dir("/src").is_error


# ---------------------------------------------------------------------------
# ③ Write-class operations have no API to reach
# ---------------------------------------------------------------------------


def test_snapshot_view_has_no_write_api(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    view = SnapshotView(record.path)
    for forbidden in (
        "write",
        "write_file",
        "edit",
        "edit_file",
        "append",
        "delete",
        "remove",
        "unlink",
        "mkdir",
        "rename",
        "rmdir",
        "restore",
    ):
        assert not hasattr(view, forbidden), f"SnapshotView must not expose {forbidden}"


# ---------------------------------------------------------------------------
# ④ vfs_ls / vfs_read via ToolRegistry
# ---------------------------------------------------------------------------


def test_vfs_tools_register_into_registry(vfs_home):
    registry = ToolRegistry()
    registry.register_all(vfs_tools())
    names = registry.list_names()
    assert "vfs_ls" in names
    assert "vfs_read" in names


def test_vfs_read_returns_snapshot_content(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    registry = ToolRegistry()
    registry.register_all(vfs_tools())
    context = _make_context(tmp_path)

    # By id and by 1-based index — the revert_turn reference semantics.
    by_id = _call_tool(registry, "vfs_read", {"snapshot": record.id, "path": "note.txt"}, context)
    assert not by_id.is_error
    assert "before" in by_id.content

    by_index = _call_tool(registry, "vfs_read", {"snapshot": "1", "path": "note.txt"}, context)
    assert not by_index.is_error
    assert "before" in by_index.content


def test_vfs_ls_lists_snapshot_root(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    registry = ToolRegistry()
    registry.register_all(vfs_tools())
    context = _make_context(tmp_path)

    result = _call_tool(registry, "vfs_ls", {"snapshot": record.id}, context)
    assert not result.is_error
    assert "src/" in result.content
    assert "note.txt" in result.content


def test_vfs_tools_bad_snapshot_ref_is_error(tmp_path, vfs_home):
    _make_project(tmp_path)
    registry = ToolRegistry()
    registry.register_all(vfs_tools())
    context = _make_context(tmp_path)

    bad_ref = _call_tool(registry, "vfs_read", {"snapshot": "999", "path": "note.txt"}, context)
    assert bad_ref.is_error
    assert "snapshot not found" in bad_ref.content

    bad_id = _call_tool(registry, "vfs_ls", {"snapshot": "no-such-id"}, context)
    assert bad_id.is_error


def test_vfs_tools_reject_path_escapes(tmp_path, vfs_home):
    record = _make_project(tmp_path)
    registry = ToolRegistry()
    registry.register_all(vfs_tools())
    context = _make_context(tmp_path)

    result = _call_tool(
        registry, "vfs_read", {"snapshot": record.id, "path": "../outside.txt"}, context
    )
    assert result.is_error


# ---------------------------------------------------------------------------
# ⑤ bootstrap registers the vfs tools into the main-agent registry
# ---------------------------------------------------------------------------


def test_build_tool_registry_includes_vfs_tools(tmp_path, vfs_home):
    (tmp_path / "project").mkdir()
    config = load_config(project_root=tmp_path / "project")
    registry, manager = asyncio.run(
        build_tool_registry(config=config, cwd=str(tmp_path / "project"))
    )
    names = registry.list_names()
    assert "vfs_ls" in names
    assert "vfs_read" in names
