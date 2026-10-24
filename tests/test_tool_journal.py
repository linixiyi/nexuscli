"""journal 模块单元测试（spec 2026-10-25_add-tool-journal，F9）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from nexuscli.tools.journal import (
    JOURNAL_SCHEMA_VERSION,
    ToolClaim,
    ToolJournal,
    ToolJournalConflictError,
    ToolJournalSchemaError,
    input_hash,
)


@pytest.fixture()
def journal(tmp_path: Path) -> ToolJournal:
    return ToolJournal(path=tmp_path / "journal.db")


SCOPE = "/tmp/project"
RUN = "run1"


def test_claim_execute_then_complete_then_reuse(journal: ToolJournal) -> None:
    claim = journal.claim_tool(
        scope=SCOPE,
        run_id=RUN,
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "a.txt", "content": "hi"},
    )
    assert claim.action == "execute"
    assert claim.status == "started"

    journal.complete_tool(
        scope=SCOPE, run_id=RUN, tool_use_id="c1", content="written", is_error=False
    )

    again = journal.claim_tool(
        scope=SCOPE,
        run_id=RUN,
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "a.txt", "content": "hi"},
    )
    assert again.action == "reuse"
    assert again.serialized_result == {"content": "written", "is_error": False}


def test_claim_identity_conflict(journal: ToolJournal) -> None:
    journal.claim_tool(
        scope=SCOPE,
        run_id=RUN,
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "a.txt"},
    )
    with pytest.raises(ToolJournalConflictError):
        journal.claim_tool(
            scope=SCOPE,
            run_id=RUN,
            tool_use_id="c1",
            tool_name="write_file",
            tool_input={"path": "OTHER.txt"},  # 同 id 异参
        )
    with pytest.raises(ToolJournalConflictError):
        journal.claim_tool(
            scope=SCOPE,
            run_id=RUN,
            tool_use_id="c1",
            tool_name="bash",
            tool_input={"path": "a.txt"},  # 同 id 异工具
        )


def test_started_and_outcome_unknown_fail_closed(journal: ToolJournal) -> None:
    journal.claim_tool(
        scope=SCOPE, run_id=RUN, tool_use_id="c1", tool_name="bash", tool_input={"command": "x"}
    )
    # started（进程死在中途）→ outcome_unknown
    claim = journal.claim_tool(
        scope=SCOPE, run_id=RUN, tool_use_id="c1", tool_name="bash", tool_input={"command": "x"}
    )
    assert claim.action == "outcome_unknown"

    # 显式标 unknown 后同样 fail-closed
    journal2 = ToolJournal(path=journal.path)
    journal2.claim_tool(
        scope=SCOPE, run_id="run2", tool_use_id="c2", tool_name="bash", tool_input={"command": "y"}
    )
    journal2.mark_outcome_unknown(scope=SCOPE, run_id="run2", tool_use_id="c2")
    claim2 = journal2.claim_tool(
        scope=SCOPE, run_id="run2", tool_use_id="c2", tool_name="bash", tool_input={"command": "y"}
    )
    assert claim2.action == "outcome_unknown"


def test_complete_and_unknown_require_started_row(journal: ToolJournal) -> None:
    with pytest.raises(ToolJournalConflictError):
        journal.complete_tool(
            scope=SCOPE, run_id=RUN, tool_use_id="missing", content="", is_error=False
        )
    with pytest.raises(ToolJournalConflictError):
        journal.mark_outcome_unknown(scope=SCOPE, run_id=RUN, tool_use_id="missing")
    # completed 行不可再 complete（无 started 行）
    journal.claim_tool(
        scope=SCOPE, run_id=RUN, tool_use_id="c9", tool_name="write_file", tool_input={}
    )
    journal.complete_tool(scope=SCOPE, run_id=RUN, tool_use_id="c9", content="x", is_error=False)
    with pytest.raises(ToolJournalConflictError):
        journal.complete_tool(
            scope=SCOPE, run_id=RUN, tool_use_id="c9", content="y", is_error=False
        )


def test_hash_lookup_reuse_and_fail_closed(journal: ToolJournal) -> None:
    journal.claim_tool(
        scope=SCOPE,
        run_id=RUN,
        tool_use_id="c1",
        tool_name="write_file",
        tool_input={"path": "a.txt", "content": "same"},
    )
    journal.complete_tool(scope=SCOPE, run_id=RUN, tool_use_id="c1", content="ok", is_error=False)

    # hash 命中 completed（新 tool_use_id 的重生成调用）
    hit = journal.lookup_by_hash(
        scope=SCOPE,
        run_id=RUN,
        tool_name="write_file",
        tool_input={"content": "same", "path": "a.txt"},
    )
    assert hit is not None and hit.action == "reuse"
    assert hit.serialized_result == {"content": "ok", "is_error": False}

    # 未命中 → None（参数漂移即照常执行）
    miss = journal.lookup_by_hash(
        scope=SCOPE,
        run_id=RUN,
        tool_name="write_file",
        tool_input={"path": "a.txt", "content": "DIFFERENT"},
    )
    assert miss is None

    # hash 命中 started → fail-closed
    journal.claim_tool(
        scope=SCOPE,
        run_id="run3",
        tool_use_id="cx",
        tool_name="write_file",
        tool_input={"path": "b.txt", "content": "z"},
    )
    started = journal.lookup_by_hash(
        scope=SCOPE,
        run_id="run3",
        tool_name="write_file",
        tool_input={"path": "b.txt", "content": "z"},
    )
    assert started is not None and started.action == "outcome_unknown"


def test_schema_versioning_rejects(tmp_path: Path) -> None:
    # 正常初始化 → version 记录
    path = tmp_path / "journal.db"
    ToolJournal(path=path)
    conn = sqlite3.connect(path)
    version = conn.execute("select version from journal_schema where id = 1").fetchone()[0]
    conn.close()
    assert version == JOURNAL_SCHEMA_VERSION

    # 业务表存在但无元数据 → 拒绝（前版本库）
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(legacy)
    conn.executescript(
        "CREATE TABLE tool_invocations (scope TEXT, run_id TEXT, tool_use_id TEXT, "
        "tool_name TEXT, input_hash TEXT, status TEXT, serialized_result TEXT, "
        "created_at TEXT, updated_at TEXT);"
    )
    conn.close()
    with pytest.raises(ToolJournalSchemaError):
        ToolJournal(path=legacy)

    # 版本不匹配 → 拒绝
    future = tmp_path / "future.db"
    ToolJournal(path=future)
    conn = sqlite3.connect(future)
    with conn:
        conn.execute("update journal_schema set version = 99 where id = 1")
    conn.close()
    with pytest.raises(ToolJournalSchemaError):
        ToolJournal(path=future)


def test_check_constraints_enforced(journal: ToolJournal) -> None:
    # completed 必须有 serialized_result：绕过 API 直插应被 CHECK 拒绝
    with pytest.raises(sqlite3.IntegrityError):
        conn = sqlite3.connect(journal.path)
        conn.execute(
            "insert into tool_invocations(scope, run_id, tool_use_id, tool_name, input_hash,"
            " status, serialized_result, created_at, updated_at)"
            " values (?, ?, ?, ?, ?, 'completed', NULL, 't', 't')",
            (SCOPE, RUN, "cx", "write_file", "h"),
        )
        conn.close()


def test_input_hash_is_canonical() -> None:
    assert input_hash({"a": 1, "b": 2}) == input_hash({"b": 2, "a": 1})
    assert input_hash({"a": 1}) != input_hash({"a": 2})


def test_corrupt_serialized_result_raises_conflict(journal: ToolJournal, tmp_path: Path) -> None:
    journal.claim_tool(
        scope=SCOPE, run_id=RUN, tool_use_id="c1", tool_name="write_file", tool_input={}
    )
    journal.complete_tool(scope=SCOPE, run_id=RUN, tool_use_id="c1", content="ok", is_error=False)
    # 破坏落库内容
    conn = sqlite3.connect(journal.path)
    with conn:
        conn.execute(
            "update tool_invocations set serialized_result = '{not json' "
            "where run_id = ? and tool_use_id = 'c1'",
            (RUN,),
        )
    conn.close()
    with pytest.raises(ToolJournalConflictError):
        journal.claim_tool(
            scope=SCOPE, run_id=RUN, tool_use_id="c1", tool_name="write_file", tool_input={}
        )
    with pytest.raises(ToolJournalConflictError):
        journal.lookup_by_hash(scope=SCOPE, run_id=RUN, tool_name="write_file", tool_input={})


def test_claim_dataclass_shape() -> None:
    claim = ToolClaim(
        action="execute", tool_use_id="c", tool_name="t", input_hash="h", status="started"
    )
    assert claim.serialized_result is None
