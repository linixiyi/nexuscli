"""Docs contract：README 安装路径 / quickstart 与社区文件（spec 2026-11-07 task_0007）。

断言 task_0007 的文档交付面：
1. README 三种安装路径（`uv tool install` / `pip install` PyPI 渠道 / 源码）、
   60 秒上手、docs 索引、与 CI 口径一致的开发/测试命令；
2. CONTRIBUTING.md 写清 `uv sync --extra dev`、pytest、ruff、mypy 开发循环；
3. SECURITY.md 有私有漏洞报告渠道与「护栏不是沙箱」边界声明；
4. GitHub issue / PR 模板结构可用（issue forms 必填顶层键与合法条目类型）。

task_0033 追加（文档站搜索与英文入口）：
5. site/build.py 生成 search-index.json（标题/路径/正文摘要）且条目路径可达；
6. build.py 页面外壳引用搜索 UI（搜索框 + 结果列表的 id 约定）；
7. 英文入口 README.en.md 链接中文 README 与英文 quickstart，链接可达。

task_0039 追加（--mode plan 只读语义）：
8. README 与 docs/plan-execute.md、docs/permissions.md 区分三种「plan」机制
   （--mode plan 只读规划 / /plan 交互式 Plan-and-Execute / Shift+Tab REPL 只读态），
   且互设链接；
9. CLI 的 --mode help 写明 plan 只读、不执行写入、--yes 被忽略（离线渲染 --help）；
10. `--mode plan --yes` 打印 warning 且不中断运行（fake _run_prompt，不需要 API key）。

以文本断言为主，不校验截图（demo/workbench 图片是 task_0008 的 manual 交付）。
不用 PyYAML（与 tests/test_release_workflow.py 同一前提）：
模板是本仓库自有的受控文件，行级结构检查足够，且不给测试引入未声明依赖。
仓库根从 `__file__` 推导——裸 `pytest` 跑单文件时仓库根不在 sys.path。
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

from typer.testing import CliRunner

import nexuscli
import nexuscli.entrypoints.cli as cli
from nexuscli.config import NexusCliConfig

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_CONTRIBUTING = _ROOT / "CONTRIBUTING.md"
_SECURITY = _ROOT / "SECURITY.md"
_PR_TEMPLATE = _ROOT / ".github" / "pull_request_template.md"
_BUILD_SCRIPT = _ROOT / "site" / "build.py"
_SEARCH_INDEX = _ROOT / "site" / "search-index.json"
_README_EN = _ROOT / "README.en.md"
_EN_QUICKSTART = _ROOT / "docs" / "en" / "quickstart.md"
_ISSUE_TEMPLATES = {
    "bug": _ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.yml",
    "feature": _ROOT / ".github" / "ISSUE_TEMPLATE" / "feature_request.yml",
}
_PLAN_DOCS = {
    "README.md": _README,
    "docs/plan-execute.md": _ROOT / "docs" / "plan-execute.md",
    "docs/permissions.md": _ROOT / "docs" / "permissions.md",
}

# GitHub issue forms 支持的条目类型（docs.github.com/actions 之外的 forms 规范）
_ISSUE_FORM_TYPES = {"markdown", "textarea", "input", "dropdown", "checkboxes"}


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少 {path.relative_to(_ROOT)}"
    return path.read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    """返回顶层 `key:` 到下一个顶层键之间的文本（保持缩进，供行级正则用）。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln == f"{key}:"), None)
    assert start is not None, f"缺少顶层 `{key}:` 块"
    block: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            block.append(line)
            continue
        if not line.startswith((" ", "\t", "#")):
            break  # 下一个顶层键，块结束
        block.append(line)
    return "\n".join(block)


def test_community_files_exist() -> None:
    for path in (_CONTRIBUTING, _SECURITY, _PR_TEMPLATE, *_ISSUE_TEMPLATES.values()):
        assert path.is_file() and path.stat().st_size > 0, f"缺少社区文件 {path.relative_to(_ROOT)}"


def test_readme_documents_three_install_paths() -> None:
    """README 必须给出 uv tool / PyPI（0.2.1 起）/ 源码三条安装路径。"""
    text = _read(_README)
    assert "uv tool install nexuscli" in text, "缺 uv tool 安装路径"
    assert "pip install nexuscli" in text, "缺 pip 安装路径"
    assert "git clone https://github.com/linixiyi/nexuscli.git" in text, "缺源码安装路径"
    assert "uv sync --extra dev" in text, "源码安装缺 uv sync 步骤"
    assert re.search(r"PyPI 渠道 0\.2\.1 起", text), "安装节缺「PyPI 渠道 0.2.1 起」标注"


def test_readme_quickstart_is_copyable() -> None:
    """60 秒上手：配 key → 第一条 -p 命令 → 交互模式，命令可直接照抄。"""
    text = _read(_README)
    assert re.search(r"^#+\s.*60 秒上手", text, re.M), "缺 60 秒上手小节"
    for command in (
        "NEXUSCLI_PROVIDER=deepseek",
        'nexuscli -p "帮我总结这个项目"',
        "nexuscli doctor --cwd .",
    ):
        assert command in text, f"quickstart 缺命令 {command!r}"
    # -p 模式的失败关闭边界必须在快速开始区域讲清
    assert "失败关闭" in text


def test_readme_links_docs_index_and_dev_commands() -> None:
    """docs 索引存在，且开发命令与 CI job 的口径一致（含 mypy 与端点文档校验）。"""
    text = _read(_README)
    assert "深入文档索引" in text, "缺 docs 索引节"
    for doc in ("docs/config.md", "docs/permissions.md", "docs/tools.md"):
        assert f"({doc})" in text, f"docs 索引缺 {doc}"
    for command in (
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run python scripts/check_endpoint_docs.py",
        "uv run --with mypy mypy src/nexuscli",
        "uv run python -m pytest",
    ):
        assert command in text, f"README 开发命令缺 {command!r}"


def test_contributing_pins_the_dev_loop() -> None:
    """CONTRIBUTING 必须写清依赖同步、pytest、ruff、mypy 四件事。"""
    text = _read(_CONTRIBUTING)
    for command in (
        "uv sync --extra dev",
        "uv run python -m pytest",
        "uv run ruff check .",
        "uv run ruff format",
        "uv run --with mypy mypy src/nexuscli",
    ):
        assert command in text, f"CONTRIBUTING 缺命令 {command!r}"


def test_security_has_private_channel_and_guardrail_boundary() -> None:
    """SECURITY 必须有私有报告渠道与「护栏不是沙箱」边界，且支持版本随包版本走。"""
    text = _read(_SECURITY)
    assert "security/advisories/new" in text, "缺 GitHub 私有漏洞报告入口"
    assert re.search(r"不要用公开", text), "必须明确禁止用公开 issue 报告漏洞"
    assert "护栏不是沙箱" in text, "缺「护栏不是沙箱」边界声明"
    major_minor = ".".join(nexuscli.__version__.split(".")[:2])
    assert re.search(rf"\|\s*{re.escape(major_minor)}\.x\s*\|", text), (
        f"支持版本表缺当前版本线 {major_minor}.x"
    )


def test_issue_templates_are_valid_github_forms() -> None:
    """两个 issue 模板都是可用的 GitHub issue forms：顶层键齐全、条目类型合法。"""
    names: set[str] = set()
    for path in _ISSUE_TEMPLATES.values():
        text = _read(path)
        assert "\t" not in text, f"{path.name} 含 tab 字符（GitHub YAML 解析会失败）"
        for key in ("name", "description", "body"):
            # 顶层键：行内值（`name: x`）或块形式（`body:` 换行接序列）都算存在
            assert re.search(rf"^{key}:(\s\S.*)?$", text, re.M), f"{path.name} 缺顶层 `{key}:`"
        body = _top_level_block(text, "body")
        types = re.findall(r"^\s*- type:\s*(\S+)\s*$", body, re.M)
        assert types, f"{path.name} 的 body 没有任何条目"
        bad = [t for t in types if t not in _ISSUE_FORM_TYPES]
        assert not bad, f"{path.name} 含非法条目类型 {bad}"
        assert any(t != "markdown" for t in types), f"{path.name} 缺可填写条目"
        assert re.search(r"required:\s*true\b", body), f"{path.name} 缺 required 校验"
        name_match = re.search(r'^name:\s*"?([^"\n]+?)"?\s*$', text, re.M)
        assert name_match, f"{path.name} 缺 name"
        names.add(name_match.group(1))
    assert len(names) == len(_ISSUE_TEMPLATES), f"issue 模板 name 重复: {names}"


def test_bug_template_asks_what_matters() -> None:
    """缺陷模板必须收集现象、复现步骤与版本，并把安全漏洞引流到私有渠道。"""
    text = _read(_ISSUE_TEMPLATES["bug"])
    for keyword in ("发生了什么", "复现步骤", "NexusCLI 版本"):
        assert keyword in text, f"bug 模板缺 {keyword!r}"
    assert "SECURITY.md" in text, "bug 模板必须把安全漏洞引流到 SECURITY.md 私有渠道"


def test_feature_template_asks_problem_first() -> None:
    """功能模板先问真实场景，再问方案。"""
    text = _read(_ISSUE_TEMPLATES["feature"])
    for keyword in ("要解决的问题", "期望的方案"):
        assert keyword in text, f"feature 模板缺 {keyword!r}"


def test_pull_request_template_is_usable() -> None:
    """PR 模板含分节、变更说明与可勾选的自查清单。"""
    text = _read(_PR_TEMPLATE)
    assert re.search(r"^## \S", text, re.M), "PR 模板缺 `## ` 分节"
    for section in ("变更说明", "自查清单"):
        assert section in text, f"PR 模板缺 {section!r}"
    checkboxes = re.findall(r"^- \[ \] ", text, re.M)
    assert len(checkboxes) >= 3, f"PR 模板勾选项过少: {len(checkboxes)}"
    assert "ruff" in text and "pytest" in text, "PR 自查项应覆盖 lint 与测试"


# --------------------------------------------------------------------------
# task_0033：文档站搜索与英文入口
# --------------------------------------------------------------------------


def test_search_index_exists_with_title_path_summary() -> None:
    """site/search-index.json 存在，条目是标题/路径/正文摘要且路径可达。

    索引是构建产物：先跑 `uv run --with markdown,pygments python site/build.py`
    （verify 命令前半段）再跑本测试，见任务 0033 的 verify 行。
    """
    assert _SEARCH_INDEX.is_file(), "缺 site/search-index.json（先跑 site/build.py）"
    entries = json.loads(_SEARCH_INDEX.read_text(encoding="utf-8"))
    assert isinstance(entries, list) and len(entries) >= 10, "搜索索引为空或条目过少"
    paths: set[str] = set()
    for entry in entries:
        assert set(entry) == {"title", "path", "summary"}, f"索引条目字段不符: {entry}"
        assert entry["title"], f"索引条目标题为空: {entry['path']}"
        assert entry["summary"], f"索引条目缺正文摘要: {entry['path']}"
        assert entry["path"].endswith(".html"), f"索引路径非站点页面: {entry['path']}"
        assert (_ROOT / "site" / entry["path"]).is_file(), f"索引路径不可达: {entry['path']}"
        paths.add(entry["path"])
    # 全部 21 篇 docs + 教程都必须入索引
    for probe in ("docs/permissions.html", "docs/config.html", "docs/eval.html", "tutorial.html"):
        assert probe in paths, f"搜索索引缺 {probe}"


def test_build_script_wires_search_ui() -> None:
    """build.py 的页面外壳引用搜索 UI：索引文件 + 搜索框 + 结果列表的 id 约定。"""
    text = _read(_BUILD_SCRIPT)
    assert "search-index.json" in text, "build.py 未引用 search-index.json"
    for marker in ('id="siteSearch"', 'id="searchResults"', "search-results", "sr-item"):
        assert marker in text, f"build.py 搜索 UI 缺 {marker!r}"


def test_english_entry_links_resolve() -> None:
    """英文入口 README.en.md 链接中文 README 与英文 quickstart，且相对链接可达。"""
    entry = _read(_README_EN)
    quick = _read(_EN_QUICKSTART)
    assert "](README.md)" in entry, "英文入口缺中文 README 链接"
    assert "](docs/en/quickstart.md)" in entry, "英文入口缺英文 quickstart 链接"
    # 两个英文文件里的所有相对 .md 链接都必须指向存在的文件（「链接可达」）
    for text, base, name in (
        (entry, _ROOT, "README.en.md"),
        (quick, _ROOT / "docs" / "en", "docs/en/quickstart.md"),
    ):
        for target in re.findall(r"\]\(([^)#]+\.md)\)", text):
            assert not target.startswith(("http://", "https://")), (
                f"{name} 相对链接写成了绝对 URL: {target}"
            )
            assert (base / target).is_file(), f"{name} 链接不可达: {target}"
    # 英文 quickstart 必须可直接照抄：装、配 key、第一条 -p、诊断
    for command in (
        "uv tool install nexuscli",
        "pip install nexuscli",
        "NEXUSCLI_PROVIDER=",
        'nexuscli -p "',
        "nexuscli doctor",
        "--yes",
    ):
        assert command in quick, f"英文 quickstart 缺 {command!r}"
    # 英文入口声明「不是全文翻译、完整口径在中文文档」，避免读者误判覆盖面
    assert "not a full translation" in entry


# --------------------------------------------------------------------------
# task_0039：--mode plan 只读语义与三种「plan」机制
# --------------------------------------------------------------------------


def _sentences(text: str) -> list[str]:
    """按句切分（。或换行）：表格行与列表项各自成句，供同句关键词断言用。"""
    return [part.strip() for part in re.split(r"[。\n]", text) if part.strip()]


def test_plan_mechanisms_are_distinguished_in_docs() -> None:
    """README 与两份 docs 必须区分三种「plan」机制，并把 --yes 无效写进文档。

    同句断言（含表格行/列表项）保证不是各提一句的巧合：--mode plan 必须与
    「只读」同句出现，且与 --yes 无效/忽略的说明同句出现；三种机制各自被点名。
    """
    for name, path in _PLAN_DOCS.items():
        text = _read(path)
        sentences = _sentences(text)
        assert any("--mode plan" in s and "只读" in s for s in sentences), (
            f"{name} 缺 --mode plan 的只读语义说明"
        )
        assert any(
            "--mode plan" in s and "--yes" in s and ("无效" in s or "忽略" in s) for s in sentences
        ), f"{name} 缺 --mode plan 下 --yes 无效的说明"
        for keyword in ("/plan", "Shift+Tab"):
            assert keyword in text, f"{name} 缺 {keyword} 机制的区分"


def test_plan_mechanism_docs_cross_link() -> None:
    """README 索引两份 docs；plan-execute 与 permissions 互设链接。"""
    readme = _read(_README)
    for doc in ("docs/plan-execute.md", "docs/permissions.md"):
        assert f"]({doc})" in readme, f"README 缺 {doc} 链接"
    plan_doc = _read(_PLAN_DOCS["docs/plan-execute.md"])
    assert "](permissions.md" in plan_doc, "plan-execute.md 缺 permissions.md 链接"
    perm_doc = _read(_PLAN_DOCS["docs/permissions.md"])
    assert "](plan-execute.md" in perm_doc, "permissions.md 缺 plan-execute.md 链接"


def test_cli_help_documents_plan_readonly() -> None:
    """--mode 的 help 必须写明 plan 只读、不执行写入、--yes 被忽略。"""
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    # rich 面板会在词边界折行且行首有 │ 列分隔符，去掉后折叠空白再断言短语
    output = re.sub(r"\s+", " ", result.output.replace("│", " "))
    for phrase in (
        "plan is read-only planning",
        "never executes writes",
        "--yes is ignored in plan mode",
    ):
        assert phrase in output, f"--mode help 缺 {phrase!r}"


async def _fake_run_prompt(*args, **kwargs) -> None:
    """单次模式的替身：吞掉调用即可，warning 在 main() 里就已发出。"""
    _ = args, kwargs


def _all_output(result) -> str:
    parts = [result.output]
    with contextlib.suppress(ValueError, AttributeError):
        parts.append(result.stderr)
    return "".join(parts)


def test_plan_mode_yes_prints_warning(tmp_path, monkeypatch) -> None:
    """--mode plan --yes 打印 warning 且不中断；非 plan 或未给 --yes 都不打印。"""
    config = NexusCliConfig()
    monkeypatch.setattr(cli, "load_config", lambda project_root, overrides=None: config)
    monkeypatch.setattr(cli, "_run_prompt", _fake_run_prompt)
    invoke = lambda *flags: CliRunner().invoke(  # noqa: E731 - 测试内的小闭包
        cli.app, ["-p", "x", *flags, "--cwd", str(tmp_path)]
    )

    warned = invoke("--mode", "plan", "--yes")
    assert warned.exit_code == 0
    assert "Warning: --mode plan" in _all_output(warned)
    assert "--yes is ignored in plan mode" in _all_output(warned)

    for flags in (("--mode", "plan"), ("--mode", "react", "--yes")):
        quiet = invoke(*flags)
        assert quiet.exit_code == 0
        assert "Warning" not in _all_output(quiet), f"{flags} 不该触发 warning"
