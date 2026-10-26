"""eval — Offline task-level regression evaluation for the agent.

A scripted (fake) LLM client drives a real ``Agent`` through JSON scenarios;
assertions check the tool-call sequence and final output, and consecutive runs
are diffed into pass→fail (regression) / fail→pass (fixed) verdicts. A
scenario may declare ``user_turns`` (+ an optional ``max_rounds`` cap) to
become a multi-round user simulation: the scripted user's messages continue
the same agent session, and the assertions judge the cumulative run. A
scenario may instead declare ``memory_eval`` to run on the memory-recall
track: no agent loop — a hermetic temp-DB ``MemoryManager`` is seeded and the
queries assert recall ranking (target in the top-K, distractors not ahead).

The real-model track (``eval/real.py``, ``nexuscli eval run --real``) is the
deliberately non-hermetic counterpart: a real LLM from the normal config
chain drives the real read-only builtin tools, write calls are fail-closed
denied and audited, token usage is collected onto each result, and the report
lands in ``.nexuscli/evals/last-real-report.json`` (never the hermetic
baseline). Tests stay hermetic through the injectable LLM-client factory and
judge seam.

Public surface:
    :func:`load_eval_scenarios` / :func:`load_eval_scenario` / :func:`builtin_eval_dir`
        Scenario loading and strict schema validation (:class:`EvalSchemaError`);
        the ``difficulty`` tier vocabulary is :data:`DIFFICULTY_LEVELS`.
    :func:`run_eval_scenarios` / :func:`run_scenario`
        Offline scripted execution (:class:`EvalRunResult`) on both tracks.
    :func:`load_previous_report` / :func:`build_report` / :func:`write_report`
        Regression comparison and persistence.
    :func:`load_real_eval_scenarios` / :func:`load_real_eval_scenario`
        Real-track scenario loading (minimal strict schema, track-conflict
        keys rejected); :func:`builtin_real_eval_dir` locates the set.
    :func:`run_real_scenarios` / :func:`run_real_scenario`
        Real-model execution (:class:`RealEvalResult`), with
        :class:`RealTrackError` for unusable credentials/providers.
    :func:`build_real_report` / :func:`write_real_report` / :func:`format_real_report`
        Real-track report assembly and persistence (``REAL_REPORT_RELATIVE``).
"""

from nexuscli.eval.loader import (
    DEFAULT_DIFFICULTY,
    DEFAULT_MEMORY_TOP_K,
    DIFFICULTY_LEVELS,
    EvalAssertions,
    EvalScenario,
    EvalSchemaError,
    MemoryEvalSpec,
    MemoryQuerySpec,
    MemorySeedEntry,
    RealEvalScenario,
    RealJudgeSpec,
    ScriptedTool,
    ScriptedTurn,
    ScriptedUserTurn,
    builtin_eval_dir,
    builtin_real_eval_dir,
    load_eval_scenario,
    load_eval_scenarios,
    load_real_eval_scenario,
    load_real_eval_scenarios,
)
from nexuscli.eval.real import (
    REAL_REPORT_RELATIVE,
    REAL_TRACK_TOOLS,
    RealEvalResult,
    RealTrackError,
    build_real_report,
    format_real_report,
    run_real_scenario,
    run_real_scenarios,
    write_real_report,
)
from nexuscli.eval.reporter import (
    REPORT_RELATIVE,
    PreviousReportError,
    build_report,
    comparison_verdict,
    format_report,
    load_previous_report,
    previous_statuses,
    write_report,
)
from nexuscli.eval.runner import EvalRunResult, run_eval_scenarios, run_scenario

__all__ = [
    "DEFAULT_DIFFICULTY",
    "DEFAULT_MEMORY_TOP_K",
    "DIFFICULTY_LEVELS",
    "REAL_REPORT_RELATIVE",
    "REAL_TRACK_TOOLS",
    "REPORT_RELATIVE",
    "EvalAssertions",
    "EvalRunResult",
    "EvalScenario",
    "EvalSchemaError",
    "MemoryEvalSpec",
    "MemoryQuerySpec",
    "MemorySeedEntry",
    "PreviousReportError",
    "RealEvalResult",
    "RealEvalScenario",
    "RealJudgeSpec",
    "RealTrackError",
    "ScriptedTool",
    "ScriptedTurn",
    "ScriptedUserTurn",
    "builtin_eval_dir",
    "builtin_real_eval_dir",
    "build_real_report",
    "build_report",
    "comparison_verdict",
    "format_real_report",
    "format_report",
    "load_eval_scenario",
    "load_eval_scenarios",
    "load_previous_report",
    "load_real_eval_scenario",
    "load_real_eval_scenarios",
    "previous_statuses",
    "run_eval_scenarios",
    "run_real_scenario",
    "run_real_scenarios",
    "run_scenario",
    "write_real_report",
    "write_report",
]
