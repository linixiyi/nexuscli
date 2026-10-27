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
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Coroutine
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
# eval/reporter.py REPORT_RELATIVE and eval/real.py REAL_REPORT_RELATIVE
# (read-only consumption: this module never writes either file).
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
