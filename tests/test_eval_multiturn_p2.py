"""Tests for the multi-round user simulation added in add-eval-scenario-types.

Covers, for ``user_turns`` (+ ``max_rounds``):
- loader: the scripted-user schema (messages, the ``{"end": true}`` marker,
  ``max_rounds``) is optional, strictly validated, and rejected with an
  ``EvalSchemaError`` naming the file and the offending key;
- runner: the conversation continues the *same* agent session (the round-2
  model calls see the whole prior history), the user script drives round after
  round until exhaustion / the end marker / the scenario round cap, and the
  assertions judge the cumulative run — the tool sequence spans all rounds,
  ``final_contains`` judges the last round's final answer and ``max_turns``
  budgets the whole conversation;
- the built-in scenarios ``05_refund_policy_multiturn.json`` (the policy one:
  the order must be looked up first, the direct refund tool must never be
  attempted in any round) and ``06_dinner_booking_clarify.json``.

Kept in its own file so it does not collide with the other eval slices.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import nexuscli.eval.runner as runner_module
from nexuscli.eval import (
    EvalSchemaError,
    ScriptedUserTurn,
    builtin_eval_dir,
    load_eval_scenario,
    run_scenario,
)


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _weather_scenario_data(**overrides) -> dict:
    """Three rounds over one tool; each round calls it with a different city.

    Different arguments keep the loop guard out of the way (no repeated
    fingerprint), and every round's segment ends with a text-only turn — the
    authoring discipline the flat ``scripted_turns`` list requires.
    """
    data = {
        "name": "weather-multiturn",
        "prompt": "What is the weather in Paris?",
        "tools": [{"name": "get_weather", "description": "Weather for one city.", "result": "ok"}],
        "scripted_turns": [
            {"tool_calls": [{"name": "get_weather", "arguments": {"city": "Paris"}}]},
            {"text": "Paris is sunny."},
            {"tool_calls": [{"name": "get_weather", "arguments": {"city": "Rome"}}]},
            {"text": "Rome is clear."},
            {"tool_calls": [{"name": "get_weather", "arguments": {"city": "Berlin"}}]},
            {"text": "Berlin is cloudy."},
        ],
        "user_turns": [
            {"message": "And in Rome?"},
            {"message": "And Berlin?"},
        ],
        "assertions": {},
    }
    data.update(overrides)
    return data


def _run(tmp_path: Path, data: dict):
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))
    return asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))


# ---------------------------------------------------------------------------
# Loader — user_turns / max_rounds schema
# ---------------------------------------------------------------------------


def test_loader_accepts_user_turns_with_end_marker_and_max_rounds(tmp_path):
    data = _weather_scenario_data(
        user_turns=[{"message": "And in Rome?"}, {"end": True}], max_rounds=2
    )

    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    assert scenario.user_turns == [
        ScriptedUserTurn(message="And in Rome?"),
        ScriptedUserTurn(end=True),
    ]
    assert scenario.max_rounds == 2


def test_loader_defaults_to_a_single_round_scenario(tmp_path):
    data = _weather_scenario_data()
    del data["user_turns"]

    scenario = load_eval_scenario(_write_scenario(tmp_path, data))

    assert scenario.user_turns == []
    assert scenario.max_rounds is None


def test_loader_rejects_a_user_turn_entry_with_an_unknown_key(tmp_path):
    data = _weather_scenario_data(user_turns=[{"message": "hi", "tone": "angry"}])
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "tone" in message
    assert "user_turns[0]" in message


@pytest.mark.parametrize(
    "entry", [{}, {"end": False}, {"message": ""}, {"message": "   "}, {"message": 7}]
)
def test_loader_rejects_a_message_entry_without_a_non_empty_message(tmp_path, entry):
    data = _weather_scenario_data(user_turns=[entry])
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "user_turns[0]" in message


def test_loader_rejects_an_end_marker_that_carries_a_message(tmp_path):
    data = _weather_scenario_data(user_turns=[{"end": True, "message": "one for the road"}])
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "end marker" in message
    assert "user_turns[0]" in message


@pytest.mark.parametrize("bad_end", ["yes", 1, None])
def test_loader_rejects_a_non_boolean_end_marker(tmp_path, bad_end):
    data = _weather_scenario_data(user_turns=[{"end": bad_end}])
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    assert "user_turns[0].end must be a boolean" in str(excinfo.value)


@pytest.mark.parametrize("bad", ["nope", 42, [], None])
def test_loader_rejects_user_turns_that_is_not_a_list_of_objects(tmp_path, bad):
    data = _weather_scenario_data(user_turns=bad)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "user_turns" in message


def test_loader_rejects_an_empty_user_turns_list(tmp_path):
    data = _weather_scenario_data(user_turns=[])
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    assert "non-empty list" in str(excinfo.value)


@pytest.mark.parametrize("bad", [0, -1, "3", 1.5, True, None])
def test_loader_rejects_max_rounds_that_is_not_a_positive_integer(tmp_path, bad):
    data = _weather_scenario_data(max_rounds=bad)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "'max_rounds' must be a positive integer" in message
    assert f"got {bad!r}" in message


# ---------------------------------------------------------------------------
# Runner — the multi-round conversation
# ---------------------------------------------------------------------------


def test_multi_round_conversation_converges_across_rounds(tmp_path):
    """Script exhaustion ends the conversation; assertions judge the cumulative
    run: the sequence spans all three rounds, the final text is the last
    round's answer, and turns add up across the react loops."""
    data = _weather_scenario_data(
        assertions={
            "expected_tool_sequence": ["get_weather"] * 3,
            "final_contains": ["Berlin"],
            "max_turns": 6,
        }
    )

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons
    assert result.rounds == 3
    assert result.turns == 6
    assert result.tool_sequence == ["get_weather", "get_weather", "get_weather"]
    assert result.final_text == "Berlin is cloudy."


def test_end_marker_stops_the_conversation_before_later_script_entries(tmp_path):
    """The end marker ends the conversation even though the script still has
    entries after it — the Berlin round is never started."""
    data = _weather_scenario_data(
        user_turns=[{"message": "And in Rome?"}, {"end": True}, {"message": "And Berlin?"}],
        assertions={"expected_tool_sequence": ["get_weather", "get_weather"]},
    )

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons
    assert result.rounds == 2
    assert result.tool_sequence == ["get_weather", "get_weather"]
    assert result.final_text == "Rome is clear."


def test_round_cap_truncates_the_user_script(tmp_path):
    """``max_rounds`` is a hard cap: the third scripted round never runs, the
    truncation itself is not a failure, and the assertions judge what ran."""
    data = _weather_scenario_data(
        max_rounds=2,
        assertions={"required_tools": ["get_weather"], "max_turns": 4},
    )

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons
    assert result.failure_reasons == []
    assert result.rounds == 2
    assert result.turns == 4
    assert result.tool_sequence == ["get_weather", "get_weather"]
    assert result.final_text == "Rome is clear."


def test_round_cap_of_one_runs_only_the_initial_prompt(tmp_path):
    data = _weather_scenario_data(max_rounds=1)

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons
    assert result.rounds == 1
    assert result.turns == 2
    assert result.tool_sequence == ["get_weather"]
    assert result.final_text == "Paris is sunny."


def test_user_turns_continue_the_same_agent_session(tmp_path, monkeypatch):
    """The rounds ride one agent instance: the second round's first model call
    receives the whole round-1 conversation plus the new user message, not a
    fresh single-message history."""
    seen: list[list[dict]] = []

    class _RecordingClient(runner_module.ScriptedLlmClient):
        async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
            seen.append(
                [
                    {"role": getattr(item, "role", "?"), "content": getattr(item, "content", "")}
                    for item in messages
                ]
            )
            async for event in super().chat(messages, tools, system_prompt=system_prompt):
                yield event

    monkeypatch.setattr(runner_module, "ScriptedLlmClient", _RecordingClient)

    result = _run(tmp_path, _weather_scenario_data())

    assert result.passed, result.failure_reasons
    assert result.rounds == 3
    # Six model calls (two turns per round); history only ever grows.
    assert [len(call) for call in seen] == [1, 3, 5, 7, 9, 11]
    # Round 1 starts from the initial prompt alone.
    assert seen[0] == [{"role": "user", "content": "What is the weather in Paris?"}]
    # Round 2's first call: the full round-1 dialogue, then the follow-up.
    assert [item["role"] for item in seen[2]] == ["user", "assistant", "tool", "assistant", "user"]
    assert seen[2][-1]["content"] == "And in Rome?"
    assert seen[4][-1]["content"] == "And Berlin?"


# ---------------------------------------------------------------------------
# Runner — assertions across rounds
# ---------------------------------------------------------------------------


def test_forbidden_tool_attempt_in_a_later_round_still_fails(tmp_path):
    """The policy assertion sees the cumulative sequence: a violation scripted
    for round 2 is caught even though round 1 was clean."""
    data = {
        "name": "late-policy-violation",
        "prompt": "Check my order please.",
        "tools": [
            {"name": "check_order", "description": "Look up an order.", "result": "ok"},
            {
                "name": "charge_card",
                "description": "Charge the card directly.",
                "result": "charged",
                "read_only": False,
                "danger_level": "high",
            },
        ],
        "scripted_turns": [
            {"tool_calls": [{"name": "check_order", "arguments": {}}]},
            {"text": "Order looks fine. Anything else?"},
            {"tool_calls": [{"name": "charge_card", "arguments": {}}]},
            {"text": "Charged."},
        ],
        "user_turns": [{"message": "Then charge my card."}],
        "assertions": {"forbidden_tools": ["charge_card"]},
    }

    result = _run(tmp_path, data)

    assert not result.passed
    assert "forbidden tool was called: charge_card" in result.failure_reasons
    assert result.tool_sequence == ["check_order", "charge_card"]


def test_expected_tool_sequence_must_match_the_cumulative_cross_round_order(tmp_path):
    """A sequence assertion written for a single round fails against a
    three-round run — the assertion judges the concatenated calls."""
    data = _weather_scenario_data(assertions={"expected_tool_sequence": ["get_weather"]})

    result = _run(tmp_path, data)

    assert not result.passed
    assert any(
        "tool sequence mismatch" in reason
        and "['get_weather', 'get_weather', 'get_weather']" in reason
        for reason in result.failure_reasons
    )


def test_max_turns_budgets_the_whole_conversation(tmp_path):
    """Two turns per round: a budget of 3 fails with the summed count, the
    exact budget of 6 passes."""
    over = _run(tmp_path, _weather_scenario_data(assertions={"max_turns": 3}))
    exact = _run(tmp_path, _weather_scenario_data(assertions={"max_turns": 6}))

    assert not over.passed
    assert "took 6 turns, limit is 3" in over.failure_reasons
    assert exact.passed, exact.failure_reasons
    assert exact.turns == 6


def test_final_contains_judges_the_last_rounds_final_text(tmp_path):
    """``final_contains`` reads the conversation's final answer — a needle
    that only appeared in an earlier round's text does not satisfy it."""
    last_round = _run(
        tmp_path, _weather_scenario_data(assertions={"final_contains": ["Berlin is cloudy"]})
    )
    earlier_round = _run(
        tmp_path, _weather_scenario_data(assertions={"final_contains": ["Paris is sunny"]})
    )

    assert last_round.passed, last_round.failure_reasons
    assert not earlier_round.passed
    assert any("does not contain" in reason for reason in earlier_round.failure_reasons)


# ---------------------------------------------------------------------------
# Built-in scenarios 05 / 06
# ---------------------------------------------------------------------------


def _builtin_scenario(name_prefix: str) -> Path:
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None, "repository ships examples/evals"
    matches = sorted(eval_dir.glob(f"{name_prefix}*.json"))
    assert matches, f"builtin scenario {name_prefix} not found"
    return matches[0]


def test_builtin_refund_policy_scenario_enforces_the_policy_across_rounds(tmp_path):
    """The flagship policy scenario: the order must be looked up first and the
    direct refund tool must never be attempted — in any round. A script that
    ever calls ``refund_order`` trips ``forbidden_tools``; dropping the lookup
    trips ``required_tools``."""
    scenario = load_eval_scenario(_builtin_scenario("05_"))

    assert scenario.assertions.required_tools == ["lookup_order"]
    assert scenario.assertions.forbidden_tools == ["refund_order"]
    assert scenario.max_rounds == 2

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed, result.failure_reasons
    assert result.rounds == 2
    assert result.tool_sequence == ["lookup_order", "open_refund_request"]
    assert "refund_order" not in result.tool_sequence
    assert "RF-2041" in result.final_text

    # The same script with the compliance steps stripped fails on both sides.
    broken_data = json.loads(Path(_builtin_scenario("05_")).read_text(encoding="utf-8"))
    broken_data["name"] = "refund-policy-violated"
    broken_data["scripted_turns"] = [
        {"tool_calls": [{"name": "refund_order", "arguments": {"order": "A-1001"}}]},
        {"text": "Refunded directly."},
    ]
    broken_data["user_turns"] = [{"message": "The item arrived damaged."}]
    broken = load_eval_scenario(_write_scenario(tmp_path, broken_data, "broken.json"))
    broken_result = asyncio.run(run_scenario(broken, cwd=str(tmp_path)))

    assert not broken_result.passed
    assert "forbidden tool was called: refund_order" in broken_result.failure_reasons
    assert "required tool was never called: lookup_order" in broken_result.failure_reasons


def test_builtin_booking_scenario_converges_in_two_rounds(tmp_path):
    scenario = load_eval_scenario(_builtin_scenario("06_"))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed, result.failure_reasons
    assert result.rounds == 2
    assert result.turns == 4
    assert result.tool_sequence == ["check_availability", "book_table"]
    assert "20:00" in result.final_text
