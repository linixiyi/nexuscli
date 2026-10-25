#!/usr/bin/env python3
"""从 src/nexuscli/runtime/api.py 的 ROUTE_MANIFEST 生成 docs/runtime-api.md 端点表。

模式照搬（Ported from kitagent (MIT, zed123214/kitagent)
scripts/generate_wire_protocol.py：代码常量是文档的唯一事实源，生成 + 校验双
入口，生成内容标注 Do not edit manually）。差异：本仓库只重写 docs/runtime-api.md
里的 BEGIN/END GENERATED 围栏节，其余手写小节原样保留；对常量做 ast 解析而非
import——文档生成不需要拉起 api.py 的依赖树。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_API_SOURCE = _ROOT / "src" / "nexuscli" / "runtime" / "api.py"
_DOCS_PATH = _ROOT / "docs" / "runtime-api.md"

_BEGIN_MARKER = "<!-- BEGIN GENERATED: endpoint-table (do not edit manually) -->"
_END_MARKER = "<!-- END GENERATED: endpoint-table -->"


def _manifest_entries(source: str) -> list[tuple[str, str, str, str]]:
    """ast 提取 api.py 模块级 ROUTE_MANIFEST（(method, path_pattern, auth, summary)）。

    结构不合法（非四元组 / 非字符串字面量 / 清单为空 / 常量缺失）一律 SystemExit：
    文档同步宁可红也不静默产出残缺表。
    """

    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        if "ROUTE_MANIFEST" not in names or not isinstance(node.value, ast.Tuple):
            continue
        entries: list[tuple[str, str, str, str]] = []
        for element in node.value.elts:
            if not isinstance(element, ast.Tuple) or len(element.elts) != 4:
                raise SystemExit("ERROR: ROUTE_MANIFEST 条目必须是四元组")
            if not all(
                isinstance(field, ast.Constant) and isinstance(field.value, str)
                for field in element.elts
            ):
                raise SystemExit("ERROR: ROUTE_MANIFEST 条目字段必须全是字符串字面量")
            method, path_pattern, auth, summary = (str(field.value) for field in element.elts)
            entries.append((method, path_pattern, auth, summary))
        if not entries:
            raise SystemExit("ERROR: ROUTE_MANIFEST 为空，拒绝生成空端点表")
        return entries
    raise SystemExit(f"ERROR: {_API_SOURCE} 未找到模块级 ROUTE_MANIFEST 常量")


def generate() -> str:
    """渲染端点表 markdown（围栏节内内容，不含 BEGIN/END 标记本身）。"""

    entries = _manifest_entries(_API_SOURCE.read_text(encoding="utf-8"))
    lines = [
        "> 本节由 `scripts/generate_endpoint_docs.py` 从 `src/nexuscli/runtime/api.py` "
        "的 `ROUTE_MANIFEST` 生成，**请勿手改**。",
        "",
        "| 方法 | 路径 | 鉴权 | 语义 |",
        "| --- | --- | --- | --- |",
    ]
    for method, path_pattern, auth, summary in entries:
        lines.append(f"| {method} | `{path_pattern}` | {auth} | {summary} |")
    return "\n".join(lines) + "\n"


def _render_doc(doc_text: str, section: str) -> str:
    """用 section 替换 docs 内 BEGIN/END 围栏之间的内容；标记缺失即报错。"""

    begin = doc_text.find(_BEGIN_MARKER)
    end = doc_text.find(_END_MARKER)
    if begin == -1 or end == -1 or end < begin:
        raise SystemExit(
            f"ERROR: {_DOCS_PATH} 缺少端点表围栏标记（BEGIN/END GENERATED: endpoint-table）"
        )
    head = doc_text[: begin + len(_BEGIN_MARKER)]
    tail = doc_text[end:]
    return f"{head}\n{section}\n{tail}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate the endpoint table in docs/runtime-api.md"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the docs match ROUTE_MANIFEST; exit 1 on drift",
    )
    args = parser.parse_args(argv)

    if not _DOCS_PATH.exists():
        print(
            f"ERROR: {_DOCS_PATH} not found — the fenced endpoint-table section must exist",
            file=sys.stderr,
        )
        raise SystemExit(1)
    section = generate()
    doc_text = _DOCS_PATH.read_text(encoding="utf-8")
    updated = _render_doc(doc_text, section)
    if args.check:
        if updated != doc_text:
            print(
                f"ERROR: {_DOCS_PATH} endpoint table out of sync with ROUTE_MANIFEST — "
                "run: uv run python scripts/generate_endpoint_docs.py",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"OK: {_DOCS_PATH} endpoint table is up to date.")
        return
    _DOCS_PATH.write_text(updated, encoding="utf-8")
    print(f"Generated endpoint table in {_DOCS_PATH}")


if __name__ == "__main__":
    main()
