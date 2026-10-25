"""工具调用日志（F9）：幂等重放的 fail-closed 底座。

语义对标 kitagent 的 claim 协议（.spec/plans/2026-10-25_workbench-and-readme/
05-execution-durability.md F9 节定稿）：

- 认领四分支：无记录 → execute；completed → reuse（反序列化结果回填，handler 不再执行）；
  started/outcome_unknown → fail-closed（拒绝自动重放有副作用的调用）；
  同 (scope, run_id, tool_use_id) 但 tool_name/input_hash 不同 → 类型化冲突。
- 三态由 SQLite CHECK 兜底：completed 必须有 serialized_result，其余必须没有。
- 诚实边界：这是面向确定性本地工具的重放协议，不对任意 shell 命令、MCP 工具、
  外部服务承诺 exactly-once——外部副作用发生在 started 与 completed 之间时只能如实标
  outcome_unknown 并拒绝自动重放。
- 库位置 ~/.nexuscli/tool_journal/journal.db（conftest HOME 重定向覆盖）；原始参数不落库
  （只存规范化输入哈希）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

JOURNAL_SCHEMA_VERSION = 1

ClaimAction = Literal["execute", "reuse", "outcome_unknown"]


class ToolJournalError(RuntimeError):
    """journal 层错误的基类（稳定语义，调用方按类型处置）。"""


class ToolJournalSchemaError(ToolJournalError):
    """库不可用：schema 元数据缺失但业务表存在（前版本库），或版本不匹配。"""


class ToolJournalConflictError(ToolJournalError):
    """同 (scope, run_id, tool_use_id) 的身份（tool_name/input_hash）不一致，或结果反序列化失败。"""


def input_hash(tool_input: Any) -> str:
    payload = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def journal_db_path() -> Path:
    return Path.home() / ".nexuscli" / "tool_journal" / "journal.db"


@dataclass(frozen=True, slots=True)
class ToolClaim:
    action: ClaimAction
    tool_use_id: str
    tool_name: str
    input_hash: str
    status: str
    serialized_result: dict[str, Any] | None = None


def _dump_result(content: str, is_error: bool) -> str:
    return json.dumps(
        {"content": content, "is_error": is_error},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _load_result(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolJournalConflictError(f"serialized_result is not valid JSON: {exc}") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("content"), str)
        or not isinstance(value.get("is_error"), bool)
    ):
        raise ToolJournalConflictError(
            "serialized_result must be {'content': str, 'is_error': bool}"
        )
    return value


_DDL = """
CREATE TABLE IF NOT EXISTS journal_schema (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_invocations (
    scope             TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    tool_use_id       TEXT NOT NULL,
    tool_name         TEXT NOT NULL,
    input_hash        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('started','completed','outcome_unknown')),
    serialized_result TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    PRIMARY KEY (scope, run_id, tool_use_id),
    CHECK ((status = 'completed' AND serialized_result IS NOT NULL)
        OR (status != 'completed' AND serialized_result IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_scope_run ON tool_invocations(scope, run_id);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_hash
    ON tool_invocations(scope, run_id, tool_name, input_hash);
"""


class ToolJournal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or journal_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _setup(self) -> None:
        with self._connect() as conn:
            if not self._has_table(conn, "journal_schema") and self._has_table(
                conn, "tool_invocations"
            ):
                # 既有业务表但无版本元数据：前版本库，拒绝猜测
                raise ToolJournalSchemaError(
                    "tool journal database has tool_invocations but no journal_schema "
                    "metadata; refusing to operate on an unknown schema"
                )
            conn.executescript(_DDL)
            if not self._has_table(conn, "journal_schema"):
                raise ToolJournalSchemaError("journal_schema table missing after setup")
            row = conn.execute("select version from journal_schema where id = 1").fetchone()
            if row is None:
                conn.execute(
                    "insert into journal_schema(id, version) values (1, ?)",
                    (JOURNAL_SCHEMA_VERSION,),
                )
            elif int(row[0]) != JOURNAL_SCHEMA_VERSION:
                raise ToolJournalSchemaError(
                    f"tool journal schema version {row[0]} != supported {JOURNAL_SCHEMA_VERSION}"
                )

    @staticmethod
    def _has_table(conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?", (name,)
        ).fetchone()
        return row is not None

    def claim_tool(
        self,
        *,
        scope: str,
        run_id: str,
        tool_use_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> ToolClaim:
        """认领一次调用：四分支语义见模块 docstring。同 id 异身份 → 冲突。"""

        input_digest = input_hash(tool_input)
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                """
                select tool_name, input_hash, status, serialized_result
                from tool_invocations
                where scope = ? and run_id = ? and tool_use_id = ?
                """,
                (scope, run_id, tool_use_id),
            ).fetchone()
            if row is not None:
                stored_name, stored_hash, status, raw_result = row
                if stored_name != tool_name or stored_hash != input_digest:
                    raise ToolJournalConflictError(
                        "tool invocation identity mismatch: same tool_use_id with "
                        "different tool_name/input"
                    )
                if status == "completed":
                    return ToolClaim(
                        action="reuse",
                        tool_use_id=tool_use_id,
                        tool_name=tool_name,
                        input_hash=input_digest,
                        status=status,
                        serialized_result=_load_result(str(raw_result)),
                    )
                # started（上次进程死在执行中途）与 outcome_unknown 同样 fail-closed
                return ToolClaim(
                    action="outcome_unknown",
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    input_hash=input_digest,
                    status=str(status),
                )
            conn.execute(
                """
                insert into tool_invocations(
                    scope, run_id, tool_use_id, tool_name, input_hash,
                    status, serialized_result, created_at, updated_at
                ) values (?, ?, ?, ?, ?, 'started', NULL, ?, ?)
                """,
                (scope, run_id, tool_use_id, tool_name, input_digest, now, now),
            )
        return ToolClaim(
            action="execute",
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            input_hash=input_digest,
            status="started",
        )

    def complete_tool(
        self,
        *,
        scope: str,
        run_id: str,
        tool_use_id: str,
        content: str,
        is_error: bool,
    ) -> None:
        """副作用已发生、结果已产生：落库序列化结果（写失败必须原样上抛）。"""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                update tool_invocations
                set status = 'completed', serialized_result = ?, updated_at = ?
                where scope = ? and run_id = ? and tool_use_id = ? and status = 'started'
                """,
                (_dump_result(content, is_error), _now(), scope, run_id, tool_use_id),
            )
            if cursor.rowcount != 1:
                raise ToolJournalConflictError(f"complete_tool: no started row for {tool_use_id!r}")

    def mark_outcome_unknown(self, *, scope: str, run_id: str, tool_use_id: str) -> None:
        """执行异常/超时：结果未知，fail-closed（不提供 serialized_result）。"""

        with self._connect() as conn:
            cursor = conn.execute(
                """
                update tool_invocations
                set status = 'outcome_unknown', updated_at = ?
                where scope = ? and run_id = ? and tool_use_id = ? and status = 'started'
                """,
                (_now(), scope, run_id, tool_use_id),
            )
            if cursor.rowcount != 1:
                raise ToolJournalConflictError(
                    f"mark_outcome_unknown: no started row for {tool_use_id!r}"
                )

    def lookup_by_hash(
        self,
        *,
        scope: str,
        run_id: str,
        tool_name: str,
        tool_input: Any,
    ) -> ToolClaim | None:
        """hash 级尽力去重（弱保证）：同 run 内同工具同参的既有记录。

        LLM 重生成的调用拿不到原 tool_use_id；恢复 run 沿用原 run_id 时，
        按 (tool_name, input_hash) 查既有记录——completed 复用，started/outcome_unknown
        fail-closed，未命中 None。参数漂移即 miss 并照常执行（诚实边界）。
        """

        digest = input_hash(tool_input)
        with self._connect() as conn:
            row = conn.execute(
                """
                select tool_use_id, status, serialized_result
                from tool_invocations
                where scope = ? and run_id = ? and tool_name = ? and input_hash = ?
                order by created_at
                limit 1
                """,
                (scope, run_id, tool_name, digest),
            ).fetchone()
        if row is None:
            return None
        tool_use_id, status, raw_result = row
        if status == "completed":
            return ToolClaim(
                action="reuse",
                tool_use_id=str(tool_use_id),
                tool_name=tool_name,
                input_hash=digest,
                status=status,
                serialized_result=_load_result(str(raw_result)),
            )
        return ToolClaim(
            action="outcome_unknown",
            tool_use_id=str(tool_use_id),
            tool_name=tool_name,
            input_hash=digest,
            status=str(status),
        )


_journal: ToolJournal | None = None


def get_tool_journal() -> ToolJournal:
    """进程级单例（schema 校验一次）；测试可直接构造 ToolJournal(path=...) 绕开。"""

    global _journal
    if _journal is None:
        _journal = ToolJournal()
    return _journal


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
