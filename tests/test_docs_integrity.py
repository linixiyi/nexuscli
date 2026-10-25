"""README/docs 文档完整性机械校验（spec 2026-10-25_update-readme-layering）。

前四个用例对应 03 规划文档「校验面」的定稿：
1. README 目录锚点解析（剔除 fenced code block 后的标题 slug 集合）
2. README / docs / TUTORIAL 相对链接目标文件存在
3. `path.md#frag` 形态链接的 fragment 命中目标文件标题 slug 集合
4. README 行数预算 + site/build.py 白名单三集合断言（DOC_META == PAGER_ORDER == docs/*.md stems）

第 5 个为 lane A3「文档鉴权滞后修复」增补（spec 2026-10-25_port-alignment-quick-wins）：
5. docs/workbench.md 与 docs/runtime-api.md 必须包含 bootstrap / csrf / cookie
   关键词——F14 鉴权改三件套后两篇文档仍只写 x-api-key 的腐烂防护

锚点算法与 site/build.py 的 CJKSlugify 同一口径（GitHub 规则）：
小写 → 删除非（Unicode 词字符/空白/连字符）→ 空格逐一替换为连字符 → 重复标题 -N 去重。
用例是纯仓库文件检查，不触碰 Path.home()；conftest 的 HOME/USERPROFILE 重定向照常生效。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TUTORIAL = ROOT / "TUTORIAL.md"
DOCS = ROOT / "docs"
SITE_BUILD = ROOT / "site" / "build.py"

README_LINE_MIN = 360
README_LINE_MAX = 440

_INLINE_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _slugify(title: str) -> str:
    text = title.strip().lower()
    kept: list[str] = []
    for ch in text:
        if ch.isalnum() or ch == "_" or ch == "-" or ch == " ":
            kept.append(ch)
    return "".join(kept).replace(" ", "-")


def _strip_fenced_blocks(text: str) -> str:
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            inside = not inside
            out.append("")
            continue
        out.append("" if inside else line)
    return "\n".join(out)


def _heading_slugs(text: str) -> set[str]:
    slugs: dict[str, int] = {}
    for line in _strip_fenced_blocks(text).splitlines():
        match = _HEADING.match(line)
        if not match:
            continue
        slug = _slugify(match.group(2))
        if not slug:
            continue
        count = slugs.get(slug, 0)
        slugs[slug] = count + 1
        if count:
            slugs[f"{slug}-{count}"] = 0
    return set(slugs)


def _inline_targets(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return _INLINE_LINK.findall(text)


def test_readme_toc_anchors_resolve() -> None:
    slugs = _heading_slugs(README.read_text(encoding="utf-8"))
    broken: list[str] = []
    for target in _inline_targets(README):
        if not target.startswith("#"):
            continue
        frag = target[1:]
        if frag not in slugs:
            broken.append(target)
    assert not broken, f"README 内锚点未命中标题 slug: {broken}"


def test_relative_links_exist() -> None:
    files = [README, TUTORIAL, *sorted(DOCS.glob("*.md"))]
    broken: list[str] = []
    for path in files:
        for target in _inline_targets(path):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            rel = target.split("#", 1)[0]
            if not rel:
                continue
            resolved = (path.parent / rel).resolve()
            if not resolved.exists():
                broken.append(f"{path.name} -> {target}")
    assert not broken, f"相对链接目标不存在: {broken}"


def test_anchor_fragments_resolve() -> None:
    files = [README, TUTORIAL, *sorted(DOCS.glob("*.md"))]
    broken: list[str] = []
    for path in files:
        for target in _inline_targets(path):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if "#" not in target:
                continue
            rel, frag = target.split("#", 1)
            if not rel or not frag:
                continue
            target_file = (path.parent / rel).resolve()
            if not target_file.is_file():
                continue  # 文件存在性由 test_relative_links_exist 负责
            slugs = _heading_slugs(target_file.read_text(encoding="utf-8"))
            if frag not in slugs:
                broken.append(f"{path.name} -> {target}")
    assert not broken, f"节级锚点未命中目标文件标题: {broken}"


def test_readme_line_budget_and_site_registry() -> None:
    line_count = len(README.read_text(encoding="utf-8").splitlines())
    assert README_LINE_MIN <= line_count <= README_LINE_MAX, (
        f"README {line_count} 行超出验收带 [{README_LINE_MIN}, {README_LINE_MAX}]"
    )

    tree = ast.parse(SITE_BUILD.read_text(encoding="utf-8"))
    doc_meta: dict[str, str] = {}
    pager_order: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "DOC_META" in names and isinstance(node.value, ast.Dict):
            doc_meta = {key.value: "" for key in node.value.keys if isinstance(key, ast.Constant)}
        if "PAGER_ORDER" in names and isinstance(node.value, ast.List):
            pager_order = [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)]
    assert doc_meta, "site/build.py 未解析到 DOC_META"
    assert pager_order, "site/build.py 未解析到 PAGER_ORDER"

    stems = {p.stem for p in DOCS.glob("*.md")}
    assert set(doc_meta) == set(pager_order) == stems, (
        f"白名单与 docs 目录不一致: 仅 DOC_META={sorted(set(doc_meta) - stems)}, "
        f"仅 PAGER_ORDER={sorted(set(pager_order) - stems)}, "
        f"仅 docs={sorted(stems - set(doc_meta))}"
    )


def test_workbench_auth_keywords_present() -> None:
    """文档腐烂机械防护：两篇 API 文档必须提及 F14 浏览器会话鉴权三件套。

    F14 把工作台鉴权改为「一次性 #bootstrap 口令 → HttpOnly SameSite=strict cookie
    → 变更请求 x-csrf-token → Origin 白名单；x-api-key 头与 SSE api_key 查询参数
    保留为外部通道」（src/nexuscli/runtime/api.py 的 _handle/_handle_bootstrap 与
    src/nexuscli/runtime/websession.py）后，两篇文档仍只写 x-api-key 滞后过一次——
    关键词断言防复发。大小写不敏感：文档中 cookie 以 Set-Cookie、csrf 以
    x-csrf-token 等形态出现。
    """

    keywords = ("bootstrap", "csrf", "cookie")
    paths = [ROOT / "docs" / "workbench.md", ROOT / "docs" / "runtime-api.md"]
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        missing = [kw for kw in keywords if kw not in text]
        assert not missing, (
            f"{path.name} 缺少浏览器会话鉴权关键词 {missing}；"
            "鉴权语义见 src/nexuscli/runtime/websession.py"
        )
