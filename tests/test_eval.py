"""Tests for ``nexuscli.eval`` — offline scripted eval regression.

Covers:
- scenario loading and strict schema validation (clear errors with the file
  path, unknown keys rejected, deny rules bound to defined tools);
- the runner driving a real react Agent with a scripted LLM client and stub
  tools, including the built-in happy-path, permission-denial,
  multi-turn-convergence and loop-guard-repeat scenarios;
- all four assertions (expected_tool_sequence, final_contains,
  forbidden_tools, max_turns) failing with readable reasons;
- the reporter's regression comparison matrix and report round-trip;
- the ``nexuscli eval list`` / ``eval run`` CLI commands, including
  regression detection across two runs against a fabricated baseline.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.eval import (
    EvalRunResult,
    EvalSchemaError,
    build_report,
    builtin_eval_dir,
    comparison_verdict,
    format_report,
    load_eval_scenario,
    load_eval_scenarios,
    load_previous_report,
    previous_statuses,
    run_eval_scenarios,
    run_scenario,
    write_report,
)
from nexuscli.eval.reporter import REPORT_RELATIVE, PreviousReportError

runner = CliRunner()


def _builtin_scenario(name_prefix: str) -> Path:
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None, "repository ships examples/evals"
    matches = sorted(eval_dir.glob(f"{name_prefix}*.json"))
    assert matches, f"builtin scenario {name_prefix} not found"
    return matches[0]


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _simple_scenario_data(**overrides) -> dict:
    data = {
        "name": "simple",
        "prompt": "do the thing",
        "tools": [{"name": "list_tasks", "description": "List tasks.", "result": "t1"}],
        "scripted_turns": [
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
            {"text": "task t1 done"},
        ],
        "assertions": {"expected_tool_sequence": ["list_tasks"], "max_turns": 2},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_builtin_eval_dir_ships_the_builtin_scenarios():
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None
    scenarios = load_eval_scenarios(eval_dir)
    names = [scenario.name for scenario in scenarios]
    assert len(names) == 5
    assert set(names) == {
        "read-task-details",
        "denied-privileged-call",
        "multi-turn-search-converge",
        "repeat-call-loop-guard",
        "memory-recall-target-first",
    }


def test_loader_rejects_missing_prompt(tmp_path):
    path = _write_scenario(tmp_path, {"name": "x", "scripted_turns": [{"text": "hi"}]})
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert str(path) in str(excinfo.value)
    assert "prompt" in str(excinfo.value)


def test_loader_rejects_invalid_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert str(path) in str(excinfo.value)


def test_loader_rejects_unknown_assertion_key(tmp_path):
    data = _simple_scenario_data()
    data["assertions"]["pass_at_k"] = 1
    path = _write_scenario(tmp_path, data)
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert "pass_at_k" in str(excinfo.value)
    assert "expected_tool_sequence" in str(excinfo.value)


def test_loader_rejects_empty_scripted_turns(tmp_path):
    path = _write_scenario(tmp_path, {"name": "x", "prompt": "p", "scripted_turns": []})
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert "scripted_turns" in str(excinfo.value)


def test_loader_rejects_turn_without_text_or_tool_calls(tmp_path):
    path = _write_scenario(
        tmp_path, {"name": "x", "prompt": "p", "scripted_turns": [{"unrelated": 1}]}
    )
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert "scripted_turns[0]" in str(excinfo.value)


def test_loader_rejects_deny_rule_targeting_undefined_tool(tmp_path):
    data = _simple_scenario_data(permission_deny=["deploy_app"])
    path = _write_scenario(tmp_path, data)
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert "deploy_app" in str(excinfo.value)


def test_loader_accepts_deny_rule_for_defined_tool(tmp_path):
    data = _simple_scenario_data(permission_deny=["list_tasks"])
    path = _write_scenario(tmp_path, data)
    scenario = load_eval_scenario(path)
    assert scenario.permission_deny == ["list_tasks"]


def test_loader_rejects_duplicate_tool_names(tmp_path):
    data = _simple_scenario_data(
        tools=[
            {"name": "same", "result": "a"},
            {"name": "same", "result": "b"},
        ]
    )
    path = _write_scenario(tmp_path, data)
    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)
    assert "same" in str(excinfo.value)


def test_load_directory_returns_scenarios_sorted_by_filename(tmp_path):
    _write_scenario(tmp_path, _simple_scenario_data(name="b-second"), "b.json")
    _write_scenario(tmp_path, _simple_scenario_data(name="a-first"), "a.json")
    (tmp_path / "ignored.txt").write_text("not json", encoding="utf-8")

    scenarios = load_eval_scenarios(tmp_path)

    assert [scenario.name for scenario in scenarios] == ["a-first", "b-second"]


# ---------------------------------------------------------------------------
# Runner — built-in scenarios through a real react Agent
# ---------------------------------------------------------------------------


def test_all_builtin_scenarios_pass_offline(tmp_path):
    scenarios = load_eval_scenarios(builtin_eval_dir())

    results = run_eval_scenarios(scenarios, cwd=str(tmp_path))

    assert [result.passed for result in results] == [True] * len(scenarios)
    assert len(results) == 5
    assert all(result.failure_reasons == [] for result in results)


def test_happy_path_scenario_records_tool_sequence_and_final_text(tmp_path):
    scenario = load_eval_scenario(_builtin_scenario("01_"))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed
    assert result.tool_sequence == ["list_tasks", "get_task_detail"]
    assert "fix login" in result.final_text
    assert result.turns == 3


def test_denied_scenario_goes_through_the_real_permission_gate(tmp_path):
    """The stub tool's canned result ('deployed') must never reach the model:
    the real deny rule intercepts the call, which is what the final text
    asserts — proving the gate chain is real, not stubbed."""
    scenario = load_eval_scenario(_builtin_scenario("02_"))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed
    assert result.tool_sequence == ["deploy_app"]
    assert "denied" in result.final_text
    assert "deploy succeeded" not in result.final_text


def test_multi_turn_scenario_converges_within_budget(tmp_path):
    scenario = load_eval_scenario(_builtin_scenario("03_"))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed
    assert result.tool_sequence == ["search_docs", "read_doc", "draft_reply"]
    assert result.turns == 4


# ---------------------------------------------------------------------------
# Runner — assertion failures
# ---------------------------------------------------------------------------


def test_max_turns_assertion_fails_when_loop_overshoots(tmp_path):
    data = _simple_scenario_data(
        scripted_turns=[
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
            {"text": "done"},
        ]
    )
    data["assertions"] = {"max_turns": 1}
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert not result.passed
    assert any("took 3 turns, limit is 1" in reason for reason in result.failure_reasons)


def test_forbidden_tool_assertion_fails_on_call(tmp_path):
    data = _simple_scenario_data()
    data["assertions"] = {"forbidden_tools": ["list_tasks"]}
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert not result.passed
    assert "forbidden tool was called: list_tasks" in result.failure_reasons


def test_final_contains_assertion_fails_on_missing_text(tmp_path):
    data = _simple_scenario_data()
    data["assertions"] = {"final_contains": "totally absent"}
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert not result.passed
    assert any("does not contain" in reason for reason in result.failure_reasons)


def test_tool_sequence_assertion_fails_on_mismatch(tmp_path):
    data = _simple_scenario_data()
    data["assertions"] = {"expected_tool_sequence": ["other_tool", "list_tasks"]}
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert not result.passed
    assert any("tool sequence mismatch" in reason for reason in result.failure_reasons)


def test_unknown_tool_call_fails_the_sequence_assertion(tmp_path):
    """A scripted turn calling an unregistered tool still reports a clean
    failure (the executor returns an error tool result, not an exception),
    and the sequence assertion catches the unexpected call."""
    data = _simple_scenario_data(
        tools=[],
        scripted_turns=[
            {"tool_calls": [{"name": "missing_tool", "arguments": {}}]},
            {"text": "gave up"},
        ],
    )
    data["assertions"] = {"expected_tool_sequence": ["list_tasks"]}
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert not result.passed
    assert any("tool sequence mismatch" in reason for reason in result.failure_reasons)


# ---------------------------------------------------------------------------
# Reporter — regression comparison
# ---------------------------------------------------------------------------


def test_comparison_verdict_matrix():
    assert comparison_verdict(None, "pass") == "new"
    assert comparison_verdict(None, "fail") == "new"
    assert comparison_verdict("pass", "fail") == "regression"
    assert comparison_verdict("fail", "pass") == "fixed"
    assert comparison_verdict("pass", "pass") == "unchanged"
    assert comparison_verdict("fail", "fail") == "still_failing"


def test_build_report_against_fabricated_previous_report():
    previous = {
        "scenarios": [
            {"name": "regressed", "status": "pass"},
            {"name": "fixed", "status": "fail"},
            {"name": "stable", "status": "pass"},
            {"name": "still-broken", "status": "fail"},
        ]
    }
    results = [
        EvalRunResult(name="regressed", passed=False, failure_reasons=["boom"]),
        EvalRunResult(name="fixed", passed=True),
        EvalRunResult(name="stable", passed=True),
        EvalRunResult(name="still-broken", passed=False, failure_reasons=["boom"]),
        EvalRunResult(name="brand-new", passed=True),
    ]

    report = build_report(results, previous)

    verdicts = {entry["name"]: entry["comparison"]["verdict"] for entry in report["scenarios"]}
    assert verdicts == {
        "regressed": "regression",
        "fixed": "fixed",
        "stable": "unchanged",
        "still-broken": "still_failing",
        "brand-new": "new",
    }
    assert report["summary"] == {
        "total": 5,
        "passed": 3,
        "failed": 2,
        "regressions": 1,
        "fixed": 1,
        # The fabricated baseline carries no cost fields -> no cost buckets.
        "cost_increased": 0,
        "cost_decreased": 0,
        "cost_unchanged": 0,
        # Hand-built results carry the default tier -> everything lands in medium.
        "difficulty": {
            "short": {"total": 0, "passed": 0, "failed": 0, "regressions": 0},
            "medium": {"total": 5, "passed": 3, "failed": 2, "regressions": 1},
            "long": {"total": 0, "passed": 0, "failed": 0, "regressions": 0},
        },
    }


def test_report_roundtrip_and_missing_baseline(tmp_path):
    report = build_report([EvalRunResult(name="s", passed=True)], previous=None)

    path = write_report(tmp_path, report)

    assert path == tmp_path / REPORT_RELATIVE
    assert load_previous_report(tmp_path) == report
    assert load_previous_report(tmp_path / "does-not-exist") is None


def test_corrupt_previous_report_raises_instead_of_silently_resetting(tmp_path):
    path = tmp_path / REPORT_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ corrupted", encoding="utf-8")

    with pytest.raises(PreviousReportError) as excinfo:
        load_previous_report(tmp_path)

    assert "unreadable previous report" in str(excinfo.value)


def test_previous_statuses_normalizes_unknown_entries():
    report = {
        "scenarios": [
            {"name": "ok", "status": "pass"},
            {"name": "bad", "status": "fail"},
            {"name": "weird", "status": "unexpected-value"},
            {"no-name-here": True},
        ]
    }
    assert previous_statuses(report) == {"ok": "pass", "bad": "fail", "weird": "fail"}


def test_format_report_lines_failures_and_summary():
    report = build_report(
        [
            EvalRunResult(name="good", passed=True),
            EvalRunResult(name="bad", passed=False, failure_reasons=["missing x"]),
        ],
        previous=None,
    )

    lines = format_report(report)

    assert lines[0].startswith("PASS\tgood\t[new]")
    assert lines[1].startswith("FAIL\tbad\t[new]\tmissing x")
    assert lines[2] == (
        "total=2 passed=1 failed=1 regressions=0 fixed=0 "
        "cost_increased=0 cost_decreased=0 cost_unchanged=0"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_eval_list_lists_builtin_scenarios():
    result = runner.invoke(cli.app, ["eval", "list"])

    assert result.exit_code == 0
    assert "read-task-details" in result.output
    assert "denied-privileged-call" in result.output
    assert "multi-turn-search-converge" in result.output
    assert "memory-recall-target-first" in result.output


def test_eval_run_writes_report_and_exits_zero(tmp_path):
    result = runner.invoke(cli.app, ["eval", "run", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert "PASS\tread-task-details" in result.output
    assert "total=5 passed=5 failed=0" in result.output
    report_path = tmp_path / REPORT_RELATIVE
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["total"] == 5
    assert all(entry["comparison"]["verdict"] == "new" for entry in report["scenarios"])


def test_eval_run_detects_regression_then_fix_across_runs(tmp_path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    data = _simple_scenario_data(name="wired")
    path = _write_scenario(scenario_dir, data)

    first = runner.invoke(
        cli.app, ["eval", "run", "--file", str(scenario_dir), "--cwd", str(tmp_path)]
    )
    assert first.exit_code == 0
    assert "[new]" in first.output

    # Break the wiring: the expected tool is never called -> regression.
    data["assertions"]["expected_tool_sequence"] = ["list_tasks", "extra_step"]
    path.write_text(json.dumps(data), encoding="utf-8")
    second = runner.invoke(
        cli.app, ["eval", "run", "--file", str(scenario_dir), "--cwd", str(tmp_path)]
    )
    assert second.exit_code == 1
    assert "[regression]" in second.output
    assert "tool sequence mismatch" in second.output

    # Restore the wiring -> the same scenario flips to fixed.
    data["assertions"]["expected_tool_sequence"] = ["list_tasks"]
    path.write_text(json.dumps(data), encoding="utf-8")
    third = runner.invoke(
        cli.app, ["eval", "run", "--file", str(scenario_dir), "--cwd", str(tmp_path)]
    )
    assert third.exit_code == 0
    assert "[fixed]" in third.output


def test_eval_run_with_bad_schema_file_exits_cleanly(tmp_path):
    bad = _write_scenario(tmp_path, {"name": "broken"})  # no prompt, no turns

    result = runner.invoke(cli.app, ["eval", "run", "--file", str(bad), "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert str(bad) in result.output
    assert "prompt" in result.output


def test_eval_run_with_corrupt_previous_report_fails_loudly(tmp_path):
    report_dir = tmp_path / REPORT_RELATIVE.parent
    report_dir.mkdir(parents=True)
    (tmp_path / REPORT_RELATIVE).write_text("not a report", encoding="utf-8")

    result = runner.invoke(cli.app, ["eval", "run", "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert "unreadable previous report" in result.output
