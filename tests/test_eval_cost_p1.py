"""Tests for the P1 cost-determinism metrics in ``nexuscli.eval.reporter``.

An offline scripted run has no tokens, but its turn count and tool-call count
are fully deterministic — they are the harness's "cost" (the scripted mapping
of HAL cost control). Covers:

- every scenario entry carries ``turns`` and ``total_tool_calls`` plus a
  ``cost`` diff object against the previous report;
- a same-scenario increase in turns or tool calls emits a human-readable
  warning (``"4 turns → 7 turns"`` style) and nothing else changes: the
  PASS/FAIL status and the comparison verdict are untouched;
- decreases and flat costs stay silent;
- the summary counts increased/decreased/unchanged scenarios separately
  (scenarios without a comparable baseline land in no bucket);
- ``format_report`` and the real ``nexuscli eval run`` surface the warning and
  the counts, so the regression is visible, not just persisted.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.eval import EvalRunResult, build_report, format_report
from nexuscli.eval.reporter import REPORT_RELATIVE, cost_comparison, previous_costs

runner = CliRunner()


def _baseline(name: str, *, turns: int, calls: int, status: str = "pass") -> dict:
    """One previous-report scenario entry carrying both cost metrics."""
    return {"name": name, "status": status, "turns": turns, "total_tool_calls": calls}


def _previous(*entries: dict) -> dict:
    return {"scenarios": list(entries)}


def _result(name: str, *, turns: int, calls: int, passed: bool = True) -> EvalRunResult:
    """A run result with ``calls`` tool invocations (names are irrelevant to cost)."""
    return EvalRunResult(
        name=name,
        passed=passed,
        failure_reasons=[] if passed else ["boom"],
        tool_sequence=[f"tool_{index}" for index in range(calls)],
        turns=turns,
    )


# ---------------------------------------------------------------------------
# Cost fields on every entry
# ---------------------------------------------------------------------------


def test_entry_records_turns_and_total_tool_calls():
    result = EvalRunResult(name="s", passed=True, tool_sequence=["a", "b"], turns=3)

    report = build_report([result], previous=None)

    entry = report["scenarios"][0]
    assert entry["turns"] == 3
    assert entry["total_tool_calls"] == 2
    assert entry["total_tool_calls"] == len(entry["tool_sequence"])
    # No previous report -> nothing to compare against, and no warning.
    assert entry["cost"] == {"change": None, "warnings": []}


def test_summary_always_carries_the_three_cost_counters():
    report = build_report([_result("s", turns=1, calls=1)], previous=None)

    summary = report["summary"]
    assert summary["cost_increased"] == 0
    assert summary["cost_decreased"] == 0
    assert summary["cost_unchanged"] == 0


# ---------------------------------------------------------------------------
# Increase -> visible warning text
# ---------------------------------------------------------------------------


def test_turn_increase_warns_with_before_after_text():
    previous = _previous(_baseline("s", turns=4, calls=2))

    report = build_report([_result("s", turns=7, calls=2)], previous)

    cost = report["scenarios"][0]["cost"]
    assert cost["change"] == "increased"
    assert cost["warnings"] == ["4 turns → 7 turns"]


def test_tool_call_increase_warns_with_before_after_text():
    previous = _previous(_baseline("s", turns=2, calls=1))

    report = build_report([_result("s", turns=2, calls=2)], previous)

    cost = report["scenarios"][0]["cost"]
    assert cost["change"] == "increased"
    assert cost["warnings"] == ["1 tool call → 2 tool calls"]


def test_both_metrics_increasing_emit_one_warning_each():
    previous = _previous(_baseline("s", turns=2, calls=1))

    report = build_report([_result("s", turns=4, calls=3)], previous)

    assert report["scenarios"][0]["cost"]["warnings"] == [
        "2 turns → 4 turns",
        "1 tool call → 3 tool calls",
    ]


def test_mixed_direction_counts_as_increase_and_warns_only_for_the_rising_metric():
    """Turns up, tool calls down: any rising metric is a cost increase."""
    previous = _previous(_baseline("s", turns=4, calls=3))

    report = build_report([_result("s", turns=6, calls=1)], previous)

    cost = report["scenarios"][0]["cost"]
    assert cost["change"] == "increased"
    assert cost["warnings"] == ["4 turns → 6 turns"]


# ---------------------------------------------------------------------------
# Decrease / flat -> silent
# ---------------------------------------------------------------------------


def test_decrease_and_flat_cost_stay_silent():
    previous = _previous(
        _baseline("cheaper", turns=7, calls=3),
        _baseline("flat", turns=2, calls=1),
    )
    results = [_result("cheaper", turns=5, calls=1), _result("flat", turns=2, calls=1)]

    report = build_report(results, previous)

    cheaper, flat = report["scenarios"]
    assert cheaper["cost"] == {"change": "decreased", "warnings": []}
    assert flat["cost"] == {"change": "unchanged", "warnings": []}


# ---------------------------------------------------------------------------
# Cost never changes the verdict
# ---------------------------------------------------------------------------


def test_cost_increase_does_not_change_pass_status_or_comparison_verdict():
    previous = _previous(_baseline("s", turns=2, calls=1))

    report = build_report([_result("s", turns=5, calls=2)], previous)

    entry = report["scenarios"][0]
    assert entry["status"] == "pass"
    assert entry["failure_reasons"] == []
    assert entry["comparison"] == {"previous": "pass", "verdict": "unchanged"}
    assert entry["cost"]["change"] == "increased"
    summary = report["summary"]
    assert summary["passed"] == 1
    assert summary["failed"] == 0
    assert summary["regressions"] == 0
    assert summary["cost_increased"] == 1


def test_cost_change_is_independent_of_a_real_regression():
    """A scenario can regress (pass -> fail) while getting cheaper: the two
    axes are reported side by side, neither rewrites the other."""
    previous = _previous(_baseline("s", turns=5, calls=2))

    report = build_report([_result("s", turns=2, calls=1, passed=False)], previous)

    entry = report["scenarios"][0]
    assert entry["status"] == "fail"
    assert entry["comparison"]["verdict"] == "regression"
    assert entry["cost"] == {"change": "decreased", "warnings": []}
    assert report["summary"]["regressions"] == 1
    assert report["summary"]["cost_decreased"] == 1


# ---------------------------------------------------------------------------
# Summary buckets
# ---------------------------------------------------------------------------


def test_summary_counts_cost_changes_separately_and_skips_unbaselined():
    previous = _previous(
        _baseline("up", turns=2, calls=1),
        _baseline("down", turns=6, calls=4),
        _baseline("flat", turns=3, calls=2),
        {"name": "old-format", "status": "pass"},  # written before cost fields existed
    )
    results = [
        _result("up", turns=2, calls=2),
        _result("down", turns=3, calls=1),
        _result("flat", turns=3, calls=2),
        _result("old-format", turns=1, calls=1),
        _result("never-seen", turns=1, calls=1),
    ]

    report = build_report(results, previous)

    changes = {entry["name"]: entry["cost"]["change"] for entry in report["scenarios"]}
    assert changes == {
        "up": "increased",
        "down": "decreased",
        "flat": "unchanged",
        "old-format": None,
        "never-seen": None,
    }
    summary = report["summary"]
    assert summary["total"] == 5
    assert summary["cost_increased"] == 1
    assert summary["cost_decreased"] == 1
    assert summary["cost_unchanged"] == 1


# ---------------------------------------------------------------------------
# Baseline extraction is tolerant of partial / malformed previous entries
# ---------------------------------------------------------------------------


def test_previous_costs_keeps_only_integer_metrics_that_are_present():
    report = _previous(
        _baseline("full", turns=4, calls=2),
        {"name": "turns-only", "status": "pass", "turns": 3},
        {"name": "bogus", "status": "pass", "turns": "many", "total_tool_calls": True},
        {"no-name-here": True},
    )

    assert previous_costs(report) == {
        "full": {"turns": 4, "total_tool_calls": 2},
        "turns-only": {"turns": 3},
    }
    assert previous_costs(None) == {}


def test_partial_baseline_only_compares_the_metrics_it_has():
    previous = _previous({"name": "s", "status": "pass", "turns": 2})

    # Tool calls went 'up' from nothing, but there is no baseline for them.
    report = build_report([_result("s", turns=2, calls=3)], previous)

    assert report["scenarios"][0]["cost"] == {"change": "unchanged", "warnings": []}


def test_cost_comparison_without_baseline_is_neutral():
    assert cost_comparison(None, turns=9, total_tool_calls=9) == {"change": None, "warnings": []}
    assert cost_comparison({}, turns=9, total_tool_calls=9) == {"change": None, "warnings": []}


def test_cost_warning_uses_singular_for_exactly_one():
    comparison = cost_comparison({"turns": 1, "total_tool_calls": 0}, turns=2, total_tool_calls=1)

    assert comparison["warnings"] == ["1 turn → 2 turns", "0 tool calls → 1 tool call"]


# ---------------------------------------------------------------------------
# Visibility: format_report and the real CLI
# ---------------------------------------------------------------------------


def test_format_report_shows_cost_line_under_the_scenario_and_counts_in_summary():
    previous = _previous(_baseline("s", turns=4, calls=1))

    lines = format_report(build_report([_result("s", turns=7, calls=2)], previous))

    assert lines[0] == "PASS\ts\t[unchanged]"
    assert lines[1] == "COST\ts\t4 turns → 7 turns; 1 tool call → 2 tool calls"
    assert lines[2].endswith("cost_increased=1 cost_decreased=0 cost_unchanged=0")


def test_format_report_has_no_cost_line_when_cost_is_flat_or_lower():
    previous = _previous(
        _baseline("flat", turns=2, calls=1), _baseline("cheaper", turns=5, calls=3)
    )
    results = [_result("flat", turns=2, calls=1), _result("cheaper", turns=3, calls=1)]

    lines = format_report(build_report(results, previous))

    assert lines == [
        "PASS\tflat\t[unchanged]",
        "PASS\tcheaper\t[unchanged]",
        "total=2 passed=2 failed=0 regressions=0 fixed=0 "
        "cost_increased=0 cost_decreased=1 cost_unchanged=1",
        # Per-tier line (hand-built results carry the default tier, medium).
        "difficulty: short total=0 passed=0 failed=0 regressions=0 | "
        "medium total=2 passed=2 failed=0 regressions=0 | "
        "long total=0 passed=0 failed=0 regressions=0",
    ]


def test_eval_run_warns_when_cost_grows_across_runs_but_still_exits_zero(tmp_path: Path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    data = {
        "name": "costly",
        "prompt": "do the thing",
        "tools": [
            {"name": "list_tasks", "description": "List tasks.", "result": "t1"},
            {"name": "get_task", "description": "Fetch one task.", "result": "t1: details"},
        ],
        "scripted_turns": [
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
            {"text": "task t1 done"},
        ],
        "assertions": {"expected_tool_sequence": ["list_tasks"], "max_turns": 3},
    }
    path = scenario_dir / "costly.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    argv = ["eval", "run", "--file", str(scenario_dir), "--cwd", str(tmp_path)]

    first = runner.invoke(cli.app, argv)
    assert first.exit_code == 0, first.output
    assert "COST\t" not in first.output
    baseline = json.loads((tmp_path / REPORT_RELATIVE).read_text(encoding="utf-8"))
    assert baseline["scenarios"][0]["turns"] == 2
    assert baseline["scenarios"][0]["total_tool_calls"] == 1

    # Same scenario now needs an extra tool round-trip: still passes, costs more.
    data["scripted_turns"].insert(1, {"tool_calls": [{"name": "get_task", "arguments": {}}]})
    data["assertions"]["expected_tool_sequence"] = ["list_tasks", "get_task"]
    path.write_text(json.dumps(data), encoding="utf-8")

    second = runner.invoke(cli.app, argv)
    assert second.exit_code == 0, second.output
    assert "PASS\tcostly\t[unchanged]" in second.output
    assert "COST\tcostly\t2 turns → 3 turns; 1 tool call → 2 tool calls" in second.output
    assert "cost_increased=1 cost_decreased=0 cost_unchanged=0" in second.output
    report = json.loads((tmp_path / REPORT_RELATIVE).read_text(encoding="utf-8"))
    entry = report["scenarios"][0]
    assert entry["status"] == "pass"
    assert entry["total_tool_calls"] == 2
    assert entry["cost"]["change"] == "increased"
