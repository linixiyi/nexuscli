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

Public surface:
    :func:`load_eval_scenarios` / :func:`load_eval_scenario` / :func:`builtin_eval_dir`
        Scenario loading and strict schema validation (:class:`EvalSchemaError`);
        the ``difficulty`` tier vocabulary is :data:`DIFFICULTY_LEVELS`.
    :func:`run_eval_scenarios` / :func:`run_scenario`
        Offline scripted execution (:class:`EvalRunResult`) on both tracks.
    :func:`load_previous_report` / :func:`build_report` / :func:`write_report`
        Regression comparison and persistence.
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
    ScriptedTool,
    ScriptedTurn,
    ScriptedUserTurn,
    builtin_eval_dir,
    load_eval_scenario,
    load_eval_scenarios,
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
    "REPORT_RELATIVE",
    "EvalAssertions",
    "EvalRunResult",
    "EvalScenario",
    "EvalSchemaError",
    "MemoryEvalSpec",
    "MemoryQuerySpec",
    "MemorySeedEntry",
    "PreviousReportError",
    "ScriptedTool",
    "ScriptedTurn",
    "ScriptedUserTurn",
    "builtin_eval_dir",
    "build_report",
    "comparison_verdict",
    "format_report",
    "load_eval_scenario",
    "load_eval_scenarios",
    "load_previous_report",
    "previous_statuses",
    "run_eval_scenarios",
    "run_scenario",
    "write_report",
]
