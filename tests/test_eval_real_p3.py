"""Tests for the real-model eval track (``nexuscli eval run --real``).

Everything here stays hermetic: the LLM client is injected (the
``llm_client_factory`` constructor seam, or the equivalent
``nexuscli.eval.real.create_llm_client`` monkeypatch surface for the CLI) and
the judge is injected as a callable. No test performs a network call — real
traffic only happens on an explicit user-driven ``--real`` run.

Covers:
- the real-track loader: minimal strict schema, judge shape, hermetic-only
  track-conflict keys and unknown keys rejected with the file named;
- the runner: real read-only builtin tools actually execute, write calls are
  fail-closed denied through the real permission gate and audited (a user
  allow rule cannot punch through), usage/cost collection from the stream,
  the serial repeat loop with a fresh client per run, agent errors and
  termination warnings, and the judge seam (explicit failure without an
  evaluator, threshold verdicts with one);
- the track config variant: deny rules computed for every builtin outside the
  read-only subset, audit pinned on, hitl_mode pinned to auto;
- the CLI: flag parsing (--real/--repeat), the no-key actionable error, and
  the report landing in last-real-report.json while the hermetic baseline
  last-report.json is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
import nexuscli.eval.real as real_module
from nexuscli.eval import (
    REAL_REPORT_RELATIVE,
    REAL_TRACK_TOOLS,
    EvalSchemaError,
    RealTrackError,
    builtin_real_eval_dir,
    load_real_eval_scenario,
    load_real_eval_scenarios,
    run_real_scenarios,
)
from nexuscli.eval.real import (
    RealEvalResult,
    _load_real_config,
    _real_registry,
    build_real_report,
    write_real_report,
)
from nexuscli.eval.reporter import REPORT_RELATIVE
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.types import Usage

runner = CliRunner()


@pytest.fixture
def user_home(_isolate_user_home):
    """The throwaway user home the autouse conftest fixture created."""
    return _isolate_user_home


def _write_real_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_real_data(**overrides) -> dict:
    data = {
        "name": "real-smoke",
        "prompt": "Read notes.txt and report its first line.",
        "assertions": {"max_turns": 4},
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Fake LLM client (the injection seam in action)
# ---------------------------------------------------------------------------


class _FakeCost:
    def __init__(self, total: float) -> None:
        self._total = total

    def to_dict(self) -> dict:
        return {"total_cost": self._total}


class FakeRealLlmClient:
    """Scripted stand-in for a real LLM client, speaking the real protocol.

    Yields the same streaming events the real OpenAI-compatible client does
    (including one ``usage`` event per turn) plus a deterministic
    ``calculate_cost``. Snapshots the tool definitions and the messages it is
    called with, so tests can assert what the model saw.
    """

    model_name = "fake-real-model"
    provider_name = "fake-real"
    max_context_window = 100_000

    def __init__(self, turns: list[dict], *, per_turn_usage: dict | None = None) -> None:
        self._turns = list(turns)
        self._index = 0
        self._per_turn_usage = per_turn_usage or {"input_tokens": 10, "output_tokens": 5}
        self.seen_tool_names: list[list[str]] = []
        self.seen_messages: list[list] = []

    def calculate_cost(self, usage, *, currency: str = "usd"):  # noqa: ANN001, ANN202
        if currency != "usd":
            raise ValueError("no price profile for currency")
        return _FakeCost(round(0.002 * usage.total_tokens, 6))

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ANN001, ARG002
        self.seen_tool_names.append([tool["function"]["name"] for tool in tools])
        self.seen_messages.append(list(messages))
        if self._index >= len(self._turns):
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        turn = self._turns[self._index]
        self._index += 1
        for index, call in enumerate(turn.get("tool_calls", [])):
            yield {
                "type": "tool_call_delta",
                "tool_call": {
                    "index": index,
                    "id": f"call_{self._index}_{index}",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call.get("arguments") or {}),
                    },
                },
            }
        if turn.get("text"):
            yield {"type": "text_delta", "text": turn["text"]}
        if turn.get("error") is not None:
            yield {"type": "error", "error": turn["error"]}
            return
        yield {"type": "usage", "usage": dict(self._per_turn_usage)}
        yield {
            "type": "message_end",
            "stop_reason": "tool_use" if turn.get("tool_calls") else "end_turn",
        }


def _text_turn(text: str) -> dict:
    return {"text": text}


def _tool_turn(name: str, arguments: dict) -> dict:
    return {"tool_calls": [{"name": name, "arguments": arguments}]}


# ---------------------------------------------------------------------------
# Loader — the real-track schema
# ---------------------------------------------------------------------------


def test_real_loader_accepts_minimal_schema(tmp_path):
    path = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": "answers the question", "threshold": 0.7, "model": "glm-5.2"}
        ),
    )

    scenario = load_real_eval_scenario(path)

    assert scenario.name == "real-smoke"
    assert scenario.prompt.strip()
    assert scenario.assertions.max_turns == 4
    assert scenario.judge is not None
    assert scenario.judge.rubric == ["answers the question"]
    assert scenario.judge.threshold == 0.7
    assert scenario.judge.model == "glm-5.2"
    assert scenario.source == str(path)


def test_real_loader_accepts_scenario_without_assertions_or_judge(tmp_path):
    path = _write_real_scenario(tmp_path, {"name": "bare", "prompt": "hello"})

    scenario = load_real_eval_scenario(path)

    assert scenario.assertions.max_turns is None
    assert scenario.judge is None


@pytest.mark.parametrize(
    "conflict_key",
    ["tools", "scripted_turns", "memory_eval", "permission_deny", "hitl_mode", "max_rounds"],
)
def test_real_loader_rejects_track_conflict_keys(tmp_path, conflict_key):
    data = _minimal_real_data()
    data[conflict_key] = {"anything": True}  # the conflict check precedes shape parsing
    path = _write_real_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert str(path) in message
    assert conflict_key in message
    assert "real-track" in message


def test_real_loader_rejects_user_turns_as_track_conflict(tmp_path):
    data = _minimal_real_data(user_turns=[{"message": "again"}])
    path = _write_real_scenario(tmp_path, data)

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    assert "user_turns" in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_real_loader_rejects_hermetic_only_top_level_keys(tmp_path):
    path = _write_real_scenario(
        tmp_path, _minimal_real_data(description="hermetic-only", difficulty="short")
    )

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    message = str(excinfo.value)
    assert "unknown scenario key(s)" in message
    assert "difficulty" in message
    assert "real-track scenarios support" in message
    assert str(path) in message


@pytest.mark.parametrize(
    ("judge", "fragment"),
    [
        ({"threshold": 0.7}, "judge.rubric"),
        ({"rubric": ["r"]}, "judge.threshold"),
        ({"rubric": ["r"], "threshold": 1.5}, "judge.threshold"),
        ({"rubric": ["r"], "threshold": True}, "judge.threshold"),
        ({"rubric": [], "threshold": 0.5}, "judge.rubric"),
        ({"rubric": ["r", "  "], "threshold": 0.5}, "judge.rubric"),
        ({"rubric": ["r"], "threshold": 0.5, "model": ""}, "judge.model"),
        ({"rubric": ["r"], "threshold": 0.5, "extra": 1}, "unknown key"),
    ],
)
def test_real_loader_validates_judge_shape(tmp_path, judge, fragment):
    path = _write_real_scenario(tmp_path, _minimal_real_data(judge=judge))

    with pytest.raises(EvalSchemaError) as excinfo:
        load_real_eval_scenario(path)

    assert fragment in str(excinfo.value)
    assert str(path) in str(excinfo.value)


def test_builtin_real_dir_ships_two_runnable_scenarios():
    real_dir = builtin_real_eval_dir()
    assert real_dir is not None, "repository ships examples/evals/real"
    scenarios = load_real_eval_scenarios(real_dir)
    names = [scenario.name for scenario in scenarios]
    assert len(names) == 2
    assert names == ["real-readonly-converge", "real-repo-question-answer"]
    # One plain smoke scenario and one judge-scored repository Q&A scenario,
    # both loadable with the strict real-track schema.
    assert all(scenario.judge is None or scenario.judge.threshold for scenario in scenarios)
    judge_scenarios = [scenario for scenario in scenarios if scenario.judge is not None]
    assert len(judge_scenarios) == 1
    assert judge_scenarios[0].judge.rubric
    assert 0 < judge_scenarios[0].judge.threshold <= 1


# ---------------------------------------------------------------------------
# Track config variant — the read-only enforcement layers
# ---------------------------------------------------------------------------


def test_real_config_pins_audit_and_computes_deny_rules_for_everything_outside_subset(tmp_path):
    config = _load_real_config(str(tmp_path))

    assert config.features.audit_log is True
    assert config.policy.hitl_mode == "auto"
    assert config.policy.plan_mode is False
    builtin_names = {tool.name for tool in get_builtin_tools()}
    deny = set(config.permissions.deny)
    assert builtin_names - REAL_TRACK_TOOLS <= deny, "every non-read-only builtin is deny-ruled"
    assert not (REAL_TRACK_TOOLS & deny), "read-only tools stay executable"


def test_real_allowlist_excludes_user_state_recording_tools(tmp_path):
    """Review finding: two flagged-read-only builtins are not zero-write.

    ``search_memory`` calls ``MemoryManager.recall`` with the default
    ``mark_access=True`` — every call UPDATEs ``access_count`` on the user's
    real memory DB and runs ``_purge_expired`` (a DELETE of expired rows)
    (memory/manager.py). ``load_skill`` records usage counters to
    ``~/.nexuscli/skills/usage.json`` behind the agent's always-truthy skill
    buffer (tools/builtins.py -> skill/registry.py record_use). Both stay
    out of the allowlist (deny-ruled + audited like any other write), so the
    track's "unattended runs never write" promise is literal; re-adding them
    requires removing their user-state writes first.
    """
    assert "search_memory" not in REAL_TRACK_TOOLS
    assert "load_skill" not in REAL_TRACK_TOOLS
    # And the computed deny rules cover them (enforced end-to-end by the
    # config test above; asserted here by name so a future re-addition is a
    # conscious, reviewed decision).
    config = _load_real_config(str(tmp_path))
    assert "search_memory" in config.permissions.deny
    assert "load_skill" in config.permissions.deny


def test_real_config_deny_wins_over_user_allow_rules(user_home, tmp_path):
    (user_home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (user_home / ".nexuscli" / "config.json").write_text(
        json.dumps({"permissions": {"allow": ["write_file"]}}), encoding="utf-8"
    )

    config = _load_real_config(str(tmp_path))

    assert "write_file" in config.permissions.deny
    assert "write_file" in config.permissions.allow


def test_real_registry_mounts_all_builtins_so_writes_hit_the_real_gate():
    registry = _real_registry()

    assert set(registry.list_names()) == {tool.name for tool in get_builtin_tools()}


# ---------------------------------------------------------------------------
# Runner — real tools, fail-closed writes, usage, repeat
# ---------------------------------------------------------------------------


def _run(scenario_path, *, root, repeat=1, turns, per_turn_usage=None, judge=None):
    scenario = load_real_eval_scenario(scenario_path)
    clients: list[FakeRealLlmClient] = []

    def factory(llm_config):  # noqa: ANN001, ANN202
        client = FakeRealLlmClient(turns, per_turn_usage=per_turn_usage)
        clients.append(client)
        return client

    results = run_real_scenarios(
        [scenario],
        root=str(root),
        repeat=repeat,
        llm_client_factory=factory,
        judge=judge,
    )
    return results, clients


def test_real_track_executes_a_real_readonly_builtin_tool(tmp_path):
    (tmp_path / "notes.txt").write_text("ROOT-NOTE-CONTENT", encoding="utf-8")
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            prompt="Read notes.txt.",
            assertions={"max_turns": 4, "required_tools": ["read_file"]},
        ),
    )

    results, clients = _run(
        scenario,
        root=tmp_path,
        turns=[_tool_turn("read_file", {"path": "notes.txt"}), _text_turn("done")],
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    assert result.tool_sequence == ["read_file"]
    # The real builtin handler ran: the tool result fed back to the model is
    # the actual file content, not a stub canned string.
    followup_messages = clients[0].seen_messages[1]
    tool_contents = [str(getattr(message, "content", "")) for message in followup_messages]
    assert any("ROOT-NOTE-CONTENT" in content for content in tool_contents)


def test_real_track_denies_write_call_fail_closed_and_audits_it(user_home, tmp_path):
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(prompt="Write a file.", assertions={"max_turns": 4}),
    )

    results, clients = _run(
        scenario,
        root=tmp_path,
        turns=[
            _tool_turn("write_file", {"path": "evil.txt", "content": "nope"}),
            _text_turn("understood"),
        ],
    )

    result = results[0]
    # The denial does not fail the run by itself (forbidden_tools does that);
    # what matters: nothing was written, the gate denied it, and it is audited.
    assert not (tmp_path / "evil.txt").exists()
    assert result.tool_sequence == ["write_file"]
    followup_messages = clients[0].seen_messages[1]
    tool_contents = [str(getattr(message, "content", "")) for message in followup_messages]
    assert any("denied by permission rule" in content for content in tool_contents)
    audit_path = user_home / ".nexuscli" / "audit.jsonl"
    assert audit_path.is_file(), "audit log must be on for the real track"
    events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    write_events = [event for event in events if event["tool_name"] == "write_file"]
    assert write_events
    assert write_events[-1]["outcome"] == "deny"
    assert write_events[-1]["approver"] == "permission-rule"


def test_real_track_collects_usage_and_cost_from_the_stream(tmp_path):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data(assertions={"max_turns": 4}))

    results, _ = _run(
        scenario,
        root=tmp_path,
        turns=[_tool_turn("list_dir", {"path": "."}), _text_turn("2 entries")],
        per_turn_usage={"input_tokens": 12, "output_tokens": 4, "reasoning_tokens": 1},
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    # Two turns, both counted.
    assert result.usage.input_tokens == 24
    assert result.usage.output_tokens == 8
    assert result.usage.reasoning_tokens == 2
    assert result.usage.total_tokens == 32
    assert result.cost["usd"]["total_cost"] == pytest.approx(0.002 * 32)
    assert "cny" not in result.cost


def test_real_repeat_loop_runs_serially_with_a_fresh_client_per_run(tmp_path):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data(assertions={"max_turns": 4}))

    results, clients = _run(
        scenario,
        root=tmp_path,
        repeat=3,
        turns=[_text_turn("ok")],
    )

    assert [result.repetition for result in results] == [1, 2, 3]
    assert all(result.name == "real-smoke" for result in results)
    assert len(clients) == 3, "one fresh LLM client per repetition"
    assert all(client.seen_messages for client in clients), "every run talked to its client"


def test_real_run_reports_agent_error_and_termination_as_failures(tmp_path):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data(assertions={"max_turns": 2}))
    error_results, _ = _run(
        scenario,
        root=tmp_path,
        turns=[{"text": "boom", "error": RuntimeError("provider exploded")}],
    )
    assert not error_results[0].passed
    assert any("agent errored" in reason for reason in error_results[0].failure_reasons)

    runaway = _write_real_scenario(
        tmp_path,
        _minimal_real_data(assertions={"max_turns": 1}),
        filename="runaway.json",
    )
    loop_results, _ = _run(
        runaway,
        root=tmp_path,
        turns=[_tool_turn("list_dir", {"path": "."})] * 10,
    )
    assert not loop_results[0].passed
    assert any(
        "did not converge within its turn budget" in reason
        for reason in loop_results[0].failure_reasons
    )


# ---------------------------------------------------------------------------
# Judge seam
# ---------------------------------------------------------------------------


_JUDGE_REPLY = "SCORE: 0.90\nREASON: cites the file"


def test_default_judge_scores_via_the_same_factory(tmp_path):
    """judge=None uses the default LLM judge built on the injected factory.

    The uniform scripted reply drives both calls: the agent's single text turn
    becomes the judged answer, and the judge's own fresh client replays the
    same ``SCORE:`` reply, which parses to 0.90 >= 0.6.
    """
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.6},
            assertions={"max_turns": 4},
        ),
    )

    results, clients = _run(scenario, root=tmp_path, turns=[_text_turn(_JUDGE_REPLY)])

    result = results[0]
    assert result.passed, result.failure_reasons
    assert len(clients) == 2, "one client for the run, one for the judge call"
    assert result.judge == {
        "score": 0.9,
        "threshold": 0.6,
        "passed": True,
        "explanation": "cites the file",
        "model": None,
    }


def test_default_judge_below_threshold_fails_with_score_and_reason(tmp_path):
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.6},
            assertions={"max_turns": 4},
        ),
    )

    results, _ = _run(scenario, root=tmp_path, turns=[_text_turn("SCORE: 0.20\nREASON: vague")])

    result = results[0]
    assert not result.passed
    assert result.judge is not None and result.judge["passed"] is False
    assert any(
        "judge score 0.20 is below threshold 0.60" in reason and "vague" in reason
        for reason in result.failure_reasons
    )


def test_default_judge_unparsable_reply_scores_zero_fail_closed(tmp_path):
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.6},
            assertions={"max_turns": 4},
        ),
    )

    results, _ = _run(scenario, root=tmp_path, turns=[_text_turn("sounds fine to me")])

    result = results[0]
    assert not result.passed
    assert result.judge is not None and result.judge["score"] == 0.0
    assert any("judge reply unparsable" in reason for reason in result.failure_reasons)


def test_default_judge_model_override_reaches_the_factory(tmp_path):
    """judge.model swaps the model on the judge's own factory call only."""
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.5, "model": "judge-model-x"},
            assertions={"max_turns": 4},
        ),
    )
    seen_models: list[str | None] = []

    def factory(llm_config):  # noqa: ANN001, ANN202
        seen_models.append(llm_config.model)
        return FakeRealLlmClient([_text_turn(_JUDGE_REPLY)])

    scenario_obj = load_real_eval_scenario(scenario)
    results = run_real_scenarios([scenario_obj], root=str(tmp_path), llm_client_factory=factory)

    assert results[0].passed, results[0].failure_reasons
    # First call = the run (the config-chain default model), second = the judge override.
    assert seen_models == ["deepseek-v4-flash", "judge-model-x"]
    assert results[0].judge is not None and results[0].judge["model"] == "judge-model-x"


def test_default_judge_without_override_follows_the_run_model(tmp_path):
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.5},
            assertions={"max_turns": 4},
        ),
    )
    seen_models: list[str | None] = []

    def factory(llm_config):  # noqa: ANN001, ANN202
        seen_models.append(llm_config.model)
        return FakeRealLlmClient([_text_turn(_JUDGE_REPLY)])

    results = run_real_scenarios(
        [load_real_eval_scenario(scenario)], root=str(tmp_path), llm_client_factory=factory
    )

    assert results[0].passed, results[0].failure_reasons
    assert seen_models == ["deepseek-v4-flash", "deepseek-v4-flash"], (
        "without judge.model the judge follows the run's provider/model"
    )


def test_default_judge_usage_is_folded_into_the_run_total(tmp_path):
    """The judge call is real cost: its usage joins the run's usage total."""
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.5},
            assertions={"max_turns": 4},
        ),
    )

    results, _ = _run(
        scenario,
        root=tmp_path,
        turns=[_text_turn(_JUDGE_REPLY)],
        per_turn_usage={"input_tokens": 10, "output_tokens": 5},
    )

    result = results[0]
    assert result.passed, result.failure_reasons
    # One agent turn + one judge call, each 10 in / 5 out.
    assert result.usage.input_tokens == 20
    assert result.usage.output_tokens == 10


def test_real_judge_evaluator_threshold_verdict(tmp_path):
    scenario = _write_real_scenario(
        tmp_path,
        _minimal_real_data(
            judge={"rubric": ["cites the file"], "threshold": 0.6, "model": "glm-5.2"},
            assertions={"max_turns": 4},
        ),
    )
    seen: list[tuple[str, tuple[float, str]]] = []

    async def strong_judge(final_text, spec):  # noqa: ANN001, ANN202
        seen.append((final_text, spec.rubric))
        return 0.9, "cites the file"

    passing, _ = _run(scenario, root=tmp_path, turns=[_text_turn("answer")], judge=strong_judge)
    assert passing[0].passed
    assert seen[0][0] == "answer"
    assert seen[0][1] == ["cites the file"]
    assert passing[0].judge == {
        "score": 0.9,
        "threshold": 0.6,
        "passed": True,
        "explanation": "cites the file",
        "model": "glm-5.2",
    }

    async def weak_judge(final_text, spec):  # noqa: ANN001, ARG001
        return 0.2, "vague"

    failing, _ = _run(scenario, root=tmp_path, turns=[_text_turn("answer")], judge=weak_judge)
    assert not failing[0].passed
    assert any(
        "judge score 0.20 is below threshold 0.60" in reason and "vague" in reason
        for reason in failing[0].failure_reasons
    )


def test_real_scenario_without_judge_never_calls_the_evaluator(tmp_path):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data(assertions={"max_turns": 4}))
    calls: list[int] = []

    async def judge(final_text, spec):  # noqa: ANN001, ARG001
        calls.append(1)
        return 0.0, "ignored"

    results, _ = _run(scenario, root=tmp_path, turns=[_text_turn("ok")], judge=judge)

    assert results[0].passed
    assert calls == [], "judge must not run when the scenario declares none"


# ---------------------------------------------------------------------------
# No-key / unusable provider — never a silent skip
# ---------------------------------------------------------------------------


@pytest.fixture
def _no_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "NEXUSCLI_API_KEY",
        "DEEPSEEK_API_KEY",
        "GLM_API_KEY",
        "ZAI_API_KEY",
        "KIMI_API_KEY",
        "STEP_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_runner_without_key_raises_real_track_error(tmp_path, _no_llm_env):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data())

    with pytest.raises(RealTrackError) as excinfo:
        run_real_scenarios([load_real_eval_scenario(scenario)], root=str(tmp_path))

    message = str(excinfo.value)
    assert "no LLM API key" in message
    assert "NEXUSCLI_API_KEY" in message
    assert "eval run --real" in message


def test_runner_with_unusable_provider_raises_real_track_error(user_home, tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    (user_home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    (user_home / ".nexuscli" / "config.json").write_text(
        json.dumps({"llm": {"provider": "bogus-provider"}}), encoding="utf-8"
    )
    scenario = _write_real_scenario(tmp_path, _minimal_real_data())

    with pytest.raises(RealTrackError) as excinfo:
        run_real_scenarios([load_real_eval_scenario(scenario)], root=str(tmp_path))

    assert "unusable LLM provider" in str(excinfo.value)
    assert "bogus-provider" in str(excinfo.value)


def test_runner_rejects_repeat_below_one(tmp_path):
    scenario = _write_real_scenario(tmp_path, _minimal_real_data())

    with pytest.raises(ValueError, match="repeat"):
        run_real_scenarios(
            [load_real_eval_scenario(scenario)],
            root=str(tmp_path),
            repeat=0,
            llm_client_factory=lambda config: FakeRealLlmClient([]),
        )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_real_report_shape_and_independent_file(tmp_path):
    results = [
        RealEvalResult(
            name="s",
            passed=True,
            repetition=1,
            turns=2,
            usage=Usage(
                input_tokens=100, output_tokens=50, cache_hit_tokens=20, reasoning_tokens=5
            ),
            cost={"usd": {"currency": "usd", "total_cost": 0.1}},
            judge={
                "score": 0.9,
                "threshold": 0.6,
                "passed": True,
                "explanation": "ok",
                "model": None,
            },
            source="x.json",
        ),
        RealEvalResult(name="s", passed=False, repetition=2, failure_reasons=["boom"]),
    ]

    report = build_real_report(results, repeat=2)
    path = write_real_report(tmp_path, report)

    assert path == tmp_path / REAL_REPORT_RELATIVE
    assert report["track"] == "real"
    assert report["repeat"] == 2
    entry = report["scenarios"][0]
    assert entry["name"] == "s"
    assert [run["index"] for run in entry["runs"]] == [1, 2]
    assert entry["runs"][1]["status"] == "fail"
    assert entry["runs"][0]["usage"]["total_tokens"] == 150
    assert entry["runs"][0]["judge"]["score"] == 0.9
    assert entry["runs"][1]["judge"] is None
    # pass^k: run 2 failed, so the scenario is not an all-pass scenario.
    assert entry["pass_k"] == {"repeat": 2, "passed_runs": 1, "failed_runs": 1, "all_pass": False}
    # Cost block: accumulated and averaged token usage with the splits intact.
    assert entry["cost"]["runs"] == 2
    assert entry["cost"]["total_usage"] == {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_hit_tokens": 20,
        "cache_miss_tokens": 0,
        "reasoning_tokens": 5,
        "total_tokens": 150,
    }
    assert entry["cost"]["avg_usage"]["input_tokens"] == 50.0
    assert entry["cost"]["avg_usage"]["reasoning_tokens"] == 2.5
    assert entry["cost"]["total_cost"]["usd"]["total_cost"] == 0.1
    # Summary: all-pass rate over scenarios plus run-wide totals.
    assert report["summary"]["total_runs"] == 2
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["scenarios_total"] == 1
    assert report["summary"]["scenarios_all_pass"] == 0
    assert report["summary"]["all_pass_rate"] == 0.0
    assert report["summary"]["total_usage"]["total_tokens"] == 150
    assert report["summary"]["total_cost"]["usd"]["total_cost"] == 0.1
    # Comparison is intentionally absent: the real track does not diff.
    assert "comparison" not in entry


def test_report_pass_k_all_pass_rate_both_sides():
    results = [
        RealEvalResult(name="stable", passed=True, repetition=1),
        RealEvalResult(name="stable", passed=True, repetition=2),
        RealEvalResult(name="stable", passed=True, repetition=3),
        RealEvalResult(name="flaky", passed=True, repetition=1),
        RealEvalResult(name="flaky", passed=False, repetition=2, failure_reasons=["boom"]),
        RealEvalResult(name="flaky", passed=True, repetition=3),
    ]

    report = build_real_report(results, repeat=3)

    by_name = {entry["name"]: entry for entry in report["scenarios"]}
    assert by_name["stable"]["pass_k"] == {
        "repeat": 3,
        "passed_runs": 3,
        "failed_runs": 0,
        "all_pass": True,
    }
    assert by_name["flaky"]["pass_k"]["all_pass"] is False
    assert by_name["flaky"]["pass_k"]["passed_runs"] == 2
    assert report["summary"]["scenarios_total"] == 2
    assert report["summary"]["scenarios_all_pass"] == 1
    assert report["summary"]["all_pass_rate"] == 0.5
    assert report["summary"]["total_runs"] == 6
    assert report["summary"]["passed"] == 5
    assert report["summary"]["failed"] == 1
    # Every repetition is kept in the report, nothing dropped.
    assert [run["index"] for run in by_name["flaky"]["runs"]] == [1, 2, 3]


def test_report_cost_aggregation_sums_usage_and_cost(tmp_path):
    def _run_result(repetition: int) -> RealEvalResult:
        return RealEvalResult(
            name="s",
            passed=True,
            repetition=repetition,
            usage=Usage(
                input_tokens=100,
                output_tokens=30,
                cache_hit_tokens=40,
                cache_miss_tokens=60,
                reasoning_tokens=8,
            ),
            cost={"usd": {"currency": "usd", "total_cost": 0.064}},
        )

    report = build_real_report([_run_result(1), _run_result(2)], repeat=2)

    entry = report["scenarios"][0]
    # Usage.__post_init__ fills input_tokens from the cache split and total
    # from input+output, so both runs carry total 130.
    assert entry["cost"]["total_usage"]["input_tokens"] == 200
    assert entry["cost"]["total_usage"]["cache_hit_tokens"] == 80
    assert entry["cost"]["total_usage"]["cache_miss_tokens"] == 120
    assert entry["cost"]["total_usage"]["reasoning_tokens"] == 16
    assert entry["cost"]["total_usage"]["total_tokens"] == 260
    assert entry["cost"]["avg_usage"]["input_tokens"] == 100.0
    assert entry["cost"]["avg_usage"]["total_tokens"] == 130.0
    assert entry["cost"]["total_cost"]["usd"]["total_cost"] == pytest.approx(0.128)
    assert entry["cost"]["total_cost"]["usd"]["currency"] == "usd"
    assert report["summary"]["total_usage"]["total_tokens"] == 260
    assert report["summary"]["total_cost"]["usd"]["total_cost"] == pytest.approx(0.128)


def test_report_pass_k_with_repeat_one_is_the_plain_pass_rate():
    report = build_real_report(
        [RealEvalResult(name="a", passed=True), RealEvalResult(name="b", passed=False)],
        repeat=1,
    )

    assert report["summary"]["scenarios_total"] == 2
    assert report["summary"]["scenarios_all_pass"] == 1
    assert report["summary"]["all_pass_rate"] == 0.5


# ---------------------------------------------------------------------------
# CLI — flag parsing, no-key error, report file separation
# ---------------------------------------------------------------------------


def test_cli_repeat_requires_real_track(tmp_path):
    result = runner.invoke(cli.app, ["eval", "run", "--repeat", "2", "--cwd", str(tmp_path)])

    assert result.exit_code != 0
    assert "--real" in result.output


def test_cli_repeat_must_be_positive(tmp_path):
    result = runner.invoke(
        cli.app, ["eval", "run", "--real", "--repeat", "0", "--cwd", str(tmp_path)]
    )

    assert result.exit_code != 0


def test_cli_real_without_key_exits_nonzero_with_actionable_error(
    tmp_path, _no_llm_env, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_real_scenario(scenario_dir, _minimal_real_data())

    result = runner.invoke(
        cli.app,
        ["eval", "run", "--real", "--file", str(scenario_dir), "--cwd", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "no LLM API key" in result.output
    assert "NEXUSCLI_API_KEY" in result.output
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists(), "no report on a failed start"


def test_cli_real_run_monkeypatched_client_writes_real_report_only(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key-eval")
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    (tmp_path / "notes.txt").write_text("CLI-SMOKE", encoding="utf-8")
    _write_real_scenario(
        scenario_dir,
        _minimal_real_data(assertions={"max_turns": 4, "required_tools": ["read_file"]}),
    )

    def fake_factory(llm_config, **_kwargs):  # noqa: ANN001, ARG001, ANN202
        return FakeRealLlmClient(
            [_tool_turn("read_file", {"path": "notes.txt"}), _text_turn("CLI-SMOKE read")]
        )

    monkeypatch.setattr(real_module, "create_llm_client", fake_factory)

    result = runner.invoke(
        cli.app,
        [
            "eval",
            "run",
            "--real",
            "--repeat",
            "2",
            "--file",
            str(scenario_dir),
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PASS\treal-smoke\t[run 1/2]" in result.output
    assert "total_runs=2 passed=2 failed=0" in result.output
    report_path = tmp_path / REAL_REPORT_RELATIVE
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["track"] == "real"
    assert report["summary"]["total_runs"] == 2
    assert report["summary"]["passed"] == 2
    assert report["summary"]["failed"] == 0
    assert report["summary"]["scenarios_all_pass"] == 1
    assert report["scenarios"][0]["pass_k"]["all_pass"] is True
    assert report["scenarios"][0]["runs"][0]["usage"]["total_tokens"] > 0
    # The hermetic baseline file is never touched by a --real run.
    assert not (tmp_path / REPORT_RELATIVE).exists()


def test_cli_real_with_bad_schema_file_exits_cleanly(tmp_path):
    bad = _write_real_scenario(tmp_path, {"name": "broken", "scripted_turns": [{"text": "x"}]})

    result = runner.invoke(
        cli.app, ["eval", "run", "--real", "--file", str(bad), "--cwd", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert str(bad) in result.output
    assert "real-track" in result.output


def test_cli_hermetic_run_still_writes_hermetic_report(tmp_path):
    """The hermetic track's own behavior is unchanged next to the new flags."""
    result = runner.invoke(cli.app, ["eval", "run", "--cwd", str(tmp_path)])

    assert result.exit_code == 0
    assert (tmp_path / REPORT_RELATIVE).is_file()
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()


def test_real_report_roundtrip_matches_writer(tmp_path):
    report = build_real_report([RealEvalResult(name="s", passed=True)], repeat=1)
    path = write_real_report(tmp_path, report)

    assert json.loads(path.read_text(encoding="utf-8")) == report
