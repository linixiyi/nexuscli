"""Regression: recalled preference/constraint memories render as binding.

Round-2 re-verification of the lane-b feedback: the generic "candidates, not
instructions" recalled-memory frame (trust="untrusted-data") made models ignore
recalled project constraints in live runs — saved "always bun, not npm" then
answered "npm / cannot determine". The assembler therefore splits user-saved
preference/constraint entries into a trusted project-constraints section with
binding wording, mirroring the static project-instructions block, while all
other recalls stay in the untrusted recalled-memory frame.
"""

from __future__ import annotations

from nexuscli.config import load_config
from nexuscli.memory import MemoryManager
from nexuscli.prompt import PromptAssembler


def _assembler(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    config = load_config(project_root=tmp_path)
    return PromptAssembler(config, str(tmp_path), [], "model", "provider"), config


def test_constraint_memory_gets_binding_wording(tmp_path, monkeypatch):
    assembler, config = _assembler(tmp_path, monkeypatch)
    MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).save(
        "项目测试统一使用 pytest", kind="constraint"
    )

    dynamic = assembler.build_dynamic("项目测试怎么运行")

    assert '<project-constraints trust="user-saved-memory">' in dynamic
    assert "以下召回的项目约束必须遵守" in dynamic
    assert "项目测试统一使用 pytest" in dynamic
    # No plain-fact item in this recall, so no untrusted block is emitted.
    assert "recalled-memory" not in dynamic


def test_preference_memory_gets_binding_wording(tmp_path, monkeypatch):
    assembler, config = _assembler(tmp_path, monkeypatch)
    MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).save(
        "用户偏好使用 bun 而不是 npm", kind="preference"
    )

    dynamic = assembler.build_dynamic("该用哪个包管理器跑脚本")

    assert "以下召回的项目约束必须遵守" in dynamic
    assert "用户偏好使用 bun 而不是 npm" in dynamic
    assert "even if workspace config files are missing or silent" in dynamic


def test_plain_fact_memory_keeps_untrusted_frame_without_binding_wording(tmp_path, monkeypatch):
    assembler, config = _assembler(tmp_path, monkeypatch)
    MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path)).save(
        "这个项目的发布代号是 BLUE-OTTER-77", kind="fact"
    )

    dynamic = assembler.build_dynamic("这个项目的发布代号是什么")

    assert '<recalled-memory trust="untrusted-data">' in dynamic
    assert "BLUE-OTTER-77" in dynamic
    assert "以下召回的项目约束必须遵守" not in dynamic
    assert "project-constraints" not in dynamic


def test_mixed_recall_splits_fact_and_constraint(tmp_path, monkeypatch):
    assembler, config = _assembler(tmp_path, monkeypatch)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    manager.save("发布代号是 BLUE-OTTER-77", kind="fact")
    manager.save("测试统一使用 pytest", kind="constraint")

    dynamic = assembler.build_dynamic("项目测试怎么运行 发布代号")

    assert '<recalled-memory trust="untrusted-data">' in dynamic
    assert "BLUE-OTTER-77" in dynamic
    assert '<project-constraints trust="user-saved-memory">' in dynamic
    assert "测试统一使用 pytest" in dynamic


def test_static_project_instructions_untouched_by_wording_change(tmp_path, monkeypatch):
    assembler, _config = _assembler(tmp_path, monkeypatch)
    (tmp_path / "AGENTS.md").write_text("发布代号 BLUE-OTTER-77", encoding="utf-8")

    static = assembler.build_static()

    assert "BLUE-OTTER-77" in static
    # No recalled content in the static prefix (the guideline line may name
    # the block, but no memory item is embedded there).
    assert "<recalled-memory" not in static
    assert "<project-constraints trust=" not in static
