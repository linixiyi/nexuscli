"""vfs/view.py — A read-only view over one workspace snapshot directory.

A :class:`SnapshotView` mounts a snapshot directory (the pre-change workspace
copy under ``~/.nexuscli/snapshots/``) at the virtual path ``/`` and exposes
exactly four read operations: ``read`` / ``exists`` / ``list_dir`` / ``info``.
There is deliberately no write, edit, or delete API — read-only is a
structural guarantee, not a convention: the class cannot mutate the snapshot
because no mutating method exists on it.

Path handling mirrors :func:`nexuscli.tools.file_ops.resolve_path` /
:class:`nexuscli.policy.path_guard.PathGuard`: a virtual path is resolved
against the mount root and the *resolved* location must stay inside the
snapshot directory, so ``..`` chains (and any symlink that ever appears in a
snapshot) cannot escape. Drive-anchored real paths (``C:\\...``) and UNC
paths (``\\\\server\\share``) are rejected outright: they are addresses of the
real filesystem, not of the virtual tree. A single leading ``/`` is the
virtual mount root, so ``/src/app.py`` is a valid virtual address that maps
inside the snapshot.
"""

from __future__ import annotations

from pathlib import Path

from nexuscli.tools.file_ops import FileOpResult

# The virtual mount point spellings that all address the snapshot root itself.
_ROOT_ALIASES = frozenset({"", ".", "/", "\\"})


class VfsPathError(ValueError):
    """Raised when a virtual path is rejected by the snapshot view."""


class SnapshotView:
    """Read-only, path-guarded view of one snapshot directory mounted at ``/``."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------
    # Public read API (the only operations this class offers)
    # ------------------------------------------------------------------

    def read(self, path: str, *, offset: int = 1, limit: int = 500) -> FileOpResult:
        """Return numbered lines from a snapshot file, like ``file_ops.read_file``.

        Deliberately the *same* semantics as the workspace ``read_file`` —
        whole-file read with ``errors="replace"``, no size or binary gate, then
        a 1-based line window. Comparing before/after matters most for large
        generated files, so a snapshot-only size limit would break exactly the
        reads this view exists for. Snapshots already exclude
        ``build``/``dist``/``node_modules``/... at creation time
        (snapshot/service.py ``SKIP_DIRS``), so no per-path skip is applied
        here.
        """
        try:
            resolved = self.resolve(path)
        except VfsPathError as exc:
            return FileOpResult(str(exc), is_error=True)
        if not resolved.is_file():
            return FileOpResult(f"Not a file in snapshot: {path}", is_error=True)
        try:
            raw = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return FileOpResult(f"Failed to read {path}: {exc}", is_error=True)

        offset = max(offset, 1)
        lines = raw.splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{idx + offset}: {line}" for idx, line in enumerate(selected))
        return FileOpResult(numbered, display_summary=f"Read snapshot:{_display(path)}")

    def exists(self, path: str) -> bool:
        """Whether *path* exists inside the snapshot. Rejected paths are not present."""
        try:
            return self.resolve(path).exists()
        except VfsPathError:
            return False

    def list_dir(self, path: str) -> FileOpResult:
        """List entries of a snapshot directory, directories marked with ``/``."""
        try:
            resolved = self.resolve(path)
        except VfsPathError as exc:
            return FileOpResult(str(exc), is_error=True)
        if not resolved.is_dir():
            return FileOpResult(f"Not a directory in snapshot: {path}", is_error=True)
        try:
            children = sorted(
                resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
            )
        except OSError as exc:
            return FileOpResult(f"Failed to list {path}: {exc}", is_error=True)
        rows = [f"{child.name}/" if child.is_dir() else child.name for child in children]
        return FileOpResult("\n".join(rows) or "(empty directory)")

    def info(self, path: str) -> FileOpResult:
        """Return metadata about a snapshot entry, like ``file_ops.get_file_info``."""
        try:
            resolved = self.resolve(path)
        except VfsPathError as exc:
            return FileOpResult(str(exc), is_error=True)
        if not resolved.exists():
            return FileOpResult(f"Path does not exist in snapshot: {path}", is_error=True)
        try:
            stat = resolved.stat()
        except OSError as exc:
            return FileOpResult(f"Failed to stat {path}: {exc}", is_error=True)
        kind = "directory" if resolved.is_dir() else "file"
        return FileOpResult(
            f"Path: {_display(path)}\n"
            f"Type: {kind}\n"
            f"Size: {stat.st_size}\n"
            f"Modified: {stat.st_mtime:.0f}\n"
            f"Permissions: {oct(stat.st_mode & 0o777)}"
        )

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def resolve(self, virtual_path: str) -> Path:
        """Map a virtual path onto a real path under the snapshot root.

        Raises :class:`VfsPathError` when the address is rejected: drive or UNC
        anchors, or a resolved location outside the snapshot tree.
        """
        text = str(virtual_path)
        if text.startswith("\\\\"):
            # UNC share (\\server\share\...) — a real-filesystem address.
            raise VfsPathError(f"absolute path rejected by snapshot view: {text}")
        stripped = text.lstrip("/\\")
        if Path(stripped).drive:
            # Drive-anchored path (C:\..., C:relative) — real-filesystem address.
            raise VfsPathError(f"absolute path rejected by snapshot view: {text}")
        if stripped in _ROOT_ALIASES:
            return self.root
        resolved = (self.root / stripped).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise VfsPathError(f"path escapes snapshot root: {text}") from exc
        return resolved


def _display(path: str) -> str:
    """Normalize a virtual address for display: '/' separators, mount root as '/'."""
    stripped = str(path).lstrip("/\\")
    return "/" if stripped in _ROOT_ALIASES else Path(stripped).as_posix()
