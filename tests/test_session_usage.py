"""Tests for per-turn usage records in session transcripts (W-G5).

Every completed regular turn appends a ``type: usage`` record line to the
session JSONL; resuming sums those lines into a baseline so historical usage
continues counting instead of restarting from zero. usage.db semantics are
unchanged (covered by tests/test_usage_history.py).
"""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.session_cmds import _apply_resume, _record_session_usage
from nexuscli.entrypoints.commands.usage import _session_usage_payload
from nexuscli.entrypoints.repl import PermissionModeController, ReplSessionState, _handle_slash
from nexuscli.session import SessionStore, SessionUsageTotals
from nexuscli.types import Message, Usage


def _make_store(tmp_path):
    return SessionStore(root=tmp_path / "sessions")


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _turn_usage(total: int = 100, output: int = 20) -> Usage:
    return Usage(input_tokens=total - output, output_tokens=output)


def _usage_lines(path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    for line in lines[1:]:
        data = json.loads(line)
        if isinstance(data, dict) and data.get("type") == "usage":
            records.append(data)
    return records


def test_append_usage_writes_record_line_with_expected_fields(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="hi")])

    writer.append_usage(
        model="m1",
        provider="deepseek",
        usage=Usage(input_tokens=80, output_tokens=20, cache_hit_tokens=50, cache_miss_tokens=30),
        cost={"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
        ts="2026-10-11 10:00:00",
    )

    records = _usage_lines(writer.path)
    assert len(records) == 1
    record = records[0]
    assert record["type"] == "usage"
    assert record["ts"] == "2026-10-11 10:00:00"
    assert record["model"] == "m1"
    assert record["provider"] == "deepseek"
    assert record["input_tokens"] == 80
    assert record["output_tokens"] == 20
    assert record["total_tokens"] == 100
    assert record["cache_hit_tokens"] == 50
    assert record["cache_miss_tokens"] == 30
    assert record["cost_usd"] == 0.01
    assert record["cost_cny"] == 0.07


def test_load_skips_usage_records_and_corrupt_lines(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="real message")])
    writer.append_usage(usage=_turn_usage())

    record = store.load(writer.meta.id)

    assert record is not None
    assert [m.content for m in record.messages] == ["real message"]
    assert record.meta.message_count == 1


def test_rewrite_preserves_usage_records(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append([Message(role="user", content="one"), Message(role="assistant", content="two")])
    writer.append_usage(usage=_turn_usage(), ts="2026-10-11 10:00:00")

    # History shrink (compression / clear-style rewrite) rebuilds the body;
    # the usage ledger must survive it.
    writer.append([Message(role="user", content="summary")])

    record = store.load(writer.meta.id)
    assert record is not None
    assert [m.content for m in record.messages] == ["summary"]
    assert len(_usage_lines(writer.path)) == 1
    assert store.load_usage(writer.meta.id).total_tokens == 100


def test_load_usage_sums_baselines_and_defaults_to_zero(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path))
    writer.append_usage(usage=_turn_usage(total=100, output=20), ts="2026-10-11 10:00:00")
    writer.append_usage(usage=_turn_usage(total=30, output=30), ts="2026-10-11 10:01:00")

    totals = store.load_usage(writer.meta.id)

    assert totals.turns == 2
    assert totals.input_tokens == 80
    assert totals.output_tokens == 50
    assert totals.total_tokens == 130
    assert totals.cost_usd == 0.0 and totals.cost_cny == 0.0
    # Unknown session or missing file degrades to zeros, never raises.
    assert store.load_usage("missing-id") == SessionUsageTotals()


def test_apply_resume_sets_usage_baseline_from_transcript(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="resumable")])
    writer.append_usage(usage=_turn_usage(total=100, output=20), ts="2026-10-11 10:00:00")
    writer.append_usage(usage=_turn_usage(total=30, output=30), ts="2026-10-11 10:01:00")
    baseline_expected = store.load_usage(writer.meta.id)

    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    _apply_resume(console, state, agent, resume=writer.meta.id, continue_last=False)

    assert len(agent.history) == 1
    assert state.usage_baseline == baseline_expected
    assert state.usage_baseline.total_tokens == 130
    assert state.process_usage == SessionUsageTotals()


def test_resume_slash_sets_usage_baseline(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="resumable")])
    writer.append_usage(usage=_turn_usage(), ts="2026-10-11 10:00:00")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    agent = SimpleNamespace(
        history=[],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
    )
    console = _console()

    should_exit = asyncio.run(
        _handle_slash(
            f"/resume {writer.meta.id}",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert should_exit is False
    assert state.usage_baseline.turns == 1
    assert state.usage_baseline.total_tokens == 100


def test_record_session_usage_appends_and_accumulates(tmp_path):
    store = _make_store(tmp_path)
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    state.writer.append([Message(role="user", content="turn one")])
    agent = SimpleNamespace(
        llm_client=SimpleNamespace(model_name="m1", provider_name="deepseek"),
        last_usage=_turn_usage(total=100, output=20),
        last_cost={"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
    )

    _record_session_usage(state, agent)
    _record_session_usage(state, agent)

    assert state.process_usage.turns == 2
    assert state.process_usage.total_tokens == 200
    assert state.process_usage.cost_usd == 0.02
    assert len(_usage_lines(state.writer.path)) == 2
    records = _usage_lines(state.writer.path)
    assert records[0]["model"] == "m1" and records[0]["provider"] == "deepseek"


def test_record_session_usage_is_best_effort(tmp_path):
    store = _make_store(tmp_path)
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    broken_agent = SimpleNamespace(last_usage=None, last_cost=None)

    # A malformed agent (no llm_client, None usage) must not raise out.
    _record_session_usage(state, broken_agent)

    assert state.process_usage == SessionUsageTotals()


def test_session_usage_payload_combines_baseline_and_process():
    baseline = SessionUsageTotals(
        turns=3,
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        cost_usd=0.03,
    )
    process = SessionUsageTotals(turns=1, input_tokens=10, output_tokens=5, total_tokens=15)
    state = SimpleNamespace(usage_baseline=baseline, process_usage=process)

    payload = _session_usage_payload(state)

    assert payload["historical_total"]["turns"] == 4
    assert payload["historical_total"]["total_tokens"] == 165
    assert payload["current_process"]["turns"] == 1
    assert payload["current_process"]["total_tokens"] == 15
    assert set(payload["sources"]) == {"historical_total", "current_process"}
    assert "transcript" in payload["sources"]["historical_total"]


def test_session_usage_payload_without_state_is_zeroed():
    payload = _session_usage_payload(None)

    assert payload["historical_total"] == SessionUsageTotals().to_dict()
    assert payload["current_process"] == SessionUsageTotals().to_dict()
    assert set(payload["sources"]) == {"historical_total", "current_process"}


def _dirty_process() -> SessionUsageTotals:
    """A non-zero process accumulator, as if earlier turns were recorded."""
    return SessionUsageTotals(turns=2, input_tokens=160, output_tokens=40, total_tokens=200)


def test_apply_resume_resets_process_usage(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="resumable")])
    writer.append_usage(usage=_turn_usage(), ts="2026-10-11 10:00:00")

    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    state.process_usage = _dirty_process()  # turns of the previous transcript
    agent = SimpleNamespace(history=[], cwd=str(tmp_path))
    console = _console()

    _apply_resume(console, state, agent, resume=writer.meta.id, continue_last=False)

    # baseline + process must equal THIS transcript's ledger, so the old
    # transcript's process turns must not survive the switch.
    assert state.process_usage == SessionUsageTotals()
    assert state.usage_baseline.turns == 1


def test_resume_slash_resets_process_usage(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="resumable")])
    writer.append_usage(usage=_turn_usage(), ts="2026-10-11 10:00:00")
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    state.process_usage = _dirty_process()
    agent = SimpleNamespace(
        history=[],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
    )
    console = _console()

    asyncio.run(
        _handle_slash(
            f"/resume {writer.meta.id}",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert state.process_usage == SessionUsageTotals()
    assert state.usage_baseline.turns == 1


def test_clear_slash_resets_process_usage(tmp_path):
    store = _make_store(tmp_path)
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))
    state.usage_baseline = _dirty_process()
    state.process_usage = _dirty_process()
    agent = SimpleNamespace(
        history=[Message(role="user", content="old")],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
        clear_history=lambda: None,
    )
    console = _console()

    asyncio.run(
        _handle_slash(
            "/clear",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert state.usage_baseline == SessionUsageTotals()
    assert state.process_usage == SessionUsageTotals()


def test_fork_slash_resets_process_usage(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="fork me")])
    writer.append_usage(usage=_turn_usage(), ts="2026-10-11 10:00:00")
    state = ReplSessionState(store=store, writer=writer)
    state.usage_baseline = _dirty_process()
    state.process_usage = _dirty_process()
    agent = SimpleNamespace(
        history=[Message(role="user", content="before fork")],
        cwd=str(tmp_path),
        llm_client=SimpleNamespace(model_name="m1", provider_name="p"),
        skill_context_buffer=SimpleNamespace(clear=lambda: None),
    )
    console = _console()

    asyncio.run(
        _handle_slash(
            "/fork",
            console,
            str(tmp_path),
            NexusCliConfig(),
            agent,
            None,
            PermissionModeController(NexusCliConfig()),
            None,
            state,
        )
    )

    assert state.usage_baseline == SessionUsageTotals()
    assert state.process_usage == SessionUsageTotals()


def test_load_usage_degrades_bad_numeric_fields(tmp_path):
    store = _make_store(tmp_path)
    writer = store.new_writer(cwd=str(tmp_path), model="m1", provider="deepseek")
    writer.append([Message(role="user", content="hi")])
    writer.append_usage(usage=_turn_usage(total=100, output=20), ts="2026-10-11 10:00:00")
    # Hand-edited line: valid JSON, garbage numbers. Contract says the
    # baseline degrades instead of raising.
    path = store.root / f"{writer.meta.id}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    bad = json.dumps(
        {
            "type": "usage",
            "ts": "t",
            "input_tokens": "abc",
            "output_tokens": None,
            "total_tokens": [],
            "cost_usd": {"nested": 1},
            "cost_cny": "1.5",
        },
        ensure_ascii=False,
    )
    lines.append(bad)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    totals = store.load_usage(writer.meta.id)

    assert totals.turns == 1  # the bad line is skipped whole, not partially applied
    assert totals.input_tokens == 80  # only the well-formed line contributes
    assert totals.cost_cny == 0.0  # "1.5" belongs to the degraded line


def test_single_shot_session_usage_combines_baseline_and_run():
    from nexuscli.entrypoints.cli import _single_shot_session_usage

    baseline = SessionUsageTotals(turns=3, input_tokens=100, output_tokens=50, total_tokens=150)
    run = SessionUsageTotals.from_turn(usage=Usage(input_tokens=80, output_tokens=20))

    payload = _single_shot_session_usage(SimpleNamespace(usage=run, cost=None), baseline)

    assert payload["historical_total"]["turns"] == 4
    # Usage defaults total_tokens to input + output (100) for the run.
    assert payload["historical_total"]["total_tokens"] == 250
    assert payload["current_process"]["turns"] == 1
    assert payload["current_process"]["total_tokens"] == 100
    assert set(payload["sources"]) == {"historical_total", "current_process"}
