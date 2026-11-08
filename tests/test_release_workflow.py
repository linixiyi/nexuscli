"""Release workflow 与版本一致性测试（spec 2026-11-07_fix-nexuscli-release-hardening，task_0005）。

解析 `.github/workflows/release.yml` 文本，断言 tag 触发、OIDC 权限、`pypi`
environment、`uv build`、产物内容校验（`scripts/check_release_artifacts.py`）、
`twine check`、pinned publish 步骤存在且顺序正确，
workflow 不出现任何 token/secrets 引用（Trusted Publishing 模式）；另断言
`pyproject.toml` 与 `src/nexuscli/__init__.py` 两处版本一致且为 0.2.1。

不用 PyYAML：workflow 是本仓库自有的受控文件，行级正则足够，且不给测试引入
未声明依赖。仓库根从 `__file__` 推导——裸 `pytest`（非 `python -m pytest`）
跑单文件时仓库根不在 sys.path（与 tests/test_workbench_static.py 同一前提）。
"""

from __future__ import annotations

import re
from pathlib import Path

import nexuscli

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOW = _ROOT / ".github" / "workflows" / "release.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_INIT = _ROOT / "src" / "nexuscli" / "__init__.py"
_CHANGELOG = _ROOT / "CHANGELOG.md"

EXPECTED_VERSION = "0.2.1"
PUBLISH_ACTION = "pypa/gh-action-pypi-publish"


def _workflow_text() -> str:
    assert _WORKFLOW.is_file(), "缺少 .github/workflows/release.yml"
    return _WORKFLOW.read_text(encoding="utf-8")


def _top_level_block(text: str, key: str) -> str:
    """返回顶层 `key:` 到下一个顶层键之间的文本（保持缩进，供行级正则用）。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln == f"{key}:"), None)
    assert start is not None, f"workflow 缺少顶层 `{key}:` 块"
    block: list[str] = []
    for line in lines[start + 1 :]:
        if not line.strip():
            block.append(line)
            continue
        if not line.startswith((" ", "\t", "#")):
            break  # 下一个顶层键，块结束
        block.append(line)
    return "\n".join(block)


def test_workflow_triggers_on_version_tags() -> None:
    """`on.push.tags` 必须恰好是 `["v*"]`，不随普通 push 发版。"""
    on_block = _top_level_block(_workflow_text(), "on")
    assert re.search(r"^  push:$", on_block, re.M), on_block
    assert re.search(r'^    tags:\s*\[\s*"v\*"\s*\]\s*$', on_block, re.M), on_block
    assert "branches:" not in on_block, "release 不应由普通分支 push 触发"


def test_workflow_requests_oidc_write_permission() -> None:
    """Trusted Publishing 需要 `id-token: write`，且不得请求其他写权限。"""
    permissions = _top_level_block(_workflow_text(), "permissions")
    assert re.search(r"^\s+id-token:\s*write\s*$", permissions, re.M), permissions
    assert not re.search(
        r"^\s+\S+:\s*write\s*$", permissions.replace("id-token: write", ""), re.M
    ), f"permissions 里出现了 id-token 之外的 write：{permissions}"


def test_publish_job_runs_in_pypi_environment() -> None:
    """发布 job 必须声明 `environment: pypi`（PyPI trusted publisher 绑定点）。"""
    text = _workflow_text()
    assert re.search(r"^\s+environment:\s*pypi\s*$", text, re.M), text


def test_workflow_builds_then_checks_before_publish() -> None:
    """`uv build` → 产物内容校验 → `python -m twine check dist/*` → publish，顺序即执行顺序。"""
    text = _workflow_text()
    build = re.search(r"run:\s*uv build\s*$", text, re.M)
    artifacts = re.search(
        r"run:\s*uv run python scripts/check_release_artifacts\.py\s*$", text, re.M
    )
    twine = re.search(r"run:\s*\S.*python -m twine check dist/\*", text, re.M)
    publish = re.search(rf"uses:\s*{re.escape(PUBLISH_ACTION)}@", text)
    assert build, "缺少 `uv build` 步骤（产出 wheel/sdist）"
    assert artifacts, "缺少 `scripts/check_release_artifacts.py` 产物内容校验步骤"
    assert twine, "缺少 `python -m twine check dist/*` 步骤"
    assert publish, f"缺少 {PUBLISH_ACTION} 上传步骤"
    assert build.start() < artifacts.start() < twine.start() < publish.start(), (
        "build → artifact check → twine check → publish 顺序不对"
    )


def test_publish_action_is_version_pinned() -> None:
    """publish 动作必须按 commit SHA pin（40 hex），同行注释保留精确版本号。"""
    match = re.search(
        rf"uses:\s*{re.escape(PUBLISH_ACTION)}@(\S+)(?:\s*#\s*(v\d+\.\d+\.\d+))?",
        _workflow_text(),
    )
    assert match, f"缺少 {PUBLISH_ACTION} 上传步骤"
    ref, version_comment = match.group(1), match.group(2)
    assert re.fullmatch(r"[0-9a-f]{40}", ref), f"publish action 未按 commit SHA pin：@{ref}"
    assert version_comment, f"publish action 的 SHA pin 行缺少版本注释（# vX.Y.Z）：@{ref}"


_WORKFLOW_DIR = _ROOT / ".github" / "workflows"


def test_all_workflow_actions_are_sha_pinned() -> None:
    """ci.yml / release.yml 的每个 `uses:` 都按 commit SHA pin 并带版本注释。

    浮动大版本 tag（@v4 / @v5）可被上游移动，在 id-token: write 的发布链里
    等于可注入代码；SHA pin + 版本注释既锁死行为又保留可读版本号。
    """
    workflow_files = sorted(_WORKFLOW_DIR.glob("*.yml"))
    assert {p.name for p in workflow_files} >= {"ci.yml", "release.yml"}
    for path in workflow_files:
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.search(r"uses:\s*(\S+)@(\S+)", line)
            if match is None:
                continue
            action, ref = match.group(1), match.group(2)
            assert action.startswith(("actions/", "astral-sh/", "pypa/")), (
                f"{path.name}:{i} 出现未知 action 来源：{action}"
            )
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{path.name}:{i} {action} 未按 commit SHA pin：@{ref}"
            )
            assert re.search(r"#\s*v\d+", line), (
                f"{path.name}:{i} {action} 的 SHA pin 行缺少版本注释（# vX.Y.Z）"
            )


def test_workflow_never_references_tokens_or_secrets() -> None:
    """Trusted Publishing 模式下不得出现任何 token/password/secrets 引用。"""
    text = _workflow_text().lower()
    for pattern in (r"api_?token", r"password", r"secrets\."):
        assert not re.search(pattern, text), f"workflow 命中 `{pattern}`，违背 trusted publishing"


def test_version_is_consistent_across_declared_places() -> None:
    """`pyproject.toml [project].version` 与 `__init__.__version__` 一致且为 0.2.1。"""
    pyproject = _PYPROJECT.read_text(encoding="utf-8")
    project_table = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]
    pyproject_match = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project_table, re.M)
    assert pyproject_match, "pyproject [project] 表缺少 version"

    init_match = re.search(
        r'^__version__\s*=\s*"([^"]+)"\s*$', _INIT.read_text(encoding="utf-8"), re.M
    )
    assert init_match, "src/nexuscli/__init__.py 缺少 __version__"

    assert pyproject_match.group(1) == init_match.group(1) == EXPECTED_VERSION, (
        f"pyproject={pyproject_match.group(1)} __init__={init_match.group(1)} "
        f"期望一致且为 {EXPECTED_VERSION}"
    )
    # 运行时真值（uv 环境安装的包）与源码文本一致
    assert nexuscli.__version__ == EXPECTED_VERSION
    # CHANGELOG 有对应条目
    changelog = _CHANGELOG.read_text(encoding="utf-8")
    assert f"## [{EXPECTED_VERSION}]" in changelog, "CHANGELOG 缺少 0.2.1 条目"
