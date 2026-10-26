"""Tests for the scenario difficulty tiers added in eval-harness-p1.

Covers:
- loader: ``difficulty`` accepts exactly ``short`` / ``medium`` / ``long``,
  defaults to ``medium`` when absent, and rejects everything else (wrong case,
  empty, ``null``, non-strings) with an ``EvalSchemaError`` that names the
  file, the key and the three tiers;
- runner: the run result carries the scenario's tier, and the tier never
  changes a verdict;
- reporter: every entry carries its tier and the summary's ``difficulty``
  block aggregates total/passed/failed/regressions per tier — every tier
  always present, consistent with the top-level counts, ``regressions``
  counting only ``pass -> fail``;
- the regression-localisation view: ``format_report`` and the real
  ``nexuscli eval run`` print a per-tier line that shows *which* tier
  regressed.

Kept in its own file so it does not collide with the other p1 slices.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
from nexuscli.eval import (
    DEFAULT_DIFFICULTY,
    DIFFICULTY_LEVELS,
    EvalRunResult,
    EvalSchemaError,
    build_report,
    builtin_eval_dir,
    format_report,
    load_eval_scenario,
    load_eval_scenarios,
    load_previous_report,
    run_scenario,
    write_report,
)
from nexuscli.eval.reporter import REPORT_RELATIVE, difficulty_summary, format_difficulty_line

runner = CliRunner()

_ABSENT = object()
_ZERO_TIER = {"total": 0, "passed": 0, "failed": 0, "regressions": 0}


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _scenario_data(name: str = "tiered", *, difficulty=_ABSENT, **overrides) -> dict:
    """A minimal passing one-tool scenario; ``difficulty`` only when given
    (so ``None`` can be written as a JSON ``null`` on purpose)."""
    data = {
        "name": name,
        "prompt": "do the thing",
        "tools": [{"name": "ping", "description": "Ping.", "result": "pong"}],
        "scripted_turns": [
            {"tool_calls": [{"name": "ping", "arguments": {}}]},
            {"text": "pong received"},
        ],
        "assertions": {"expected_tool_sequence": ["ping"], "max_turns": 2},
    }
    if difficulty is not _ABSENT:
        data["difficulty"] = difficulty
    data.update(overrides)
    return data


def _result(name: str, *, difficulty: str, passed: bool = True) -> EvalRunResult:
    return EvalRunResult(
        name=name,
        passed=passed,
        failure_reasons=[] if passed else ["boom"],
        difficulty=difficulty,
    )


def _previous(*entries: dict) -> dict:
    return {"scenarios": list(entries)}


def _baseline(name: str, status: str = "pass") -> dict:
    return {"name": name, "status": status}


# ---------------------------------------------------------------------------
# Loader — schema
# ---------------------------------------------------------------------------


def test_difficulty_vocabulary_is_exactly_the_three_tiers_in_report_order():
    assert DIFFICULTY_LEVELS == ("short", "medium", "long")
    assert DEFAULT_DIFFICULTY == "medium"
    assert DEFAULT_DIFFICULTY in DIFFICULTY_LEVELS


@pytest.mark.parametrize("tier", DIFFICULTY_LEVELS)
def test_loader_accepts_each_tier(tmp_path, tier):
    scenario = load_eval_scenario(_write_scenario(tmp_path, _scenario_data(difficulty=tier)))

    assert scenario.difficulty == tier


def test_loader_defaults_difficulty_to_medium_when_absent(tmp_path):
    data = _scenario_data()
    assert "difficulty" not in data

    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    assert scenario.difficulty == "medium"
    assert scenario.difficulty == DEFAULT_DIFFICULTY


@pytest.mark.parametrize(
    "bad",
    [
        "hard",  # not a tier
        "Short",  # case-sensitive
        "LONG",
        " medium ",  # no trimming
        "",  # present but empty is not "absent"
        None,  # explicit JSON null is not "absent" either
        42,
        True,
        ["short"],  # unhashable: must be a schema error, not a TypeError
        {"tier": "short"},
    ],
)
def test_loader_rejects_difficulty_outside_the_three_tiers(tmp_path, bad):
    path = _write_scenario(tmp_path, _scenario_data(difficulty=bad))

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "'difficulty'" in message
    assert "must be one of" in message
    for tier in DIFFICULTY_LEVELS:
        assert tier in message
    assert f"got {bad!r}" in message


def test_difficulty_error_message_is_human_readable_and_names_the_file(tmp_path):
    path = _write_scenario(tmp_path, _scenario_data(difficulty="hard"), "typo.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    assert str(excinfo.value) == (
        f"{path}: 'difficulty' must be one of ['short', 'medium', 'long'], got 'hard'"
    )


def test_directory_load_names_the_one_file_with_the_bad_tier(tmp_path):
    _write_scenario(tmp_path, _scenario_data("fine", difficulty="short"), "a_fine.json")
    bad = _write_scenario(tmp_path, _scenario_data("broken", difficulty="epic"), "b_broken.json")

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenarios(tmp_path)

    message = str(excinfo.value)
    assert str(bad) in message
    assert "a_fine.json" not in message


def test_builtin_scenarios_all_load_with_a_valid_tier():
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None, "repository ships examples/evals"

    scenarios = load_eval_scenarios(eval_dir)

    assert scenarios
    assert all(scenario.difficulty in DIFFICULTY_LEVELS for scenario in scenarios)


# ---------------------------------------------------------------------------
# Runner — the result carries the tier, the tier never judges
# ---------------------------------------------------------------------------


def test_run_result_carries_the_scenario_tier(tmp_path):
    scenario = load_eval_scenario(_write_scenario(tmp_path, _scenario_data(difficulty="long")))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.difficulty == "long"
    assert result.passed, result.failure_reasons


def test_tier_does_not_change_the_verdict_or_the_reasons(tmp_path):
    """The same broken scenario fails identically under every tier."""
    broken = {"expected_tool_sequence": ["ping", "extra_step"], "max_turns": 2}
    outcomes = {}
    for tier in DIFFICULTY_LEVELS:
        data = _scenario_data(f"broken-{tier}", difficulty=tier, assertions=broken)
        scenario = load_eval_scenario(_write_scenario(tmp_path, data, f"{tier}.json"))
        result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))
        outcomes[tier] = (result.passed, result.failure_reasons)

    assert all(not passed for passed, _ in outcomes.values())
    reasons = {tuple(failure_reasons) for _, failure_reasons in outcomes.values()}
    assert len(reasons) == 1, reasons
    assert any("tool sequence mismatch" in reason for reason in next(iter(reasons)))


def test_hand_built_run_result_defaults_to_the_default_tier():
    assert EvalRunResult(name="x", passed=True).difficulty == DEFAULT_DIFFICULTY


# ---------------------------------------------------------------------------
# Reporter — per-tier aggregation
# ---------------------------------------------------------------------------


def test_every_entry_carries_its_tier():
    results = [_result("s", difficulty="short"), _result("l", difficulty="long")]

    report = build_report(results, previous=None)

    assert [entry["difficulty"] for entry in report["scenarios"]] == ["short", "long"]


def test_summary_difficulty_block_lists_all_three_tiers_in_order_even_when_empty():
    report = build_report([_result("only-short", difficulty="short")], previous=None)

    tiers = report["summary"]["difficulty"]
    assert list(tiers) == ["short", "medium", "long"]
    assert tiers["short"] == {"total": 1, "passed": 1, "failed": 0, "regressions": 0}
    assert tiers["medium"] == _ZERO_TIER
    assert tiers["long"] == _ZERO_TIER


def test_summary_aggregates_pass_and_fail_per_tier_consistently_with_the_totals():
    results = [
        _result("s1", difficulty="short"),
        _result("s2", difficulty="short"),
        _result("s3", difficulty="short", passed=False),
        _result("m1", difficulty="medium"),
        _result("l1", difficulty="long", passed=False),
        _result("l2", difficulty="long", passed=False),
    ]

    summary = build_report(results, previous=None)["summary"]

    tiers = summary["difficulty"]
    assert tiers["short"] == {"total": 3, "passed": 2, "failed": 1, "regressions": 0}
    assert tiers["medium"] == {"total": 1, "passed": 1, "failed": 0, "regressions": 0}
    assert tiers["long"] == {"total": 2, "passed": 0, "failed": 2, "regressions": 0}
    # The tier buckets partition the run: they add up to the top-level counts.
    for key in ("total", "passed", "failed"):
        assert sum(tier[key] for tier in tiers.values()) == summary[key]


def test_summary_counts_only_pass_to_fail_as_a_regression_per_tier():
    """``failed`` and ``regressions`` are different numbers: a still-failing
    or brand-new failure raises ``failed`` but not ``regressions``."""
    previous = _previous(
        _baseline("short-regress"),
        _baseline("medium-ok"),
        _baseline("long-regress-a"),
        _baseline("long-regress-b"),
        _baseline("long-still", status="fail"),
    )
    results = [
        _result("short-regress", difficulty="short", passed=False),
        _result("medium-ok", difficulty="medium"),
        _result("long-regress-a", difficulty="long", passed=False),
        _result("long-regress-b", difficulty="long", passed=False),
        _result("long-still", difficulty="long", passed=False),  # fail -> fail
        _result("long-new", difficulty="long", passed=False),  # no baseline
    ]

    summary = build_report(results, previous)["summary"]

    tiers = summary["difficulty"]
    assert tiers["short"] == {"total": 1, "passed": 0, "failed": 1, "regressions": 1}
    assert tiers["medium"] == {"total": 1, "passed": 1, "failed": 0, "regressions": 0}
    assert tiers["long"] == {"total": 4, "passed": 0, "failed": 4, "regressions": 2}
    assert summary["regressions"] == 3
    assert sum(tier["regressions"] for tier in tiers.values()) == summary["regressions"]


def test_no_previous_report_means_zero_regressions_in_every_tier():
    results = [_result(tier, difficulty=tier, passed=False) for tier in DIFFICULTY_LEVELS]

    tiers = build_report(results, previous=None)["summary"]["difficulty"]

    assert all(tier["failed"] == 1 and tier["regressions"] == 0 for tier in tiers.values())


def test_difficulty_summary_skips_entries_outside_the_three_tiers():
    """Only reachable with a fabricated entry (the loader never emits another
    value): it lands in no bucket rather than inventing a fourth tier."""
    good = {"name": "ok", "status": "pass", "difficulty": "short", "comparison": {"verdict": "new"}}
    odd = {"name": "odd", "status": "fail", "difficulty": "epic", "comparison": {"verdict": "new"}}
    untagged = {"name": "none", "status": "fail", "comparison": {"verdict": "new"}}

    tiers = difficulty_summary([good, odd, untagged])

    assert list(tiers) == ["short", "medium", "long"]
    assert tiers["short"] == {"total": 1, "passed": 1, "failed": 0, "regressions": 0}
    assert tiers["medium"] == _ZERO_TIER
    assert tiers["long"] == _ZERO_TIER


def test_report_round_trips_tiers_through_json(tmp_path):
    report = build_report([_result("l", difficulty="long", passed=False)], previous=None)

    write_report(tmp_path, report)
    loaded = load_previous_report(tmp_path)

    assert loaded is not None
    assert loaded["scenarios"][0]["difficulty"] == "long"
    assert loaded["summary"]["difficulty"] == report["summary"]["difficulty"]
    assert loaded["summary"]["difficulty"]["long"]["failed"] == 1


# ---------------------------------------------------------------------------
# Regression-localisation view
# ---------------------------------------------------------------------------


def test_format_report_ends_with_a_tier_line_that_shows_where_the_regressions_sit():
    previous = _previous(
        _baseline("short-a"), _baseline("medium-a"), _baseline("long-a"), _baseline("long-b")
    )
    results = [
        _result("short-a", difficulty="short"),
        _result("medium-a", difficulty="medium"),
        _result("long-a", difficulty="long", passed=False),
        _result("long-b", difficulty="long", passed=False),
    ]

    lines = format_report(build_report(results, previous))

    # Overall line is untouched and still right before the tier line.
    assert lines[-2] == (
        "total=4 passed=2 failed=2 regressions=2 fixed=0 "
        "cost_increased=0 cost_decreased=0 cost_unchanged=0"
    )
    assert lines[-1] == (
        "difficulty: short total=1 passed=1 failed=0 regressions=0 | "
        "medium total=1 passed=1 failed=0 regressions=0 | "
        "long total=2 passed=0 failed=2 regressions=2"
    )


def test_format_difficulty_line_renders_a_missing_or_partial_block_as_zeros():
    zeros = (
        "difficulty: short total=0 passed=0 failed=0 regressions=0 | "
        "medium total=0 passed=0 failed=0 regressions=0 | "
        "long total=0 passed=0 failed=0 regressions=0"
    )
    assert format_difficulty_line(None) == zeros
    assert format_difficulty_line({}) == zeros

    partial = format_difficulty_line({"long": {"total": 1, "failed": 1}})

    assert partial.endswith("| long total=1 passed=0 failed=1 regressions=0")
    assert partial.startswith("difficulty: short total=0 passed=0 failed=0 regressions=0 |")


def test_eval_run_prints_the_tier_breakdown_and_localises_a_regression(tmp_path: Path):
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    for index, tier in enumerate(DIFFICULTY_LEVELS, start=1):
        _write_scenario(
            scenario_dir, _scenario_data(f"{tier}-case", difficulty=tier), f"{index}_{tier}.json"
        )
    argv = ["eval", "run", "--file", str(scenario_dir), "--cwd", str(tmp_path)]

    first = runner.invoke(cli.app, argv)

    assert first.exit_code == 0, first.output
    assert (
        "difficulty: short total=1 passed=1 failed=0 regressions=0 | "
        "medium total=1 passed=1 failed=0 regressions=0 | "
        "long total=1 passed=1 failed=0 regressions=0"
    ) in first.output

    # Break only the long scenario: the tier line must point at ``long``.
    long_path = scenario_dir / "3_long.json"
    data = json.loads(long_path.read_text(encoding="utf-8"))
    data["assertions"]["expected_tool_sequence"] = ["ping", "extra_step"]
    long_path.write_text(json.dumps(data), encoding="utf-8")

    second = runner.invoke(cli.app, argv)

    assert second.exit_code == 1, second.output
    assert "FAIL\tlong-case\t[regression]" in second.output
    assert (
        "difficulty: short total=1 passed=1 failed=0 regressions=0 | "
        "medium total=1 passed=1 failed=0 regressions=0 | "
        "long total=1 passed=0 failed=1 regressions=1"
    ) in second.output
    report = json.loads((tmp_path / REPORT_RELATIVE).read_text(encoding="utf-8"))
    tiers = report["summary"]["difficulty"]
    assert tiers["long"]["regressions"] == 1
    assert tiers["short"]["regressions"] == 0
    assert tiers["medium"]["regressions"] == 0
    by_name = {entry["name"]: entry for entry in report["scenarios"]}
    assert by_name["long-case"]["difficulty"] == "long"
    assert by_name["long-case"]["comparison"]["verdict"] == "regression"


def test_eval_run_with_a_bad_tier_exits_one_naming_the_file_and_the_key(tmp_path: Path):
    bad = _write_scenario(tmp_path, _scenario_data(difficulty="impossible"), "bad_tier.json")

    result = runner.invoke(cli.app, ["eval", "run", "--file", str(bad), "--cwd", str(tmp_path)])

    assert result.exit_code == 1
    assert str(bad) in result.output
    assert "'difficulty'" in result.output
    assert "got 'impossible'" in result.output
