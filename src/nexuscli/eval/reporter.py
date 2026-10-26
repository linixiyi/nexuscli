"""reporter.py — Persist eval runs and compare them against the previous one.

Each run writes a machine-readable report to
``<project>/.nexuscli/evals/last-report.json``. On the next run the previous
report is read back and every scenario gets a comparison verdict:

- ``pass -> fail``          = ``regression``
- ``fail -> pass``          = ``fixed``
- ``pass -> pass``          = ``unchanged``
- ``fail -> fail``          = ``still_failing``
- no previous result        = ``new``

Every entry also records the deterministic offline cost metrics — ``turns``
and ``total_tool_calls`` (there are no tokens in a scripted run, so these are
the "cost"). A same-scenario increase emits a visible warning
(``"4 turns → 7 turns"`` style) and the summary counts
increased/decreased/unchanged scenarios separately; cost never changes a
PASS/FAIL verdict.

Every entry also carries the scenario's ``difficulty`` tier (``short`` /
``medium`` / ``long``, declared in the scenario file, see the loader) and the
summary's ``difficulty`` block aggregates PASS/FAIL and regression counts per
tier, every tier always present. That block is the point of the tiers: when
the top-level ``regressions`` count is non-zero, it shows *which* tier is
degrading. Difficulty never changes a verdict either.

The report is intentionally JSON-serializable plain data (no dataclass dump)
so CI pipelines can diff or publish it directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nexuscli.eval.loader import DIFFICULTY_LEVELS
from nexuscli.eval.runner import EvalRunResult

REPORT_RELATIVE = Path(".nexuscli") / "evals" / "last-report.json"


class PreviousReportError(ValueError):
    """Raised when an existing last-report.json is not a readable report."""


def load_previous_report(project_root: str | Path) -> dict[str, Any] | None:
    """Read the previous report; ``None`` when missing.

    A present-but-corrupt file raises :class:`PreviousReportError` instead of
    being silently ignored: silently dropping a baseline would turn real
    regressions into ``new`` verdicts.
    """
    path = Path(project_root) / REPORT_RELATIVE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreviousReportError(f"{path}: unreadable previous report: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise PreviousReportError(f"{path}: previous report is not a valid eval report")
    return data


def previous_statuses(report: dict[str, Any]) -> dict[str, str]:
    """``name -> "pass" | "fail"`` map from a report document."""
    statuses: dict[str, str] = {}
    for entry in report.get("scenarios", []):
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            statuses[entry["name"]] = "pass" if entry.get("status") == "pass" else "fail"
    return statuses


def comparison_verdict(previous: str | None, current: str) -> str:
    """Compare one scenario's statuses into a comparison verdict."""
    if previous is None:
        return "new"
    if previous == "pass" and current == "fail":
        return "regression"
    if previous == "fail" and current == "pass":
        return "fixed"
    if previous == "fail" and current == "fail":
        return "still_failing"
    return "unchanged"


# Cost metric key -> noun used in the human-readable warning.
_COST_METRICS: tuple[tuple[str, str], ...] = (("turns", "turn"), ("total_tool_calls", "tool call"))


def previous_costs(report: dict[str, Any] | None) -> dict[str, dict[str, int]]:
    """``name -> {"turns": n, "total_tool_calls": m}`` cost baseline from a report.

    Only the metrics actually present (and integer-typed) in each previous
    entry are kept: a report written before the cost fields existed, or a
    hand-fabricated baseline, yields a partial or empty baseline and the
    comparison skips the missing metrics instead of inventing a value.
    """
    costs: dict[str, dict[str, int]] = {}
    for entry in (report or {}).get("scenarios", []):
        if not (isinstance(entry, dict) and isinstance(entry.get("name"), str)):
            continue
        metrics: dict[str, int] = {}
        for key, _noun in _COST_METRICS:
            value = entry.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                metrics[key] = value
        if metrics:
            costs[entry["name"]] = metrics
    return costs


def cost_comparison(
    previous: dict[str, int] | None, *, turns: int, total_tool_calls: int
) -> dict[str, Any]:
    """Diff one scenario's cost metrics against its baseline.

    Returns ``{"change": <change>, "warnings": [...]}`` where ``change`` is
    ``"increased"`` / ``"decreased"`` / ``"unchanged"``, or ``None`` when the
    scenario has no comparable baseline (new scenario, or a previous entry
    without cost fields). Any metric going up is an increase and gets its own
    warning line (``"4 turns → 7 turns"``); a decrease only counts when nothing
    went up. Warnings are advisory: they never touch the PASS/FAIL verdict.
    """
    if not previous:
        return {"change": None, "warnings": []}
    current = {"turns": turns, "total_tool_calls": total_tool_calls}
    warnings: list[str] = []
    decreased = False
    for key, noun in _COST_METRICS:
        if key not in previous:
            continue
        old, new = previous[key], current[key]
        if new > old:
            warnings.append(f"{_count(old, noun)} → {_count(new, noun)}")
        elif new < old:
            decreased = True
    if warnings:
        change = "increased"
    elif decreased:
        change = "decreased"
    else:
        change = "unchanged"
    return {"change": change, "warnings": warnings}


def _count(value: int, noun: str) -> str:
    """``"1 turn"`` / ``"4 turns"`` — singular only for exactly one."""
    return f"{value} {noun}" if value == 1 else f"{value} {noun}s"


def build_report(results: list[EvalRunResult], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the report document (entries + summary) for a run."""
    baseline = previous_statuses(previous) if previous else {}
    cost_baseline = previous_costs(previous)
    entries: list[dict[str, Any]] = []
    for result in results:
        current = "pass" if result.passed else "fail"
        previous_status = baseline.get(result.name)
        total_tool_calls = len(result.tool_sequence)
        entry: dict[str, Any] = {
            "name": result.name,
            "status": current,
            "difficulty": result.difficulty,
            "failure_reasons": list(result.failure_reasons),
            "tool_sequence": list(result.tool_sequence),
            "turns": result.turns,
            # Conversation rounds actually run (1 for a single-round scenario).
            "rounds": result.rounds,
            "total_tool_calls": total_tool_calls,
            "comparison": {
                "previous": previous_status,
                "verdict": comparison_verdict(previous_status, current),
            },
            "cost": cost_comparison(
                cost_baseline.get(result.name),
                turns=result.turns,
                total_tool_calls=total_tool_calls,
            ),
        }
        entries.append(entry)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenarios": entries,
        "summary": _summary(entries),
    }


def _summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    verdicts = [entry["comparison"]["verdict"] for entry in entries]
    # Scenarios without a comparable baseline (change is None) land in no
    # cost bucket, so the three counts need not add up to ``total``.
    cost_changes = [entry["cost"]["change"] for entry in entries]
    return {
        "total": len(entries),
        "passed": sum(entry["status"] == "pass" for entry in entries),
        "failed": sum(entry["status"] == "fail" for entry in entries),
        "regressions": verdicts.count("regression"),
        "fixed": verdicts.count("fixed"),
        "cost_increased": cost_changes.count("increased"),
        "cost_decreased": cost_changes.count("decreased"),
        "cost_unchanged": cost_changes.count("unchanged"),
        "difficulty": difficulty_summary(entries),
    }


# Per-tier counters in the summary's ``difficulty`` block, in output order.
_TIER_COUNTERS: tuple[str, ...] = ("total", "passed", "failed", "regressions")


def difficulty_summary(entries: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per-tier PASS/FAIL/regression counts, every tier always present.

    ``{"short": {...}, "medium": {...}, "long": {...}}`` in
    :data:`DIFFICULTY_LEVELS` order, each holding ``total`` / ``passed`` /
    ``failed`` / ``regressions``. Empty tiers are emitted as zeros so the block
    has the same shape on every run (stable for CI diffs, and "no coverage at
    this tier" stays visible). ``regressions`` counts the tier's
    ``pass -> fail`` verdicts — the number that says where a run degraded.

    Only entries tagged with one of the three tiers are counted. Real runs
    always are (the loader validates the tier and fills in the default); a
    fabricated entry carrying anything else lands in no bucket, like an
    unbaselined scenario in the cost counts.
    """
    buckets = {tier: dict.fromkeys(_TIER_COUNTERS, 0) for tier in DIFFICULTY_LEVELS}
    for entry in entries:
        tier = entry.get("difficulty")
        bucket = buckets.get(tier) if isinstance(tier, str) else None
        if bucket is None:
            continue
        bucket["total"] += 1
        bucket["passed" if entry.get("status") == "pass" else "failed"] += 1
        if (entry.get("comparison") or {}).get("verdict") == "regression":
            bucket["regressions"] += 1
    return buckets


def write_report(project_root: str | Path, report: dict[str, Any]) -> Path:
    """Write the report under ``<project_root>/.nexuscli/evals/last-report.json``."""
    path = Path(project_root) / REPORT_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def format_report(report: dict[str, Any]) -> list[str]:
    """Human-readable lines for the CLI: one per scenario, a ``COST`` warning
    line right under any scenario whose cost grew, then two summary lines —
    the overall counts and the per-tier ``difficulty:`` breakdown (which tier
    the failures and regressions sit in)."""
    lines: list[str] = []
    for entry in report.get("scenarios", []):
        name = entry.get("name", "?")
        status = entry.get("status", "?")
        comparison = entry.get("comparison") or {}
        verdict = comparison.get("verdict", "new")
        mark = "PASS" if status == "pass" else "FAIL"
        line = f"{mark}\t{name}\t[{verdict}]"
        if status == "fail":
            reasons = entry.get("failure_reasons") or ["unknown failure"]
            line += "\t" + "; ".join(str(reason) for reason in reasons)
        lines.append(line)
        warnings = (entry.get("cost") or {}).get("warnings") or []
        if warnings:
            lines.append(f"COST\t{name}\t" + "; ".join(str(warning) for warning in warnings))
    summary = report.get("summary") or {}
    lines.append(
        f"total={summary.get('total', 0)} passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} regressions={summary.get('regressions', 0)} "
        f"fixed={summary.get('fixed', 0)} "
        f"cost_increased={summary.get('cost_increased', 0)} "
        f"cost_decreased={summary.get('cost_decreased', 0)} "
        f"cost_unchanged={summary.get('cost_unchanged', 0)}"
    )
    lines.append(format_difficulty_line(summary.get("difficulty")))
    return lines


def format_difficulty_line(tiers: dict[str, Any] | None) -> str:
    """The per-tier summary line, e.g. ``difficulty: short total=2 passed=2
    failed=0 regressions=0 | medium ... | long ...``.

    Always lists all three tiers in :data:`DIFFICULTY_LEVELS` order so the eye
    can run across them and spot the tier whose ``failed`` / ``regressions``
    moved; a missing or partial block renders as zeros.
    """
    segments: list[str] = []
    for tier in DIFFICULTY_LEVELS:
        counts = (tiers or {}).get(tier) or {}
        segments.append(
            f"{tier} " + " ".join(f"{key}={counts.get(key, 0)}" for key in _TIER_COUNTERS)
        )
    return "difficulty: " + " | ".join(segments)
