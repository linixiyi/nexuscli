"""file_ops.py — Encapsulated file operations for the terminal agent.

Provides pure, reusable functions for reading, writing, editing, listing,
globing, and searching files. Keeps business logic separate from tool
definitions so that builtins.py remains a thin wiring layer. PDF files
(.pdf suffix) are extracted as text via the optional pypdf dependency;
when pypdf is missing the read returns an error with install guidance.
"""

from __future__ import annotations

import glob as glob_module
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from nexuscli.lsp import diagnose_file
from nexuscli.policy import PathGuard

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FileOpResult:
    """Return value for every file_ops function."""

    content: str
    is_error: bool = False
    display_summary: str | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "dist", "build", "target", "__pycache__"})
MAX_FILE_SIZE = 1_000_000  # 1 MB


def resolve_path(cwd: str, value: str, path_guard_enabled: bool = True) -> Path:
    """Resolve *value* against workspace root *cwd*, optionally enforcing the path guard."""
    if path_guard_enabled:
        return PathGuard(cwd).validate(value)
    path = Path(value)
    return path if path.is_absolute() else Path(cwd).resolve() / path


def skip_file(path: Path) -> bool:
    """Return True when *path* should be ignored (binary, oversized, inside skipped dirs)."""
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    try:
        return path.stat().st_size > MAX_FILE_SIZE
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def read_file(
    cwd: str,
    path: str,
    *,
    offset: int = 1,
    limit: int = 500,
    path_guard_enabled: bool = True,
) -> FileOpResult:
    """Return numbered lines from a text file.

    *offset* is 1-based; *limit* caps the returned lines.
    """
    resolved = resolve_path(cwd, path, path_guard_enabled)
    if not resolved.is_file():
        return FileOpResult(f"Not a file: {resolved}", is_error=True)

    # Case-insensitive .pdf suffix routes to optional pypdf text extraction
    # (mirrors ZCode's isPdfPath: suffix check only, no content sniffing).
    if resolved.suffix.lower() == ".pdf":
        return _read_pdf_text(resolved, cwd, offset=offset, limit=limit)

    try:
        raw = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return FileOpResult(f"Failed to read {resolved}: {exc}", is_error=True)

    offset = max(offset, 1)
    lines = raw.splitlines()
    selected = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{idx + offset}: {line}" for idx, line in enumerate(selected))
    rel = _relative_to(resolved, cwd)
    return FileOpResult(numbered, display_summary=f"Read {rel}")


def _read_pdf_text(resolved: Path, cwd: str, *, offset: int, limit: int) -> FileOpResult:
    """Extract text from a PDF via pypdf and apply the text path's line window."""
    # Lazy import: non-PDF paths must not pay for (or require) pypdf.
    try:
        import pypdf
    except ImportError:
        return FileOpResult(
            f"Cannot read {resolved.name}: .pdf text extraction requires the optional "
            "pypdf dependency, which is not installed. Install the pdf extra to read PDFs: "
            '`uv sync --extra pdf` (or `pip install "nexuscli[pdf]"`).',
            is_error=True,
        )

    try:
        reader = pypdf.PdfReader(str(resolved))
        # Whole-document extraction (minimal slice: no page ranges); empty pages
        # contribute "" so the join never sees None.
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:  # corrupt / encrypted / malformed PDFs raise assorted errors
        return FileOpResult(f"Failed to extract PDF text from {resolved}: {exc}", is_error=True)

    offset = max(offset, 1)
    lines = text.splitlines()
    selected = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{idx + offset}: {line}" for idx, line in enumerate(selected))
    return FileOpResult(numbered, display_summary=f"Read {_relative_to(resolved, cwd)}")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def write_file(
    cwd: str,
    path: str,
    content: str,
    *,
    append: bool = False,
    path_guard_enabled: bool = True,
    run_diagnostics: bool = True,
) -> FileOpResult:
    """Write (or append) *content* to *path*.

    When *run_diagnostics* is True (default) Python files will be
    syntax-checked and results appended to the returned message.
    """
    resolved = resolve_path(cwd, path, path_guard_enabled)
    encoded = content.encode("utf-8")
    if len(encoded) > 5 * 1024 * 1024:
        return FileOpResult("write_file rejected: content exceeds 5 MB", is_error=True)

    resolved.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    try:
        with resolved.open(mode, encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        return FileOpResult(f"Failed to write {resolved}: {exc}", is_error=True)

    rel = _relative_to(resolved, cwd)
    suffix = ""
    if run_diagnostics and resolved.suffix == ".py":
        diagnostics = diagnose_file(resolved)
        if diagnostics:
            suffix = "\n\nDiagnostics:\n" + "\n".join(diagnostics)
    return FileOpResult(f"Wrote {rel}{suffix}", display_summary=f"Wrote {rel}")


# ---------------------------------------------------------------------------
# Edit (surgical line-based replacement — inspired by Claude Code)
# ---------------------------------------------------------------------------


def edit_file(
    cwd: str,
    path: str,
    old_text: str,
    new_text: str,
    *,
    path_guard_enabled: bool = True,
    dry_run: bool = False,
) -> FileOpResult:
    """Replace the first occurrence of *old_text* with *new_text* in *path*.

    This is a surgical find-and-replace that operates on the raw file
    content (not line-based), which makes it suitable for structured edits.
    When *dry_run* is True the diff is returned without modifying the file.
    """
    resolved = resolve_path(cwd, path, path_guard_enabled)
    if not resolved.is_file():
        return FileOpResult(f"Not a file: {resolved}", is_error=True)

    try:
        mtime_before = resolved.stat().st_mtime_ns
        # Strict decoding on purpose: writing back a lossily-decoded file would
        # corrupt the undecodable bytes, so refuse instead.
        original = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return FileOpResult(
            f"edit_file failed: {_relative_to(resolved, cwd)} is not valid UTF-8 text. "
            "Refusing to edit a file that cannot be decoded.",
            is_error=True,
        )
    except OSError as exc:
        return FileOpResult(f"Failed to read {resolved}: {exc}", is_error=True)

    if old_text not in original:
        return FileOpResult(
            "edit_file failed: `old_text` not found in the file. "
            "Provide the exact existing text to replace.",
            is_error=True,
        )

    new_content = original.replace(old_text, new_text, 1)
    if new_content == original:
        return FileOpResult("edit_file: no changes made (old_text == new_text).")

    # Build a simple diff summary
    diff_lines = _build_diff_summary(old_text, new_text)

    if dry_run:
        return FileOpResult(
            f"[DRY RUN] Would edit {_relative_to(resolved, cwd)}\n" + diff_lines,
            display_summary=f"Dry-run edit {_relative_to(resolved, cwd)}",
        )

    try:
        if resolved.stat().st_mtime_ns != mtime_before:
            return FileOpResult(
                "edit_file failed: file changed on disk while editing; "
                "re-read it and retry with fresh content.",
                is_error=True,
            )
    except OSError as exc:
        return FileOpResult(f"Failed to stat {resolved}: {exc}", is_error=True)

    try:
        with resolved.open("w", encoding="utf-8") as handle:
            handle.write(new_content)
    except OSError as exc:
        return FileOpResult(f"Failed to write {resolved}: {exc}", is_error=True)

    rel = _relative_to(resolved, cwd)
    suffix = ""
    if resolved.suffix == ".py":
        diagnostics = diagnose_file(resolved)
        if diagnostics:
            suffix = "\n\nDiagnostics:\n" + "\n".join(diagnostics)

    return FileOpResult(
        f"Edited {rel}\n" + diff_lines + suffix,
        display_summary=f"Edited {rel}",
    )


def _build_diff_summary(old_text: str, new_text: str) -> str:
    """Produce a compact summary of changes (not a full unified diff)."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    parts: list[str] = []
    if len(old_lines) <= 5 and len(new_lines) <= 5:
        for line in old_lines:
            parts.append(f"-{line}")
        for line in new_lines:
            parts.append(f"+{line}")
    else:
        parts.append(f"--- removed {len(old_lines)} line(s)")
        parts.append(f"+++ added {len(new_lines)} line(s)")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def list_directory(
    cwd: str,
    path: str,
    *,
    path_guard_enabled: bool = True,
) -> FileOpResult:
    """List entries in a directory, directories marked with a trailing ``/``."""
    resolved = resolve_path(cwd, path, path_guard_enabled)
    if not resolved.is_dir():
        return FileOpResult(f"Not a directory: {resolved}", is_error=True)

    rows: list[str] = []
    try:
        children = sorted(
            resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        )
    except OSError as exc:
        return FileOpResult(f"Failed to list {resolved}: {exc}", is_error=True)

    for child in children:
        marker = "/" if child.is_dir() else ""
        rows.append(f"{child.name}{marker}")
    return FileOpResult("\n".join(rows) or "(empty directory)")


# ---------------------------------------------------------------------------
# Glob
# ---------------------------------------------------------------------------


def glob_files(
    cwd: str,
    pattern: str,
    *,
    limit: int = 100,
) -> FileOpResult:
    """Find files matching *pattern* relative to *cwd*."""
    root = Path(cwd).resolve()
    if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
        return FileOpResult("glob pattern must stay inside workspace", is_error=True)

    matches = glob_module.glob(str(root / pattern), recursive=True)
    rels: list[str] = []
    for match in sorted(matches):
        mp = Path(match).resolve()
        try:
            rels.append(str(mp.relative_to(root)))
        except ValueError:
            continue
        if len(rels) >= limit:
            break
    return FileOpResult("\n".join(rels) or "(no matches)")


# ---------------------------------------------------------------------------
# Directory tree (recursive, depth-limited)
# ---------------------------------------------------------------------------


def directory_tree(
    cwd: str,
    path: str = ".",
    *,
    max_depth: int = 3,
    path_guard_enabled: bool = True,
    exclude_patterns: tuple[str, ...] | None = None,
) -> FileOpResult:
    """Return a recursive tree view of files and directories.

    *max_depth* limits recursion depth to avoid excessive output.
    """
    resolved = resolve_path(cwd, path, path_guard_enabled)
    if not resolved.is_dir():
        return FileOpResult(f"Not a directory: {resolved}", is_error=True)

    exclude = set(exclude_patterns) if exclude_patterns else SKIP_DIRS

    lines: list[str] = [f"{resolved.name}/"]
    _walk_tree(resolved, resolved, "", max_depth, exclude, lines)
    return FileOpResult("\n".join(lines))


def _walk_tree(
    root: Path,
    current: Path,
    prefix: str,
    max_depth: int,
    exclude: set[str],
    lines: list[str],
) -> None:
    if max_depth <= 0:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError:
        return
    for i, child in enumerate(entries):
        if child.name in exclude:
            continue
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        marker = "/" if child.is_dir() else ""
        lines.append(f"{prefix}{connector}{child.name}{marker}")
        if child.is_dir():
            extension = "    " if is_last else "│   "
            _walk_tree(root, child, prefix + extension, max_depth - 1, exclude, lines)


# ---------------------------------------------------------------------------
# Grep / search
# ---------------------------------------------------------------------------

_RG_SKIP_GLOBS = tuple(f"!{name}/**" for name in sorted(SKIP_DIRS))


def _find_rg() -> str | None:
    """Return the ripgrep executable resolved from PATH, or None.

    Test seam: probed on every grep() invocation and never cached, so tests
    can monkeypatch this symbol to simulate a machine without ripgrep
    (patching it to None) or to an arbitrary executable (failure injection).
    """
    return shutil.which("rg")


def _grep_with_rg(
    rg_path: str,
    start: Path,
    root: Path,
    pattern: str,
    *,
    use_regex: bool,
    limit: int,
) -> list[str] | None:
    """Search *start* with ripgrep and map the output onto the grep() format.

    Returns the collected ``rel:line: text`` lines, or None when ripgrep
    failed and the caller must fall back to the pure-Python scan. The two
    paths are mutually exclusive for a single call, mirroring ZCode's
    createRipgrepFallback which only installs a fallback when no executable
    rg exists in the shell.

    Flag rationale:
    - ``--vimgrep`` emits ``path:line:col:text``, which splits cleanly into
      the existing ``rel:line: text`` result format;
    - ``--no-ignore --hidden`` matches the pure-Python path's ``rglob("*")``
      semantics, which do not honour .gitignore rules and include hidden
      files;
    - ``--max-filesize=1M`` approximates ``MAX_FILE_SIZE = 1_000_000``
      (ripgrep size suffixes are 1024-based, so the exact boundary differs;
      tests use small files and never hit it);
    - ``--fixed-strings`` implements the ``use_regex=False`` literal
      substring match;
    - ``cwd=str(start)`` makes rg print root-relative paths, keeping Windows
      drive letters (``E:\\``) out of the ``path:line`` splitting.

    Skip globs mirror skip_file()'s SKIP_DIRS rule. Each glob must be passed
    via ``--glob``: a bare ``!dir/**`` positional is treated by rg as a
    search path (stderr error, exit code 2; verified against ripgrep 15.2.0).
    """
    command = [
        rg_path,
        "--vimgrep",
        "--no-heading",
        "--with-filename",
        "--no-ignore",
        "--hidden",
        "--max-filesize=1M",
    ]
    for skip_glob in _RG_SKIP_GLOBS:
        command += ["--glob", skip_glob]
    if not use_regex:
        command.append("--fixed-strings")
    command += ["-e", pattern, "."]

    try:
        process = subprocess.Popen(
            command,
            cwd=str(start),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # rg emits UTF-8 regardless of platform; decoding with the locale
            # codec (e.g. cp936 on zh-CN Windows) can raise on CJK match text.
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None

    matches: list[str] = []
    for raw_line in process.stdout:
        parts = raw_line.rstrip("\n").split(":", 3)
        if len(parts) != 4:
            continue
        path_part, line_no_text, _column, text = parts
        try:
            line_no = int(line_no_text)
        except ValueError:
            return None
        try:
            rel = (start / path_part).resolve().relative_to(root)
        except ValueError:
            # Path outside the workspace root: be conservative and fall back.
            return None
        matches.append(f"{rel}:{line_no}: {text.strip()}")
        if len(matches) >= limit:
            process.terminate()
            process.wait()
            return matches

    process.wait()
    # Exit code 1 means "no matches" and is legitimate; any other code is a
    # ripgrep failure and triggers the pure-Python fallback.
    if process.returncode not in (0, 1):
        return None
    return matches


def grep(
    cwd: str,
    pattern: str,
    *,
    path: str = ".",
    limit: int = 100,
    use_regex: bool = True,
    path_guard_enabled: bool = True,
) -> FileOpResult:
    """Search file contents inside *path* (default: workspace root).

    When *use_regex* is True (default) *pattern* is treated as a regular
    expression; otherwise a plain substring match is performed.

    When ripgrep is available on PATH and *path* is a directory, the search
    is delegated to an ``rg`` subprocess and its output is mapped back onto
    the same ``rel:line: text`` result format; when ripgrep is missing or
    the subprocess fails, the pure-Python scan below runs unchanged.
    Known semantic difference: ripgrep skips binary files while the
    pure-Python path reads everything with ``errors="ignore"``, so the
    ripgrep path may return fewer matches inside binary files; results on
    text files are identical (pinned by tests). Deliberately not done: env
    overrides, alternate backends (ugrep/bfs), result caching.
    """
    root = Path(cwd).resolve()
    start = resolve_path(cwd, path, path_guard_enabled)

    try:
        compiled = re.compile(pattern) if use_regex else None
    except re.error as exc:
        return FileOpResult(f"invalid regex: {exc}", is_error=True)

    # Fast path: delegate to ripgrep when it is on PATH. A single-file search
    # has no performance problem, so it always uses the pure-Python scan.
    rg_path = _find_rg()
    if rg_path is not None and start.is_dir():
        matches = _grep_with_rg(rg_path, start, root, pattern, use_regex=use_regex, limit=limit)
        if matches is not None:
            return FileOpResult("\n".join(matches) or "(no matches)")
        # rg unavailable or failed: fall through to the pure-Python scan below.

    matches: list[str] = []
    files = [start] if start.is_file() else [p for p in start.rglob("*") if p.is_file()]

    for file_path in files:
        if skip_file(file_path):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            found = bool(compiled.search(line)) if compiled else pattern in line
            if found:
                matches.append(f"{file_path.relative_to(root)}:{line_number}: {line.strip()}")
                if len(matches) >= limit:
                    return FileOpResult("\n".join(matches))

    return FileOpResult("\n".join(matches) or "(no matches)")


# ---------------------------------------------------------------------------
# File info / metadata
# ---------------------------------------------------------------------------


def get_file_info(
    cwd: str,
    path: str,
    *,
    path_guard_enabled: bool = True,
) -> FileOpResult:
    """Return detailed metadata about a file or directory."""
    resolved = resolve_path(cwd, path, path_guard_enabled)
    if not resolved.exists():
        return FileOpResult(f"Path does not exist: {resolved}", is_error=True)

    try:
        stat = resolved.stat()
    except OSError as exc:
        return FileOpResult(f"Failed to stat {resolved}: {exc}", is_error=True)

    kind = "directory" if resolved.is_dir() else "file"
    size = stat.st_size
    mtime = stat.st_mtime
    rel = _relative_to(resolved, cwd)

    info = (
        f"Path: {rel}\n"
        f"Type: {kind}\n"
        f"Size: {_human_size(size)}\n"
        f"Modified: {mtime:.0f}\n"
        f"Permissions: {oct(stat.st_mode & 0o777)}"
    )
    return FileOpResult(info)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _relative_to(path: Path, cwd: str) -> Path:
    """Return *path* relative to *cwd* when possible, otherwise absolute."""
    try:
        return path.relative_to(Path(cwd).resolve())
    except ValueError:
        return path


def _human_size(size: int) -> str:
    """Format byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}" if isinstance(size, float) else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
