"""Docs contract for the hook subprocess environment (spec 2026-11-16 task-0002).

README 的「三条如实边界」必须把 hooks runner 并入清洗清单并撤掉
「hooks runner 仍未清洗」挂账；docs/hooks.md 必须有「子进程环境」小节
说明同口径与 `policy.env_sanitization` 开关。措辞红线：不得宣称 OS 级
沙箱——挂账表述必须保留。纯文本断言，离线可验证。
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / "README.md"
_HOOKS_DOC = _ROOT / "docs" / "hooks.md"


def test_readme_no_longer_parks_hooks_runner_as_unsanitized() -> None:
    text = _README.read_text(encoding="utf-8")
    assert "hooks runner 仍未清洗" not in text


def test_readme_lists_hooks_runner_among_sanitized_spawn_points() -> None:
    text = _README.read_text(encoding="utf-8")
    assert "slash 注入与 hooks runner 都会把清洗后的环境传给子进程" in text
    assert "`policy.env_sanitization` 可关" in text


def test_readme_keeps_os_sandbox_parking() -> None:
    text = _README.read_text(encoding="utf-8")
    assert "OS 级沙箱仍挂账" in text


def test_hooks_doc_has_child_env_section_with_flag() -> None:
    text = _HOOKS_DOC.read_text(encoding="utf-8")
    assert "## 子进程环境" in text
    assert "env_sanitization" in text


def test_hooks_doc_states_guard_not_os_sandbox() -> None:
    # 措辞红线：护栏不是沙箱，OS 级沙箱只允许以挂账口径出现。
    text = _HOOKS_DOC.read_text(encoding="utf-8")
    assert "不是沙箱" in text
    assert "OS 级沙箱" in text
