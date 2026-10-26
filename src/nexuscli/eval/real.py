"""real.py — The real-model eval track: a real LLM drives real builtin tools.

``nexuscli eval run --real`` runs each real-track scenario against a real LLM
built through the normal config chain (``load_config`` → ``create_llm_client``),
so token usage is real and the model under test is the one the user actually
runs. Unattended safety comes from three stacked layers:

- **Read-only surface**: the registry mounts the real builtin tools, but every
  tool outside the curated read-only subset (:data:`REAL_TRACK_TOOLS`) gets a
  computed ``permissions.deny`` rule — deny beats ask/allow, so even a user
  config that allows ``write_file`` cannot punch through. Denials go through
  the real gate chain and are audited.
- **Fail-closed approval** (same semantics as ``-p`` without ``--yes``): there
  is no approval callback, so anything that still reaches the approval gate
  (``requires_approval`` tools, ``ask`` rules) is denied, and the denial is
  audited too.
- **Audit on**: ``features.audit_log`` is pinned on for the track regardless
  of the user config, so every non-read-only call attempt leaves a trail.

Deliberately not hermetic (that is the hermetic track's job): network calls
happen, token usage is collected from the agent's ``usage`` events onto the
result, and the report is written to
``.nexuscli/evals/last-real-report.json`` — never to the hermetic baseline
``last-report.json``, and with no regression diff (nightly real-track
comparisons are read by a human, not diffed).

Tests stay hermetic: the LLM client is injectable (``llm_client_factory`` —
a constructor-parameter seam; monkeypatching
``nexuscli.eval.real.create_llm_client`` is the equivalent surface), and the
judge is injectable the same way. Real network traffic only happens on an
explicit ``--real`` run.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.eval.loader import RealEvalScenario, RealJudgeSpec
from nexuscli.eval.runner import (
    _CAP_HEADROOM,
    _DEFAULT_TURN_BUDGET,
    _assertion_failures,
    _final_assistant_text,
    _is_termination_warning,
)
from nexuscli.llm.factory import create_llm_client
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, Usage

# The real track's report file — deliberately a different file from the
# hermetic baseline last-report.json so a --real run can never touch the
# regression baseline (and vice versa).
REAL_REPORT_RELATIVE = Path(".nexuscli") / "evals" / "last-real-report.json"

# The curated read-only builtin subset the real track allows to EXECUTE.
# Curated by name, not derived from Tool.is_read_only: the flag is the repo's
# own classification and it is too loose for unattended runs. Known traps:
# - ``todo_write`` carries the default ``is_read_only=True`` while writing
#   ``.nexuscli/todo.json``;
# - ``search_memory`` is flagged read-only, but its ``MemoryManager.recall``
#   runs with the default ``mark_access=True`` — every call UPDATEs
#   ``access_count`` on the user's real memory DB and runs
#   ``_purge_expired`` (a DELETE of expired rows) — user-state writes;
# - ``load_skill`` is flagged read-only, but with the Agent's always-truthy
#   ``skill_context_buffer`` it calls ``SkillStateStore().record_use``,
#   writing usage counters to ``~/.nexuscli/skills/usage.json``.
# All three stay OUT of this allowlist and are deny-ruled (fail-closed +
# audited, see _load_real_config) so the track's "unattended runs never
# write" promise is literal. web_search/web_fetch are network reads;
# search_code/task_output/todo_read only read.
REAL_TRACK_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_dir",
        "glob",
        "glob_files",
        "grep",
        "grep_code",
        "directory_tree",
        "get_file_info",
        "web_search",
        "web_fetch",
        "search_code",
        "task_output",
        "todo_read",
    }
)


class RealTrackError(RuntimeError):
    """Raised when the real track cannot start (no key, unusable provider).

    The CLI turns it into a non-zero exit with an actionable message — the
    real track never silently skips a scenario for a missing credential.
    """


# The judge seam: scores a run's final answer against the scenario's judge
# spec, returning (score in [0, 1], human-readable explanation). ``None``
# means the default LLM-backed judge (:func:`_default_judge`), which calls the
# SAME client factory as the run itself — so tests can inject a fake judge here
# and real runs get the configured provider/model (or the scenario's judge
# model override) with no extra wiring.
RealJudge = Callable[[str, RealJudgeSpec], Awaitable[tuple[float, str]]]

# The default judge asks for a strict two-line reply so the score parses
# deterministically; an unparsable reply scores 0.0 (fail-closed) and carries
# the raw excerpt as the explanation.
_JUDGE_SYSTEM_PROMPT = (
    "You are an evaluation judge. Score the candidate answer against the "
    "rubric. Reply with exactly two lines and nothing else:\n"
    "SCORE: <a number between 0 and 1>\n"
    "REASON: <one short sentence citing the rubric evidence>"
)

# Both ASCII and full-width colons are accepted: a rubric written in Chinese
# often gets a Chinese reply.
_SCORE_RE = re.compile(r"score\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_REASON_RE = re.compile(r"reason\s*[:：]\s*(.+)", re.IGNORECASE)


# The failure reason appended when a run's react loop hit its turn cap or the
# loop guard aborted: a real-track scenario must converge within its own
# budget, and an unconverged run would otherwise look like a clean pass with
# truncated output. Worded for the real track (no "scripted sequence" here).
_TERMINATION_REASON = (
    "react loop terminated early: the scenario did not converge within its "
    "turn budget (cap the model with assertions.max_turns)"
)


@dataclass(slots=True)
class RealEvalResult:
    """What one real-track run (one scenario, one repetition) produced.

    ``repetition`` is the 1-based repeat index (``--repeat k`` runs each
    scenario k times, serially, with a fresh LLM client per run).
    """

    name: str
    passed: bool
    repetition: int = 1
    failure_reasons: list[str] = field(default_factory=list)
    tool_sequence: list[str] = field(default_factory=list)
    turns: int = 0
    final_text: str = ""
    usage: Usage = field(default_factory=Usage)
    cost: dict[str, Any] = field(default_factory=dict)
    judge: dict[str, Any] | None = None
    source: str = ""


def run_real_scenarios(
    scenarios: list[RealEvalScenario],
    *,
    root: str,
    repeat: int = 1,
    llm_client_factory: Callable[[Any], Any] | None = None,
    judge: RealJudge | None = None,
) -> list[RealEvalResult]:
    """Run every scenario ``repeat`` times, serially; one result per run.

    The config is loaded once through the normal chain and enforced for the
    real track (audit on, read-only deny rules, fail-closed approval); each
    run gets a deep copy so agents cannot bleed state into each other. With
    ``llm_client_factory=None`` the real client factory is used and the
    credential/provider is validated up front — a missing API key raises
    :class:`RealTrackError` before any scenario starts (no silent skips, no
    partial runs).
    """
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")
    config = _load_real_config(root)
    if llm_client_factory is None:
        llm_client_factory = _default_llm_factory(config, root=root)
    elif not config.llm.api_key:
        # An injected client owns the real credentials (tests): keep Agent's
        # not-empty validation happy the same way the hermetic runner does.
        # Not a real secret; do not treat this value as one.
        config.llm.api_key = "".join(("offline", "-", "eval", "-placeholder"))
    results: list[RealEvalResult] = []
    for scenario in scenarios:
        for repetition in range(1, repeat + 1):
            results.append(
                asyncio.run(
                    run_real_scenario(
                        scenario,
                        config=config,
                        root=root,
                        repetition=repetition,
                        llm_client_factory=llm_client_factory,
                        judge=judge,
                    )
                )
            )
    return results


async def run_real_scenario(
    scenario: RealEvalScenario,
    *,
    config: NexusCliConfig,
    root: str,
    repetition: int = 1,
    llm_client_factory: Callable[[Any], Any],
    judge: RealJudge | None = None,
) -> RealEvalResult:
    """Run one scenario once against a real (or injected) LLM and judge it.

    ``judge=None`` selects the default LLM-backed judge built on the same
    client factory; the judge's own token usage is folded into the run's
    usage total (injected fake judges contribute none — expected in tests).
    """
    result = RealEvalResult(name=scenario.name, passed=False, repetition=repetition)
    result.source = scenario.source
    tool_sequence: list[str] = []
    texts: list[str] = []
    failure_reasons: list[str] = []
    total_usage = Usage()

    llm_client = llm_client_factory(config.llm)
    agent = Agent(
        llm_client=llm_client,
        tool_registry=_real_registry(),
        config=copy.deepcopy(config),
        cwd=root,
        max_turns=(scenario.assertions.max_turns or _DEFAULT_TURN_BUDGET) + _CAP_HEADROOM,
    )
    async for event in agent.run(scenario.prompt):
        event_type = event.get("type")
        if event_type == "tool_call":
            tool_sequence.append(str(event.get("name") or ""))
        elif event_type == "text_delta":
            texts.append(str(event.get("text") or ""))
        elif event_type == "usage":
            total_usage = total_usage + Usage.from_mapping(event.get("usage") or {})
        elif event_type == "warning" and _is_termination_warning(event):
            failure_reasons.append(_TERMINATION_REASON)
        elif event_type == "error":
            failure_reasons.append(f"agent errored: {event.get('error')}")
        elif event_type == "done":
            result.turns += int(event.get("total_turns") or 0)
            result.final_text = _final_assistant_text(event.get("messages"), fallback=texts)

    judge_usage: list[Usage] = []
    if scenario.judge is not None and judge is None:
        judge = _default_judge(llm_client_factory, config, judge_usage)
    judge_reasons, judge_detail = await _judge_verdict(scenario, result.final_text, judge)
    for extra in judge_usage:
        total_usage = total_usage + extra

    result.tool_sequence = tool_sequence
    result.usage = total_usage
    # Cost is priced with the run's own client profile; judge tokens are
    # included in the usage total, so a judge on a different model is priced
    # approximately (the token counts stay exact).
    result.cost = _calculate_costs(llm_client, total_usage)
    result.judge = judge_detail
    result.failure_reasons = failure_reasons + _assertion_failures(scenario, result) + judge_reasons
    result.passed = not result.failure_reasons
    return result


async def _judge_verdict(
    scenario: RealEvalScenario, final_text: str, judge: RealJudge | None
) -> tuple[list[str], dict[str, Any] | None]:
    """The judge verdict: failure reasons plus the report's structured detail.

    A declared judge is never skipped. ``judge=None`` with no declared judge
    is the common no-judge case and yields no verdict at all; with a declared
    judge it cannot happen via :func:`run_real_scenarios` (the default judge
    is built first) and would be a caller bug — so it fails loudly instead of
    passing unjudged.
    """
    spec = scenario.judge
    if spec is None:
        return [], None
    if judge is None:
        return (
            [
                "scenario declares a judge but no judge evaluator was built; "
                "refusing to pass it unjudged"
            ],
            None,
        )
    score, explanation = await judge(final_text, spec)
    detail = {
        "score": score,
        "threshold": spec.threshold,
        "passed": score >= spec.threshold,
        "explanation": explanation,
        "model": spec.model,
    }
    if score < spec.threshold:
        reasons = [
            f"judge score {score:.2f} is below threshold {spec.threshold:.2f}: {explanation}"
        ]
    else:
        reasons = []
    return reasons, detail


def _judge_user_message(spec: RealJudgeSpec, final_text: str) -> str:
    """The judge prompt: structured rubric + the answer + the reply format."""
    rubric = "\n".join(f"- {item}" for item in spec.rubric)
    return (
        f"Rubric:\n{rubric}\n\n"
        f"Candidate answer:\n{final_text or '(no answer text)'}\n\n"
        "Reply with the two lines only."
    )


def _parse_judge_reply(text: str) -> tuple[float, str]:
    """Extract ``(score, explanation)`` from the judge's two-line reply.

    Fail-closed: a reply without a parsable ``SCORE:`` line scores 0.0 and
    quotes the raw excerpt, so a confused judge model fails the run instead
    of silently passing it.
    """
    score_match = _SCORE_RE.search(text or "")
    if score_match is None:
        return 0.0, f"judge reply unparsable (no SCORE line): {(text or '')[:200]!r}"
    score = min(1.0, max(0.0, float(score_match.group(1))))
    reason_match = _REASON_RE.search(text or "")
    explanation = reason_match.group(1).strip() if reason_match else ""
    return score, explanation


def _default_judge(
    llm_client_factory: Callable[[Any], Any],
    config: NexusCliConfig,
    usage_sink: list[Usage],
) -> RealJudge:
    """The LLM-backed judge on the SAME client factory as the run itself.

    The scenario's ``judge.model`` overrides the judge model (a fresh client
    from the same factory with the model swapped); without an override the
    judge follows the run's own provider/model. Judge-call usage events are
    appended to ``usage_sink`` so the run's cost accounting includes them.
    """

    async def judge(final_text: str, spec: RealJudgeSpec) -> tuple[float, str]:
        llm_config = copy.deepcopy(config.llm)
        if spec.model:
            llm_config.model = spec.model
        client = llm_client_factory(llm_config)
        messages = [Message(role="user", content=_judge_user_message(spec, final_text))]
        reply_parts: list[str] = []
        async for event in client.chat(messages, [], system_prompt=_JUDGE_SYSTEM_PROMPT):
            event_type = event.get("type")
            if event_type == "text_delta":
                reply_parts.append(str(event.get("text") or ""))
            elif event_type == "usage":
                usage_sink.append(Usage.from_mapping(event.get("usage") or {}))
        return _parse_judge_reply("".join(reply_parts))

    return judge


# ---------------------------------------------------------------------------
# Real-track config / registry / permission variant
# ---------------------------------------------------------------------------


def _load_real_config(root: str) -> NexusCliConfig:
    """The normal config chain, enforced for unattended real runs."""
    config = load_config(project_root=root)
    # The track's own safety pins (the ask's permission contract):
    # - audit on regardless of the user config: every non-read-only call
    #   attempt must leave a trail;
    # - hitl_mode pinned to "auto": with no approval callback, "auto" passes
    #   read-only tools and fail-closed-denies everything else, while a user
    #   "always" would deny even read-only tools and "never" would widen the
    #   surface;
    # - plan_mode stays off: the deny rules below do the rejecting AND audit
    #   it, while the plan-mode gate short-circuits before the audit record.
    config.features.audit_log = True
    config.policy.hitl_mode = "auto"
    config.policy.plan_mode = False
    # Deny everything outside the read-only subset. Deny beats ask/allow, so
    # user-level allow rules cannot punch through, and deny decisions are
    # audited by the executor (approver="permission-rule").
    builtin_names = {tool.name for tool in get_builtin_tools()}
    deny = set(config.permissions.deny) | (builtin_names - REAL_TRACK_TOOLS)
    config.permissions.deny = sorted(deny)
    return config


def _real_registry() -> ToolRegistry:
    """The real builtin tool registry (no stubs, no MCP, no plugins).

    All builtins are mounted — not just the read-only subset — so a write call
    is a *known* tool that goes through the real gate chain and comes back as
    an audited fail-closed denial, instead of an unaudited "tool not found".
    """
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


def _default_llm_factory(config: NexusCliConfig, *, root: str) -> Callable[[Any], Any]:
    """Validate the credential/provider up front, then build one client per run.

    The validation happens here, before any scenario starts, so a missing key
    or an unusable provider fails the whole command with an actionable
    message instead of failing per scenario after real money was spent.
    """
    if not config.llm.api_key:
        raise RealTrackError(
            "real-model eval track: no LLM API key configured. Set NEXUSCLI_API_KEY "
            "(or the provider's key variable, e.g. DEEPSEEK_API_KEY / GLM_API_KEY), "
            "or add llm.api_key to ~/.nexuscli/config.json or "
            f"{root}/.nexuscli/config.json, then rerun 'nexuscli eval run --real'."
        )
    try:
        # Probe construction only: validates the provider/base_url combination
        # without any network call. Each run builds its own client below so
        # repetitions never share connection state.
        create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    except ValueError as exc:
        raise RealTrackError(f"real-model eval track: unusable LLM provider: {exc}") from exc

    def factory(llm_config: Any) -> Any:
        return create_llm_client(llm_config, telemetry_enabled=config.telemetry.enabled)

    return factory


def _calculate_costs(llm_client: Any, usage: Usage) -> dict[str, Any]:
    """Cost per currency from the client's own price profile.

    Mirrors the shape of ``agent._calculate_costs`` (the same dict is carried
    by the agent's ``done`` event); kept local so the eval track does not
    import agent privates across packages.
    """
    calculator = getattr(llm_client, "calculate_cost", None)
    if not callable(calculator):
        return {}
    costs: dict[str, Any] = {}
    for currency in ("usd", "cny"):
        try:
            costs[currency] = calculator(usage, currency=currency).to_dict()
        except (KeyError, TypeError, ValueError):
            continue
    return costs


# ---------------------------------------------------------------------------
# Real-track report (independent file; no regression diff)
# ---------------------------------------------------------------------------


def build_real_report(results: list[RealEvalResult], *, repeat: int) -> dict[str, Any]:
    """Assemble the real-track report document.

    Plain JSON-serializable data. Per scenario: every repetition's result
    (nothing dropped), the ``pass_k`` block (a scenario counts toward pass^k
    only when ALL its repeats passed) and the cost block (accumulated and
    per-run-average token usage with the cache/reasoning splits, plus the
    summed per-currency cost). Per run: status, reasons, tool sequence,
    turns, usage, cost and the structured judge verdict. The summary carries
    the all-pass rate and the run-wide usage/cost totals. Deliberately no
    comparison block: the real track does not diff against a baseline.
    """
    ordered: dict[str, list[RealEvalResult]] = {}
    for result in results:
        ordered.setdefault(result.name, []).append(result)
    scenarios: list[dict[str, Any]] = []
    all_run_costs: list[dict[str, Any]] = []
    all_usages: list[Usage] = []
    for name, runs in ordered.items():
        run_entries = [
            {
                "index": run.repetition,
                "status": "pass" if run.passed else "fail",
                "failure_reasons": list(run.failure_reasons),
                "tool_sequence": list(run.tool_sequence),
                "turns": run.turns,
                "usage": run.usage.to_dict(),
                "cost": run.cost,
                "judge": dict(run.judge) if run.judge is not None else None,
            }
            for run in runs
        ]
        passed_runs = sum(run.passed for run in runs)
        scenarios.append(
            {
                "name": name,
                "source": runs[0].source,
                "pass_k": {
                    "repeat": repeat,
                    "passed_runs": passed_runs,
                    "failed_runs": len(runs) - passed_runs,
                    "all_pass": passed_runs == len(runs),
                },
                "cost": {
                    "runs": len(runs),
                    "total_usage": _sum_usage(runs).to_dict(),
                    "avg_usage": _avg_usage(runs),
                    "total_cost": _sum_costs(run.cost for run in runs),
                },
                "runs": run_entries,
            }
        )
        all_run_costs.extend(run.cost for run in runs)
        all_usages.extend(run.usage for run in runs)
    total = len(results)
    passed = sum(result.passed for result in results)
    scenarios_total = len(ordered)
    all_pass = sum(entry["pass_k"]["all_pass"] for entry in scenarios)
    summary_usage = Usage()
    for usage in all_usages:
        summary_usage = summary_usage + usage
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "track": "real",
        "repeat": repeat,
        "scenarios": scenarios,
        "summary": {
            "total_runs": total,
            "passed": passed,
            "failed": total - passed,
            "scenarios_total": scenarios_total,
            "scenarios_all_pass": all_pass,
            "all_pass_rate": round(all_pass / scenarios_total, 4) if scenarios_total else 0.0,
            "total_usage": summary_usage.to_dict(),
            "total_cost": _sum_costs(all_run_costs),
        },
    }


def _sum_usage(runs: list[RealEvalResult]) -> Usage:
    """Token totals across a scenario's runs (cache/reasoning splits intact)."""
    total = Usage()
    for run in runs:
        total = total + run.usage
    return total


def _avg_usage(runs: list[RealEvalResult]) -> dict[str, float]:
    """Per-run-average token usage; one decimal keeps the JSON readable."""
    if not runs:
        return {key: 0.0 for key in Usage().to_dict()}
    totals = _sum_usage(runs).to_dict()
    return {key: round(value / len(runs), 1) for key, value in totals.items()}


def _sum_costs(cost_dicts: Any) -> dict[str, dict[str, Any]]:
    """Fold per-run ``{currency: breakdown}`` cost dicts into one total.

    Numeric fields (token counts and cost amounts) are summed per currency;
    non-numeric fields (the ``currency`` label) are carried from the first
    breakdown that has them. Float sums are rounded to 6 decimals so nightly
    JSON diffs do not carry binary-float noise.
    """
    merged: dict[str, dict[str, Any]] = {}
    for cost in cost_dicts:
        for currency, breakdown in (cost or {}).items():
            if not isinstance(breakdown, dict):
                continue
            bucket = merged.setdefault(currency, {})
            for key, value in breakdown.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    bucket.setdefault(key, value)
                else:
                    bucket[key] = bucket.get(key, 0) + value
    for bucket in merged.values():
        for key, value in bucket.items():
            if isinstance(value, float):
                bucket[key] = round(value, 6)
    return merged


def write_real_report(project_root: str | Path, report: dict[str, Any]) -> Path:
    """Write the report under ``<project_root>/.nexuscli/evals/last-real-report.json``.

    Kept separate from the hermetic :func:`nexuscli.eval.reporter.write_report`
    on purpose: the two tracks must never touch each other's report files.
    """
    path = Path(project_root) / REAL_REPORT_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def format_real_report(report: dict[str, Any]) -> list[str]:
    """Human-readable lines for the CLI: one per run, then the summary."""
    lines: list[str] = []
    repeat = int(report.get("repeat") or 1)
    for entry in report.get("scenarios", []):
        name = entry.get("name", "?")
        for run in entry.get("runs", []):
            index = int(run.get("index") or 0)
            mark = "PASS" if run.get("status") == "pass" else "FAIL"
            line = f"{mark}\t{name}\t[run {index}/{repeat}]"
            if run.get("status") != "pass":
                reasons = run.get("failure_reasons") or ["unknown failure"]
                line += "\t" + "; ".join(str(reason) for reason in reasons)
            lines.append(line)
    summary = report.get("summary") or {}
    lines.append(
        f"total_runs={summary.get('total_runs', 0)} passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)}"
    )
    return lines
