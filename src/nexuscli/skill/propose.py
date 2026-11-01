"""propose.py — eval-report-driven improvement proposals for user skills.

Reads the latest eval report (the hermetic ``last-report.json`` by default,
the real track's ``last-real-report.json`` for ``--report real``), digests
the failing scenarios' ``failure_reasons`` and the cost warnings, and asks
the configured LLM **once** for a structured proposal: rule entries for the
skill's ``## Evolved Rules`` section, each carrying an ``evidence`` field that
must reference something actually in the digest (a failing / cost-warning
scenario name, or a verbatim failure-reason fragment). Items without such a
reference are dropped before a human ever sees them.

Honest boundaries (spec 2026-10-27_add-skill-evolve-proposal §2.1):
- The eval report is the **evidence base for proposing**, never a verifier of
  landed effect: the hermetic track runs with skill injection off
  (``eval/runner.py`` pins ``features.skill = False`` for determinism), so
  "changed skill → hermetic score moves" is not a causal chain this module
  may claim. Proposal records keep the *pre-landing* report digest for human
  comparison only.
- User-scope skills only — the same boundary as ``evolve.py``; the skill text
  is read from ``~/.nexuscli/skills/<name>/SKILL.md``.
- The LLM client is injectable via the constructor parameter; ``None`` builds
  one through the normal config chain, and a missing key fails loudly
  (:class:`ProposeCredentialsError`), never silently.
- :func:`run_post_landing_diff` is the closed loop's second half: after the
  human review lands items, it can rerun the real-track skill-rule-follow
  scenario and diff it against the pre-landing report — only behind a second
  explicit human confirmation, with the real report byte-backed-up before and
  restored after (a failed restore is alarmed, never silent).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexuscli.config import load_config
from nexuscli.llm.factory import create_llm_client
from nexuscli.skill.registry import _validate_skill_slug
from nexuscli.types import Message

# Track -> report file relative to the project root. Kept in step with
# eval/reporter.py REPORT_RELATIVE and eval/real.py REAL_REPORT_RELATIVE.
# Report consumption here (load_report/summarize_report) is read-only — but
# NOT the whole module: the post-landing diff's restore step rewrites the
# real report (backed-up bytes back, or unlink when no file existed) and its
# default runner goes through the real track's own write_real_report. The
# hermetic last-report.json is still never written by this module.
TRACK_REPORTS: dict[str, Path] = {
    "hermetic": Path(".nexuscli") / "evals" / "last-report.json",
    "real": Path(".nexuscli") / "evals" / "last-real-report.json",
}

# A failure-reason fragment short enough to quote but long enough not to match
# by accident; below this length only the scenario name counts as evidence.
_MIN_REASON_FRAGMENT = 12


class ReportUnavailableError(FileNotFoundError):
    """The eval report for the requested track is missing or unreadable."""


class NoProposalInputError(ValueError):
    """The report has no failed scenarios and no cost warnings — nothing to
    propose from."""


class ProposeCredentialsError(RuntimeError):
    """No usable LLM credential/provider for the proposal call."""


class ProposalParseError(ValueError):
    """The LLM reply could not be parsed into a JSON array of rule items."""


@dataclass(slots=True)
class ProposalItem:
    """One proposed rule plus the report evidence it must cite."""

    rule: str
    evidence: str


@dataclass(slots=True)
class SkillProposal:
    """The outcome of one proposal generation call."""

    items: list[ProposalItem]
    raw_reply: str
    dropped: int = 0
    parse_notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Report loading and digesting (read-only)
# ---------------------------------------------------------------------------


def load_report(cwd: str | Path, track: str = "hermetic") -> tuple[dict[str, Any], Path]:
    """Read the track's report file; a missing/unreadable/invalid file raises
    :class:`ReportUnavailableError` (idle case 1) — never a silent skip."""
    relative = TRACK_REPORTS.get(track)
    if relative is None:
        raise ValueError(f"unknown report track {track!r}; expected hermetic or real")
    path = Path(cwd) / relative
    if not path.is_file():
        raise ReportUnavailableError(
            f"eval report not found: {path} — run 'nexuscli eval run' "
            "(or 'nexuscli eval run --real') first"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportUnavailableError(f"{path}: unreadable eval report: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise ReportUnavailableError(f"{path}: not a valid eval report")
    return data, path


def summarize_report(report: dict[str, Any], *, track: str) -> dict[str, Any]:
    """Digest the report into failing scenarios + cost warnings.

    Hermetic shape: per scenario ``status`` / ``failure_reasons`` /
    ``cost.warnings`` (reporter.py). Real shape: per scenario ``runs`` with
    per-run ``status`` / ``failure_reasons`` and a ``pass_k`` block; the real
    track has no cost *warnings*, so its digest carries failures only and the
    report's own summary block (which includes cost totals) for context.
    """
    failures: list[dict[str, Any]] = []
    cost_warnings: list[dict[str, Any]] = []
    for entry in report.get("scenarios", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "?")
        if track == "real":
            for run in entry.get("runs") or []:
                if not isinstance(run, dict) or run.get("status") == "pass":
                    continue
                reasons = _reason_list(run.get("failure_reasons"))
                failures.append(
                    {
                        "scenario": name,
                        "run": run.get("index"),
                        "reasons": reasons,
                    }
                )
            continue
        if entry.get("status") != "pass":
            failures.append(
                {"scenario": name, "reasons": _reason_list(entry.get("failure_reasons"))}
            )
        warnings = [str(w) for w in (entry.get("cost") or {}).get("warnings") or []]
        if warnings:
            cost_warnings.append({"scenario": name, "warnings": warnings})
    return {
        "track": track,
        "generated_at": report.get("generated_at"),
        "failures": failures,
        "cost_warnings": cost_warnings,
        "summary": report.get("summary") or {},
    }


def digest_has_signal(digest: dict[str, Any]) -> bool:
    """False exactly for idle case 2: no failed scenarios, no cost warnings."""
    return bool(digest.get("failures") or digest.get("cost_warnings"))


def _reason_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return ["unknown failure"]
    reasons = [str(reason) for reason in raw if str(reason).strip()]
    return reasons or ["unknown failure"]


# ---------------------------------------------------------------------------
# Proposal generation (one LLM call)
# ---------------------------------------------------------------------------

_PROPOSE_SYSTEM_PROMPT = (
    "You help maintain a NexusCLI skill: a markdown playbook whose '## Evolved "
    "Rules' section accumulates imperative rules. You get the skill's current "
    "text plus a digest of the latest eval report (failing scenarios with "
    "failure reasons, and cost warnings). Propose concrete rule additions that "
    "address that evidence. Reply with ONLY a JSON array, one object per "
    "proposal:\n"
    '[{"rule": "<one imperative rule for ## Evolved Rules>", '
    '"evidence": "<the report scenario name this addresses, plus a short quote '
    'of the failure reason or cost warning>"}]\n'
    "Every evidence must name a scenario exactly as it appears in the digest. "
    "Propose nothing that has no report evidence. At most 5 items."
)


def _user_message(skill_text: str, digest: dict[str, Any]) -> str:
    return (
        "# Skill (current text)\n"
        f"{skill_text}\n\n"
        "# Eval report digest (JSON)\n"
        f"{json.dumps(digest, ensure_ascii=False, indent=2)}\n\n"
        "Reply with the JSON array only."
    )


class ProposalGenerator:
    """One-call proposal generation with an injectable LLM client.

    ``llm_client=None`` builds the client through the normal config chain
    (:func:`nexuscli.config.load_config` → :func:`nexuscli.llm.factory.create_llm_client`);
    a missing key raises :class:`ProposeCredentialsError` with install
    guidance — the same fail-loud contract as the real eval track.
    """

    def __init__(self, llm_client: Any | None = None, cwd: str | Path = "."):
        self._llm_client = llm_client
        self._cwd = cwd

    def generate(self, skill_text: str, digest: dict[str, Any]) -> SkillProposal:
        return _run_async(self._agenerate(skill_text, digest))

    async def _agenerate(self, skill_text: str, digest: dict[str, Any]) -> SkillProposal:
        client = self._llm_client if self._llm_client is not None else _default_client(self._cwd)
        reply = await _collect_text(
            client.chat(
                [Message(role="user", content=_user_message(skill_text, digest))],
                [],
                system_prompt=_PROPOSE_SYSTEM_PROMPT,
            )
        )
        items, notes = _parse_proposal_reply(reply)
        backed = [item for item in items if _evidence_supported(item.evidence, digest)]
        return SkillProposal(
            items=backed,
            raw_reply=reply,
            dropped=len(items) - len(backed),
            parse_notes=notes,
        )


def _default_client(cwd: str | Path) -> Any:
    """Config-chain client for callers without an injected one (idle case 3)."""
    config = load_config(project_root=str(cwd))
    if not config.llm.api_key:
        raise ProposeCredentialsError(
            "skill propose: no LLM API key configured. Set NEXUSCLI_API_KEY "
            "(or the provider's key variable, e.g. DEEPSEEK_API_KEY / GLM_API_KEY), "
            "or add llm.api_key to ~/.nexuscli/config.json or "
            f"{cwd}/.nexuscli/config.json, then rerun '/skill propose <name>'."
        )
    try:
        return create_llm_client(config.llm, telemetry_enabled=config.telemetry.enabled)
    except ValueError as exc:
        raise ProposeCredentialsError(f"skill propose: unusable LLM provider: {exc}") from exc


async def _collect_text(events: Any) -> str:
    """Consume one ``client.chat`` event stream into its text reply (the same
    consumption the eval real track's judge uses)."""
    parts: list[str] = []
    async for event in events:
        if isinstance(event, dict) and event.get("type") == "text_delta":
            parts.append(str(event.get("text") or ""))
    return "".join(parts)


def _parse_proposal_reply(text: str) -> tuple[list[ProposalItem], list[str]]:
    """Parse the JSON-array reply; an unparsable reply fails loudly.

    Notes collect per-entry defects (kept for the record) — only a reply with
    zero parseable rule entries raises.
    """
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*[ \t]*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    notes: list[str] = []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProposalParseError(
            f"proposal reply unparsable (no JSON array): {(text or '')[:200]!r}"
        ) from exc
    if not isinstance(data, list):
        raise ProposalParseError(f"proposal reply is not a JSON array: {(text or '')[:200]!r}")
    items: list[ProposalItem] = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            notes.append(f"item {index}: not an object, dropped at parse")
            continue
        rule = str(entry.get("rule") or "").strip()
        evidence = str(entry.get("evidence") or "").strip()
        if not rule:
            notes.append(f"item {index}: empty rule, dropped at parse")
            continue
        if not evidence:
            notes.append(f"item {index}: empty evidence, dropped by evidence filter")
            continue
        items.append(ProposalItem(rule=rule, evidence=evidence))
    if not items:
        raise ProposalParseError(
            "proposal reply contained no rule entries: "
            + ("; ".join(notes) if notes else repr((text or "")[:200]))
        )
    return items, notes


def _evidence_supported(evidence: str, digest: dict[str, Any]) -> bool:
    """The evidence gate: a proposal lands in the review list only when its
    evidence references the report digest — by naming a failing or
    cost-warning scenario (case-insensitive substring), or by quoting a
    failure-reason fragment verbatim (>= _MIN_REASON_FRAGMENT chars).
    Paraphrased evidence that matches neither is treated as unsupported and
    the item is dropped."""
    text = (evidence or "").strip().lower()
    if not text:
        return False
    for failure in digest.get("failures") or []:
        name = str(failure.get("scenario") or "").strip().lower()
        if name and name in text:
            return True
        for reason in failure.get("reasons") or []:
            fragment = str(reason).strip().lower()
            if len(fragment) >= _MIN_REASON_FRAGMENT and fragment in text:
                return True
    for warning in digest.get("cost_warnings") or []:
        name = str(warning.get("scenario") or "").strip().lower()
        if name and name in text:
            return True
        for line in warning.get("warnings") or []:
            fragment = str(line).strip().lower()
            if len(fragment) >= _MIN_REASON_FRAGMENT and fragment in text:
                return True
    return False


# ---------------------------------------------------------------------------
# Proposal records (~/.nexuscli/skill_history/<name>/proposals/<ts>.json)
# ---------------------------------------------------------------------------


def proposals_dir(store_root: str | Path, name: str) -> Path:
    """Validated proposals directory for ``name`` under the evolution store."""
    return Path(store_root) / _validate_skill_slug(name) / "proposals"


def save_proposal_record(store_root: str | Path, name: str, record: dict[str, Any]) -> Path:
    """Archive one proposal record; collision-safe filename (ts, ts-1, ...)."""
    directory = proposals_dir(store_root, name)
    directory.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stem}.json"
    suffix = 1
    while path.exists():
        path = directory / f"{stem}-{suffix}.json"
        suffix += 1
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def list_proposal_records(store_root: str | Path, name: str) -> list[tuple[Path, dict[str, Any]]]:
    """All archived records for ``name``, oldest first; unreadable files are
    skipped (a corrupt record must not hide the readable history)."""
    directory = proposals_dir(store_root, name)
    if not directory.is_dir():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            records.append((path, data))
    return records


def _run_async(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run the proposal coroutine from sync code — including from inside the
    REPL's running prompt loop, where plain ``asyncio.run`` would raise."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coroutine).result()


# ---------------------------------------------------------------------------
# Post-landing real-eval diff (the closed loop's second half)
# ---------------------------------------------------------------------------

# The scenario the post-landing diff runs: 03 is the skill-rule-follow
# validator (landed 2026-11-02_add-skill-layered-loading) — the real-track
# scenario whose outcome a skill landing can actually move. Loaded with --file
# semantics (one scenario file), never the full set: the full diff belongs to
# the nightly track, and a single run keeps the token cost bounded (spec
# 2026-11-02_add-propose-post-landing-diff §2.4).
_POST_LANDING_SCENARIO_FILE = "03_skill_rule_follow.json"

# The Reason line handed to the confirm callback before anything real happens
# — the approval-prompt convention: a stacked question must say which gate is
# asking, that it costs real gateway tokens, and roughly how many.
POST_LANDING_CONFIRM_REASON = (
    "real gateway call: rerun real-eval scenario 03_skill_rule_follow "
    "(~10k-20k tokens) against the just-landed skill, then restore the "
    "previous last-real-report.json"
)


def _run_blocking(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a sync callable that internally calls ``asyncio.run`` (the real
    track runs one event loop per scenario) from sync code — including from
    inside the REPL's running prompt loop (``_handle_slash`` is async), where
    a direct ``asyncio.run`` would raise. Same contract as :func:`_run_async`
    for coroutine runners."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return fn(*args)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


def _default_post_landing_runner(cwd: str | Path, llm_client_factory: Any | None) -> dict[str, Any]:
    """The real runner behind ``runner=None``: scenario 03 with --file
    semantics, the full ``eval run --real`` chain.

    Load the one scenario file, run it through
    :func:`nexuscli.eval.real.run_real_scenarios`, build and WRITE the real
    report (that write is what the backup/restore around the runner
    protects), and return the report for the diff. The eval imports stay
    inside this function: they pull the agent/tool graph in, and none of it
    is needed until a real run is actually confirmed. A missing key raises
    :class:`nexuscli.eval.real.RealTrackError` with install guidance before
    any scenario starts (real.py fails loudly, never silently).
    """
    from nexuscli.eval.loader import builtin_real_eval_dir, load_real_eval_scenarios
    from nexuscli.eval.real import build_real_report, run_real_scenarios, write_real_report

    base = builtin_real_eval_dir()
    if base is None:
        raise FileNotFoundError(
            "post-landing diff: real-track scenario set (examples/evals/real) "
            f"not found; cannot run {_POST_LANDING_SCENARIO_FILE}"
        )
    scenario_path = base / _POST_LANDING_SCENARIO_FILE
    if not scenario_path.is_file():
        raise FileNotFoundError(f"post-landing diff: scenario file not found: {scenario_path}")
    root = str(Path(cwd).resolve())
    scenarios = load_real_eval_scenarios(scenario_path)
    results = run_real_scenarios(scenarios, root=root, llm_client_factory=llm_client_factory)
    report = build_real_report(results, repeat=1)
    write_real_report(root, report)
    return report


def _parse_report_bytes(raw: bytes | None) -> dict[str, Any] | None:
    """Best-effort parse of the backed-up report for the before side.

    An unreadable pre-landing report means ``before=None``, never a crash —
    the *bytes* are what the restore promises, not the parse.
    """
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _real_scenario_side(
    report: dict[str, Any] | None, *, name: str | None = None
) -> dict[str, Any] | None:
    """One scenario's compared face from a real-track report.

    ``name=None`` takes the first scenario entry (the post-landing run is a
    single-scenario --file run by construction); with ``name`` set it looks
    that scenario up — the before side of the same scenario in the
    pre-landing report. ``None`` means nothing comparable (no report, or the
    scenario is not in it); the record then shows ``before: null`` instead of
    inventing a side. ``status`` is ``pass`` exactly when
    ``pass_k.all_pass``: a real-track scenario only counts as passed when ALL
    its repeats passed (``build_real_report``'s contract), and
    ``failure_reasons`` folds every non-pass run's reasons in run order.
    ``all_pass_rate`` rides with its denominator ``scenarios_total``: the
    before side is usually the full nightly aggregate, the after side a
    single-scenario run — the two rates must never read as like-for-like.
    """
    if not isinstance(report, dict):
        return None
    entries = [entry for entry in report.get("scenarios") or [] if isinstance(entry, dict)]
    if name is None:
        entry = entries[0] if entries else None
    else:
        entry = next((e for e in entries if str(e.get("name") or "") == name), None)
    if entry is None:
        return None
    pass_k = entry.get("pass_k") if isinstance(entry.get("pass_k"), dict) else {}
    all_pass = bool(pass_k.get("all_pass"))
    reasons: list[str] = []
    for run in entry.get("runs") or []:
        if not isinstance(run, dict) or run.get("status") == "pass":
            continue
        reasons.extend(
            str(reason) for reason in run.get("failure_reasons") or [] if str(reason).strip()
        )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "scenario": str(entry.get("name") or "?"),
        "status": "pass" if all_pass else "fail",
        "all_pass": all_pass,
        "failure_reasons": reasons,
        "all_pass_rate": summary.get("all_pass_rate"),
        "scenarios_total": summary.get("scenarios_total"),
    }


def _restore_real_report(path: Path, before_bytes: bytes | None) -> tuple[bool, list[str]]:
    """Restore the pre-run state of the real report file.

    With a backup: the exact bytes go back (``write_bytes`` — no re-encoding,
    no re-serialization). Without one (no report existed before the run): the
    diff run's report is removed again — restoring the file's *absence* is
    the same promise as restoring its bytes; either way the next nightly run
    must find the file exactly as it was, never the single-scenario diff
    report. Failure is loud: ``(False, [warning])`` — the caller archives
    ``report_restored=false`` and alarms once, it never silently pollutes the
    nightly baseline.
    """
    try:
        if before_bytes is None:
            if path.exists():
                path.unlink()
        else:
            path.write_bytes(before_bytes)
        return True, []
    except OSError as exc:
        return False, [
            f"post-landing diff: FAILED to restore {path} ({exc}); the nightly "
            "real-track baseline may be polluted — restore it manually before "
            "the next nightly run"
        ]


def run_post_landing_diff(
    record: dict[str, Any],
    cwd: str | Path,
    runner: Callable[[], dict[str, Any]] | None = None,
    confirm: Callable[[str], bool] | None = None,
    llm_client_factory: Any | None = None,
    *,
    interactive: bool | None = None,
) -> dict[str, Any]:
    """Backup → run → diff → restore, writing ``record['post_landing']``.

    Four steps, each with its own failure path (fields are included per path,
    never filled with placeholders):

    ① Back the real report up in memory (missing file → ``before=None``; an
    unreadable file aborts before anything is touched — no backup, no run).
    ② Call ``runner`` — ``None`` builds the real one: rerun scenario
    03_skill_rule_follow through the normal real-track chain. ③ Diff the
    pre-landing report against the new one for the same scenario (status /
    ``pass_k.all_pass`` / failure reasons / summary ``all_pass_rate``) into
    ``before`` / ``after``. ④ Restore the backed-up bytes (or the file's
    absence); a failed restore archives ``report_restored=false`` plus a
    one-time ``warnings`` entry.

    Degradations: not interactive (caller flag, else ``sys.stdin.isatty()``
    — skill.py's existing gate style) → ``skipped_reason=non-interactive``;
    the confirm callback refusing (or absent — the ``-p`` no-callback
    convention) → ``declined``; a runner failure → ``errors`` with the
    message (the real track's credential errors carry install guidance) and
    the restore still runs.

    ``llm_client_factory`` only reaches the real runner (the real track's own
    test seam); tests otherwise inject ``runner`` directly. Returns the
    record, so callers can archive it.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        record["post_landing"] = {"skipped_reason": "non-interactive"}
        return record
    if confirm is None or not confirm(POST_LANDING_CONFIRM_REASON):
        record["post_landing"] = {"declined": True}
        return record

    report_path = Path(cwd) / TRACK_REPORTS["real"]
    before_bytes: bytes | None = None
    if report_path.is_file():
        try:
            before_bytes = report_path.read_bytes()
        except OSError as exc:
            # No backup, no run: a real run would overwrite the baseline with
            # a single-scenario report and leave no way back.
            record["post_landing"] = {
                "errors": [f"post-landing diff aborted: cannot back up {report_path}: {exc}"]
            }
            return record

    before_report = _parse_report_bytes(before_bytes)
    post: dict[str, Any] = {}
    try:
        try:
            if runner is None:
                report = _run_blocking(_default_post_landing_runner, cwd, llm_client_factory)
            else:
                report = _run_blocking(runner)
        except Exception as exc:  # noqa: BLE001 - every failure mode lands in the record
            post["errors"] = [str(exc)]
            report = None
        if report is not None:
            after_side = _real_scenario_side(report)
            if after_side is None:
                post["errors"] = ["post-landing diff: report carries no scenario entries"]
            else:
                post["scenario"] = after_side["scenario"]
                post["before"] = _real_scenario_side(before_report, name=after_side["scenario"])
                post["after"] = after_side
    except BaseException as exc:
        # KeyboardInterrupt (Ctrl+C mid real run) and friends unwind through
        # here: mark the interruption so the archived record shows a half-run
        # diff instead of a silently missing block, then let it propagate.
        post.setdefault("errors", []).append(
            f"post-landing diff interrupted before completing ({type(exc).__name__})"
        )
        raise
    finally:
        # The restore runs on every path that got past the backup — a runner
        # exception included, and even a KeyboardInterrupt unwinding through
        # here: the nightly baseline must never keep the diff run's
        # single-scenario report.
        restored, warnings = _restore_real_report(report_path, before_bytes)
        post["report_restored"] = restored
        if warnings:
            post["warnings"] = warnings
        record["post_landing"] = post
    return record
