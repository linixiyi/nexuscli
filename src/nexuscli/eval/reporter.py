"""reporter.py — Persist eval runs and compare them against the previous one.

Each run writes a machine-readable report to
``<project>/.nexuscli/evals/last-report.json``. On the next run the previous
report is read back and every scenario gets a comparison verdict:

- ``pass -> fail``          = ``regression``
- ``fail -> pass``          = ``fixed``
- ``pass -> pass``          = ``unchanged``
- ``fail -> fail``          = ``still_failing``
- no previous result        = ``new``

The report is intentionally JSON-serializable plain data (no dataclass dump)
so CI pipelines can diff or publish it directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

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


def build_report(results: list[EvalRunResult], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the report document (entries + summary) for a run."""
    baseline = previous_statuses(previous) if previous else {}
    entries: list[dict[str, Any]] = []
    for result in results:
        current = "pass" if result.passed else "fail"
        previous_status = baseline.get(result.name)
        entry: dict[str, Any] = {
            "name": result.name,
            "status": current,
            "failure_reasons": list(result.failure_reasons),
            "tool_sequence": list(result.tool_sequence),
            "turns": result.turns,
            "comparison": {
                "previous": previous_status,
                "verdict": comparison_verdict(previous_status, current),
            },
        }
        entries.append(entry)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scenarios": entries,
        "summary": _summary(entries),
    }


def _summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    verdicts = [entry["comparison"]["verdict"] for entry in entries]
    return {
        "total": len(entries),
        "passed": sum(entry["status"] == "pass" for entry in entries),
        "failed": sum(entry["status"] == "fail" for entry in entries),
        "regressions": verdicts.count("regression"),
        "fixed": verdicts.count("fixed"),
    }


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
    """Human-readable lines (one per scenario + a summary line) for the CLI."""
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
    summary = report.get("summary") or {}
    lines.append(
        f"total={summary.get('total', 0)} passed={summary.get('passed', 0)} "
        f"failed={summary.get('failed', 0)} regressions={summary.get('regressions', 0)} "
        f"fixed={summary.get('fixed', 0)}"
    )
    return lines
