"""Path-prefix subtree scoping for rag search (semantic port of OpenViking PathScope).

语义来源（语义改写，非逐字搬运）：OpenViking ``openviking/storage/expr.py`` 的
``PathScope`` —— 路径前缀 scope 表达式，带相对深度控制（``depth < 0`` 无界子树、
``depth == 0`` 仅节点自身、正深度为节点加有限层后代），@ commit
9a57854d51dd598e10a96f4d805268a0f1c16736；其求值契约见同上游
``storage/vectordb_adapters/base.py``（编译为前缀条件 + ``-d=`` 深度参数）与
``storage/vectordb_adapters/opengauss/sql.py`` 的 ``_build_path_scope_clause``
（节点自身或 ``prefix + '/'`` 后代、LIKE 转义、尾随分隔符归一）。

本模块只取无界子树（depth < 0）一档：nexuscli 的 code index 按相对仓库根的
POSIX 相对路径存行，scope 检索需要的是「节点自身或其后代」的前缀匹配，不需要
表达式 AST 与深度档位，故不逐字搬运 ``expr.py`` 的九个 dataclass，也不引入上游
求值栈（vectordb/backend 类型轴）。
"""

from __future__ import annotations

from pathlib import Path


def normalize_scope(root: str | Path, scope: str) -> str | None:
    """Normalize *scope* to a POSIX path prefix relative to *root*.

    Accepts relative or absolute input, either path separator, optional leading
    ``./`` and trailing ``/``. Returns:

    - ``""`` when the scope resolves to the root itself (match everything);
    - a POSIX prefix such as ``"src/sub"`` for a subtree;
    - ``None`` when the scope cannot intersect the index (absolute path outside
      the root, or ``..`` escaping the root) — callers return no rows instead
      of raising, per the search contract.
    """
    text = scope.replace("\\", "/").strip()
    if not text or text in {".", "/"}:
        return ""
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(Path(root).resolve())
        except ValueError:
            return None
    parts: list[str] = []
    for part in candidate.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def scope_matches(path: str, prefix: str) -> bool:
    """True when *path* (stored POSIX relative path) lies inside the *prefix* subtree.

    Mirrors the upstream PathScope contract "the node itself or its descendants":
    exact match, or a path starting with ``prefix + "/"``. An empty prefix (the
    root itself) matches everything.
    """
    if not prefix:
        return True
    normalized = path.replace("\\", "/")
    return normalized == prefix or normalized.startswith(prefix + "/")


def scope_like_clause(prefix: str) -> tuple[str, list[str]] | None:
    """SQL fragment restricting the ``path`` column to the *prefix* subtree.

    Returns ``(clause, params)`` to AND into the candidate query, or ``None``
    when the prefix is empty (root itself — no extra clause, matching the
    unscoped query shape). ``%``/``_``/``\\`` in the prefix are escaped so
    directory names containing LIKE wildcards still match literally, mirroring
    upstream ``_escape_like_pattern``.

    The clause matches both POSIX (``a/b``) and win32 (``a\\b``) storage forms:
    ``code_chunks.path`` is stored via ``str(Path.relative_to(...))``
    (``code_index.rebuild``), which uses backslash separators on Windows, so a
    POSIX-only pattern would silently match nothing there. Each form only ever
    matches rows stored in its own separator style, and the Python-side
    ``scope_matches`` (separator-normalizing, case-sensitive) makes the final
    verdict.
    """
    if not prefix:
        return None

    def _escape_like(pattern: str) -> str:
        return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    win_prefix = prefix.replace("/", "\\")
    return (
        "(path = ? or path like ? escape '\\' or path = ? or path like ? escape '\\')",
        [
            prefix,
            f"{_escape_like(prefix)}/%",
            win_prefix,
            f"{_escape_like(win_prefix)}\\\\%",
        ],
    )
