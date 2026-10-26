"""Tests for the three trajectory assertions added in eval-harness-p1.

Covers, for ``required_tools`` / ``max_tool_calls`` / ``max_total_tool_calls``:
- strict schema validation in the loader (wrong shapes, empty maps, zero or
  boolean counts, empty tool names) failing with ``EvalSchemaError`` that names
  the offending file and key;
- positive runs through the real react Agent (subset semantics regardless of
  call order, caps met exactly);
- negative runs with human-readable failure reasons;
- the built-in ``04_repeat_call_loop_guard`` scenario, which pins the loop
  guard's interception point (the third identical call) while converging, and
  the abort path one repeat past it.

Kept in its own file so it does not collide with the other p1 slices.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexuscli.eval import (
    EvalSchemaError,
    builtin_eval_dir,
    load_eval_scenario,
    run_scenario,
)


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _two_tool_scenario_data(**overrides) -> dict:
    """Two stub tools called in the *reverse* of their declaration order.

    The reversed order is what separates ``required_tools`` (subset, any
    order) from ``expected_tool_sequence`` (exact order) in the tests below.
    """
    data = {
        "name": "assertions-p1",
        "prompt": "do the thing",
        "tools": [
            {"name": "list_tasks", "description": "List tasks.", "result": "t1"},
            {"name": "get_task_detail", "description": "Show one task.", "result": "detail"},
        ],
        "scripted_turns": [
            {"tool_calls": [{"name": "get_task_detail", "arguments": {"task": "t1"}}]},
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
            {"text": "done"},
        ],
        "assertions": {},
    }
    data.update(overrides)
    return data


def _repeat_call_turns(times: int) -> list[dict]:
    """``times`` identical ``poll_job`` calls (same arguments -> same fingerprint)."""
    return [{"tool_calls": [{"name": "poll_job", "arguments": {"job": "deploy-7"}}]}] * times


def _run(tmp_path: Path, data: dict):
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))
    return asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))


# ---------------------------------------------------------------------------
# Loader — schema
# ---------------------------------------------------------------------------


def test_loader_accepts_all_three_new_assertions(tmp_path):
    data = _two_tool_scenario_data(
        assertions={
            "required_tools": ["list_tasks", "get_task_detail"],
            "max_tool_calls": {"list_tasks": 2, "get_task_detail": 1},
            "max_total_tool_calls": 3,
        }
    )

    assertions = load_eval_scenario(_write_scenario(tmp_path, data)).assertions

    assert assertions.required_tools == ["list_tasks", "get_task_detail"]
    assert assertions.max_tool_calls == {"list_tasks": 2, "get_task_detail": 1}
    assert assertions.max_total_tool_calls == 3
    # The pre-existing four stay untouched (and absent here).
    assert assertions.expected_tool_sequence is None
    assert assertions.max_turns is None


def test_loader_accepts_required_tools_as_a_bare_string(tmp_path):
    """Same string-or-list leniency as ``forbidden_tools``."""
    data = _two_tool_scenario_data(assertions={"required_tools": "list_tasks"})

    assertions = load_eval_scenario(_write_scenario(tmp_path, data)).assertions

    assert assertions.required_tools == ["list_tasks"]


@pytest.mark.parametrize("bad", [42, [1, 2], {"list_tasks": True}, ["ok", None]])
def test_loader_rejects_required_tools_that_are_not_strings(tmp_path, bad):
    data = _two_tool_scenario_data(assertions={"required_tools": bad})
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "required_tools" in message


@pytest.mark.parametrize("bad", [["list_tasks", 2], "list_tasks=2", 3, {}])
def test_loader_rejects_max_tool_calls_that_is_not_a_non_empty_object(tmp_path, bad):
    data = _two_tool_scenario_data(assertions={"max_tool_calls": bad})
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "max_tool_calls" in message
    assert "non-empty object" in message


@pytest.mark.parametrize("bad_limit", [0, -1, "3", 1.5, True])
def test_loader_rejects_max_tool_calls_values_that_are_not_positive_integers(tmp_path, bad_limit):
    data = _two_tool_scenario_data(assertions={"max_tool_calls": {"list_tasks": bad_limit}})
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "max_tool_calls['list_tasks']" in message
    assert "positive integer" in message


def test_loader_rejects_max_tool_calls_with_an_empty_tool_name(tmp_path):
    data = _two_tool_scenario_data(assertions={"max_tool_calls": {"   ": 2}})
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "max_tool_calls keys must be non-empty tool names" in message


@pytest.mark.parametrize("bad", [0, -3, "5", 2.5, True, [5]])
def test_loader_rejects_max_total_tool_calls_that_is_not_a_positive_integer(tmp_path, bad):
    data = _two_tool_scenario_data(assertions={"max_total_tool_calls": bad})
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "max_total_tool_calls must be a positive integer" in message


def test_loader_still_rejects_unknown_assertion_keys_and_lists_the_new_ones(tmp_path):
    data = _two_tool_scenario_data(assertions={"max_tool_call": {"list_tasks": 1}})  # typo
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "max_tool_call" in message
    for supported in ("required_tools", "max_tool_calls", "max_total_tool_calls"):
        assert supported in message


# ---------------------------------------------------------------------------
# Runner — required_tools (subset, any order)
# ---------------------------------------------------------------------------


def test_required_tools_passes_in_any_order_where_exact_sequence_would_fail(tmp_path):
    """Calls happen as [get_task_detail, list_tasks]; the subset assertion lists
    them the other way round and still passes — the exact-sequence assertion
    with the same list does not. That gap is the whole point of the new key."""
    required = ["list_tasks", "get_task_detail"]

    subset = _run(tmp_path, _two_tool_scenario_data(assertions={"required_tools": required}))
    exact = _run(tmp_path, _two_tool_scenario_data(assertions={"expected_tool_sequence": required}))

    assert subset.tool_sequence == ["get_task_detail", "list_tasks"]
    assert subset.passed, subset.failure_reasons
    assert not exact.passed
    assert any("tool sequence mismatch" in reason for reason in exact.failure_reasons)


def test_required_tools_passes_when_more_tools_than_listed_were_called(tmp_path):
    result = _run(tmp_path, _two_tool_scenario_data(assertions={"required_tools": ["list_tasks"]}))

    assert result.passed, result.failure_reasons


def test_required_tools_fails_naming_each_tool_never_called(tmp_path):
    data = _two_tool_scenario_data(
        assertions={"required_tools": ["list_tasks", "audit_log", "send_report"]}
    )

    result = _run(tmp_path, data)

    assert not result.passed
    assert "required tool was never called: audit_log" in result.failure_reasons
    assert "required tool was never called: send_report" in result.failure_reasons
    assert not any("list_tasks" in reason for reason in result.failure_reasons)


# ---------------------------------------------------------------------------
# Runner — max_tool_calls / max_total_tool_calls
# ---------------------------------------------------------------------------


def test_call_caps_pass_when_met_exactly(tmp_path):
    data = _two_tool_scenario_data(
        assertions={
            "max_tool_calls": {"list_tasks": 1, "get_task_detail": 1},
            "max_total_tool_calls": 2,
        }
    )

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons


def test_max_tool_calls_ignores_capped_tools_that_were_never_called(tmp_path):
    """A cap is an upper bound, not a requirement: 0 calls is within any cap."""
    data = _two_tool_scenario_data(assertions={"max_tool_calls": {"never_called": 1}})

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons


def test_max_tool_calls_fails_with_actual_count_and_limit(tmp_path):
    data = _two_tool_scenario_data(
        scripted_turns=[
            {"tool_calls": [{"name": "list_tasks", "arguments": {"page": 1}}]},
            {"tool_calls": [{"name": "list_tasks", "arguments": {"page": 2}}]},
            {"text": "done"},
        ],
        assertions={"max_tool_calls": {"list_tasks": 1}},
    )

    result = _run(tmp_path, data)

    assert not result.passed
    assert "tool list_tasks was called 2 times, limit is 1" in result.failure_reasons


def test_max_total_tool_calls_fails_with_total_and_limit(tmp_path):
    result = _run(tmp_path, _two_tool_scenario_data(assertions={"max_total_tool_calls": 1}))

    assert not result.passed
    assert "made 2 tool calls in total, limit is 1" in result.failure_reasons


def test_call_caps_report_independently_alongside_existing_assertions(tmp_path):
    """Each failing assertion contributes its own reason; none shadows another."""
    data = _two_tool_scenario_data(
        assertions={
            "required_tools": ["audit_log"],
            "max_tool_calls": {"get_task_detail": 1, "list_tasks": 1},
            "max_total_tool_calls": 1,
            "forbidden_tools": ["list_tasks"],
            "max_turns": 1,
        }
    )

    result = _run(tmp_path, data)

    assert not result.passed
    assert result.failure_reasons == [
        "forbidden tool was called: list_tasks",
        "required tool was never called: audit_log",
        "made 2 tool calls in total, limit is 1",
        "took 3 turns, limit is 1",
    ]


# ---------------------------------------------------------------------------
# Built-in scenario 04 — loop guard interplay
# ---------------------------------------------------------------------------


def test_builtin_repeat_scenario_converges_at_the_loop_guard_interception_point(tmp_path):
    """Three identical ``poll_job`` calls: with the default
    ``agent.loop_detection_threshold`` (3) the third is intercepted (nudged,
    not executed) and the script converges on turn 4. All three new assertions
    hold at their exact caps. A guard that intercepted earlier would abort the
    run (termination failure); one that let more calls through would break the
    caps — so the scenario pins the interception boundary from both sides."""
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None, "repository ships examples/evals"
    matches = sorted(eval_dir.glob("*repeat_call_loop_guard*.json"))
    assert matches, "builtin scenario repeat-call-loop-guard not found"
    scenario = load_eval_scenario(matches[0])
    assert scenario.name == "repeat-call-loop-guard"
    assert scenario.assertions.required_tools == ["poll_job"]
    assert scenario.assertions.max_tool_calls == {"poll_job": 3}
    assert scenario.assertions.max_total_tool_calls == 3

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed, result.failure_reasons
    assert result.tool_sequence == ["poll_job", "poll_job", "poll_job"]
    assert result.turns == 4
    assert "loop guard" in result.final_text


def test_repeating_one_call_past_the_loop_guard_fails_with_count_and_termination(tmp_path):
    """Four identical calls: the guard nudges on the third and aborts on the
    fourth. The intercepted calls still surface as ``tool_call`` events, so the
    per-tool cap of 3 reports 4 — alongside the runner's own early-termination
    reason. Both are readable on their own."""
    data = {
        "name": "repeat-past-guard",
        "prompt": "poll until done",
        "tools": [{"name": "poll_job", "description": "Poll a job.", "result": "running"}],
        "scripted_turns": [*_repeat_call_turns(4), {"text": "never reached"}],
        "assertions": {"max_tool_calls": {"poll_job": 3}, "max_total_tool_calls": 3},
    }

    result = _run(tmp_path, data)

    assert not result.passed
    assert result.tool_sequence == ["poll_job"] * 4
    assert "tool poll_job was called 4 times, limit is 3" in result.failure_reasons
    assert "made 4 tool calls in total, limit is 3" in result.failure_reasons
    assert any("terminated early" in reason for reason in result.failure_reasons)
