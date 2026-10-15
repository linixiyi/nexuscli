# Local docs-site generator.
# Renders docs/*.md + TUTORIAL.md into site/ HTML pages with a shared shell.
# Run: uv run --with markdown,pygments python site/build.py
from __future__ import annotations

import html
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

BASE = "https://linixiyi.github.io/nexuscli/"
REPO = "https://github.com/linixiyi/nexuscli"

# docs/*.md -> (icon, one-line description for cards / meta / pager)
DOC_META = {
    "cron": ("⏰", "离线任务注册表与外部调度器驱动方式"),
    "eval": ("🧪", "脚本化场景、断言体系与回归 diff 门禁"),
    "hooks": ("🪝", "七个生命周期事件的挂载方式与安全约束"),
    "mcp": ("🔌", "stdio / HTTP server 接入、工具过滤与自身暴露为 server"),
    "observability": ("🔭", "trace_id、OTel 遥测、模型 IO 落盘与 usage 统计"),
    "permissions": ("🔐", "allow / deny / ask 三组规则、命令级判定与审批透传"),
    "plugins": ("🧱", "本地目录插件的发现、manifest 与组件并入链"),
    "runtime-api": ("📡", "线程、turn、事件流与后台任务的 HTTP 接入面"),
    "slash-commands": ("🪄", "markdown 提示词、frontmatter 与占位符注入"),
    "spec-skill": ("📐", "规范驱动开发技能的核心概念与质量门禁"),
    "subagents": ("🧩", "后台委派、自定义代理定义与文件邮箱"),
    "workflows": ("🔀", "JSON 步骤图 DAG、依赖批次并行与内置专家流程"),
}

# reading order for the bottom pager on doc pages
PAGER_ORDER = [
    "permissions",
    "hooks",
    "mcp",
    "subagents",
    "workflows",
    "runtime-api",
    "plugins",
    "slash-commands",
    "observability",
    "cron",
    "eval",
    "spec-skill",
]

FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
    "%3Crect width='100' height='100' rx='22' fill='%237c5cff'/%3E"
    "%3Ctext x='50' y='68' font-size='52' text-anchor='middle' fill='white' "
    "font-family='monospace' font-weight='bold'%3EN%3C/text%3E%3C/svg%3E"
)

THEME_HEAD = """<script>
    (function () {
      try {
        var t = localStorage.getItem("nexuscli-theme");
        if (!t) t = window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", t);
      } catch (e) {}
    })();
  </script>"""


# --------------------------------------------------------------------------
# markdown rendering
# --------------------------------------------------------------------------

_GITHUB_STRIP = re.compile(r"[^\w\s-]", re.UNICODE)  # \w covers CJK; emoji/punct removed


class CJKSlugify:
    """GitHub-compatible slugger: keeps CJK, strips emoji/punctuation,
    spaces -> dashes (1:1, no collapsing), dedupes with -N suffix."""

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def __call__(self, value: str, separator: str = "-") -> str:
        s = value.lower()
        s = _GITHUB_STRIP.sub("", s)
        s = s.replace(" ", separator)
        n = self._seen.get(s, 0)
        self._seen[s] = n + 1
        return s if n == 0 else f"{s}{separator}{n}"


def render_markdown(text: str) -> tuple[str, str]:
    slugify = CJKSlugify()
    md = markdown.Markdown(
        extensions=["extra", "sane_lists", "toc", "codehilite", "admonition"],
        extension_configs={
            "toc": {"toc_depth": "2-4", "anchorlink": True, "slugify": slugify},
            "codehilite": {
                "guess_lang": False,
                "use_pygments": False,  # emit language-* classes; highlight via highlight.js
            },
        },
        output_format="html5",
    )
    body = md.convert(text)
    toc_html = md.toc
    md.reset()
    return body, toc_html


def strip_tags(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def first_h1(markdown_src: str) -> str:
    m = re.search(r"^#\s+(.+)$", markdown_src, re.M)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------
# link rewriting: repo-relative .md links -> site pages / GitHub
# --------------------------------------------------------------------------

_MD_LINK = re.compile(r'href="([^"#]+\.md)(#[^"]*)?"')


def rewrite_links(html_str: str, in_docs_dir: bool) -> str:
    def repl(m: re.Match[str]) -> str:
        target, frag = m.group(1), m.group(2) or ""
        if target.startswith(("http://", "https://", "/")):
            return m.group(0)  # leave absolute URLs alone
        name = Path(target).name
        prefix = "" if in_docs_dir else "docs/"
        if name == "README.md":
            url = REPO if not frag else f"{REPO}/blob/main/README.md{frag}"
            return f'href="{url}"'
        if name == "TUTORIAL.md":
            page = "tutorial.html"
            if not in_docs_dir:
                pass
            else:
                page = f"../{page}"
            return f'href="{page}{frag}"'
        return f'href="{prefix}{name[:-3]}.html{frag}"'

    return _MD_LINK.sub(repl, html_str)


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------


def nav_html(rel: str) -> str:
    return f"""<nav class="nav">
    <div class="nav-inner">
      <a class="brand" href="{rel}index.html"><span class="brand-logo">N</span>Nexus<b>CLI</b></a>
      <div class="nav-links">
        <a href="{rel}index.html#features">功能</a>
        <a href="{rel}index.html#docs">文档</a>
        <a href="{rel}tutorial.html">教程</a>
      </div>
      <div class="nav-spacer"></div>
      <a class="nav-gh" href="{REPO}" target="_blank" rel="noopener">★ GitHub</a>
      <button class="theme-btn" id="themeToggle" aria-label="切换主题">☀</button>
    </div>
  </nav>"""


def shell(*, title: str, url: str, body: str, rel: str, desc: str = "") -> str:
    desc_tag = f'\n  <meta name="description" content="{html.escape(desc)}">' if desc else ""
    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>{desc_tag}
  <meta property="og:title" content="{html.escape(title)}">
  <meta property="og:url" content="{url}">
  <link rel="icon" href="{FAVICON}">
  <link rel="stylesheet" href="{rel}style.css">
  {THEME_HEAD}
</head>
<body>
{nav_html(rel)}
{body}
<footer>
  <p>NexusCLI · <a href="{REPO}" target="_blank" rel="noopener">GitHub</a> · MIT License</p>
  <p><a href="{rel}index.html">← 返回首页</a></p>
</footer><script src="{rel}site.js"></script>
<script src="https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/highlight.min.js"></script>
<script>
    (function () {{
      var LIGHT = "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github.min.css";
      var DARK = "https://cdn.jsdelivr.net/gh/highlightjs/cdn-release@11.9.0/build/styles/github-dark.min.css";
      var link = document.createElement("link");
      link.rel = "stylesheet";
      var apply = function () {{
        var light = document.documentElement.getAttribute("data-theme") === "light";
        link.href = light ? LIGHT : DARK;
      }};
      apply();
      document.head.appendChild(link);
      window.addEventListener("nexuscli-themechange", apply);
      try {{
        new MutationObserver(apply).observe(
          document.documentElement,
          {{ attributes: true, attributeFilter: ["data-theme"] }}
        );
      }} catch (e) {{}}
    }})();
  </script>
  <script>
    hljs.highlightAll();
  </script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def doc_layout(toc_html: str, content_html: str) -> str:
    empty_toc = "<p style='color:var(--faint);font-size:13px'>（本页暂无目录）</p>"
    toc = toc_html if toc_html.strip() else empty_toc
    return f"""<div class="layout">
  <aside class="toc">
    <div class="toc-title">目录</div>
    {toc}
  </aside>
  <div class="content">
    {content_html}
  </div>
</div>"""


def build_doc_page(name: str) -> None:
    icon, blurb = DOC_META[name]
    src = (ROOT / "docs" / f"{name}.md").read_text(encoding="utf-8")
    body, toc_html = render_markdown(src)
    body = rewrite_links(body, in_docs_dir=True)
    title_text = first_h1(src) or name

    idx = PAGER_ORDER.index(name)
    prev = PAGER_ORDER[idx - 1] if idx > 0 else None
    nxt = PAGER_ORDER[idx + 1] if idx + 1 < len(PAGER_ORDER) else None

    pager_bits = []
    if prev:
        p_icon, _ = DOC_META[prev]
        p_title = first_h1((ROOT / "docs" / f"{prev}.md").read_text(encoding="utf-8"))
        pager_bits.append(
            f'<a href="{prev}.html" class="prev"><span class="dir">← 上一篇</span>'
            f'<span class="name">{p_icon} {p_title}</span></a>'
        )
    else:
        pager_bits.append(
            '<a href="../index.html" class="prev"><span class="dir">← 返回</span>'
            '<span class="name">站点首页</span></a>'
        )
    if nxt:
        n_icon, _ = DOC_META[nxt]
        n_title = first_h1((ROOT / "docs" / f"{nxt}.md").read_text(encoding="utf-8"))
        pager_bits.append(
            f'<a href="{nxt}.html" class="next"><span class="dir">下一篇 →</span>'
            f'<span class="name">{n_icon} {n_title}</span></a>'
        )
    else:
        pager_bits.append(
            '<a href="../tutorial.html" class="next"><span class="dir">最后一篇 →</span>'
            '<span class="name">🚀 运行教程</span></a>'
        )
    pager_html = f'<div class="pager">{"".join(pager_bits)}</div>'
    edit_link = (
        f'<p class="edit-link">本页由 <a href="{REPO}/blob/main/docs/{name}.md" '
        f'target="_blank" rel="noopener">docs/{name}.md</a> 构建生成 · 源文件与站点内容同步</p>'
    )
    pager_html += edit_link

    content = (
        '<div class="doc-meta">'
        '<a class="crumb" href="../index.html#docs">← 文档中心</a>'
        f"<span>·</span><span>{icon} {blurb}</span>"
        "</div>"
        '<div class="generated-banner">📄 本页由仓库 '
        f"<code>docs/{name}.md</code> 自动构建生成；如需修改请编辑源文件后重新构建。</div>"
        + body
        + pager_html
    )

    page = shell(
        title=f"{title_text} · NexusCLI 文档",
        url=f"{BASE}docs/{name}.html",
        desc=f"NexusCLI 文档：{blurb}。",
        body=doc_layout(toc_html, content),
        rel="../",
    )
    out = SITE / "docs"
    out.mkdir(exist_ok=True)
    (out / f"{name}.html").write_text(page, encoding="utf-8")
    print(f"  docs/{name}.html  <-  docs/{name}.md")


def build_tutorial_page() -> None:
    src = (ROOT / "TUTORIAL.md").read_text(encoding="utf-8")
    body, toc_html = render_markdown(src)
    body = rewrite_links(body, in_docs_dir=False)
    title_text = first_h1(src) or "运行教程"

    pager_html = (
        '<div class="pager">'
        '<a href="index.html" class="prev"><span class="dir">← 返回</span>'
        '<span class="name">站点首页</span></a>'
        '<a href="docs/permissions.html" class="next"><span class="dir">继续阅读 →</span>'
        '<span class="name">🔐 权限规则</span></a>'
        "</div>"
        f'<p class="edit-link">本页由 <a href="{REPO}/blob/main/TUTORIAL.md" '
        f'target="_blank" rel="noopener">TUTORIAL.md</a> 构建生成</p>'
    )
    content = (
        '<div class="doc-meta">'
        '<a class="crumb" href="index.html#docs">← 文档中心</a>'
        "<span>·</span><span>🚀 从零到第一次跑通 Agent 任务</span>"
        "</div>"
        '<div class="generated-banner">📄 本页由仓库 <code>TUTORIAL.md</code> 自动构建生成；'
        "如需修改请编辑源文件后重新构建。</div>" + body + pager_html
    )

    page = shell(
        title=f"{title_text} · NexusCLI",
        url=f"{BASE}tutorial.html",
        desc="NexusCLI 运行教程：从安装依赖、配置模型 API 到第一次跑通 Agent 任务的完整教程。",
        body=doc_layout(toc_html, content),
        rel="",
    )
    (SITE / "tutorial.html").write_text(page, encoding="utf-8")
    print("  tutorial.html  <-  TUTORIAL.md")


def main() -> None:
    SITE.mkdir(exist_ok=True)
    print("building doc pages:")
    for name in PAGER_ORDER:
        build_doc_page(name)
    print("building tutorial page:")
    build_tutorial_page()
    print("done.")


if __name__ == "__main__":
    main()
