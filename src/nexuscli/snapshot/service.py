from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ".nexuscli" holds the CLI's own state (config, memory db, snapshots); it must
# never be captured or restored, and skipping it also prevents snapshot trees
# under $HOME from copying themselves into their own destination. The trailing
# entries are regenerable tool-state caches that can hold tens of thousands of
# files (a security scanner's workdir alone dwarfed this repo's real sources).
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".nexuscli",
    ".mimosa",
    ".ruff_cache",
    ".pytest_cache",
}

# Fingerprint of one file relative to the snapshot root: (mtime_ns, size).
# copy2 preserves mtimes, so the fingerprint of a copy equals the source's and
# survives from one snapshot into the next.
Manifest = dict[str, tuple[int, int]]


@dataclass(slots=True)
class SnapshotRecord:
    id: str
    phase: str
    created_at: str
    path: Path
    skipped: list[str] = field(default_factory=list)


class SnapshotService:
    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()
        digest = hashlib.sha256(str(self.project_root).encode("utf-8")).hexdigest()[:16]
        self.root = Path.home() / ".nexuscli" / "snapshots" / digest
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"

    def create(self, phase: str) -> SnapshotRecord:
        snapshot_id = f"{phase}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
        target = self.root / snapshot_id
        target.mkdir(parents=True, exist_ok=True)
        try:
            # Incremental pass: fingerprint the current tree once, then reuse
            # (link) every file that is already identical in the newest
            # surviving snapshot of this project instead of copying it again.
            base = self._latest_snapshot_dir()
            manifest = _build_manifest(self.project_root)
            skipped = self._copy_tree(self.project_root, target, base=base, manifest=manifest)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        record = SnapshotRecord(
            id=snapshot_id,
            phase=phase,
            created_at=datetime.now(UTC).isoformat(),
            path=target,
            skipped=skipped,
        )
        with self.index_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "id": record.id,
                        "phase": record.phase,
                        "created_at": record.created_at,
                        "path": str(record.path),
                        "skipped": skipped,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        return record

    def list(self, limit: int = 20) -> list[SnapshotRecord]:
        if not self.index_path.exists():
            return []
        records = []
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            records.append(
                SnapshotRecord(
                    id=item["id"],
                    phase=item["phase"],
                    created_at=item["created_at"],
                    path=Path(item["path"]),
                    skipped=list(item.get("skipped") or []),
                )
            )
        return records[-limit:][::-1]

    def resolve_ref(self, snapshot_ref: str, limit: int = 200) -> SnapshotRecord:
        """Resolve a snapshot reference (1-based index, newest-first, or id).

        Numeric references index into the newest-first list window (same order
        ``/snapshot`` shows); anything else matches an exact snapshot id. Read
        only — the read-only vfs view resolves through this method too, which
        must never write the ``pre-restore`` snapshot that ``restore`` does.
        """
        records = self.list(limit=limit)
        record = None
        if snapshot_ref.isdigit():
            index = int(snapshot_ref) - 1
            if 0 <= index < len(records):
                record = records[index]
        else:
            record = next((item for item in records if item.id == snapshot_ref), None)
        if not record:
            raise ValueError(f"snapshot not found: {snapshot_ref}")
        return record

    def restore(self, snapshot_ref: str) -> SnapshotRecord:
        record = self.resolve_ref(snapshot_ref)
        if not record.path.is_dir():
            # Validate BEFORE wiping the workspace: a missing snapshot directory
            # must never leave the project emptied.
            raise ValueError(
                f"snapshot directory is missing on disk: {record.path} — nothing was restored"
            )
        self.create("pre-restore")
        self._restore_tree(record.path, self.project_root)
        return record

    def _latest_snapshot_dir(self) -> Path | None:
        """Newest recorded snapshot of this project whose directory still exists."""
        try:
            records = self.list(limit=10_000)  # newest-first
        except (KeyError, TypeError):
            return None  # unreadable index: degrade to a full copy
        return next((record.path for record in records if record.path.is_dir()), None)

    def clean(self) -> int:
        count = len(self.list(limit=10_000))
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        return count

    def _copy_tree(
        self,
        source: Path,
        target: Path,
        base: Path | None = None,
        manifest: Manifest | None = None,
    ) -> list[str]:
        """Copy workspace files into *target*, skipping files that cannot be read.

        When *base* points at the previous snapshot of the same tree and
        *manifest* fingerprints that tree, a file whose (mtime, size) matches
        the copy already in *base* is linked from there instead of re-read;
        restore passes neither and stays a full copy. Returns the skipped
        paths so a locked file degrades one snapshot instead of silently
        disabling automatic snapshots altogether.
        """
        skipped: list[str] = []

        def _on_walk_error(error: OSError) -> None:
            # An unlistable root aborts the copy; a nested one is reported in
            # skipped and the walk moves past it, like a failed copy would.
            failed = Path(error.filename) if error.filename else source
            relative = failed.relative_to(source)
            if not relative.parts:
                raise error
            skipped.append(str(relative))

        for dirpath, dirnames, filenames in os.walk(source, onerror=_on_walk_error):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            rel_dir = Path(dirpath).relative_to(source)
            destination_dir = target / rel_dir
            try:
                destination_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                skipped.append(str(rel_dir))
                dirnames[:] = []  # unwritable here: don't pretend children copied
                continue
            for name in filenames:
                if name in SKIP_DIRS:
                    continue
                rel_path = rel_dir / name
                destination = destination_dir / name
                try:
                    if not _link_unchanged(rel_path, destination, base, manifest):
                        shutil.copy2(Path(dirpath) / name, destination)
                except OSError:
                    skipped.append(str(rel_path))
        return skipped

    def _restore_tree(self, source: Path, target: Path) -> None:
        for item in target.iterdir():
            if _skip(item):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        self._copy_tree(source, target)


def _skip(path: Path) -> bool:
    return path.name in SKIP_DIRS


def _link_unchanged(
    rel_path: Path,
    destination: Path,
    base: Path | None,
    manifest: Manifest | None,
) -> bool:
    """Hard-link *destination* from the previous snapshot when the file is unchanged.

    "Unchanged" means the current tree's manifest fingerprint (mtime + size)
    equals the copy already sitting at the same relative path in *base*; a
    failed link (cross-device, link-hostile filesystem) falls back to a plain
    copy, so this returning False must always be safe.
    """
    if base is None or manifest is None:
        return False
    fingerprint = manifest.get(rel_path.as_posix())
    if fingerprint is None:
        return False
    try:
        previous = (base / rel_path).stat()
    except OSError:
        return False
    if (previous.st_mtime_ns, previous.st_size) != fingerprint:
        return False
    try:
        os.link(base / rel_path, destination)
    except OSError:
        return False
    return True


def _build_manifest(root: Path) -> Manifest:
    """Fingerprint the current tree: posix relative path -> (mtime, size).

    A file that cannot be stat'ed is left out, so it takes the plain-copy
    path (and, if that fails too, the skipped degradation).
    """
    manifest: Manifest = {}
    for rel_path, file_path in _iter_files(root):
        try:
            stat_result = file_path.stat()
        except OSError:
            continue
        manifest[rel_path.as_posix()] = (stat_result.st_mtime_ns, stat_result.st_size)
    return manifest


def _iter_files(root: Path) -> Iterator[tuple[Path, Path]]:
    """Yield (relative path, path) for every file under *root*.

    ``SKIP_DIRS`` names are pruned at every depth, files included — the same
    coverage the old top-level ``_skip`` plus the copytree ``ignore`` callback
    gave the directory-level walk.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            if name in SKIP_DIRS:
                continue
            yield rel_dir / name, Path(dirpath) / name
