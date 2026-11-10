"""Task 0002（spec 2026-11-10 fix-plan-worker-readonly）：文档同步 worker 只读语义。

README 与 docs/plan-execute.md 必须写明 plan 模式 worker 的只读语义：

- planner 指令白名单与 worker 运行时 registry 过滤是双重约束；
- worker 只看到六个只读工具（read_file/glob/grep/search_code/vfs_ls/vfs_read）；
- bash/write_file 等写入与命令工具禁止（不进 worker 工具列表）；
- 执行层只读闸继续兜底：漏网的写入/命令被拒绝并按 blocked 收场；
- 措辞红线：不声称 plan 模式会执行写入，不把 --yes 描述成可绕过只读闸。

纯文本断言，不依赖网络与真实模型。仓库根从 ``__file__`` 推导——裸 pytest 跑
单文件时仓库根不在 sys.path（与 tests/test_docs_contract.py 同一前提）。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DOC_PATHS = {
    "README.md": _ROOT / "README.md",
    "docs/plan-execute.md": _ROOT / "docs" / "plan-execute.md",
}

_SIX_TOOLS = ("read_file", "glob", "grep", "search_code", "vfs_ls", "vfs_read")

# plan 只读语义所在的句子：README 不含「plan 模式」字面量，用 --mode plan 兜住
_PLAN_MODE_MARKER = ("--mode plan", "plan 模式")


def _read(path: Path) -> str:
    assert path.is_file(), f"缺少 {path.relative_to(_ROOT)}"
    return path.read_text(encoding="utf-8")


def _sentences(text: str) -> list[str]:
    """按句切分（。或换行）：表格行与列表项各自成句，供同句关键词断言用。"""
    return [part.strip() for part in re.split(r"[。\n]", text) if part.strip()]


def _plan_mode_sentences(text: str) -> list[str]:
    return [s for s in _sentences(text) if any(marker in s for marker in _PLAN_MODE_MARKER)]


def _worker_sentences(text: str) -> list[str]:
    return [s for s in _sentences(text) if "worker" in s]


def test_docs_name_worker_runtime_readonly_as_double_constraint() -> None:
    """两份文档都把「planner 白名单 + worker 运行时 registry 过滤」写成双重约束。"""
    for name, path in _DOC_PATHS.items():
        text = _read(path)
        assert _plan_mode_sentences(text), f"{name} 缺 plan 模式语义说明"
        assert any(
            "worker" in s and "只读" in s and ("registry" in s or "运行时" in s)
            for s in _worker_sentences(text)
        ), f"{name} 缺 worker 运行时只读约束说明"
        assert any(
            "双重约束" in s or ("planner" in s and "worker" in s) for s in _sentences(text)
        ), f"{name} 缺双重约束（planner 白名单 + worker 过滤）说明"


def test_docs_say_worker_only_sees_six_readonly_tools() -> None:
    """两份文档都在 worker 语境下点名六个只读工具。"""
    for name, path in _DOC_PATHS.items():
        worker_sentences = _worker_sentences(_read(path))
        assert worker_sentences, f"{name} 没有任何 worker 说明"
        assert any(all(tool in s for tool in _SIX_TOOLS) for s in worker_sentences), (
            f"{name} 缺 worker 只见六个只读工具的点名"
        )


def test_docs_ban_bash_and_write_file_for_worker() -> None:
    """两份文档都在 worker 语境下写明 bash/write_file 被禁/不进工具列表。"""
    for name, path in _DOC_PATHS.items():
        worker_sentences = _worker_sentences(_read(path))
        assert any(
            "bash" in s and "write_file" in s and ("不进" in s or "禁止" in s or "过滤" in s)
            for s in worker_sentences
        ), f"{name} 缺 worker 禁用 bash/write_file 的说明"


def test_docs_keep_executor_gate_as_backstop() -> None:
    """两份文档都写明执行层只读闸兜底：漏网调用被拒绝并按 blocked 收场。"""
    for name, path in _DOC_PATHS.items():
        sentences = _sentences(_read(path))
        assert any(("只读闸" in s or "executor" in s) and "blocked" in s for s in sentences), (
            f"{name} 缺执行层 blocked 兜底说明"
        )


def test_docs_state_plan_mode_never_executes_writes() -> None:
    """正面主张在：plan 模式不会执行任何写入；且无句子声称 plan 模式会执行写入。"""
    for name, path in _DOC_PATHS.items():
        text = _read(path)
        plan_sentences = _plan_mode_sentences(text)
        assert plan_sentences, f"{name} 缺 plan 模式语义说明"
        assert any("不会执行任何写入" in s for s in plan_sentences), (
            f"{name} 缺「plan 模式不会执行任何写入」的正面主张"
        )
        for sentence in plan_sentences:
            # 「不会/未/没」紧跟的否定豁免：只抓肯定语气的「会执行写入」
            assert not re.search(r"(?<![不未没])会执行写入", sentence), (
                f"{name} 声称 plan 模式会执行写入：{sentence}"
            )


def test_docs_do_not_offer_yes_as_gate_bypass() -> None:
    """--yes 只能被描述为无效/不能绕过，不得写成可绕过只读闸。"""
    for name, path in _DOC_PATHS.items():
        text = _read(path)
        assert "--yes" in text, f"{name} 缺 --yes 语义说明"
        assert "不能绕过只读闸" in text, f"{name} 缺 --yes 不能绕过只读闸的说明"
        for sentence in _sentences(text):
            if "--yes" in sentence and "绕过" in sentence:
                assert "不能绕过" in sentence or "无法绕过" in sentence, (
                    f"{name} 把 --yes 描述成可绕过只读闸：{sentence}"
                )
