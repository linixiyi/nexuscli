"""Tests for glob_files noise-directory filtering (SKIP_DIRS)."""

from __future__ import annotations

from nexuscli.tools.file_ops import glob_files


def test_glob_recursive_py_excludes_noise_dirs(tmp_path):
    """`**/*.py` drops __pycache__/.venv hits but keeps real sources."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cache.py").write_text("", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "c.py").write_text("", encoding="utf-8")

    result = glob_files(str(tmp_path), "**/*.py")

    assert not result.is_error
    lines = result.content.splitlines()
    assert "src/a.py" in lines
    assert not any(line.startswith("__pycache__/") for line in lines)
    assert not any(line.startswith(".venv/") for line in lines)


def test_glob_limit_counts_only_filtered_hits(tmp_path):
    """limit=1 must not be consumed by noise entries that sort earlier."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("x = 2", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "z.py").write_text("", encoding="utf-8")

    result = glob_files(str(tmp_path), "**/*.py", limit=1)

    assert not result.is_error
    # Without post-filter truncation, .venv/a.py (lexicographically first)
    # would consume the whole quota and hide the real source.
    assert result.content == "src/a.py"


def test_glob_star_pattern_hides_noise_dir_itself(tmp_path):
    """When `*` matches a noise directory itself, the directory is dropped."""
    (tmp_path / "keep.txt").write_text("", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("", encoding="utf-8")

    result = glob_files(str(tmp_path), "*")

    assert not result.is_error
    lines = result.content.splitlines()
    assert "keep.txt" in lines
    assert "node_modules" not in lines


def test_glob_sort_stays_lexicographic(tmp_path):
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    (tmp_path / "a.txt").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")

    result = glob_files(str(tmp_path), "*.txt")

    assert result.content == "a.txt\nb.txt\nc.txt"


def test_glob_no_matches_and_out_of_bounds_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("", encoding="utf-8")

    empty = glob_files(str(tmp_path), "*.nonexistent")
    assert not empty.is_error
    assert empty.content == "(no matches)"

    absolute = glob_files(str(tmp_path), "/etc/*")
    assert absolute.is_error
    assert "must stay inside workspace" in absolute.content
