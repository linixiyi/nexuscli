"""executor 接线与 run_id 延续测试（spec 2026-10-25_add-tool-journal，F9）。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from nexuscli.config import NexusCliConfig
from nexuscli.plan.models import ExecutionPlan
from nexuscli.runtime.tasks import DurableTaskManager
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.journal import ToolJournal


@pytest.fixture()
def journal(tmp_path: Path) -> ToolJournal:
    return ToolJournal(path=tmp_path / "journal.db")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    return registry


def _context(tmp_path: Path, journal: ToolJournal, run_id: str = "run-fixed") -> ToolContext:
    return ToolContext(
        cwd=str(tmp_path),
        config=NexusCliConfig(),
        approval_callback=lambda _request: "approve",
        journal=journal,
        run_id=run_id,
    )


def _run(context: ToolContext, tmp_path: Path, name: str, arguments: dict, call_id: str):
    executor = ToolExecutor(_registry())
    call = {"id": call_id, "name": name, "arguments": arguments}
    return asyncio.run(executor.execute_all([call], context))[0]


def test_write_file_second_call_reuses_journal_result(tmp_path: Path, journal: ToolJournal) -> None:
    context = _context(tmp_path, journal)
    target = tmp_path / "out.txt"

    first = _run(context, tmp_path, "write_file", {"path": "out.txt", "content": "v1"}, "c1")
    assert not first.is_error
    assert target.read_text() == "v1"
    # 篡改工作区内容：reuse 直接回填日志结果，handler 不再执行（文件不被重写）
    target.write_text("changed-after-journal")

    second = _run(context, tmp_path, "write_file", {"path": "out.txt", "content": "v1"}, "c2")
    assert second.is_error is False
    assert second.error_type == "journal_reuse"
    assert second.content == first.content
    assert target.read_text() == "changed-after-journal"  # 未重执行


def test_started_row_fails_closed(tmp_path: Path, journal: ToolJournal) -> None:
    context = _context(tmp_path, journal)
    # 模拟上次进程死在执行中途：残留 started 行
    journal.claim_tool(
        scope=str(tmp_path),
        run_id="run-fixed",
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "x.txt", "content": "y"},
    )
    result = _run(context, tmp_path, "write_file", {"path": "x.txt", "content": "y"}, "c1")
    assert result.is_error
    assert result.error_type == "outcome_unknown"
    assert not (tmp_path / "x.txt").exists()


def test_identity_conflict_becomes_error_result(tmp_path: Path, journal: ToolJournal) -> None:
    context = _context(tmp_path, journal)
    journal.claim_tool(
        scope=str(tmp_path),
        run_id="run-fixed",
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "a.txt", "content": "x"},
    )
    result = _run(context, tmp_path, "write_file", {"path": "DIFFERENT.txt", "content": "x"}, "c1")
    assert result.is_error  # 冲突转为不中断回合的错误结果
    assert not (tmp_path / "DIFFERENT.txt").exists()


def test_bash_readonly_not_journaled(tmp_path: Path, journal: ToolJournal) -> None:
    context = _context(tmp_path, journal)
    result = _run(context, tmp_path, "bash", {"command": "ls"}, "c1")
    assert not result.is_error
    assert (
        journal.lookup_by_hash(
            scope=str(tmp_path),
            run_id="run-fixed",
            tool_name="bash",
            tool_input={"command": "ls"},
        )
        is None
    )


def test_edit_file_dry_run_not_journaled(tmp_path: Path, journal: ToolJournal) -> None:
    context = _context(tmp_path, journal)
    target = tmp_path / "edit.txt"
    target.write_text("hello world")
    _run(
        context,
        tmp_path,
        "edit_file",
        {"path": "edit.txt", "old_text": "hello", "new_text": "bye", "dry_run": True},
        "c1",
    )
    assert (
        journal.lookup_by_hash(
            scope=str(tmp_path),
            run_id="run-fixed",
            tool_name="edit_file",
            tool_input={
                "path": "edit.txt",
                "old_text": "hello",
                "new_text": "bye",
                "dry_run": True,
            },
        )
        is None
    )


def test_hash_dedup_hits_across_new_call_ids(tmp_path: Path, journal: ToolJournal) -> None:
    """恢复 run 沿用原 run_id：新生成的调用 id 按 (tool_name, input_hash) 命中已完成写入。"""

    context = _context(tmp_path, journal, run_id="resumed-run")
    first = _run(context, tmp_path, "write_file", {"path": "r.txt", "content": "same"}, "old-id")
    assert not first.is_error

    # 全新调用 id（LLM 重生成），参数完全一致 → hash 级复用，不再执行
    (tmp_path / "r.txt").write_text("mutated")
    second = _run(context, tmp_path, "write_file", {"path": "r.txt", "content": "same"}, "new-id")
    assert second.error_type == "journal_reuse"
    assert (tmp_path / "r.txt").read_text() == "mutated"  # 未重执行


def test_task_record_run_id_persisted_and_migrated(tmp_path: Path) -> None:
    # scope 会被 realpath 归一化（macOS /tmp → /private/tmp）：手插行必须用同一归一化值
    db = tmp_path / "tasks.db"
    manager = DurableTaskManager(db, scope=str(tmp_path))
    task_id = manager.add("do something", mode="react")
    record = manager.get(task_id)
    assert record is not None
    original_run_id = record.run_id
    assert original_run_id != ""

    # 模拟旧库（无 run_id 列）：新 manager 打开时 alter-table 迁移不炸
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute(
        "create table tasks (id text primary key, prompt text, status text, created_at text,"
        " updated_at text, result text, error text, mode text not null default 'react',"
        " attempts integer not null default 0, worker_id text, lease_expires_at text,"
        " scope text not null default '')"
    )
    conn.execute(
        "insert into tasks(id, prompt, status, created_at, updated_at, scope)"
        " values ('t1', 'p', 'queued', 'now', 'now', ?)",
        (str(tmp_path),),
    )
    conn.commit()
    conn.close()
    migrated = DurableTaskManager(old, scope=str(tmp_path))
    record = migrated.get("t1")
    assert record is not None
    assert record.run_id == ""  # 旧行降级为空串

    # 重新入队（requeue）不改 run_id：恢复 run 沿用原值
    claim = manager.claim_next("w1", lease_seconds=60)
    assert claim is not None and claim.run_id == original_run_id
    manager.requeue_expired()
    record = manager.get(task_id)
    assert record is not None and record.run_id == original_run_id


def test_execution_plan_run_id_roundtrip(tmp_path: Path) -> None:
    plan = ExecutionPlan(id="p1", goal="g")
    plan.run_id = "run-abc"
    data = plan.to_dict()
    assert data["run_id"] == "run-abc"
    restored = ExecutionPlan.from_dict(data)
    assert restored.run_id == "run-abc"
    # 旧 checkpoint 无 run_id → 降级空串（视为新 run）
    legacy = ExecutionPlan.from_dict({k: v for k, v in data.items() if k != "run_id"})
    assert legacy.run_id == ""
    assert json.dumps(data)  # 序列化可用
