"""Path-scope search over the code index (rag scope lane).

Pins the subtree-scoping contract added on top of BM25 search: scope 限定在
子树内命中、子树外排除、嵌套 scope、``scope=None`` 与现状逐字节兼容、分隔符与
相对/绝对形式归一、scope 无命中返回空列表而非报错、search_code 工具透传。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from nexuscli.config import load_config
from nexuscli.rag import CodeIndex
from nexuscli.rag.path_scope import normalize_scope, scope_matches
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext


def _make_index(tmp_path: Path, files: dict[str, str]) -> CodeIndex:
    for name, content in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.rebuild()
    return index


_SCOPE_FIXTURE = {
    "src/app.py": "needle here\nsecond needle line\n",
    "src/utils/helpers.py": "needle in helper\n",
    "docs/guide.md": "needle in docs\n",
    "README.md": "needle at root\n",
}


def _paths(results) -> set[str]:
    return {item.path for item in results}


def test_scope_limits_hits_to_subtree(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    results = index.search("needle", scope="src")
    assert _paths(results) == {"src/app.py", "src/utils/helpers.py"}
    results, stats = index.search_with_stats("needle", scope="docs")
    assert _paths(results) == {"docs/guide.md"}
    assert stats.matched_rows == 1
    assert stats.returned == 1


def test_scope_excludes_outside_subtree(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    results = index.search("needle", scope="src")
    assert "docs/guide.md" not in _paths(results)
    assert "README.md" not in _paths(results)


def test_nested_scope_and_ancestor_inclusion(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    # 祖先 scope 包含后代子树的命中。
    assert "src/utils/helpers.py" in _paths(index.search("needle", scope="src"))
    # 嵌套 scope 收窄到子目录，祖先文件被排除。
    nested = index.search("needle", scope="src/utils")
    assert _paths(nested) == {"src/utils/helpers.py"}


def test_none_scope_is_byte_compatible(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    legacy = index.search_with_stats("needle")
    explicit = index.search_with_stats("needle", scope=None)
    assert [(r.path, r.line, r.snippet, r.score) for r in legacy[0]] == [
        (r.path, r.line, r.snippet, r.score) for r in explicit[0]
    ]
    assert legacy[1] == explicit[1]
    # 未命中词与空查询同样保持原行为。
    assert index.search("zzzz", scope=None) == []
    assert index.search("   ", scope=None) == []


def test_scope_normalizes_separators_and_path_forms(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    baseline = index.search("needle", scope="src/utils")
    assert _paths(baseline) == {"src/utils/helpers.py"}
    # 反斜杠分隔符（win32 风格写法）归一到 POSIX。
    assert [(r.path, r.line) for r in index.search("needle", scope="src\\utils")] == [
        (r.path, r.line) for r in baseline
    ]
    # 尾随 / 与 ./ 前缀归一。
    assert [(r.path, r.line) for r in index.search("needle", scope="./src/utils/")] == [
        (r.path, r.line) for r in baseline
    ]
    # 绝对路径归一到仓库根相对形式。
    absolute = f"{tmp_path}/src/utils"
    assert [(r.path, r.line) for r in index.search("needle", scope=absolute)] == [
        (r.path, r.line) for r in baseline
    ]
    # scope 指向仓库根本身时等价于全库检索。
    assert _paths(index.search("needle", scope=".")) == _paths(index.search("needle"))


def test_scope_without_hits_returns_empty_list(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    # 不存在的子树：返回空列表，不抛错。
    assert index.search("needle", scope="does_not_exist") == []
    results, stats = index.search_with_stats("needle", scope="does_not_exist")
    assert results == []
    assert stats.matched_rows == 0
    # 根外的绝对路径与越出根的 .. 形式同样返回空。
    assert index.search("needle", scope="/etc") == []
    assert index.search("needle", scope="../outside") == []
    results, _ = index.search_with_stats("needle", scope="/etc")
    assert results == []


def test_scope_escapes_like_wildcards_in_directory_names(tmp_path):
    index = _make_index(
        tmp_path,
        {
            "my_dir/a.py": "needle in real dir\n",
            "myXdir/b.py": "needle in lookalike dir\n",
        },
    )
    # LIKE 通配未转义时 _ 会吞掉任意字符，把 myXdir 的行错误放进 my_dir 的结果。
    assert _paths(index.search("needle", scope="my_dir")) == {"my_dir/a.py"}


def test_scope_matches_backslash_stored_paths(tmp_path):
    # win32 上 rebuild 经 str(Path.relative_to(...)) 落库的是反斜杠路径；
    # 这里在任意平台模拟该存储形态，守护 scope 限定不因分隔符静默漏检。
    index = _make_index(tmp_path, {"src/utils/helpers.py": "needle in helper\n"})
    with index._connect() as conn:
        conn.execute(
            "update code_chunks set path = replace(path, '/', ?) where root = ?",
            ("\\", str(index.root)),
        )
    stored = index.search("needle")
    assert _paths(stored) == {"src\\utils\\helpers.py"}  # 模拟生效

    results = index.search("needle", scope="src/utils")
    assert _paths(results) == {"src\\utils\\helpers.py"}
    # scope 入参用反斜杠写法同样命中（归一后进同一子树匹配）。
    assert [(r.path, r.line) for r in index.search("needle", scope="src\\utils")] == [
        (r.path, r.line) for r in results
    ]


def test_normalize_scope_contract():
    assert normalize_scope("/repo", "src") == "src"
    assert normalize_scope("/repo", "src/") == "src"
    assert normalize_scope("/repo", "./src") == "src"
    assert normalize_scope("/repo", "src\\utils") == "src/utils"
    assert normalize_scope("/repo", ".") == ""
    assert normalize_scope("/repo", "/repo/src") == "src"
    assert normalize_scope("/repo", "/elsewhere") is None
    assert normalize_scope("/repo", "../outside") is None
    assert normalize_scope("/repo", "src/../docs") == "docs"


def test_scope_matches_boundary():
    assert scope_matches("src/app.py", "src")
    assert scope_matches("src", "src")
    assert not scope_matches("srcx/app.py", "src")  # 前缀必须落在分隔符边界上
    assert scope_matches("anything", "")
    assert not scope_matches("docs/guide.md", "src")


# ---------------------------------------------------------------------------
# search_code 工具透传
# ---------------------------------------------------------------------------


def _call_tool(cwd: Path, payload: dict) -> str:
    context = ToolContext(cwd=str(cwd), config=load_config(project_root=str(cwd)))
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get("search_code")
    assert tool is not None
    result = asyncio.run(tool.execute(payload, context))
    return result.content


def test_search_code_tool_passes_scope_through(tmp_path):
    index = _make_index(tmp_path, _SCOPE_FIXTURE)
    assert index.search("needle")  # 索引非空

    scoped = _call_tool(tmp_path, {"query": "needle", "scope": "docs"})
    assert "docs/guide.md" in scoped
    assert "src/app.py" not in scoped

    unscoped = _call_tool(tmp_path, {"query": "needle"})
    assert "docs/guide.md" in unscoped
    assert "src/app.py" in unscoped

    # 工具路径同样遵守「无命中返回空结果提示」而非报错。
    empty = _call_tool(tmp_path, {"query": "needle", "scope": "does_not_exist"})
    assert empty == "(no indexed matches; run /index first)"
