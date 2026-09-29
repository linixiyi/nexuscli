from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools import file_ops as fops
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import save_memory, search_memory


def test_read_write_file_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    context = ToolContext(cwd=str(tmp_path), config=config)

    async def run():
        write = registry.get("write_file")
        read = registry.get("read_file")
        assert write and read
        write_result = await write.execute(
            {"path": "hello.txt", "content": "hello\nworld\n"},
            context,
        )
        read_result = await read.execute({"path": "hello.txt"}, context)
        return write_result, read_result

    write_result, read_result = asyncio.run(run())
    assert not write_result.is_error
    assert "1: hello" in read_result.content
    assert "2: world" in read_result.content


def test_builtin_tools_include_memory_recall_and_skill_sedimentation():
    names = {tool.name for tool in get_builtin_tools()}

    assert "search_memory" in names
    assert "save_skill" in names


def test_memory_tools_save_metadata_and_recall_relevant_items(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)

    saved = asyncio.run(
        save_memory(
            {
                "content": "用户偏好用 uv 执行 Python 测试",
                "kind": "preference",
                "importance": 0.9,
            },
            context,
        )
    )
    recalled = asyncio.run(search_memory({"query": "怎么执行测试"}, context))

    assert not saved.is_error
    assert not recalled.is_error
    assert "uv" in recalled.content
    assert "preference" in recalled.content


# ---------------------------------------------------------------------------
# grep: embedded ripgrep fast path with pure-Python fallback
# ---------------------------------------------------------------------------


def _make_grep_workspace(root: Path) -> None:
    """Create a text workspace with matches, a nested dir and a SKIP_DIRS hit."""
    (root / "alpha.txt").write_text("hello world\nsecond line hello\n", encoding="utf-8")
    (root / "beta.md").write_text("say hello again\n", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "gamma.txt").write_text("no match here\nhello in subdir\n", encoding="utf-8")
    modules = root / "node_modules"
    modules.mkdir()
    (modules / "dep.js").write_text("hello from node_modules\n", encoding="utf-8")


def _expected_grep_lines() -> list[str]:
    """Expected hits for _make_grep_workspace in the canonical rel:line: text format."""
    nested_rel = Path("nested") / "gamma.txt"
    return sorted(
        [
            "alpha.txt:1: hello world",
            "alpha.txt:2: second line hello",
            "beta.md:1: say hello again",
            f"{nested_rel}:2: hello in subdir",
        ]
    )


def test_grep_falls_back_without_rg(tmp_path, monkeypatch):
    """Patching _find_rg to None must exercise the pure-Python scan unchanged."""
    monkeypatch.setattr("nexuscli.tools.file_ops._find_rg", lambda: None)
    _make_grep_workspace(tmp_path)

    result = fops.grep(str(tmp_path), "hello", use_regex=False)

    assert not result.is_error
    assert sorted(result.content.splitlines()) == _expected_grep_lines()
    assert "node_modules" not in result.content
    # Literal substring matching stays case-sensitive on the fallback path.
    assert fops.grep(str(tmp_path), "HELLO", use_regex=False).content == "(no matches)"
    # Regex mode still works when ripgrep is absent.
    regex_result = fops.grep(str(tmp_path), "hel+o")
    assert sorted(regex_result.content.splitlines()) == _expected_grep_lines()
    # limit truncation is honoured.
    truncated = fops.grep(str(tmp_path), "hello", use_regex=False, limit=2)
    assert len(truncated.content.splitlines()) == 2


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_grep_matches_fallback_when_rg_available(tmp_path, monkeypatch):
    """The rg fast path must agree with the pure-Python baseline on text files."""
    _make_grep_workspace(tmp_path)

    # Pure-Python baseline: patch _find_rg to None, then restore the real probe.
    monkeypatch.setattr("nexuscli.tools.file_ops._find_rg", lambda: None)
    baseline = fops.grep(str(tmp_path), "hello", use_regex=False)
    monkeypatch.undo()

    fast = fops.grep(str(tmp_path), "hello", use_regex=False)

    # rg and rglob traversal orders are not pinned to each other, so the two
    # runs are compared as sorted line sets rather than raw sequences.
    assert sorted(fast.content.splitlines()) == sorted(baseline.content.splitlines())
    assert sorted(fast.content.splitlines()) == _expected_grep_lines()
    assert "node_modules" not in fast.content
    # Regex mode through real ripgrep agrees with the fallback too.
    fast_regex = fops.grep(str(tmp_path), "hel+o")
    assert sorted(fast_regex.content.splitlines()) == _expected_grep_lines()


def test_grep_falls_back_when_rg_fails(tmp_path, monkeypatch):
    """A non-rg executable must trigger the fallback (win32-safe failure injection)."""
    # python.exe always rejects --vimgrep with exit code 2, so _grep_with_rg
    # sees a ripgrep failure and grep() falls back to the pure-Python scan.
    monkeypatch.setattr("nexuscli.tools.file_ops._find_rg", lambda: sys.executable)
    _make_grep_workspace(tmp_path)

    result = fops.grep(str(tmp_path), "hello", use_regex=False)

    assert not result.is_error
    assert sorted(result.content.splitlines()) == _expected_grep_lines()
    assert "node_modules" not in result.content
