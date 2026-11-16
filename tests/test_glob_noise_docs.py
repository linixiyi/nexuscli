"""Docs contract: glob noise-directory exclusion is documented in docs/tools.md."""

from __future__ import annotations

import re
from pathlib import Path

TOOLS_MD = Path(__file__).resolve().parents[1] / "docs" / "tools.md"


def test_tools_md_documents_glob_noise_exclusion():
    text = TOOLS_MD.read_text(encoding="utf-8")
    assert "glob" in text
    # SKIP_DIRS 七个目录名的代表项（文档列出全集）
    for name in (".git", ".venv", "node_modules", "dist", "build", "target", "__pycache__"):
        assert name in text
    # 与 directory_tree 同集/同口径的表述
    assert "directory_tree" in text
    assert re.search(r"同集|同口径", text)
    # limit 只对过滤后的有效命中计数
    assert "limit" in text


def test_tools_md_does_not_claim_custom_exclusion():
    """措辞不得宣称支持自定义排除；如实边界写「不支持自定义排除」不违例。"""
    text = TOOLS_MD.read_text(encoding="utf-8")
    cleaned = text.replace("不支持自定义排除", "")
    assert "支持自定义排除" not in cleaned
    assert "支持 exclude 参数" not in cleaned
