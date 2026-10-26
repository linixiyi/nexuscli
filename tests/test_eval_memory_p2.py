"""Tests for the memory-recall scenario type added in add-eval-scenario-types.

Covers, for ``memory_eval``:
- loader: the block (seed rows + query assertions) is optional, strictly
  validated (unknown keys at every level — top level included — wrong shapes,
  out-of-range seed indexes, invalid importance / temporal_kind, duplicate
  seed content that memory dedup would merge), and a memory scenario must not
  declare agent-loop keys; everything fails with ``EvalSchemaError`` naming
  the file;
- runner: the memory track never builds an agent — the seed rows go into a
  hermetic MemoryManager whose SQLite file lives in a system temp directory
  and is removed after the run; the ranking assertions judge the real fused
  recall (target in the top-K, distractors not ahead) with readable failure
  reasons;
- reporter: memory results carry zero cost metrics (no agent loop ran) and
  take part in the regression diff like any other scenario;
- the built-in scenarios ``07_memory_recall_entity_noise.json`` (entity
  anchor against same-word noise) and ``08_memory_recall_temporal_future.json``
  (a future plan outranking same-topic past events).

Kept in its own file so it does not collide with the other eval slices.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

import nexuscli.eval.runner as runner_module
from nexuscli.eval import (
    DEFAULT_MEMORY_TOP_K,
    EvalSchemaError,
    MemoryEvalSpec,
    MemoryQuerySpec,
    MemorySeedEntry,
    build_report,
    builtin_eval_dir,
    load_eval_scenario,
    run_scenario,
)


def _write_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _load(tmp_path: Path, data: dict):
    return load_eval_scenario(_write_scenario(tmp_path, data))


def _run(tmp_path: Path, data: dict):
    scenario = load_eval_scenario(_write_scenario(tmp_path, data))
    return asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))


def _memory_scenario_data(**overrides) -> dict:
    """A minimal memory scenario (verified against the real fused signals).

    The distractor is a lexical superset of the query, so it wins rank 1 and
    the related-but-weaker target lands at rank 2 — the ranking-failure tests
    below pin that arrangement from both sides.
    """
    data = {
        "name": "memory-small",
        "description": "small memory eval",
        "memory_eval": {
            "seed": [
                {"content": "deploy the payments service to production now", "importance": 0.9},
                {"content": "payments service deploy checklist", "importance": 0.4},
            ],
            "queries": [
                {
                    "query": "deploy the payments service to production",
                    "target": 1,
                    "distractors": [0],
                },
            ],
        },
    }
    data.update(overrides)
    return data


def _builtin_scenario(name_prefix: str) -> Path:
    eval_dir = builtin_eval_dir()
    assert eval_dir is not None, "repository ships examples/evals"
    matches = sorted(eval_dir.glob(f"{name_prefix}*.json"))
    assert matches, f"builtin scenario {name_prefix} not found"
    return matches[0]


# ---------------------------------------------------------------------------
# Loader — schema
# ---------------------------------------------------------------------------


def test_loader_accepts_memory_eval_with_all_fields(tmp_path):
    data = {
        "name": "memory-full",
        "description": "full spec",
        "difficulty": "long",
        "memory_eval": {
            "seed": [
                {
                    "content": "auth service runs on Keycloak",
                    "importance": 0.9,
                    "kind": "decision",
                    "entities": ["Keycloak"],
                    "temporal_kind": "past_event",
                },
                {"content": "the rollout is planned for friday"},
            ],
            "queries": [
                {"query": "Keycloak auth", "target": 0, "distractors": [1], "top_k": 2},
                {"query": "rollout plans", "target": 1},
            ],
        },
    }

    scenario = _load(tmp_path, data)

    assert scenario.memory_eval == MemoryEvalSpec(
        seed=[
            MemorySeedEntry(
                content="auth service runs on Keycloak",
                importance=0.9,
                kind="decision",
                entities=["Keycloak"],
                temporal_kind="past_event",
            ),
            MemorySeedEntry(content="the rollout is planned for friday"),
        ],
        queries=[
            MemoryQuerySpec(query="Keycloak auth", target=0, distractors=[1], top_k=2),
            MemoryQuerySpec(query="rollout plans", target=1, top_k=DEFAULT_MEMORY_TOP_K),
        ],
    )
    # The agent-loop side of the dataclass stays empty on the memory track.
    assert scenario.prompt == ""
    assert scenario.turns == []
    assert scenario.difficulty == "long"


def test_agent_scenario_defaults_memory_eval_to_none(tmp_path):
    data = {
        "name": "agent-scenario",
        "prompt": "do the thing",
        "scripted_turns": [{"text": "done"}],
    }

    scenario = _load(tmp_path, data)

    assert scenario.memory_eval is None


def test_loader_rejects_unknown_top_level_scenario_keys(tmp_path):
    data = _memory_scenario_data()
    data["memory_query"] = {"query": "typo'd key"}
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "memory_query" in message


@pytest.mark.parametrize(
    "extra",
    [
        {"prompt": "p"},
        {"scripted_turns": [{"text": "t"}]},
        {"tools": []},
        {"assertions": {}},
        {"user_turns": [{"message": "m"}]},
        {"max_rounds": 2},
    ],
)
def test_loader_rejects_memory_eval_mixed_with_agent_loop_keys(tmp_path, extra):
    data = _memory_scenario_data(**extra)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "without the agent loop" in message
    assert str(path) in message
    for key in extra:
        assert key in message


@pytest.mark.parametrize("bad", [42, "x", [], None])
def test_loader_rejects_memory_eval_that_is_not_an_object(tmp_path, bad):
    data = _memory_scenario_data()
    data["memory_eval"] = bad
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "'memory_eval' must be an object" in message


def test_loader_rejects_unknown_memory_eval_keys(tmp_path):
    data = _memory_scenario_data()
    data["memory_eval"]["kinds"] = ["fact"]
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "kinds" in message
    assert "memory_eval has unknown key(s)" in message


@pytest.mark.parametrize(
    "seed", [None, [], 42, ["not an object"], [{}], [{"content": "   "}], [{"content": 7}]]
)
def test_loader_rejects_invalid_seed_lists_and_entries(tmp_path, seed):
    data = _memory_scenario_data()
    if seed is None:
        del data["memory_eval"]["seed"]
    else:
        data["memory_eval"]["seed"] = seed
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "seed" in message


@pytest.mark.parametrize(
    "entry",
    [
        {"content": "ok", "importance": -0.1},
        {"content": "ok", "importance": 1.5},
        {"content": "ok", "importance": "0.5"},
        {"content": "ok", "importance": True},
        {"content": "ok", "kind": ""},
        {"content": "ok", "entities": "Keycloak"},
        {"content": "ok", "entities": [42]},
        {"content": "ok", "temporal_kind": "future"},
        {"content": "ok", "temporal_kind": "PAST_EVENT"},
        {"content": "ok", "mystery": 1},
    ],
)
def test_loader_rejects_invalid_seed_entry_fields(tmp_path, entry):
    data = _memory_scenario_data()
    data["memory_eval"]["seed"] = [entry]
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "memory_eval.seed[0]" in message


def test_loader_rejects_duplicate_seed_content(tmp_path):
    """``save()`` merges same-normalized content into one row, which would map
    two seed indexes onto one memory id — rejected at load time instead."""
    data = _memory_scenario_data()
    data["memory_eval"]["seed"] = [
        {"content": "Same Keycloak Content"},
        {"content": "same keycloak  content"},
    ]
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert "duplicates the content of seed[0]" in message
    assert "memory_eval.seed[1]" in message


@pytest.mark.parametrize(
    "queries",
    [
        None,
        [],
        42,
        ["not an object"],
        [{}],
        [{"target": 0}],
        [{"query": "q"}],
        [{"query": "  ", "target": 0}],
    ],
)
def test_loader_rejects_invalid_query_lists_and_entries(tmp_path, queries):
    data = _memory_scenario_data()
    if queries is None:
        del data["memory_eval"]["queries"]
    else:
        data["memory_eval"]["queries"] = queries
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "queries" in message


@pytest.mark.parametrize(
    "overrides",
    [
        {"target": 2},  # out of range (2 seed rows: 0..1)
        {"target": -1},
        {"target": "0"},
        {"target": 1.5},
        {"target": True},  # a bool is not an index
        {"distractors": 0},
        {"distractors": [0, 5]},
        {"distractors": ["0"]},
        {"top_k": 0},
        {"top_k": -1},
        {"top_k": "3"},
        {"top_k": True},
        {"unknown_key": 1},
    ],
)
def test_loader_rejects_invalid_query_fields(tmp_path, overrides):
    data = _memory_scenario_data()
    data["memory_eval"]["queries"][0].update(overrides)
    path = _write_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert "memory_eval.queries[0]" in message


# ---------------------------------------------------------------------------
# Runner — the memory-recall track
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name_prefix", ["07_", "08_"])
def test_builtin_memory_scenarios_pass_offline(tmp_path, name_prefix):
    scenario = load_eval_scenario(_builtin_scenario(name_prefix))

    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed, result.failure_reasons
    assert result.failure_reasons == []
    # No agent loop ran: no rounds beyond the trivial one, no turns, no tools.
    assert result.rounds == 1
    assert result.turns == 0
    assert result.tool_sequence == []


def test_target_miss_fails_with_the_recalled_order(tmp_path):
    """An unrelated target scores zero relevance, never reaches the recall
    window, and the failure quotes what was recalled instead."""
    data = _memory_scenario_data()
    data["name"] = "target-miss"
    data["memory_eval"] = {
        "seed": [
            {"content": "auth service configuration checklist for realm 1", "importance": 0.9},
            {"content": "auth service configuration checklist for realm 2", "importance": 0.9},
            {"content": "auth service configuration checklist for realm 3", "importance": 0.9},
            {"content": "the cafeteria serves lasagna on friday", "importance": 0.4},
        ],
        "queries": [
            {
                "query": "Bluefin Keycloak auth service configuration",
                "target": 3,
                "distractors": [0, 1, 2],
                "top_k": 3,
            },
        ],
    }

    result = _run(tmp_path, data)

    assert not result.passed
    assert any(
        "target seed #3 missed the top-3" in reason and "#2, #1, #0" in reason
        for reason in result.failure_reasons
    )


def test_distractor_ahead_of_target_fails_with_both_ranks(tmp_path):
    """The target is recalled (inside the window) but a distractor ranks
    above it — the ranking assertion fails naming both positions."""
    data = _memory_scenario_data()
    data["name"] = "distractor-ahead"

    result = _run(tmp_path, data)

    assert not result.passed
    assert any(
        "distractor seed #0 outranked target seed #1 (rank 1 vs 2)" in reason
        for reason in result.failure_reasons
    )


def test_distractors_ranked_below_the_target_pass(tmp_path):
    data = _memory_scenario_data()
    data["name"] = "target-first"
    data["memory_eval"]["queries"][0] = {
        "query": "deploy the payments service to production",
        "target": 0,
        "distractors": [1],
    }

    result = _run(tmp_path, data)

    assert result.passed, result.failure_reasons


def test_memory_eval_builds_db_in_the_system_temp_dir_and_cleans_up(tmp_path, monkeypatch):
    """The real entry point has no pytest tmp fixture: the runner must place
    its SQLite file under the system temp directory and remove it afterwards —
    never the repo, never the user home."""
    db_paths: list[Path] = []
    real_manager = runner_module.MemoryManager

    class _RecordingManager(real_manager):
        def __init__(self, db_path, scope, **kwargs):
            super().__init__(db_path, scope, **kwargs)
            db_paths.append(Path(db_path))

    monkeypatch.setattr(runner_module, "MemoryManager", _RecordingManager)

    scenario = load_eval_scenario(_builtin_scenario("07_"))
    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    assert result.passed, result.failure_reasons
    assert db_paths, "the memory manager was constructed"
    temp_root = Path(tempfile.gettempdir()).resolve()
    for db_path in db_paths:
        assert db_path.resolve().is_relative_to(temp_root)
        # The temp directory is removed when the run ends.
        assert not db_path.exists()
        assert not db_path.parent.exists()


# ---------------------------------------------------------------------------
# Reporter — same PASS/FAIL, regression diff and cost conventions
# ---------------------------------------------------------------------------


def test_memory_result_carries_zero_cost_and_diffs_like_any_scenario(tmp_path):
    scenario = load_eval_scenario(_builtin_scenario("07_"))
    result = asyncio.run(run_scenario(scenario, cwd=str(tmp_path)))

    report = build_report([result], previous=None)

    entry = report["scenarios"][0]
    assert entry["status"] == "pass"
    assert entry["turns"] == 0
    assert entry["total_tool_calls"] == 0
    assert entry["rounds"] == 1
    assert entry["cost"] == {"change": None, "warnings": []}


def test_failing_memory_scenario_counts_as_a_regression(tmp_path):
    data = _memory_scenario_data()
    data["name"] = "recall-quality"
    failing = _run(tmp_path, data)
    assert not failing.passed

    previous = {"scenarios": [{"name": "recall-quality", "status": "pass"}]}
    report = build_report([failing], previous)

    entry = report["scenarios"][0]
    assert entry["status"] == "fail"
    assert entry["comparison"]["verdict"] == "regression"
    assert report["summary"]["regressions"] == 1
