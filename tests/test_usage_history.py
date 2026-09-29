"""Tests for the local per-turn usage store (W6 task-usage-history).

``~/.nexuscli/usage.db`` gets one row per completed turn and
``/usage stats [N days]`` aggregates the trailing N-day window. The
pre-existing ``/usage`` per-turn display must stay untouched (pinned by the
slash-command test below).
"""

from __future__ import annotations

import asyncio
import io
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import (
    PermissionModeController,
    _handle_slash,
    _record_turn_usage,
)
from nexuscli.llm.usage_store import UsageStore
from nexuscli.types import Usage


def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home so the usage db is isolated.

    Mirrors tests/test_skill_force_load.py: on Windows Path.home() reads
    USERPROFILE (not HOME), so both environment variables are redirected too.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _console_text(console: Console) -> str:
    return console.file.getvalue()


def _run_slash(raw: str, console: Console, tmp_path: Path, config, agent) -> bool:
    return asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            config,
            agent,
            None,
            PermissionModeController(config),
            None,
            None,
            custom_commands=None,
        )
    )


class _BoomStore:
    """Stand-in UsageStore whose construction always explodes."""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("boom")


def test_record_and_stats_roundtrip(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()
    assert store.path == home / ".nexuscli" / "usage.db"

    usage_a = Usage(input_tokens=100, output_tokens=20)
    usage_b = Usage(input_tokens=50, output_tokens=30)
    cost_a = {"usd": {"total_cost": 0.01}}
    cost_b = {"usd": {"total_cost": 0.01}}
    store.record_turn(model="m-a", provider="p", session_id="s1", usage=usage_a, cost=cost_a)
    store.record_turn(model="m-b", provider="p", session_id="s1", usage=usage_b, cost=cost_b)

    summary = store.stats(days=30)

    assert summary["turns"] == 2
    assert summary["total_tokens"] == usage_a.total_tokens + usage_b.total_tokens
    assert summary["total_cost"]["usd"] == pytest.approx(0.02)
    assert len(summary["by_model"]) == 2
    # 120 tokens sorts above 80, so m-a leads the per-model table.
    assert summary["by_model"][0]["model"] == "m-a"
    assert summary["by_model"][0]["total_tokens"] == usage_a.total_tokens
    assert summary["by_model"][1]["model"] == "m-b"
    assert summary["by_model"][1]["total_tokens"] == usage_b.total_tokens


def test_stats_days_window_excludes_old(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()
    usage_old = Usage(input_tokens=10, output_tokens=0)
    usage_new = Usage(input_tokens=20, output_tokens=0)
    store.record_turn(model="m-old", provider="p", usage=usage_old, ts="2026-09-01 10:00:00")
    store.record_turn(model="m-new", provider="p", usage=usage_new, ts="2026-09-29 10:00:00")

    fixed_now = datetime(2026, 9, 29, 12, 0)
    week = store.stats(days=7, now=fixed_now)
    wide = store.stats(days=60, now=fixed_now)

    # 7-day window starts at 2026-09-22 12:00 — only the 09-29 row is inside.
    assert week["turns"] == 1
    assert week["total_tokens"] == usage_new.total_tokens
    assert [row["model"] for row in week["by_model"]] == ["m-new"]
    # 60-day window reaches back past 09-01 — both rows count.
    assert wide["turns"] == 2


def test_empty_db_stats_are_zero(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    store = UsageStore()

    summary = store.stats(days=7)

    assert summary["turns"] == 0
    assert summary["input_tokens"] == 0
    assert summary["output_tokens"] == 0
    assert summary["total_tokens"] == 0
    assert summary["total_cost"] == {"usd": 0.0, "cny": 0.0}
    assert summary["by_model"] == []


def test_record_turn_usage_helper_persists_and_swallows(tmp_path, monkeypatch):
    home = _isolate_home(tmp_path, monkeypatch)
    stub_usage = Usage(input_tokens=10, output_tokens=5)
    stub_cost = {"usd": {"total_cost": 0.01}}
    agent = SimpleNamespace(
        llm_client=SimpleNamespace(model_name="m", provider_name="p"),
        last_usage=stub_usage,
        last_cost=stub_cost,
    )

    _record_turn_usage(agent, "sess1")

    store = UsageStore()
    summary = store.stats(days=7)
    assert summary["turns"] == 1
    assert summary["total_tokens"] == 15
    db_path = home / ".nexuscli" / "usage.db"
    conn = sqlite3.connect(db_path)
    try:
        session_query = "select session_id from turn_usage"
        stored_session = conn.execute(session_query).fetchone()[0]
    finally:
        conn.close()
    assert stored_session == "sess1"

    # (b) Best-effort: a broken store must never raise out of the helper.
    monkeypatch.setattr("nexuscli.entrypoints.repl.UsageStore", _BoomStore)
    _record_turn_usage(agent, "sess1")


def test_usage_slash_keeps_turn_display_and_adds_stats(tmp_path, monkeypatch):
    _isolate_home(tmp_path, monkeypatch)
    config = NexusCliConfig()
    agent = SimpleNamespace(
        last_usage=Usage(input_tokens=1, output_tokens=2),
        last_cost={},
    )

    turn_console = _console()
    _run_slash("/usage", turn_console, tmp_path, config, agent)
    turn_text = _console_text(turn_console)
    assert "usage" in turn_text
    assert "pricing_note" in turn_text

    stats_console = _console()
    _run_slash("/usage stats 7", stats_console, tmp_path, config, agent)
    stats_text = _console_text(stats_console)
    assert "Usage" in stats_text
    assert "turns" in stats_text

    bad_console = _console()
    _run_slash("/usage stats abc", bad_console, tmp_path, config, agent)
    # "abc" is not a digit string: it normalizes to the 7-day default table.
    assert "last 7 day(s)" in _console_text(bad_console)
