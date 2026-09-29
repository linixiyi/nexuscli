"""Local per-turn usage persistence for NexusCLI (SQLite at ~/.nexuscli/usage.db).

One row per completed turn: token counts, model/provider identity and the costs
computed by ``agent._calculate_costs`` (``{currency: CostBreakdown.to_dict()}``).
``/usage stats [N days]`` aggregates the table over a trailing N-day window
(total tokens, total cost, per-model split).

Hard rules:
- Every SQL value is passed through bound parameters (``?`` placeholders fed
  from a tuple) — never interpolated, ``format``-ed or f-stringed into the SQL
  text (Mimosa constraint). DDL is static string literals only.
- Best-effort semantics: recording must never interrupt a turn. The REPL call
  site (``repl._record_turn_usage``) swallows every exception; this module
  raises nothing of its own beyond what ``sqlite3`` itself raises.

Timestamps are local time in the fixed ``_TS_FORMAT`` layout: lexicographic
order equals chronological order, so the day-window query is a plain string
comparison against a bound parameter — no date functions needed.

Not ported from the ZCode reference (the shape we mirror is
``packages/services/src/usage-stats/usageStatsService.ts:70`` — "App Usage
reads real agent-database stats"): the bigmodel quota/monitoring provider
family it wires up (``providers/*.ts``, six files), the aggregation background
job, retention-window/cleanup policies, and the multi-currency account system.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from nexuscli.types import Usage

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def usage_db_path(home: Path | None = None) -> Path:
    """Return the usage database path under the given (or current) home.

    ``home`` is resolved per call — same convention as
    ``session.store.sessions_root`` — so tests can redirect ``Path.home()``.
    """
    base = home if home is not None else Path.home()
    return base / ".nexuscli" / "usage.db"


class UsageStore:
    """SQLite store holding one ``turn_usage`` row per completed turn."""

    def __init__(self, path: Path | None = None):
        self.path = path or usage_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        # Same connection shape as MemoryManager._connect (memory/manager.py):
        # a busy timeout keeps concurrent REPL/worker writers from failing fast.
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 30000")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists turn_usage (
                    id integer primary key autoincrement,
                    ts text not null,
                    session_id text not null default '',
                    model text not null,
                    provider text not null default '',
                    input_tokens integer not null default 0,
                    output_tokens integer not null default 0,
                    total_tokens integer not null default 0,
                    cost_usd real not null default 0,
                    cost_cny real not null default 0
                )
                """
            )
            conn.execute("create index if not exists idx_turn_usage_ts on turn_usage(ts)")

    def record_turn(
        self,
        *,
        model: str,
        provider: str,
        session_id: str = "",
        usage: Usage,
        cost: dict[str, Any] | None = None,
        ts: str | None = None,
    ) -> None:
        """Insert one completed turn.

        ``ts`` is a fixed-instant seam for tests (defaults to "now", local
        time, in ``_TS_FORMAT``). ``cost`` follows the shape returned by
        ``agent._calculate_costs``: ``{currency: CostBreakdown.to_dict()}``
        where each breakdown carries a float ``total_cost``; missing or empty
        entries degrade to 0.0. All values go through bound parameters.
        """
        row_ts = ts or datetime.now().strftime(_TS_FORMAT)
        costs = cost or {}
        usd = costs.get("usd") or {}
        cny = costs.get("cny") or {}
        cost_usd = float(usd.get("total_cost") or 0.0)
        cost_cny = float(cny.get("total_cost") or 0.0)
        with self._connect() as conn:
            conn.execute(
                """
                insert into turn_usage
                    (ts, session_id, model, provider, input_tokens, output_tokens,
                     total_tokens, cost_usd, cost_cny)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_ts,
                    session_id,
                    model,
                    provider,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    cost_usd,
                    cost_cny,
                ),
            )

    def stats(self, days: int = 7, *, now: datetime | None = None) -> dict[str, Any]:
        """Aggregate the trailing ``days``-day window; ``now`` is a test seam.

        An empty (or fully out-of-window) table yields all zeros with an empty
        ``by_model`` list — ``coalesce`` guarantees the zero defaults.
        """
        if days < 1:
            raise ValueError("days must be >= 1")
        since = ((now or datetime.now()) - timedelta(days=days)).strftime(_TS_FORMAT)
        with self._connect() as conn:
            total = conn.execute(
                """
                select count(*) as turns,
                       coalesce(sum(input_tokens), 0) as input_tokens,
                       coalesce(sum(output_tokens), 0) as output_tokens,
                       coalesce(sum(total_tokens), 0) as total_tokens,
                       coalesce(sum(cost_usd), 0) as cost_usd,
                       coalesce(sum(cost_cny), 0) as cost_cny
                from turn_usage
                where ts >= ?
                """,
                (since,),
            ).fetchone()
            by_model_rows = conn.execute(
                """
                select model,
                       count(*) as turns,
                       coalesce(sum(total_tokens), 0) as total_tokens,
                       coalesce(sum(cost_usd), 0) as cost_usd,
                       coalesce(sum(cost_cny), 0) as cost_cny
                from turn_usage
                where ts >= ?
                group by model
                order by sum(total_tokens) desc
                """,
                (since,),
            ).fetchall()
        return {
            "days": days,
            "turns": int(total["turns"]),
            "input_tokens": int(total["input_tokens"]),
            "output_tokens": int(total["output_tokens"]),
            "total_tokens": int(total["total_tokens"]),
            "total_cost": {"usd": float(total["cost_usd"]), "cny": float(total["cost_cny"])},
            "by_model": [
                {
                    "model": str(row["model"]),
                    "turns": int(row["turns"]),
                    "total_tokens": int(row["total_tokens"]),
                    "total_cost": {
                        "usd": float(row["cost_usd"]),
                        "cny": float(row["cost_cny"]),
                    },
                }
                for row in by_model_rows
            ],
        }
