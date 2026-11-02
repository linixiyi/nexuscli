"""Tests for the baseline ablation (``nexuscli eval run --real --baseline``).

Everything stays hermetic: the LLM client is injected through the same
``llm_client_factory`` / ``create_llm_client`` monkeypatch seam the real track
uses, and the judge is injected as a callable. The fake client dispatches on
the system prompt each surface sends — the bare arm's is
``BASELINE_SYSTEM_PROMPT``, the judge's is the shared judge prompt, and the
agent's is the harness prompt — so one factory can drive both arms of the
comparison and the tests can assert what each surface really saw (the bare
arm must get an empty tool list, no skill candidates and no harness prompt).

Covers:
- the bare-model arm: single direct completion, no tools / skills / harness
  prompt, usage + cost collected, only the text-only assertion subset judged
  (tool-sequence class and max_turns are not), the scenario's judge reused
  verbatim (same rubric / threshold / model override, judge usage folded in),
  model errors fail the run;
- the comparison: both sides computed, per-scenario deltas, four-quadrant
  counts, the N/A marking for tool-class assertions, and the fact that a
  comparison run itself writes nothing (persistence is the CLI's job);
- the CLI: --baseline writes the comparison to its own file while
  last-real-report.json keeps its exact schema and agent-side-only content,
  the no-key error matches --real, --repeat + --baseline is rejected, and
  --baseline outside the real track is rejected.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import nexuscli.entrypoints.cli as cli
import nexuscli.eval.real as real_module
from nexuscli.eval import REAL_REPORT_RELATIVE, RealTrackError, load_real_eval_scenario
from nexuscli.eval.real import (
    BASELINE_SYSTEM_PROMPT,
    REAL_COMPARISON_RELATIVE,
    RealEvalResult,
    _load_real_config,
    build_real_comparison,
    run_baseline_comparison,
    run_baseline_scenario,
    write_real_comparison,
)
from nexuscli.eval.reporter import REPORT_RELATIVE
from nexuscli.types import Usage

runner = CliRunner()

_JUDGE_SYSTEM_PREFIX = "You are an evaluation judge"


def _write_real_scenario(directory: Path, data: dict, filename: str = "scenario.json") -> Path:
    path = directory / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _FakeCost:
    def __init__(self, total: float) -> None:
        self._total = total

    def to_dict(self) -> dict:
        return {"total_cost": self._total}


class _AblationFakeClient:
    """Scripted stand-in that answers per surface: baseline / judge / agent.

    The surfaces are told apart by the system prompt each one sends. Every
    call is recorded (tools, system prompt, messages) so tests can assert the
    bare arm really received an empty tool list and the raw prompt.
    """

    model_name = "fake-ablation-model"
    provider_name = "fake-ablation"
    max_context_window = 100_000

    def __init__(
        self,
        *,
        agent_text: str = "",
        baseline_text: str = "",
        judge_reply: str = "",
        agent_usage: dict | None = None,
        baseline_usage: dict | None = None,
        judge_usage: dict | None = None,
        agent_error: str | None = None,
        baseline_error: str | None = None,
    ) -> None:
        self._texts = {
            "agent": agent_text,
            "baseline": baseline_text,
            "judge": judge_reply,
        }
        self._usages = {
            "agent": agent_usage,
            "baseline": baseline_usage,
            "judge": judge_usage,
        }
        self._errors = {"agent": agent_error, "baseline": baseline_error}
        self.calls: list[dict] = []

    def calculate_cost(self, usage, *, currency: str = "usd"):  # noqa: ANN001, ANN202
        if currency != "usd":
            raise ValueError("no price profile for currency")
        return _FakeCost(round(0.001 * usage.total_tokens, 6))

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ANN001, ANN202
        if system_prompt == BASELINE_SYSTEM_PROMPT:
            surface = "baseline"
        elif system_prompt.startswith(_JUDGE_SYSTEM_PREFIX):
            surface = "judge"
        else:
            surface = "agent"
        self.calls.append(
            {
                "surface": surface,
                "tools": [tool["function"]["name"] for tool in tools],
                "system_prompt": system_prompt,
                "messages": list(messages),
            }
        )
        error = self._errors.get(surface)
        if error is not None:
            yield {"type": "error", "error": RuntimeError(error)}
            return
        text = self._texts[surface]
        if text:
            yield {"type": "text_delta", "text": text}
        usage = self._usages[surface]
        if usage is not None:
            yield {"type": "usage", "usage": dict(usage)}
        yield {"type": "message_end", "stop_reason": "end_turn"}


# ---------------------------------------------------------------------------
# The bare-model arm (run_baseline_scenario)
# ---------------------------------------------------------------------------


def test_baseline_single_completion_no_tools_no_harness_context(tmp_path):
    """The bare arm is one direct completion: no tools, no skills, no prompt."""
    scenario = _write_real_scenario(
        tmp_path,
        {
            "name": "bare-arm",
            "prompt": "Answer with the marker BASELINE-DIRECT-OK.",
            "assertions": {"final_contains": ["BASELINE-DIRECT-OK"]},
        },
    )
    clients: list[_AblationFakeClient] = []

    def factory(llm_config):  # noqa: ANN001, ARG001, ANN202
        client = _AblationFakeClient(
            baseline_text="BASELINE-DIRECT-OK straight answer",
            baseline_usage={"input_tokens": 10, "output_tokens": 5},
        )
        clients.append(client)
        return client

    result = asyncio.run(
        run_baseline_scenario(
            load_real_eval_scenario(scenario),
            config=_load_real_config(str(tmp_path)),
            llm_client_factory=factory,
        )
    )

    assert result.passed, result.failure_reasons
    assert result.final_text == "BASELINE-DIRECT-OK straight answer"
    assert result.source == str(scenario)
    # Exactly ONE LLM call, with the minimal system prompt, an empty tool
    # list and the raw scenario prompt (no skill candidates appended).
    assert len(clients) == 1
    assert len(clients[0].calls) == 1
    call = clients[0].calls[0]
    assert call["tools"] == []
    assert call["system_prompt"] == BASELINE_SYSTEM_PROMPT
    assert [str(message.content) for message in call["messages"]] == [
        "Answer with the marker BASELINE-DIRECT-OK."
    ]
    # Single completion: one turn, usage and cost collected from the stream.
    assert result.turns == 1
    assert result.tool_sequence == []
    assert result.usage.total_tokens == 15
    assert result.cost["usd"]["total_cost"] == pytest.approx(0.001 * 15)


def test_baseline_judges_only_the_text_subset_tool_class_not_judged(tmp_path):
    """Tool-trajectory assertions are not evaluated on the bare arm.

    The scenario declares every tool-class assertion; the bare model calls no
    tool and still passes on the text alone. Missing the marker is the only
    failure mode, worded exactly like the shared evaluator's.
    """
    data = {
        "name": "subset-only",
        "prompt": "Answer with the marker SUBSET-OK.",
        "assertions": {
            "max_turns": 2,
            "expected_tool_sequence": ["read_file"],
            "required_tools": ["read_file"],
            "forbidden_tools": ["bash"],
            "max_tool_calls": {"read_file": 1},
            "max_total_tool_calls": 2,
            "final_contains": ["SUBSET-OK"],
        },
    }
    scenario = load_real_eval_scenario(_write_real_scenario(tmp_path, data))
    config = _load_real_config(str(tmp_path))

    def factory(llm_config):  # noqa: ANN001, ARG001, ANN202
        return _AblationFakeClient(baseline_text="SUBSET-OK")

    passing = asyncio.run(
        run_baseline_scenario(scenario, config=config, llm_client_factory=factory)
    )
    assert passing.passed, "tool-class assertions must not fail the bare arm"
    assert passing.failure_reasons == []

    failing = asyncio.run(
        run_baseline_scenario(
            scenario,
            config=config,
            llm_client_factory=lambda llm_config: _AblationFakeClient(
                baseline_text="some other answer"
            ),
        )
    )
    assert not failing.passed
    assert failing.failure_reasons == ["final text does not contain: 'SUBSET-OK'"]


def test_baseline_reuses_the_scenario_judge_object_verbatim(tmp_path):
    """Both arms judge with the SAME judge spec: one ruler for the ablation."""
    scenario = load_real_eval_scenario(
        _write_real_scenario(
            tmp_path,
            {
                "name": "judged-bare",
                "prompt": "Answer the question.",
                "judge": {"rubric": "cites the marker", "threshold": 0.55, "model": "glm-5.2"},
            },
        )
    )
    seen: list[tuple[str, object]] = []

    async def spy_judge(final_text, spec):  # noqa: ANN001, ANN202
        seen.append((final_text, spec))
        return 0.9, "cites the marker"

    clients: list[_AblationFakeClient] = []

    def factory(llm_config):  # noqa: ANN001, ARG001, ANN202
        client = _AblationFakeClient(
            agent_text="AGENT-SIDE-ANSWER", baseline_text="BARE-SIDE-ANSWER"
        )
        clients.append(client)
        return client

    run = run_baseline_comparison(
        [scenario],
        root=str(tmp_path),
        llm_client_factory=factory,
        judge=spy_judge,
    )

    # Called once per arm, with each arm's own final text, and the exact same
    # judge spec object — same rubric, same threshold, same model override.
    assert [text for text, _ in seen] == ["AGENT-SIDE-ANSWER", "BARE-SIDE-ANSWER"]
    assert seen[0][1] is seen[1][1] is scenario.judge
    for side in (run.agent_results[0], run.baseline_results[0]):
        assert side.judge == {
            "score": 0.9,
            "threshold": 0.55,
            "passed": True,
            "explanation": "cites the marker",
            "model": "glm-5.2",
        }
        assert side.passed


def test_baseline_default_judge_keeps_rubric_threshold_and_model_override(tmp_path):
    """judge=None: the default LLM judge rides the same factory, per spec.

    The scenario's judge.model overrides the judge client's model on both
    arms, the rubric reaches the judge prompt, the threshold decides, and the
    judge call's usage is folded into the bare arm's usage total.
    """
    scenario = load_real_eval_scenario(
        _write_real_scenario(
            tmp_path,
            {
                "name": "judged-bare-default",
                "prompt": "Answer the question.",
                "judge": {"rubric": "cites the marker", "threshold": 0.55, "model": "judge-x"},
            },
        )
    )
    clients: list[_AblationFakeClient] = []

    def factory(llm_config):  # noqa: ANN001, ANN202
        client = _AblationFakeClient(
            agent_text="AGENT-SIDE-ANSWER",
            baseline_text="BARE-SIDE-ANSWER",
            agent_usage={"input_tokens": 10, "output_tokens": 5},
            baseline_usage={"input_tokens": 10, "output_tokens": 5},
            judge_reply="SCORE: 0.60\nREASON: cites the marker",
            judge_usage={"input_tokens": 100, "output_tokens": 50},
        )
        clients.append(client)
        return client

    run = run_baseline_comparison([scenario], root=str(tmp_path), llm_client_factory=factory)

    bare = run.baseline_results[0]
    assert bare.passed, bare.failure_reasons
    assert bare.judge == {
        "score": 0.6,
        "threshold": 0.55,
        "passed": True,
        "explanation": "cites the marker",
        "model": "judge-x",
    }
    judge_clients = [client for client in clients if client.calls[0]["surface"] == "judge"]
    assert len(judge_clients) == 2, "one judge call per arm"
    for client in judge_clients:
        call = client.calls[0]
        judge_message = str(call["messages"][-1].content)
        assert "- cites the marker" in judge_message, "same rubric reaches the judge"
    # Judge usage folded into the bare arm's total: run 10/5 + judge 100/50.
    assert bare.usage.input_tokens == 110
    assert bare.usage.output_tokens == 55


def test_baseline_model_error_fails_the_run(tmp_path):
    scenario = load_real_eval_scenario(
        _write_real_scenario(tmp_path, {"name": "errored", "prompt": "hello"})
    )

    result = asyncio.run(
        run_baseline_scenario(
            scenario,
            config=_load_real_config(str(tmp_path)),
            llm_client_factory=lambda llm_config: _AblationFakeClient(
                baseline_error="provider exploded"
            ),
        )
    )

    assert not result.passed
    assert any("model errored: provider exploded" in reason for reason in result.failure_reasons)


# ---------------------------------------------------------------------------
# The comparison (run_baseline_comparison / build_real_comparison)
# ---------------------------------------------------------------------------


def test_comparison_computes_both_sides_with_deltas(tmp_path):
    """One scenario, both arms: statuses, tokens and the agent-minus-baseline
    deltas land per scenario; the agent arm really drives the tool harness."""
    scenario = load_real_eval_scenario(
        _write_real_scenario(
            tmp_path,
            {
                "name": "ablation-pair",
                "prompt": "Answer with the marker PAIR-OK.",
                "assertions": {"max_turns": 4, "final_contains": ["PAIR-OK"]},
            },
        )
    )
    clients: list[_AblationFakeClient] = []

    def factory(llm_config):  # noqa: ANN001, ARG001, ANN202
        client = _AblationFakeClient(
            agent_text="PAIR-OK from the harness",
            baseline_text="PAIR-OK from the bare model",
            agent_usage={"input_tokens": 20, "output_tokens": 10},
            baseline_usage={"input_tokens": 5, "output_tokens": 5},
        )
        clients.append(client)
        return client

    run = run_baseline_comparison([scenario], root=str(tmp_path), llm_client_factory=factory)

    assert [result.name for result in run.agent_results] == ["ablation-pair"]
    assert [result.name for result in run.baseline_results] == ["ablation-pair"]
    assert all(result.passed for result in run.agent_results + run.baseline_results)
    # Arm 1 talked to the real registry (non-empty tool list, harness prompt);
    # arm 2 got the empty tool list and the bare prompt.
    agent_call = clients[0].calls[0]
    baseline_call = clients[1].calls[0]
    assert agent_call["tools"] != []
    assert agent_call["system_prompt"] != BASELINE_SYSTEM_PROMPT
    assert baseline_call["tools"] == []
    assert baseline_call["system_prompt"] == BASELINE_SYSTEM_PROMPT
    (entry,) = run.comparison["scenarios"]
    assert entry == {
        "id": "ablation-pair",
        "agent": {"status": "pass", "tokens": 30},
        "baseline": {"status": "pass", "tokens": 10},
        "delta": {"pass_delta": 0, "token_delta": 20},
    }


def test_comparison_quadrant_counts_and_summary_totals(tmp_path):
    """Four scenarios, one per quadrant, with per-side token totals."""
    markers = {
        "QUAD-BOTH": ("pass", "pass"),
        "QUAD-AGENT": ("pass", "fail"),
        "QUAD-BASE": ("fail", "pass"),
        "QUAD-NEITHER": ("fail", "fail"),
    }
    agent_pass = {"QUAD-BOTH", "QUAD-AGENT"}
    baseline_pass = {"QUAD-BOTH", "QUAD-BASE"}
    scenarios = []
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    for index, (marker, _) in enumerate(markers.items()):
        scenarios.append(
            _write_real_scenario(
                scenario_dir,
                {
                    "name": marker.lower(),
                    "prompt": f"Answer with the marker {marker}.",
                    "assertions": {"final_contains": [marker]},
                },
                filename=f"{index:02d}_{marker.lower()}.json",
            )
        )
    loaded = [load_real_eval_scenario(path) for path in scenarios]

    class _QuadrantFakeClient:
        model_name = "fake-quad-model"
        provider_name = "fake-quad"
        max_context_window = 100_000

        async def chat(self, messages, tools, *, system_prompt):  # noqa: ANN001, ANN202
            blob = " ".join(str(getattr(message, "content", "")) for message in messages)
            marker = next((m for m in markers if m in blob), "")
            if system_prompt == BASELINE_SYSTEM_PROMPT:
                reply = marker if marker in baseline_pass else "cannot answer"
                usage = {"input_tokens": 4, "output_tokens": 2}
            elif system_prompt.startswith(_JUDGE_SYSTEM_PREFIX):
                reply = "SCORE: 1.0\nREASON: ok"
                usage = {"input_tokens": 0, "output_tokens": 0}
            else:
                reply = marker if marker in agent_pass else "cannot answer"
                usage = {"input_tokens": 10, "output_tokens": 5}
            if reply:
                yield {"type": "text_delta", "text": reply}
            yield {"type": "usage", "usage": dict(usage)}
            yield {"type": "message_end", "stop_reason": "end_turn"}

    run = run_baseline_comparison(
        loaded, root=str(tmp_path), llm_client_factory=lambda llm_config: _QuadrantFakeClient()
    )

    comparison = run.comparison
    statuses = {
        entry["id"]: (entry["agent"]["status"], entry["baseline"]["status"])
        for entry in comparison["scenarios"]
    }
    assert statuses == {key.lower(): value for key, value in markers.items()}
    assert all(entry["delta"]["token_delta"] == 9 for entry in comparison["scenarios"])
    summary = comparison["summary"]
    assert summary["scenarios_total"] == 4
    assert summary["agent_pass"] == 2
    assert summary["baseline_pass"] == 2
    assert summary["pass_delta"] == 0
    assert summary["agent_pass_rate"] == 0.5
    assert summary["baseline_pass_rate"] == 0.5
    assert summary["quadrants"] == {
        "both_pass": 1,
        "agent_only": 1,
        "baseline_only": 1,
        "both_fail": 1,
    }
    assert summary["total_tokens"] == {"agent": 60, "baseline": 24}


def test_comparison_run_itself_writes_no_files(tmp_path):
    """The bare-model arm stays in memory until the CLI persists each file."""
    scenario = load_real_eval_scenario(
        _write_real_scenario(tmp_path, {"name": "pure", "prompt": "hello"})
    )

    run = run_baseline_comparison(
        [scenario],
        root=str(tmp_path),
        llm_client_factory=lambda llm_config: _AblationFakeClient(
            agent_text="agent reply", baseline_text="bare reply"
        ),
    )

    assert run.comparison["summary"]["scenarios_total"] == 1
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()
    assert not (tmp_path / REPORT_RELATIVE).exists()


def test_comparison_document_marks_tool_assertions_na(tmp_path):
    scenario = load_real_eval_scenario(
        _write_real_scenario(
            tmp_path,
            {
                "name": "na-marked",
                "prompt": "Answer with the marker NA-OK.",
                "assertions": {"required_tools": ["read_file"], "final_contains": ["NA-OK"]},
            },
        )
    )

    run = run_baseline_comparison(
        [scenario],
        root=str(tmp_path),
        llm_client_factory=lambda llm_config: _AblationFakeClient(
            agent_text="NA-OK", baseline_text="NA-OK"
        ),
    )

    marked = run.comparison["baseline"]["assertions"]
    assert marked["evaluated"] == ["final_contains", "judge"]
    assert set(marked["not_applicable"]) == {
        "expected_tool_sequence",
        "forbidden_tools",
        "required_tools",
        "max_tool_calls",
        "max_total_tool_calls",
        "max_turns",
    }
    assert run.comparison["baseline"]["system_prompt"] == BASELINE_SYSTEM_PROMPT


def test_build_real_comparison_pairs_positionally_and_counts_quadrants():
    """Pure assembly: positional pairing, quadrant counts, rates and deltas."""

    def _result(name, passed, *, tokens=0):
        return RealEvalResult(
            name=name,
            passed=passed,
            failure_reasons=[] if passed else ["boom"],
            usage=Usage(total_tokens=tokens),
        )

    doc = build_real_comparison(
        [
            _result("a", True, tokens=30),
            _result("b", False, tokens=20),
        ],
        [
            _result("a", False, tokens=10),
            _result("b", False, tokens=20),
        ],
    )

    assert doc["track"] == "real-baseline-comparison"
    assert doc["scenarios"] == [
        {
            "id": "a",
            "agent": {"status": "pass", "tokens": 30},
            "baseline": {"status": "fail", "tokens": 10},
            "delta": {"pass_delta": 1, "token_delta": 20},
        },
        {
            "id": "b",
            "agent": {"status": "fail", "tokens": 20},
            "baseline": {"status": "fail", "tokens": 20},
            "delta": {"pass_delta": 0, "token_delta": 0},
        },
    ]
    assert doc["summary"] == {
        "scenarios_total": 2,
        "agent_pass": 1,
        "baseline_pass": 0,
        "pass_delta": 1,
        "agent_pass_rate": 0.5,
        "baseline_pass_rate": 0.0,
        "quadrants": {
            "both_pass": 0,
            "agent_only": 1,
            "baseline_only": 0,
            "both_fail": 1,
        },
        "total_tokens": {"agent": 50, "baseline": 30},
    }


def test_build_real_comparison_pairs_own_baseline_despite_duplicate_names():
    """Duplicate scenario names pair positionally, never to one shared entry.

    The loader rejects duplicate tool names but not duplicate scenario names
    across a --file directory, so both arms can carry two results with the
    same name. Each agent result must be judged against its OWN baseline
    run: with name-keyed pairing both agent results mapped to the last
    baseline entry, double-counting its tokens/pass in the summary and the
    quadrants.
    """
    shared_name = "duplicated-scenario"

    def _result(name, passed, *, tokens=0):
        return RealEvalResult(
            name=name,
            passed=passed,
            failure_reasons=[] if passed else ["boom"],
            usage=Usage(total_tokens=tokens),
        )

    doc = build_real_comparison(
        [
            _result(shared_name, True, tokens=30),  # first file: agent passed
            _result(shared_name, False, tokens=20),  # second file: agent failed
        ],
        [
            _result(shared_name, False, tokens=10),  # first file: bare model failed
            _result(shared_name, True, tokens=8),  # second file: bare model passed
        ],
    )

    assert [
        (entry["agent"]["tokens"], entry["baseline"]["tokens"]) for entry in doc["scenarios"]
    ] == [
        (30, 10),
        (20, 8),
    ]
    assert doc["summary"] == {
        "scenarios_total": 2,
        "agent_pass": 1,
        "baseline_pass": 1,
        "pass_delta": 0,
        "agent_pass_rate": 0.5,
        "baseline_pass_rate": 0.5,
        "quadrants": {
            "both_pass": 0,
            "agent_only": 1,
            "baseline_only": 1,
            "both_fail": 0,
        },
        "total_tokens": {"agent": 50, "baseline": 18},
    }


def test_build_real_comparison_length_mismatch_fails_loudly():
    """An arm missing results is a caller bug, not a silent truncation."""
    with pytest.raises(ValueError, match="zip"):
        build_real_comparison(
            [RealEvalResult(name="a", passed=True), RealEvalResult(name="b", passed=True)],
            [RealEvalResult(name="a", passed=False)],
        )


def test_write_real_comparison_lands_in_its_own_file(tmp_path):
    comparison = build_real_comparison(
        [RealEvalResult(name="s", passed=True)], [RealEvalResult(name="s", passed=False)]
    )

    path = write_real_comparison(tmp_path, comparison)

    assert path == tmp_path / REAL_COMPARISON_RELATIVE
    assert path != tmp_path / REAL_REPORT_RELATIVE
    assert json.loads(path.read_text(encoding="utf-8")) == comparison


def test_comparison_without_key_raises_like_the_real_track(tmp_path, monkeypatch):
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
    scenario = load_real_eval_scenario(
        _write_real_scenario(tmp_path, {"name": "no-key", "prompt": "hello"})
    )

    with pytest.raises(RealTrackError) as excinfo:
        run_baseline_comparison([scenario], root=str(tmp_path))

    message = str(excinfo.value)
    assert "no LLM API key" in message
    assert "NEXUSCLI_API_KEY" in message
    assert "eval run --real" in message
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()


# ---------------------------------------------------------------------------
# CLI — --baseline flag wiring, file separation, combination guards
# ---------------------------------------------------------------------------


def test_cli_baseline_keeps_report_schema_and_writes_comparison_separately(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key-eval")
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_real_scenario(
        scenario_dir,
        {
            "name": "real-smoke",
            "prompt": "Answer with the marker CLI-ABLATION-OK.",
            "assertions": {"max_turns": 4, "final_contains": ["CLI-ABLATION-OK"]},
        },
    )
    monkeypatch.setattr(
        real_module,
        "create_llm_client",
        lambda llm_config, **_kwargs: _AblationFakeClient(
            agent_text="CLI-ABLATION-OK from the harness",
            baseline_text="CLI-ABLATION-OK from the bare model",
            agent_usage={"input_tokens": 20, "output_tokens": 10},
            baseline_usage={"input_tokens": 5, "output_tokens": 5},
        ),
    )

    result = runner.invoke(
        cli.app,
        [
            "eval",
            "run",
            "--real",
            "--baseline",
            "--file",
            str(scenario_dir),
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PASS\treal-smoke\t[run 1/1]" in result.output
    assert "agent=PASS baseline=PASS" in result.output
    assert "Comparison written to" in result.output

    # The real-track report keeps its exact schema and stays agent-side only:
    # one run, no baseline data anywhere in the document.
    report = json.loads((tmp_path / REAL_REPORT_RELATIVE).read_text(encoding="utf-8"))
    assert set(report) == {"generated_at", "track", "repeat", "scenarios", "summary"}
    assert report["track"] == "real"
    assert report["summary"]["total_runs"] == 1, "baseline runs must not be folded in"
    (scenario_entry,) = report["scenarios"]
    assert set(scenario_entry) == {"name", "source", "pass_k", "cost", "runs"}
    (run_entry,) = scenario_entry["runs"]
    assert set(run_entry) == {
        "index",
        "status",
        "failure_reasons",
        "tool_sequence",
        "turns",
        "usage",
        "cost",
        "judge",
    }

    def _all_keys(node):  # noqa: ANN001, ANN202
        if isinstance(node, dict):
            return set(node) | {key for value in node.values() for key in _all_keys(value)}
        if isinstance(node, list):
            return {key for item in node for key in _all_keys(item)}
        return set()

    assert not {key for key in _all_keys(report) if "baseline" in key}, (
        "no baseline key may leak into the real-track report"
    )

    # The comparison lands in its own file with both sides and the quadrants.
    comparison = json.loads((tmp_path / REAL_COMPARISON_RELATIVE).read_text(encoding="utf-8"))
    assert comparison["track"] == "real-baseline-comparison"
    assert comparison["summary"]["quadrants"]["both_pass"] == 1
    assert comparison["summary"]["agent_pass"] == 1
    assert comparison["summary"]["baseline_pass"] == 1
    (comparison_entry,) = comparison["scenarios"]
    assert comparison_entry["agent"]["tokens"] > comparison_entry["baseline"]["tokens"]
    # The hermetic baseline file is never touched.
    assert not (tmp_path / REPORT_RELATIVE).exists()


def test_cli_baseline_without_key_fails_before_anything_runs(tmp_path, monkeypatch):
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
    scenario_dir = tmp_path / "scenarios"
    scenario_dir.mkdir()
    _write_real_scenario(scenario_dir, {"name": "real-smoke", "prompt": "hello"})

    result = runner.invoke(
        cli.app,
        [
            "eval",
            "run",
            "--real",
            "--baseline",
            "--file",
            str(scenario_dir),
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "no LLM API key" in result.output
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()


def test_cli_baseline_rejects_repeat_combination(tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "eval",
            "run",
            "--real",
            "--baseline",
            "--repeat",
            "2",
            "--cwd",
            str(tmp_path),
        ],
    )

    assert result.exit_code != 0
    assert "--repeat" in result.output
    assert "--baseline" in result.output
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()


def test_cli_baseline_requires_the_real_track(tmp_path):
    result = runner.invoke(cli.app, ["eval", "run", "--baseline", "--cwd", str(tmp_path)])

    assert result.exit_code != 0
    assert "--real" in result.output
    assert not (tmp_path / REAL_REPORT_RELATIVE).exists()
    assert not (tmp_path / REAL_COMPARISON_RELATIVE).exists()
