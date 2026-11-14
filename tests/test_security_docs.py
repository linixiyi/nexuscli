"""安全文档契约：项目信任边界与执行安全（spec 2026-11-13_fix-project-trust-boundary task-0010）。

断言 README 安全段与 docs/permissions.md、docs/mcp.md、docs/tools.md 对
task-0001…0009 已落地护栏的文档交付面：
1. 项目层 config/`.env` 安全键需信任指纹（`project-trust.json`），headless fail-closed；
2. 项目层 `.nexuscli/mcp.json` 需信任（`mcp-trust.json`）且 stdio env 经清洗；
3. headless hooks（`-p` / serve）无信任不执行，用户层 hooks 不受影响；
4. slash `` !`cmd` `` 注入按 bash/execute_command 主体过 deny 与 PreToolUse hook；
5. 路径权限规则按 raw + POSIX 归一化匹配；写域扫描识别 `--flag=value` 长选项；
6. MCP `read_resource` / `get_prompt` 需审批并进审计，`list_*` 保持只读；
7. 审计归因如实：`hitl_mode=never` 记 `policy-never`，无人参与的放行不记 `hitl`；
8. 未实现的远程 daemon / RBAC 必须写明未实现，不得说成已支持。

文本断言为主：同句断言（句号/换行切分）沿用 tests/test_docs_contract.py 的
`_sentences` 风格，锚点口径与 tests/test_docs_integrity.py 的 GitHub slugify
一致；纯仓库文件检查，不依赖网络。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_PERMISSIONS = _ROOT / "docs" / "permissions.md"
_MCP = _ROOT / "docs" / "mcp.md"
_TOOLS = _ROOT / "docs" / "tools.md"

_SECURITY_DOCS = {
    "README.md": _README,
    "docs/permissions.md": _PERMISSIONS,
    "docs/mcp.md": _MCP,
    "docs/tools.md": _TOOLS,
}


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少 {path.relative_to(_ROOT)}"
    return path.read_text(encoding="utf-8")


def _sentences(text: str) -> list[str]:
    """按句切分（。或换行）：表格行与列表项各自成句，供同句关键词断言用。"""
    return [part.strip() for part in re.split(r"[。\n]", text) if part.strip()]


# --------------------------------------------------------------------------
# 1. 项目层 config / .env 安全键信任门（task-0001）
# --------------------------------------------------------------------------


def test_project_config_security_keys_require_trust() -> None:
    """permissions.md 必须写明项目层安全键需信任指纹、headless 一律失败关闭。"""
    text = _read(_PERMISSIONS)
    sentences = _sentences(text)
    assert any("项目" in s and "信任指纹" in s for s in sentences), (
        "permissions.md 缺项目层安全键信任指纹的说明"
    )
    for key in (
        "policy.*",
        "permissions.allow",
        "features.audit_log",
        "llm.base_url",
        "sandbox.*",
        "telemetry.*",
    ):
        assert key in text, f"permissions.md 缺安全键 {key!r}"
    assert "project-trust.json" in text, "permissions.md 缺信任存储路径 project-trust.json"
    assert any(
        ("-p" in s or "serve" in s) and ("失败关闭" in s or "fail-closed" in s) and "TTY" in s
        for s in sentences
    ), "permissions.md 缺 headless（-p / serve 无 TTY）失败关闭的说明"
    # 同一节覆盖三个项目层入口的独立指纹存储，互不连带授权
    assert "mcp-trust.json" in text and "hooks-trust.json" in text, (
        "permissions.md 信任节应并列 MCP / hooks 的独立指纹存储"
    )


def test_readme_security_section_documents_project_trust() -> None:
    """README 安全段必须给出项目信任门要点与 docs 链接。"""
    readme = _read(_README)
    sentences = _sentences(readme)
    assert any("安全敏感键" in s and "信任指纹" in s for s in sentences), (
        "README 安全段缺项目层安全键信任指纹说明"
    )
    assert "](docs/permissions.md#项目层信任门config-安全键--mcp--hooks)" in readme, (
        "README 缺 permissions.md 项目信任门节链接"
    )


# --------------------------------------------------------------------------
# 2. 项目层 MCP 信任门与环境清洗（task-0002）
# --------------------------------------------------------------------------


def test_project_mcp_requires_trust_and_sanitized_env() -> None:
    """mcp.md 必须写明项目层 server 需信任、stdio env 不再全量继承 os.environ。"""
    text = _read(_MCP)
    sentences = _sentences(text)
    assert any(".nexuscli/mcp.json" in s and "信任指纹" in s for s in sentences), (
        "mcp.md 缺项目层 MCP 信任指纹的说明"
    )
    assert "mcp-trust.json" in text, "mcp.md 缺 MCP 信任存储路径 mcp-trust.json"
    assert any(
        "sanitize_environment" in s and "os.environ" in s and "不再" in s for s in sentences
    ), "mcp.md 缺 stdio env 清洗（不再全量继承 os.environ）的说明"
    assert "spec.env" in text, "mcp.md 应说明 spec.env 显式值仍然保留"
    # 未信任时不 spawn：enabled server 不进入有效列表
    assert any("不 spawn" in s or "不合并" in s for s in sentences), (
        "mcp.md 缺未信任项目 server 不 spawn 的说明"
    )


def test_readme_mcp_section_documents_trust_and_env() -> None:
    """README MCP 节必须给出项目信任 + env 清洗 + resource/prompt 审批要点。"""
    readme = _read(_README)
    assert "](docs/mcp.md#项目层-mcp-的信任门与环境清洗)" in readme, "README 缺 mcp.md 信任节链接"
    sentences = _sentences(readme)
    assert any("read_resource" in s and "审批" in s for s in sentences), (
        "README MCP 节缺 read_resource 审批说明"
    )
    # 过时的「MCP stdio 未清洗」宣称必须移除
    assert "MCP stdio server、hooks runner 与 slash 命令注入路径未清洗" not in readme, (
        "README 仍声称 MCP stdio / slash 注入未清洗，与 task-0002 后的实现不符"
    )
    assert any("hooks runner" in s and "未清洗" in s for s in sentences), (
        "README 应如实保留 hooks runner 未清洗的挂账"
    )


# --------------------------------------------------------------------------
# 3. headless hooks 无信任不执行（task-0003）
# --------------------------------------------------------------------------


def test_headless_hooks_fail_closed_without_trust() -> None:
    """permissions.md 必须写明 -p / serve 的项目 hooks 无信任不执行、用户层不受影响。"""
    text = _read(_PERMISSIONS)
    sentences = _sentences(text)
    assert any("hooks" in s.lower() and "不执行" in s for s in sentences), (
        "permissions.md 缺 headless 项目 hooks 无信任不执行的说明"
    )
    assert any("用户" in s and "hooks" in s.lower() and "不受影响" in s for s in sentences), (
        "permissions.md 缺用户层 hooks 不受影响的说明"
    )
    readme = _read(_README)
    assert any("hooks" in s.lower() and "信任指纹" in s for s in _sentences(readme)), (
        "README 安全段缺项目层 hooks 信任说明"
    )


# --------------------------------------------------------------------------
# 4. slash 注入按命令主体过权限与 hook（task-0004）
# --------------------------------------------------------------------------


def test_slash_injection_evaluated_under_bash_subject() -> None:
    """permissions.md 与 tools.md 必须写明注入片段按 bash 主体过 deny / PreToolUse。"""
    for name, path in (("docs/permissions.md", _PERMISSIONS), ("docs/tools.md", _TOOLS)):
        text = _read(path)
        sentences = _sentences(text)
        assert "slash_injection" in text, f"{name} 缺内部工具名 slash_injection"
        assert any("bash" in s and ("deny" in s or "PreToolUse" in s) for s in sentences), (
            f"{name} 缺注入片段按 bash 主体过 deny / PreToolUse 的说明"
        )
        assert any("审计" in s and "主体" in s for s in sentences), (
            f"{name} 缺审计记录真实主体的说明"
        )


# --------------------------------------------------------------------------
# 5. 路径归一化与 --flag=value 写域（task-0005 / task-0006）
# --------------------------------------------------------------------------


def test_path_normalization_and_long_flag_write_scope() -> None:
    """permissions.md 必须写明路径等价形态命中与 `--flag=value` 长选项拆分。"""
    text = _read(_PERMISSIONS)
    for variant in ("./.env", "a/../.env", ".//AGENTS.md", "src/./x.py"):
        assert variant in text, f"permissions.md 缺路径变体 {variant!r} 的归一化示例"
    assert any("归一化" in s and ("./" in s or "POSIX" in s) for s in _sentences(text)), (
        "permissions.md 缺 POSIX 归一化匹配的说明"
    )
    assert "--flag=value" in text, "permissions.md 缺 --flag=value 长选项口径"
    for probe in ("--target-directory=/etc", "cp --target-directory=/etc/hosts", "-t /etc"):
        assert probe in text, f"permissions.md 缺写域长选项示例 {probe!r}"
    tools = _read(_TOOLS)
    assert "](permissions.md#写域守卫与命令路径扫描)" in tools, "docs/tools.md 缺写域长选项节链接"


def test_bash_readonly_secret_path_boundary() -> None:
    """permissions.md 只读直通节必须写明凭证路径与 git rev:path 不判只读（task-0008）。"""
    text = _read(_PERMISSIONS)
    for probe in (".git-credentials", ".config/gh", ".config/gcloud", ".azure"):
        assert probe in text, f"permissions.md 缺 secret 路径 {probe!r}"
    assert "git show HEAD:.env" in text, "permissions.md 缺 git <rev>:<path> 读凭证的示例"
    assert any("date" in s and "时钟" in s for s in _sentences(text)), (
        "permissions.md 缺 date 设时钟形态不判只读的说明"
    )
    assert "file -C" in text, "permissions.md 缺 file -C 编译 magic 不判只读的说明"


# --------------------------------------------------------------------------
# 6. MCP resource/prompt 审批（task-0009）
# --------------------------------------------------------------------------


def test_mcp_resource_and_prompt_require_approval() -> None:
    """mcp.md 必须写明 read_resource / get_prompt 需审批，list_* 保持只读。"""
    text = _read(_MCP)
    sentences = _sentences(text)
    for tool in ("read_resource", "get_prompt"):
        assert any(tool in s and "审批" in s for s in sentences), f"mcp.md 缺 {tool} 需审批的说明"
        assert any(tool in s and "审计" in s for s in sentences), f"mcp.md 缺 {tool} 进审计的说明"
    assert any("list_resources" in s and "只读" in s for s in sentences), (
        "mcp.md 应说明 list_resources 保持只读"
    )
    assert any(
        "plan" in s.lower() and "拒绝" in s and ("read_resource" in s or "get_prompt" in s)
        for s in sentences
    ), "mcp.md 应说明 plan 模式拒绝 resource/prompt 调用"
    # 过时的「virtual 工具本地只读免审批」宣称必须移除
    assert "仍注册为 read-only" not in text, (
        "mcp.md 仍声称 virtual 工具注册为 read-only，与 task-0009 后的实现不符"
    )


# --------------------------------------------------------------------------
# 7. 审计归因（task-0007）
# --------------------------------------------------------------------------


def test_audit_attribution_values_documented() -> None:
    """permissions.md 必须写明 policy-never 归因，README 同步要点。"""
    text = _read(_PERMISSIONS)
    sentences = _sentences(text)
    assert "policy-never" in text, "permissions.md 缺 policy-never 归因"
    assert any("policy-never" in s and "hitl" in s and "绝不" in s for s in sentences), (
        "permissions.md 应写明 policy-never 绝不记成人工批准的 hitl"
    )
    assert any("run-scope" in s and "readonly-rule" in s for s in sentences), (
        "permissions.md 应保留 run-scope / readonly-rule 的既有归因口径"
    )
    readme = _read(_README)
    assert "policy-never" in readme, "README 缺 policy-never 归因要点"


# --------------------------------------------------------------------------
# 8. 未实现的远程 daemon / RBAC 不得说成已支持
# --------------------------------------------------------------------------


def test_remote_daemon_and_rbac_stated_unsupported() -> None:
    """凡提到 RBAC / 远程 daemon 的句子必须带未实现/不支持类否定，且边界声明存在。"""
    for name, path in _SECURITY_DOCS.items():
        text = _read(path)
        for match in re.finditer(r"RBAC|远程 ?daemon", text):
            start = text.rfind("\n", 0, match.start()) + 1
            end = text.find("\n", match.end())
            line = text[start : end if end != -1 else len(text)]
            assert any(
                neg in line for neg in ("未实现", "不支持", "不在本包范围", "挂账", "不是")
            ), f"{name} 提到 {match.group(0)!r} 但句子没有未实现/不支持的边界: {line.strip()}"
    # 边界声明必须真实存在（不能靠「全体都不提」 vacuous 通过）
    perm = _read(_PERMISSIONS)
    assert any("RBAC" in s and "未实现" in s for s in _sentences(perm)), (
        "permissions.md 缺远程 daemon / RBAC 未实现的边界声明"
    )


# --------------------------------------------------------------------------
# 9. 文档互链与链接可达
# --------------------------------------------------------------------------


def test_security_docs_cross_links() -> None:
    """四份文档互设链接，锚点与 tests/test_docs_integrity.py 的 slugify 口径一致。"""
    readme = _read(_README)
    assert "](docs/mcp.md#项目层-mcp-的信任门与环境清洗)" in readme
    perm = _read(_PERMISSIONS)
    assert "](mcp.md#项目层-mcp-的信任门与环境清洗)" in perm, "permissions.md 缺 mcp.md 信任节链接"
    mcp = _read(_MCP)
    assert "](permissions.md#子进程环境清洗env_sanitization)" in mcp, (
        "mcp.md 缺 permissions.md 环境清洗节链接"
    )
    tools = _read(_TOOLS)
    assert "](mcp.md#readonlyhint-与只读语义)" in tools, "tools.md 缺 mcp.md 只读语义节链接"
    assert "](slash-commands.md" in tools, "tools.md 缺 slash-commands.md 链接"
