"""Tests for the usage.db source column (usage-coverage slice).

``turn_usage`` gains a ``source`` column: ``repl`` rows are the main REPL
turns that have always been recorded; ``subagent`` rows come from delegated
runs — ``task``-tool delegations and ``/workflow`` / ``/expert`` step
dispatches alike — and are written by ``subagent.run_subagent`` (both the
foreground and the background path share that recorder). ``/usage stats``
splits the window per source so delegated work is visible.
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from nexuscli.agent import subagent as subagent_module
from nexuscli.agent.bg_tasks import bg_subagent_registry
from nexuscli.agent.subagent import BUILTIN_AGENTS, run_subagent
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.usage import _usage_stats_command
from nexuscli.llm.usage_store import UsageStore
from nexuscli.tools.base import ToolContext
from nexuscli.types import Usage


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home so the usage db is isolated.

    Mirrors tests/test_usage_history.py: on Windows Path.home() reads
    USERPROFILE (not HOME), so both environment variables are redirected too.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _test_config(tmp_path: Path, session_id: str | None = None) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    config.policy.session_id = session_id
    return config


def _rows(db_path: Path) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("select * from turn_usage order by id").fetchall()  # noqa: BLE001 - test read helper
    finally:
        conn.close()


def _use_fake_llm(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    # **_kwargs: production call sites pass telemetry_enabled= (factory seam).
    monkeypatch.setattr("nexuscli.llm.factory.create_llm_client", lambda _config, **_kwargs: client)


# ---------------------------------------------------------------------------
# FakeClient (copied verbatim from tests/test_subagent.py) plus a usage event
# helper so the agent's done event carries nonzero usage for the recorder.
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal LLM stub: replays one scripted streaming-event turn per chat call."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, *turns: list[dict[str, Any]]):
        self.turns = list(turns)
        self.calls: list[int] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls.append(len(messages))
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        for event in turn:
            yield event


def _usage_event(input_tokens: int = 10, output_tokens: int = 5) -> dict[str, Any]:
    return {
        "type": "usage",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# Store: source column migration, record_turn defaults and overrides
# ---------------------------------------------------------------------------


def test_old_schema_db_migrates_and_labels_rows_repl(tmp_path, monkeypatch):
    """A pre-source-column database keeps its rows, now labelled source='repl'."""
    home = _isolate_home(tmp_path, monkeypatch)
    db_path = home / ".nexuscli" / "usage.db"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    try:
        # The old DDL, verbatim from before the source column existed.
        conn.execute(
            """
            create table turn_usage (
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
        conn.execute(
            """
            insert into turn_usage (ts, session_id, model, provider, input_tokens, output_tokens,
                                    total_tokens, cost_usd, cost_cny)
            values (
                '2026-10-01 10:00:00', 'old-sess', 'old-model', 'old-provider',
                7, 3, 10, 0.0, 0.0
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    UsageStore()  # opening the store performs the migration

    migrated = _rows(db_path)
    assert len(migrated) == 1
    assert migrated[0]["source"] == "repl"  # pre-existing rows are main REPL turns

    # The migrated table keeps accepting both default and explicit sources.
    store = UsageStore()
    store.record_turn(model="m", provider="p", usage=Usage(input_tokens=1, output_tokens=1))
    store.record_turn(
        model="m", provider="p", usage=Usage(input_tokens=2, output_tokens=2), source="subagent"
    )
    rows = _rows(db_path)
    assert [row["source"] for row in rows] == ["repl", "repl", "subagent"]


def test_record_turn_source_defaults_and_empty_degrades(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()
    db_path = store.path

    store.record_turn(model="m", provider="p", usage=Usage(input_tokens=1, output_tokens=1))
    store.record_turn(
        model="m", provider="p", usage=Usage(input_tokens=1, output_tokens=1), source=""
    )
    store.record_turn(
        model="m", provider="p", usage=Usage(input_tokens=1, output_tokens=1), source="subagent"
    )

    rows = _rows(db_path)
    assert [row["source"] for row in rows] == ["repl", "repl", "subagent"]


# ---------------------------------------------------------------------------
# run_subagent: foreground recorder
# ---------------------------------------------------------------------------


def test_run_subagent_records_usage_with_source_subagent(tmp_path, monkeypatch):
    """A completed subagent run lands one source=subagent row in usage.db."""
    _isolate_home(tmp_path, monkeypatch)
    client = FakeClient(
        [
            _usage_event(input_tokens=12, output_tokens=3),
            {"type": "text_delta", "text": "子代理报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path, session_id="sess-main")

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "小任务",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert report == "子代理报告"
    rows = _rows(Path.home() / ".nexuscli" / "usage.db")
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "subagent"
    assert row["model"] == "fake-model"
    assert row["provider"] == "fake-provider"
    assert row["session_id"] == "sess-main"
    assert row["input_tokens"] == 12
    assert row["output_tokens"] == 3
    assert row["total_tokens"] == 15


def test_run_subagent_zero_usage_records_nothing(tmp_path, monkeypatch):
    """Runs without usage metadata (scripted stubs) must not create zero rows."""
    _isolate_home(tmp_path, monkeypatch)
    client = FakeClient([{"type": "text_delta", "text": "报告"}, {"type": "message_end"}])
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path)

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["explore"],
            "探索",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert report == "报告"
    db_path = Path.home() / ".nexuscli" / "usage.db"
    # No store was ever constructed: the db file itself must not appear.
    if db_path.exists():
        assert _rows(db_path) == []


def test_run_subagent_store_failure_does_not_break_run(tmp_path, monkeypatch):
    """Best-effort: a broken store must not fail the delegation or its report."""

    class _BoomStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    _isolate_home(tmp_path, monkeypatch)
    client = FakeClient(
        [
            _usage_event(),
            {"type": "text_delta", "text": "报告照常返回"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    monkeypatch.setattr(subagent_module, "UsageStore", _BoomStore)
    config = _test_config(tmp_path)

    report = asyncio.run(
        run_subagent(
            BUILTIN_AGENTS["general-purpose"],
            "任务",
            ToolContext(cwd=str(tmp_path), config=config),
        )
    )

    assert report == "报告照常返回"


# ---------------------------------------------------------------------------
# run_subagent: background path records at completion too
# ---------------------------------------------------------------------------


@pytest.fixture
def _clean_bg_subagent_registry():
    """The registry is a process-wide singleton: drop all state after each test."""
    yield
    bg_subagent_registry._tasks.clear()
    bg_subagent_registry._results.clear()
    bg_subagent_registry._notified.clear()


def test_background_subagent_records_usage_on_completion(
    tmp_path, monkeypatch, _clean_bg_subagent_registry
):
    _isolate_home(tmp_path, monkeypatch)
    client = FakeClient(
        [
            _usage_event(input_tokens=4, output_tokens=1),
            {"type": "text_delta", "text": "后台报告"},
            {"type": "message_end", "stop_reason": "end_turn"},
        ]
    )
    _use_fake_llm(monkeypatch, client)
    config = _test_config(tmp_path, session_id="sess-bg")

    async def scenario():
        context = ToolContext(cwd=str(tmp_path), config=config)
        entry = bg_subagent_registry.start(
            BUILTIN_AGENTS["general-purpose"],
            "后台任务",
            context,
            agent_type="general-purpose",
            description="后台调研",
        )
        deadline = time.monotonic() + 10.0
        while bg_subagent_registry.status(entry.task_id) == "running":
            if time.monotonic() > deadline:
                raise AssertionError("background subagent never reached a terminal state")
            await asyncio.sleep(0.01)
        return entry

    entry = asyncio.run(scenario())

    finished = bg_subagent_registry.result(entry.task_id)
    assert finished is not None
    assert finished.status == "completed"
    rows = _rows(Path.home() / ".nexuscli" / "usage.db")
    assert len(rows) == 1
    assert rows[0]["source"] == "subagent"
    assert rows[0]["total_tokens"] == 5
    assert rows[0]["session_id"] == "sess-bg"


# ---------------------------------------------------------------------------
# stats: per-source grouping, in data and in the /usage stats view
# ---------------------------------------------------------------------------


def test_stats_groups_by_source(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()
    store.record_turn(model="m", provider="p", usage=Usage(input_tokens=10, output_tokens=0))
    store.record_turn(
        model="m",
        provider="p",
        usage=Usage(input_tokens=5, output_tokens=5),
        source="subagent",
        cost={"usd": {"total_cost": 0.01}},
    )

    summary = store.stats(days=30)

    assert summary["turns"] == 2
    by_source = {row["source"]: row for row in summary["by_source"]}
    assert set(by_source) == {"repl", "subagent"}
    assert by_source["repl"]["turns"] == 1
    assert by_source["repl"]["total_tokens"] == 10
    assert by_source["subagent"]["turns"] == 1
    assert by_source["subagent"]["total_tokens"] == 10
    assert by_source["subagent"]["total_cost"]["usd"] == pytest.approx(0.01)


def test_empty_db_stats_by_source_is_empty(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()

    summary = store.stats(days=7)

    assert summary["by_source"] == []


def test_usage_stats_command_renders_by_source_table(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()
    store.record_turn(model="m", provider="p", usage=Usage(input_tokens=10, output_tokens=0))
    store.record_turn(
        model="m",
        provider="p",
        usage=Usage(input_tokens=5, output_tokens=5),
        source="subagent",
    )

    console = Console(file=io.StringIO(), width=200)
    _usage_stats_command("7", console)

    text = console.file.getvalue()
    assert "By source" in text
    assert "subagent" in text
    assert "repl" in text


def test_usage_stats_command_without_source_rows_renders_no_by_source_table(tmp_path, monkeypatch):
    """No rows in the window → no empty By source table, stats stay alive."""
    _isolate_home(tmp_path, monkeypatch)

    console = Console(file=io.StringIO(), width=200)
    _usage_stats_command("7", console)

    text = console.file.getvalue()
    assert "By source" not in text
    assert "last 7 day(s)" in text


def test_usage_stats_command_tolerates_store_without_by_source(tmp_path, monkeypatch):
    """A stubbed store without the new key must not kill the stats view."""

    class _LegacyStore:
        def __init__(self) -> None:
            pass

        def stats(self, days: int) -> dict[str, Any]:
            return {
                "days": days,
                "turns": 1,
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "total_cost": {"usd": 0.0, "cny": 0.0},
                "by_model": [],
            }

    _isolate_home(tmp_path, monkeypatch)
    monkeypatch.setattr("nexuscli.entrypoints.commands.usage.UsageStore", _LegacyStore)

    console = Console(file=io.StringIO(), width=200)
    _usage_stats_command("7", console)

    text = console.file.getvalue()
    assert "turns" in text
    assert "By source" not in text
