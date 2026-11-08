# Local docs-site generator.
# Renders docs/*.md + TUTORIAL.md into site/ HTML pages with a shared shell,
# plus site/search-index.json for the shell's client-side search box.
#
# Canonical run command (the only supported one):
#   uv run --with markdown,pygments python site/build.py
# markdown is NOT a project dependency (absent from pyproject.toml
# `dependencies` and from uv.lock); pygments appears in uv.lock only as a
# transitive dep of rich/pytest. --with provisions both explicitly and
# ephemerally. Plain `.venv/bin/python site/build.py` only works on machines
# where someone hand-installed markdown into .venv — do not rely on it.
from __future__ import annotations

import html
import json
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

BASE = "https://linixiyi.github.io/nexuscli/"
REPO = "https://github.com/linixiyi/nexuscli"

# docs/*.md -> (icon, one-line description for cards / meta / pager)
DOC_META = {
    "config": ("🔧", "配置优先级链、provider key、价格覆盖与循环/回合上限"),
    "cron": ("⏰", "离线任务注册表与外部调度器驱动方式"),
    "eval": (
        "🧪",
        "双轨评测：hermetic 回归门禁 + 真模型轨道（消融、pass^k 稳定性、受控写域质量门）",
    ),
    "hooks": ("🪝", "七个生命周期事件的挂载方式与安全约束"),
    "mcp": ("🔌", "stdio / HTTP server 接入、工具过滤与自身暴露为 server"),
    "memory-context": ("🧠", "记忆三层、动态召回、压缩预算与 provider 超限重试"),
    "observability": ("🔭", "trace_id、OTel 遥测、模型 IO 落盘与 usage 统计"),
    "permissions": ("🔐", "allow / deny / ask 三组规则、命令级判定与会话权限模式"),
    "plan-execute": ("🧭", "Plan-and-Execute 的图级 checkpoint、续跑与协作式中断"),
    "repl-commands": ("💬", "内置斜杠命令参考：/model 选择器、usage 双口径与会话增强"),
    "plugins": ("🧱", "本地目录插件的发现、manifest 与组件并入链"),
    "runtime-api": ("📡", "线程、turn、事件流与后台任务的 HTTP 接入面"),
    "workbench": ("🖥️", "浏览器可视化工作台：会话回放、实时事件流、任务面板与用量统计"),
    "sessions": ("💾", "会话持久化、恢复与 usage 台账续算、快照与回放"),
    "skills": ("🎯", "Skill 分层加载、Top-K 匹配与 save_skill 沉淀"),
    "slash-commands": ("🪄", "markdown 提示词、frontmatter 与占位符注入"),
    "spec-skill": ("📐", "规范驱动开发技能的核心概念与质量门禁"),
    "subagents": ("🧩", "后台委派、自定义代理定义与文件邮箱"),
    "tools": (
        "🧰",
        "内置工具补充口径：pdf/rg 差异、vfs 视图、BM25 检索（scope 目录限定）与图片输入",
    ),
    "testing-matrix": ("🧭", "147 个用户表面的测试状态：测过什么、没测过什么"),
    "workflows": ("🔀", "JSON 步骤图 DAG、依赖批次并行与内置专家流程"),
}

# reading order for the bottom pager on doc pages
PAGER_ORDER = [
    "permissions",
    "hooks",
    "mcp",
    "config",
    "repl-commands",
    "tools",
    "skills",
    "memory-context",
    "subagents",
    "workflows",
    "runtime-api",
    "workbench",
    "sessions",
    "plan-execute",
    "plugins",
    "slash-commands",
    "observability",
    "cron",
    "eval",
    "spec-skill",
    "testing-matrix",
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
# search index: title / path / plain-text summary per page
# --------------------------------------------------------------------------

SEARCH_SUMMARY_LEN = 180


def plain_text(markdown_src: str) -> str:
    """Markdown -> single-line plain text, for search summaries.

    Drops fenced code / inline markup; keeps the prose so CJK and English
    terms are both searchable. Not a full renderer — summary-grade fidelity.
    """
    text = re.sub(r"```.*?```", " ", markdown_src, flags=re.S)  # fenced code
    text = re.sub(r"^\s*\|?\s*:?-{3,}[\s:|-]*\|?\s*$", " ", text, flags=re.M)  # table rules
    text = re.sub(r"`([^`]*)`", r"\1", text)  # inline code
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> link text
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)  # heading markers
    text = re.sub(r"^\s*>+\s?", "", text, flags=re.M)  # blockquotes
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)  # list bullets
    text = re.sub(r"^!!!.*$", "", text, flags=re.M)  # admonition directives
    text = text.replace("|", " ")
    text = re.sub(r"[*_]+", "", text)  # emphasis markers
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def page_summary(markdown_src: str) -> str:
    """Body summary: drop the H1 (the title is indexed separately)."""
    body = re.sub(r"^#\s+.+?$", "", markdown_src, count=1, flags=re.M)
    return plain_text(body)[:SEARCH_SUMMARY_LEN]


def build_search_index() -> None:
    """Emit site/search-index.json consumed by the shell's client-side search.

    Paths are site-root-relative (``docs/config.html``); the inlined search
    script prefixes them with the page's ``rel`` prefix at runtime.
    """
    entries = []
    for name in PAGER_ORDER:
        src = (ROOT / "docs" / f"{name}.md").read_text(encoding="utf-8")
        entries.append(
            {
                "title": first_h1(src) or name,
                "path": f"docs/{name}.html",
                "summary": page_summary(src),
            }
        )
    tutorial_src = (ROOT / "TUTORIAL.md").read_text(encoding="utf-8")
    entries.append(
        {
            "title": first_h1(tutorial_src) or "运行教程",
            "path": "tutorial.html",
            "summary": page_summary(tutorial_src),
        }
    )
    (SITE / "search-index.json").write_text(
        json.dumps(entries, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"  search-index.json  <-  {len(entries)} pages")


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


# --------------------------------------------------------------------------
# client-side search over search-index.json
# --------------------------------------------------------------------------
# style.css / site.js are hand-maintained outside this script, so the search
# box ships as inline <style>/<script> emitted by the shell below. __REL__ in
# SEARCH_JS is replaced per page with the root-relative prefix ("../" on doc
# pages, "" at the site root) so index paths resolve from any depth.


SEARCH_STYLE = """<style>
  .nav-search { position: relative; }
  #siteSearch {
    width: 200px; padding: 7px 12px; border-radius: 999px;
    border: 1px solid var(--border); background: var(--surface);
    color: var(--text); font: inherit; font-size: 13.5px; outline: none;
  }
  #siteSearch::placeholder { color: var(--faint); }
  #siteSearch:focus { border-color: var(--accent); }
  .search-results {
    position: absolute; top: calc(100% + 8px); right: 0; width: 340px;
    max-height: 60vh; overflow: auto; padding: 6px; z-index: 60;
    background: var(--surface); border: 1px solid var(--border-strong);
    border-radius: 12px; box-shadow: var(--shadow);
  }
  .sr-item { display: block; padding: 8px 10px; border-radius: 8px; }
  .sr-item:hover { background: var(--surface-2); text-decoration: none; }
  .sr-title { color: var(--text); font-weight: 600; font-size: 13.5px; }
  .sr-path { color: var(--faint); font-size: 11.5px; font-family: var(--mono); margin-top: 1px; }
  .sr-sum { color: var(--muted); font-size: 12px; margin-top: 2px; line-height: 1.5; }
  .sr-empty { padding: 8px 10px; color: var(--faint); font-size: 13px; }
  .search-results mark { background: none; color: var(--accent); font-weight: 700; }
  @media (max-width: 640px) {
    #siteSearch { width: 128px; }
    .search-results { width: min(340px, 92vw); }
  }
</style>"""

SEARCH_JS = """(function () {
  "use strict";
  var input = document.getElementById("siteSearch");
  var box = document.getElementById("searchResults");
  if (!input || !box) return;
  var rel = "__REL__";
  var index = null;

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function load(cb) {
    if (index) { cb(); return; }
    fetch(rel + "search-index.json")
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) { index = Array.isArray(data) ? data : []; cb(); })
      .catch(function () { index = []; cb(); });
  }

  function search(q) {
    var terms = q.toLowerCase().split(/\\s+/).filter(Boolean);
    var items = [];
    if (terms.length) {
      items = index.filter(function (e) {
        var hay = (e.title + " " + e.path + " " + e.summary).toLowerCase();
        return terms.every(function (t) { return hay.indexOf(t) !== -1; });
      });
      items.sort(function (a, b) {
        var ta = a.title.toLowerCase(), tb = b.title.toLowerCase();
        var sa = terms.some(function (t) { return ta.indexOf(t) !== -1; }) ? 0 : 1;
        var sb = terms.some(function (t) { return tb.indexOf(t) !== -1; }) ? 0 : 1;
        return sa - sb;
      });
      items = items.slice(0, 8);
    }
    return { items: items, terms: terms };
  }

  function highlight(text, terms) {
    var safe = esc(text);
    if (!terms.length) return safe;
    var re = new RegExp("(" + terms.map(function (t) {
      return t.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");
    }).join("|") + ")", "gi");
    return safe.replace(re, "<mark>$1</mark>");
  }

  function render(q) {
    if (!q.trim()) { box.hidden = true; return; }
    var r = search(q);
    if (!r.items.length) {
      box.innerHTML = '<div class="sr-empty">没有匹配「' + esc(q) + '」的结果</div>';
    } else {
      box.innerHTML = r.items.map(function (e) {
        var sum = e.summary.slice(0, 110) + (e.summary.length > 110 ? "…" : "");
        return '<a class="sr-item" href="' + rel + e.path + '">'
          + '<div class="sr-title">' + highlight(e.title, r.terms) + '</div>'
          + '<div class="sr-path">' + esc(e.path) + '</div>'
          + '<div class="sr-sum">' + esc(sum) + '</div>'
          + "</a>";
      }).join("");
    }
    box.hidden = false;
  }

  input.addEventListener("input", function () {
    load(function () { render(input.value); });
  });
  input.addEventListener("focus", function () {
    load(function () { if (input.value) render(input.value); });
  });
  input.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") {
      input.value = "";
      box.hidden = true;
      input.blur();
    } else if (ev.key === "Enter") {
      load(function () {
        var r = search(input.value);
        if (r.items.length) window.location.href = rel + r.items[0].path;
      });
    }
  });
  document.addEventListener("keydown", function (ev) {
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === "k" || ev.key === "K")) {
      ev.preventDefault();
      input.focus();
      input.select();
    }
  });
  document.addEventListener("click", function (ev) {
    if (!box.hidden && !box.contains(ev.target) && ev.target !== input) box.hidden = true;
  });
  box.addEventListener("click", function () {
    box.hidden = true;
    input.value = "";
  });
})();
"""


def nav_html(rel: str) -> str:
    return f"""<nav class="nav">
    <div class="nav-inner">
      <a class="brand" href="{rel}index.html"><span class="brand-logo">N</span>Nexus<b>CLI</b></a>
      <div class="nav-links">
        <a href="{rel}index.html#features">功能</a>
        <a href="{rel}index.html#docs">文档</a>
        <a href="{rel}tutorial.html">教程</a>
      </div>
      <div class="nav-search">
        <input id="siteSearch" type="search" placeholder="搜索文档… (Ctrl K)"
               autocomplete="off" aria-label="搜索文档">
        <div id="searchResults" class="search-results" hidden></div>
      </div>
      <div class="nav-spacer"></div>
      <a class="nav-gh" href="{REPO}" target="_blank" rel="noopener">★ GitHub</a>
      <button class="theme-btn" id="themeToggle" aria-label="切换主题">☀</button>
    </div>
  </nav>"""


def shell(*, title: str, url: str, body: str, rel: str, desc: str = "") -> str:
    desc_tag = f'\n  <meta name="description" content="{html.escape(desc)}">' if desc else ""
    search_js = SEARCH_JS.replace("__REL__", rel)
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
  {SEARCH_STYLE}
  {THEME_HEAD}
</head>
<body>
{nav_html(rel)}
{body}
<footer>
  <p>NexusCLI · <a href="{REPO}" target="_blank" rel="noopener">GitHub</a> · MIT License</p>
  <p><a href="{rel}index.html">← 返回首页</a></p>
</footer><script src="{rel}site.js"></script>
<script>
{search_js}
</script>
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
    print("building search index:")
    build_search_index()
    print("done.")


if __name__ == "__main__":
    main()
