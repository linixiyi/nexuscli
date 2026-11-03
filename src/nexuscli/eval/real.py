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

The baseline ablation (``eval run --real --baseline``) runs the same scenario
set twice — once through the agent harness above, once against the bare
model (:func:`run_baseline_scenario`: a single direct completion, no tools,
no skill injection, no agent system prompt) — and assembles the harness-gain
comparison into ``.nexuscli/evals/last-real-comparison.json``
(:func:`run_baseline_comparison` / :func:`build_real_comparison`). The
comparison is a third report file on purpose: ``last-real-report.json`` keeps
its exact schema and stays agent-side only, so its downstream consumers
(propose / post-landing diff) never see baseline data.

**The controlled write sandbox** (``sandbox_write: true``, spec
2026-11-03_add-real-sandbox-write-quality) is the track's one, explicitly
scoped exception to the zero-write invariant above — a per-scenario opt-in,
never a widening of :data:`REAL_TRACK_TOOLS`. A sandbox scenario:

- gets a fresh empty ``tempfile.mkdtemp`` sandbox (prefix
  :data:`SANDBOX_DIR_PREFIX`) and its Agent runs with the sandbox as
  ``cwd`` — the file tools build their :class:`~nexuscli.policy.PathGuard`
  from ``ToolContext.cwd`` (``tools/file_ops.resolve_path``), so the guard
  root IS the sandbox and the repository itself stays unwritable;
- keeps the registry unchanged (all builtins mounted) but un-denies
  :data:`REAL_SANDBOX_WRITE_TOOLS` (``write_file`` / ``edit_file``) in the
  scenario's config and allow-rules them, so the real gate chain lets them
  execute (a bare allow rule is the executor's standing auto-approve);
  ``bash`` and every other non-whitelisted tool stays deny-ruled — the
  sandbox opens *file writes only*, never command execution;
- is audited exactly like the read-only track: every write attempt (allowed,
  escaped or denied) leaves an audit record;
- runs a quality gate after the loop (:func:`run_quality_gate`: py_compile
  every sandbox ``.py``, then ``python -m ruff check --output-format
  concise`` in a subprocess; a missing ruff degrades the metric to ``null``
  with an explanatory note) and records the result as the run's ``quality``
  block. The ``max_ruff_violations`` / ``require_compile_clean`` assertion
  keys judge that block fail-closed: a ``null`` ruff metric fails a declared
  ``max_ruff_violations`` (never silently passes it);
- has its sandbox removed after the run (best-effort: a failed removal
  becomes a single in-process :class:`SandboxCleanupWarning`, never an
  exception and never silence).

A scenario without ``sandbox_write`` runs exactly as before — the sandbox
branch is unreachable, no sandbox is created, and the report's ``quality``
field is ``null``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.eval.loader import EvalAssertions, RealEvalScenario, RealJudgeSpec
from nexuscli.eval.reporter import append_report_history
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

# The baseline-ablation comparison file — a THIRD report file, deliberately
# independent of last-real-report.json: that report's schema and its
# agent-side-only content are a contract with downstream consumers (skill
# propose / post-landing diff read it), so comparison data must never be
# folded in. A --baseline run writes BOTH files; a plain --real run writes
# only the report above and never touches this one.
REAL_COMPARISON_RELATIVE = Path(".nexuscli") / "evals" / "last-real-comparison.json"

# The bare-model baseline's ENTIRE system prompt, chosen to say nothing the
# model could leverage: no tool guidance, no persona, no workspace context —
# the smallest nudge that still yields a direct answer. Everything the agent
# side adds on top of this (tool registry, skill candidates, harness prompt)
# is what the ablation measures. Recorded verbatim in the comparison document
# (``baseline.system_prompt``) so nightly readers can see exactly what the
# bare arm got.
BASELINE_SYSTEM_PROMPT = "You are a helpful assistant. Answer directly."

# The curated read-only builtin subset the real track allows to EXECUTE.
# Curated by name, not derived from Tool.is_read_only: the flag is the repo's
# own classification and it is too loose for unattended runs. Track
# invariant, stated honestly after the 2026-11-02 layered-loading change:
# an unattended real run performs ZERO writes into the workspace, plus ONE
# accepted user-dir write — load_skill's usage counter (see below). That is
# a narrowing of the original "unattended runs never write" promise, made
# deliberately and reviewed, not silently. Known traps:
# - ``todo_write`` carries the default ``is_read_only=True`` while writing
#   ``.nexuscli/todo.json`` (workspace write);
# - ``search_memory`` is flagged read-only, but its ``MemoryManager.recall``
#   runs with the default ``mark_access=True`` — every call UPDATEs
#   ``access_count`` on the user's real memory DB and runs
#   ``_purge_expired`` (a DELETE of expired rows). That is BEHAVIOR-STATE:
#   the writes change what the memory system recalls, so they stay excluded.
# Both stay OUT of this allowlist and are deny-ruled (fail-closed + audited,
# see _load_real_config).
#
# ``load_skill`` was excluded until 2026-11-02 on the same "user-state
# write" reasoning, and is deliberately back IN with a different
# classification: its writes are ACCOUNTING-ONLY — the injection seam calls
# ``SkillStateStore().record_use`` which atomically rewrites the usage
# counters at ``~/.nexuscli/usage.json`` (sibling of skills.json). Nothing
# reads them to change behavior (matching, candidates, recall are all
# untouched), and its content surface is read-only by construction: the
# registry resolves a skill NAME against the fixed skill roots only (no
# path parameter, no traversal; every returned text is a SKILL.md from
# those roots). It is IN so the real track's candidate -> load_skill ->
# full-text chain is reachable (spec 2026-11-02_add-skill-layered-loading
# §2.2); re-excluding it would re-break that chain.
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
        "load_skill",
    }
)

# The sandbox_write extension set (spec 2026-11-03_add-real-sandbox-write-quality):
# the ONLY tools a ``sandbox_write`` scenario adds to its own executable
# surface, and only for that one scenario's config. :data:`REAL_TRACK_TOOLS`
# itself stays untouched — the main whitelist's "default read-only" semantics
# are the track invariant; this set is the explicitly reviewed exception a
# scenario must opt into. Both tools stay outside REAL_TRACK_TOOLS: a
# non-sandbox scenario denies them exactly as before. ``bash`` is deliberately
# absent — the sandbox opens file writes only, never command execution.
REAL_SANDBOX_WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})

# The write sandbox's temp-directory prefix (system temp dir, fresh empty
# directory per scenario run, removed after the run). The recognizable prefix
# is the residue story: a failed cleanup leaves a findable
# ``nexuscli-eval-write-*`` directory instead of silent junk.
SANDBOX_DIR_PREFIX = "nexuscli-eval-write-"


class SandboxCleanupWarning(UserWarning):
    """A sandbox_write scenario's sandbox directory could not be removed.

    The run itself is unaffected (the report and its quality block are fine);
    the warning names the leftover path so it can be removed manually.
    """


# The in-process one-shot flag behind :func:`_cleanup_sandbox`: at most one
# cleanup warning per process, per the spec's "一次性警告" contract — a
# repeated failure must not flood every scenario's output.
_SANDBOX_CLEANUP_WARNED = False


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

# One violation line of `ruff check --output-format concise`: every diagnostic
# starts with the ``path:line:col: `` position prefix — classic rules carry an
# alphanumeric code there (``demo.py:1:8: F401 [*] ...``), newer ruff versions
# report syntax errors as ``invalid-syntax`` messages. Counting the prefix
# (the trailing ``Found N errors.`` summary line has none) covers both; a
# violation exit with no matching line is reported as unjudgeable rather than
# as zero violations.
_RUFF_CONCISE_LINE = re.compile(r"\S+:\d+:\d+: ")


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

    ``quality`` is the sandbox_write scenario's quality-gate block
    (:func:`run_quality_gate`); ``None`` for every read-only scenario and for
    a sandbox scenario whose agent loop raised before the gate could run. The
    report carries it verbatim (additive — existing report keys unchanged).
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
    quality: dict[str, Any] | None = None


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

    A ``sandbox_write`` scenario runs against a fresh one-shot sandbox
    directory instead of ``root``: the Agent's ``cwd`` is the sandbox, so the
    file tools' PathGuard root is the sandbox and the repository stays
    unwritable; the scenario's config additionally opens the
    :data:`REAL_SANDBOX_WRITE_TOOLS` channel (:func:`_apply_sandbox_write_config`).
    After the loop (successful or not) the quality gate runs over the sandbox
    while it still exists, and the sandbox is then removed best-effort.
    """
    result = RealEvalResult(name=scenario.name, passed=False, repetition=repetition)
    result.source = scenario.source
    tool_sequence: list[str] = []
    texts: list[str] = []
    failure_reasons: list[str] = []
    total_usage = Usage()

    sandbox: Path | None = None
    run_config = copy.deepcopy(config)
    cwd = root
    if scenario.sandbox_write:
        sandbox = Path(tempfile.mkdtemp(prefix=SANDBOX_DIR_PREFIX))
        cwd = str(sandbox)
        _apply_sandbox_write_config(run_config)

    quality: dict[str, Any] | None = None
    try:
        llm_client = llm_client_factory(config.llm)
        agent = Agent(
            llm_client=llm_client,
            tool_registry=_real_registry(),
            config=run_config,
            cwd=cwd,
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
        if sandbox is not None:
            # Gate while the sandbox still exists; the cleanup below runs
            # whether the gate (or the loop above) failed or not.
            quality = run_quality_gate(sandbox)
    finally:
        if sandbox is not None:
            _cleanup_sandbox(sandbox)

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
    result.quality = quality
    result.failure_reasons = (
        failure_reasons
        + _assertion_failures(scenario, result)
        + _quality_assertion_failures(quality, scenario.assertions)
        + judge_reasons
    )
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
    #   it, while the plan-mode gate short-circuits before the audit record;
    # - path_guard pinned ON: the track's confinement promise (reads confined
    #   to the workspace; sandbox_write writes confined to the sandbox) rests
    #   entirely on the file tools' PathGuard (tools/file_ops.resolve_path
    #   builds it from policy.path_guard_enabled), and the sandbox branch
    #   allow-rules the write pair into executor auto-approve — a user config
    #   with path_guard_enabled=false must not be able to switch that
    #   confinement off underneath an unattended run.
    config.features.audit_log = True
    config.policy.hitl_mode = "auto"
    config.policy.plan_mode = False
    config.policy.path_guard_enabled = True
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


# ---------------------------------------------------------------------------
# sandbox_write — the controlled write sandbox
# ---------------------------------------------------------------------------


def _apply_sandbox_write_config(config: NexusCliConfig) -> None:
    """Open the controlled write channel for ONE sandbox_write scenario.

    Mutates the run's deep-copied config in place (the caller passes a copy;
    the shared track config is never touched). Two edits, nothing else:

    - un-deny :data:`REAL_SANDBOX_WRITE_TOOLS` by removing their bare-name
      deny rules — those are the track's own computed denies
      (``builtin_names - REAL_TRACK_TOOLS``), and a scenario that opted into
      the sandbox explicitly overrides the track's default posture (the same
      category of track pin as audit-on / hitl-auto above). Any *spec'd* deny
      rule the user wrote (e.g. ``write_file(src/**)``) stays and keeps
      denying — deny still beats allow;
    - allow-rule both tools, so ``requires_approval`` calls execute through
      the executor's standing allow-rule auto-approve instead of failing
      closed on the missing approval callback (the audit then attributes the
      calls to ``permission-rule``, same as any allow-ruled execution).

    ``bash`` and every other tool outside ``REAL_TRACK_TOOLS |
    REAL_SANDBOX_WRITE_TOOLS`` remain deny-ruled.
    """
    config.permissions.deny = [
        rule for rule in config.permissions.deny if rule not in REAL_SANDBOX_WRITE_TOOLS
    ]
    config.permissions.allow = sorted(set(config.permissions.allow) | REAL_SANDBOX_WRITE_TOOLS)


def _python_executable() -> str:
    """The interpreter for the quality gate's ``python -m ruff`` subprocess.

    Indirection exists so tests can point it at a path that cannot spawn a
    process (exercising the degradation branch hermetically).
    """
    return sys.executable


def _ruff_install_guidance() -> str:
    return (
        "ruff is not available in this environment; the lint metric is recorded as null. "
        "Install it with 'uv sync --extra dev' (ruff ships in the dev extra) to enable it."
    )


def _ruff_violations(sandbox: Path) -> tuple[int | None, str | None]:
    """Count ruff violations in the sandbox; ``(None, note)`` when unjudgeable.

    Runs ``<python> -m ruff check --output-format concise <sandbox>`` as a
    subprocess and parses the concise lines (``path:line:col: CODE message``).
    Fail-closed by contract: any run that cannot be *parsed* into a count —
    ruff missing, the subprocess unusable, an unexpected exit code, a
    violation exit with zero parsable lines — returns ``None`` plus a
    human-readable note instead of a guessed number.
    """
    if find_spec("ruff") is None:
        return None, _ruff_install_guidance()
    try:
        completed = subprocess.run(
            [
                _python_executable(),
                "-m",
                "ruff",
                "check",
                "--output-format",
                "concise",
                str(sandbox),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"ruff check could not run ({exc}); the lint metric is recorded as null"
    if "No module named ruff" in (completed.stderr or ""):
        return None, _ruff_install_guidance()
    if completed.returncode not in (0, 1):
        excerpt = ((completed.stderr or "") + (completed.stdout or "")).strip()[:200]
        return None, (
            f"ruff check exited with {completed.returncode} (expected 0/1); "
            f"raw output excerpt: {excerpt!r}"
        )
    lines = [
        line for line in (completed.stdout or "").splitlines() if _RUFF_CONCISE_LINE.search(line)
    ]
    if completed.returncode == 1 and not lines:
        excerpt = ((completed.stderr or "") + (completed.stdout or "")).strip()[:200]
        return None, (
            "ruff reported a violation exit but no parsable concise line; "
            f"raw output excerpt: {excerpt!r}"
        )
    return len(lines), None


def run_quality_gate(sandbox: Path) -> dict[str, Any]:
    """The sandbox_write quality gate: objective metrics over the sandbox's .py.

    Collects every ``*.py`` file under the sandbox (sorted relative POSIX
    paths), compiles each with :mod:`py_compile` (``compile_ok`` — a single
    syntax error makes it ``False``), then counts ruff violations in one
    subprocess (:func:`_ruff_violations`; missing ruff degrades to ``null``
    with a note). Returns the report's ``quality`` block::

        {"files_written": [...], "ruff_violations": int | None,
         "compile_ok": bool, "ruff_note": str | None}

    An empty sandbox is a clean gate (no files, 0 violations, compile ok) —
    whether "the agent wrote nothing" passes is the assertions' job, not the
    gate's.
    """
    files = sorted(path for path in sandbox.rglob("*.py") if path.is_file())
    compile_ok = True
    for file in files:
        try:
            # Writes the .pyc next to the source (inside the sandbox, which is
            # about to be removed) — standard py_compile behavior, no side
            # effect outside the sandbox.
            py_compile.compile(str(file), doraise=True)
        except (py_compile.PyCompileError, OSError):
            compile_ok = False
    violations, note = _ruff_violations(sandbox)
    return {
        "files_written": [file.relative_to(sandbox).as_posix() for file in files],
        "ruff_violations": violations,
        "compile_ok": compile_ok,
        "ruff_note": note,
    }


def _quality_assertion_failures(
    quality: dict[str, Any] | None, assertions: EvalAssertions
) -> list[str]:
    """The quality assertions' verdict on the recorded gate block.

    ``max_ruff_violations`` (match on ``is not None`` — ``0`` is a real cap)
    fails when the metric is null (fail-closed: an unjudgeable metric must
    never silently pass its cap; the reason carries the gate's note so the
    reader sees the install guidance), or when the count exceeds the cap.
    ``require_compile_clean`` fails when the sandbox did not compile cleanly.
    Without either key the gate block is recorded but never touches
    PASS/FAIL. A declared assertion with no gate block at all is a broken
    run, not a pass — it fails loudly.
    """
    declared = assertions.max_ruff_violations is not None or bool(assertions.require_compile_clean)
    if not declared:
        return []
    if quality is None:
        return [
            "scenario declares quality assertion(s) but the run produced no quality block; "
            "refusing to pass it unjudged"
        ]
    failures: list[str] = []
    if assertions.max_ruff_violations is not None:
        violations = quality.get("ruff_violations")
        if violations is None:
            failures.append(
                "max_ruff_violations cannot be judged: the ruff metric is null "
                f"({quality.get('ruff_note') or 'no note recorded'}) — a null metric "
                "fails closed; install ruff ('uv sync --extra dev') to make it judgeable"
            )
        elif violations > assertions.max_ruff_violations:
            failures.append(
                f"ruff violations {violations} exceed max_ruff_violations "
                f"{assertions.max_ruff_violations}"
            )
    if assertions.require_compile_clean and not quality.get("compile_ok"):
        failures.append("sandbox code does not compile cleanly (require_compile_clean)")
    return failures


def _cleanup_sandbox(sandbox: Path) -> None:
    """Remove the sandbox after the run; a failure warns once, never raises.

    Best-effort by contract (spec §2.2): the run's results are already in
    hand, so a stubborn sandbox (locked file, transient OSError) must not
    fail the run — but it must also not vanish silently: the FIRST failure in
    this process emits one :class:`SandboxCleanupWarning` naming the leftover
    path (recognizable by :data:`SANDBOX_DIR_PREFIX`); further failures in the
    same process stay silent.
    """
    global _SANDBOX_CLEANUP_WARNED
    try:
        shutil.rmtree(sandbox)
    except OSError as exc:
        if _SANDBOX_CLEANUP_WARNED:
            return
        _SANDBOX_CLEANUP_WARNED = True
        # Mirror the reporter's degradation: even a -W error warning filter
        # must not turn the cleanup into a raise.
        with contextlib.suppress(Exception):
            warnings.warn(
                f"eval write sandbox not removed ({exc}): {sandbox} — remove the "
                f"'{SANDBOX_DIR_PREFIX}' directory manually; the run itself is unaffected",
                SandboxCleanupWarning,
                stacklevel=2,
            )


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
    turns, usage, cost, the structured judge verdict and — additive as of
    spec 2026-11-03_add-real-sandbox-write-quality — the sandbox_write
    scenario's ``quality`` block (``null`` for every read-only run; existing
    report keys are unchanged, so pre-sandbox consumers keep reading the
    document). The summary carries the all-pass rate and the run-wide
    usage/cost totals. Deliberately no comparison block: the real track does
    not diff against a baseline.
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
                "quality": dict(run.quality) if run.quality is not None else None,
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


def write_real_report(
    project_root: str | Path, report: dict[str, Any], *, append_history: bool = True
) -> Path:
    """Write the report under ``<project_root>/.nexuscli/evals/last-real-report.json``.

    Kept separate from the hermetic :func:`nexuscli.eval.reporter.write_report`
    on purpose: the two tracks must never touch each other's report files.
    After the main file lands, the same document is also appended as a
    best-effort timestamped snapshot under ``history/real/``
    (:func:`append_real_report_history`); a snapshot failure never fails the
    write. ``append_history=False`` opts out — for instrumentation that
    writes a report the caller is about to roll back (the post-landing diff
    in ``skill/propose.py``): history must never record a run whose main
    report the backup/restore then erases.
    """
    path = Path(project_root) / REAL_REPORT_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if append_history:
        append_real_report_history(project_root, report)
    return path


def append_real_report_history(project_root: str | Path, report: dict[str, Any]) -> Path | None:
    """Append the real-track report under ``history/real/`` (best-effort).

    Thin wrapper over :func:`nexuscli.eval.reporter.append_report_history` so
    both tracks carry one history-append function each (spec
    2026-11-03_add-eval-history-and-skill-polish): identical degradation
    contract — a snapshot failure becomes a single :class:`EvalHistoryWarning`
    and never an exception.
    """
    return append_report_history(project_root, report, track="real")


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


# ---------------------------------------------------------------------------
# Baseline ablation — the bare-model arm (`eval run --real --baseline`)
# ---------------------------------------------------------------------------

# Tool-trajectory and turn-budget assertions are meaningless against the bare
# model (it gets no tools and has no turn loop); the comparison document names
# them once, up front, instead of marking every scenario entry.
_BASELINE_NA_ASSERTIONS: tuple[str, ...] = (
    "expected_tool_sequence",
    "forbidden_tools",
    "required_tools",
    "max_tool_calls",
    "max_total_tool_calls",
    "max_turns",
)


async def run_baseline_scenario(
    scenario: RealEvalScenario,
    *,
    config: NexusCliConfig,
    llm_client_factory: Callable[[Any], Any],
    judge: RealJudge | None = None,
) -> RealEvalResult:
    """Run one scenario against the bare model: a single direct completion.

    The ablation's "no harness" arm. The scenario's prompt goes to the SAME
    LLM the agent side uses (same config chain, same provider/model — the
    gain must be attributable to the harness, not to a model difference) as
    ONE ``chat`` completion with an empty tool list, no skill candidates and
    no agent system prompt — just :data:`BASELINE_SYSTEM_PROMPT`. The result
    reuses :class:`RealEvalResult` so the two arms stay shape-comparable;
    ``turns`` is pinned to 1 (one LLM call, no react loop) and
    ``tool_sequence`` stays empty (nothing can be called).

    Assertion subset: ``final_contains`` plus the scenario's own judge,
    reused verbatim (same rubric, same threshold, same judge-model override —
    one ruler for both arms). The tool-sequence class and ``max_turns`` are
    not evaluated here; :func:`build_real_comparison` marks them N/A in the
    document. ``judge=None`` selects the default LLM-backed judge on the same
    factory, exactly like :func:`run_real_scenario`.
    """
    result = RealEvalResult(name=scenario.name, passed=False)
    result.source = scenario.source
    texts: list[str] = []
    failure_reasons: list[str] = []
    total_usage = Usage()

    llm_client = llm_client_factory(config.llm)
    messages = [Message(role="user", content=scenario.prompt)]
    async for event in llm_client.chat(messages, [], system_prompt=BASELINE_SYSTEM_PROMPT):
        event_type = event.get("type")
        if event_type == "text_delta":
            texts.append(str(event.get("text") or ""))
        elif event_type == "usage":
            total_usage = total_usage + Usage.from_mapping(event.get("usage") or {})
        elif event_type == "error":
            failure_reasons.append(f"model errored: {event.get('error')}")
    # The completion's text deltas ARE the final answer: there is no message
    # list to extract a last assistant turn from, unlike the agent arm.
    result.final_text = "".join(texts)
    result.turns = 1

    judge_usage: list[Usage] = []
    if scenario.judge is not None and judge is None:
        judge = _default_judge(llm_client_factory, config, judge_usage)
    judge_reasons, judge_detail = await _judge_verdict(scenario, result.final_text, judge)
    for extra in judge_usage:
        total_usage = total_usage + extra

    result.usage = total_usage
    result.cost = _calculate_costs(llm_client, total_usage)
    result.judge = judge_detail
    result.failure_reasons = (
        failure_reasons + _baseline_assertion_failures(scenario, result) + judge_reasons
    )
    result.passed = not result.failure_reasons
    return result


def _baseline_assertion_failures(scenario: RealEvalScenario, result: RealEvalResult) -> list[str]:
    """The text-only subset of the scenario's assertions: ``final_contains``.

    Deliberately NOT :func:`nexuscli.eval.runner._assertion_failures`: the
    other six keys read a tool trajectory the bare model cannot have (it
    receives an empty tool list), so judging them would report every scenario
    as a tool failure instead of the answer quality the ablation compares.
    The wording matches the shared evaluator so reports read the same.
    """
    failures: list[str] = []
    assertions = scenario.assertions
    if assertions.final_contains is not None:
        for needle in assertions.final_contains:
            if needle not in result.final_text:
                failures.append(f"final text does not contain: {needle!r}")
    return failures


@dataclass(slots=True)
class BaselineComparisonRun:
    """Everything one ``--baseline`` double-run produced.

    ``agent_results`` feeds the unchanged real-track report: the CLI builds
    ``last-real-report.json`` from it exactly as a plain ``--real`` run
    would, so that file's schema and agent-side-only content are untouched.
    ``baseline_results`` is the bare-model arm and stays in memory — it is
    never folded into the real-track report. ``comparison`` is the document
    for ``last-real-comparison.json`` (:func:`write_real_comparison`).
    """

    agent_results: list[RealEvalResult]
    baseline_results: list[RealEvalResult]
    comparison: dict[str, Any]


def run_baseline_comparison(
    scenarios: list[RealEvalScenario],
    *,
    root: str,
    llm_client_factory: Callable[[Any], Any] | None = None,
    judge: RealJudge | None = None,
) -> BaselineComparisonRun:
    """Run the agent arm, then the bare-model arm, and assemble the deltas.

    Both arms run the same scenario list serially against the same client
    factory (one fresh client per run, per-side usage and judge). A one-shot
    double-run is the fair-sampling choice — model output varies between
    calls, so caching an earlier agent side would compare against a different
    sample; that is also why ``--repeat`` cannot combine with ``--baseline``
    (the CLI rejects the combination outright).

    The agent arm reuses :func:`run_real_scenarios` unchanged — track config
    enforcement, read-only deny rules, fail-closed approval, the judge seam,
    and (with no injected factory) the same up-front no-key
    :class:`RealTrackError` as a plain ``--real`` run. The bare-model arm
    stays in memory: this function writes nothing; persistence is the
    caller's job (report via the unchanged :func:`build_real_report` /
    :func:`write_real_report` path, comparison via
    :func:`write_real_comparison`).
    """
    agent_results = run_real_scenarios(
        scenarios,
        root=root,
        repeat=1,
        llm_client_factory=llm_client_factory,
        judge=judge,
    )
    config = _load_real_config(root)
    if llm_client_factory is None:
        llm_client_factory = _default_llm_factory(config, root=root)
    baseline_results = [
        asyncio.run(
            run_baseline_scenario(
                scenario,
                config=config,
                llm_client_factory=llm_client_factory,
                judge=judge,
            )
        )
        for scenario in scenarios
    ]
    return BaselineComparisonRun(
        agent_results=agent_results,
        baseline_results=baseline_results,
        comparison=build_real_comparison(agent_results, baseline_results),
    )


def build_real_comparison(
    agent_results: list[RealEvalResult],
    baseline_results: list[RealEvalResult],
) -> dict[str, Any]:
    """Assemble the agent-vs-baseline comparison document (pure data).

    Results pair **positionally**: both arms iterate the same scenario list
    in the same order and produce exactly one result per scenario
    (:func:`run_real_scenarios` with ``repeat=1``, and the bare-model loop
    above), so ``zip(..., strict=True)`` gives each agent run its own
    baseline run. A length mismatch is a caller bug and fails loudly rather
    than silently truncating. Pairing by name would be wrong instead: the
    loader rejects duplicate tool names but not duplicate scenario names,
    so two same-named files in a ``--file`` directory would both map to the
    last baseline entry and double-count its tokens/pass. Deltas are agent
    minus baseline: ``pass_delta > 0`` is the harness's net gain (scenarios
    only the agent passed), ``token_delta > 0`` is the harness's extra token
    cost. The four-quadrant counts make the agreement structure visible:
    scenarios both arms pass, only the agent passes, only the bare model
    passes, and neither passes (the bare model failing tool-required
    questions is the expected ablation signal, not a defect). The bare
    arm's N/A assertions are stated once up front instead of per scenario.
    """
    quadrants = {"both_pass": 0, "agent_only": 0, "baseline_only": 0, "both_fail": 0}
    scenarios: list[dict[str, Any]] = []
    agent_pass = baseline_pass = 0
    agent_tokens = baseline_tokens = 0
    for agent, baseline in zip(agent_results, baseline_results, strict=True):
        if agent.passed:
            agent_pass += 1
        if baseline.passed:
            baseline_pass += 1
        agent_total = agent.usage.total_tokens
        baseline_total = baseline.usage.total_tokens
        agent_tokens += agent_total
        baseline_tokens += baseline_total
        if agent.passed and baseline.passed:
            quadrants["both_pass"] += 1
        elif agent.passed:
            quadrants["agent_only"] += 1
        elif baseline.passed:
            quadrants["baseline_only"] += 1
        else:
            quadrants["both_fail"] += 1
        scenarios.append(
            {
                "id": agent.name,
                "agent": {"status": "pass" if agent.passed else "fail", "tokens": agent_total},
                "baseline": {
                    "status": "pass" if baseline.passed else "fail",
                    "tokens": baseline_total,
                },
                "delta": {
                    "pass_delta": int(agent.passed) - int(baseline.passed),
                    "token_delta": agent_total - baseline_total,
                },
            }
        )
    total = len(scenarios)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "track": "real-baseline-comparison",
        "baseline": {
            "system_prompt": BASELINE_SYSTEM_PROMPT,
            "assertions": {
                "evaluated": ["final_contains", "judge"],
                "not_applicable": list(_BASELINE_NA_ASSERTIONS),
            },
        },
        "scenarios": scenarios,
        "summary": {
            "scenarios_total": total,
            "agent_pass": agent_pass,
            "baseline_pass": baseline_pass,
            "pass_delta": agent_pass - baseline_pass,
            "agent_pass_rate": round(agent_pass / total, 4) if total else 0.0,
            "baseline_pass_rate": round(baseline_pass / total, 4) if total else 0.0,
            "quadrants": quadrants,
            "total_tokens": {"agent": agent_tokens, "baseline": baseline_tokens},
        },
    }


def write_real_comparison(project_root: str | Path, comparison: dict[str, Any]) -> Path:
    """Write the comparison under ``<root>/.nexuscli/evals/last-real-comparison.json``.

    A separate writer from :func:`write_real_report` on purpose: the two
    files serve different consumers (propose / post-landing diff read the
    agent-side report; nightly readers read this one) and neither may leak
    into the other.
    """
    path = Path(project_root) / REAL_COMPARISON_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def format_real_comparison(comparison: dict[str, Any]) -> list[str]:
    """Human-readable lines for the CLI: one per scenario, then the summary."""
    lines: list[str] = []
    for entry in comparison.get("scenarios", []):
        agent = entry.get("agent") or {}
        baseline = entry.get("baseline") or {}
        delta = entry.get("delta") or {}
        lines.append(
            f"{entry.get('id', '?')}\t"
            f"agent={str(agent.get('status', '?')).upper()} "
            f"baseline={str(baseline.get('status', '?')).upper()} "
            f"tokens={agent.get('tokens', 0)}/{baseline.get('tokens', 0)} "
            f"(pass_delta={delta.get('pass_delta', 0):+d}, "
            f"token_delta={delta.get('token_delta', 0):+d})"
        )
    summary = comparison.get("summary") or {}
    quadrants = summary.get("quadrants") or {}
    quadrant_text = " ".join(
        f"{key}={quadrants.get(key, 0)}"
        for key in ("both_pass", "agent_only", "baseline_only", "both_fail")
    )
    total = summary.get("scenarios_total", 0)
    lines.append(
        f"agent_pass={summary.get('agent_pass', 0)}/{total} "
        f"baseline_pass={summary.get('baseline_pass', 0)}/{total} "
        f"pass_delta={summary.get('pass_delta', 0):+d} | quadrants: {quadrant_text}"
    )
    return lines
