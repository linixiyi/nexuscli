"""skill.py — /skill subcommand handler (list, show, on/off, reload, one-shot
load, evolve, stats, rollback, propose, proposals)."""

from __future__ import annotations

import contextlib
import sys
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.prompt import Prompt

from nexuscli.agent import Agent
from nexuscli.entrypoints.commands.goal import _push_turn_context
from nexuscli.skill import SkillRegistry, SkillStateStore
from nexuscli.skill.evolve import SkillEvolutionStore
from nexuscli.skill.propose import (
    ProposalGenerator,
    ProposalParseError,
    ProposeCredentialsError,
    ReportUnavailableError,
    digest_has_signal,
    list_proposal_records,
    load_report,
    save_proposal_record,
    summarize_report,
)


def _skill_command(arg: str, console: Console, cwd: str, agent: Agent) -> None:
    """Handle /skill subcommands; `load` one-shot injects a skill into the next prompt."""
    registry = SkillRegistry(cwd)
    sub, _, rest = arg.partition(" ")
    # `/skill load <name>` rewrites the next prompt: the skill is pushed onto
    # the agent's one-shot skill_context_buffer, which the next turn drains and
    # prepends to the user message as `## Loaded Skill: <name>` (see
    # agent._prepend_skill_context). Words after the skill name (ZCode's
    # optional `[task]` suffix) are deliberately ignored — minimal slice.
    if sub == "load" and rest:
        name = rest.split()[0]
        skill = SkillRegistry(cwd).load(name)
        if not skill:
            console.print(f'Skill "{name}" not found.')
            return
        _push_turn_context(agent, skill.name, skill.content)
        # Usage accounting at the real injection seam: the body queued here is
        # what the next turn prepends to the user message. Accounting must
        # never block a load, so IO errors are swallowed.
        with contextlib.suppress(OSError):
            SkillStateStore().record_use(skill.name)
        console.print(
            f'[green]Skill "{skill.name}" loaded; '
            "it will be prepended to your next message.[/green]"
        )
        return
    if sub == "show" and rest:
        skill = registry.load(rest.strip())
        if not skill:
            console.print(f'Skill "{rest.strip()}" not found.')
            return
        console.print(skill.content[:12_000])
        return
    if sub == "on" and rest:
        console.print("enabled" if registry.enable(rest.strip()) else "skill not found")
        return
    if sub == "off" and rest:
        console.print("disabled" if registry.disable(rest.strip()) else "skill not found")
        return
    if sub == "reload":
        registry.reload()
        console.print("skills reloaded")
        return
    if sub == "evolve" and rest:
        _skill_evolve(rest, console)
        return
    if sub == "rollback" and rest:
        _skill_rollback(rest, console)
        return
    if sub == "propose":
        _skill_propose(rest, console, cwd, agent)
        return
    if sub == "proposals":
        _skill_proposals(rest, console)
        return
    if sub == "stats":
        _skill_stats(registry, console)
        return
    rows = registry.all_skills()
    if sub and sub not in {"list", "ls"}:
        # /skill <name> used to fall through to the full table, which looked
        # like the command had acted on that name. Say plainly that the name is
        # unknown, then show what is available.
        names = ", ".join(item.name for item in rows[:12]) or "(none)"
        console.print(f'Unknown skill: "{sub}". Available: {names}')
        if len(rows) > 12:
            console.print(f"[dim]…and {len(rows) - 12} more; run /skill to list all.[/dim]")
        return
    lines = [
        f"{item.name}\t{item.source}\t{'on' if item.enabled else 'off'}\t{item.description}"
        for item in rows
    ]
    console.print("\n".join(lines) or "(no skills)")


def _skill_evolve(rest: str, console: Console) -> None:
    """`/skill evolve <name> <rule...>` — snapshot, then append a rule entry."""
    name, _, rule = rest.partition(" ")
    name = name.strip()
    rule = rule.strip()
    if not name or not rule:
        console.print("usage: /skill evolve <name> <rule...>")
        return
    store = SkillEvolutionStore()
    try:
        version = store.save_versioned(name, rule, reason="evolve")
    except FileNotFoundError:
        console.print(f'Skill "{name}" not found in user skills ({store.skill_path(name).parent}).')
        return
    except ValueError as exc:
        console.print(f"evolve failed: {exc}")
        return
    console.print(
        f'[green]Rule appended to "{name}"; pre-change state kept as v{version} '
        f"({store.skill_path(name)}).[/green]"
    )


def _skill_rollback(rest: str, console: Console) -> None:
    """`/skill rollback <name> <version>` — restore a snapshot of the skill."""
    name, _, version_text = rest.partition(" ")
    name = name.strip()
    store = SkillEvolutionStore()
    try:
        version = int(version_text.strip())
    except ValueError:
        console.print("usage: /skill rollback <name> <version>")
        return
    try:
        safety_version = store.rollback(name, version)
    except FileNotFoundError:
        console.print(f'Skill "{name}" not found in user skills ({store.skill_path(name).parent}).')
        return
    except ValueError as exc:
        console.print(f"rollback failed: {exc}")
        return
    if safety_version is None:
        console.print(f'Version v{version} of "{name}" not found (see /skill stats).')
        return
    console.print(
        f'[green]"{name}" rolled back to v{version}; prior state kept as v{safety_version}.[/green]'
    )


def _skill_stats(registry: SkillRegistry, console: Console) -> None:
    """`/skill stats` — per-skill usage counters and evolution history."""
    store = SkillEvolutionStore()
    usage = SkillStateStore().usage()
    lines = []
    for item in registry.all_skills():
        try:
            info = store.version_info(item.name)
        except ValueError:
            # A hand-written frontmatter name that is not a slug (e.g.
            # ``Demo``) cannot key the version store; degrade this one row
            # to version=0/history=0 instead of failing the whole listing.
            info = {"version": 0, "history": []}
        entry = usage.get(item.name)
        if not isinstance(entry, dict):
            entry = {}
        try:
            used = int(entry.get("used", 0) or 0)
        except (TypeError, ValueError):
            used = 0
        last_used = str(entry.get("last_used") or "-")
        lines.append(
            f"{item.name}\tused={used}\tlast_used={last_used}"
            f"\tversion={info['version']}\thistory={len(info['history'])}"
        )
    console.print("\n".join(lines) or "(no skills)")


_PROPOSE_USAGE = "usage: /skill propose <name> [--report hermetic|real]"
_PROPOSALS_USAGE = "usage: /skill proposals <name>"
# Per-item review answers (propose.py's record vocabulary: applied/skipped).
_REVIEW_HELP = "Apply? (y / n / s, s skips the rest)"
_REVIEW_ANSWERS = {"y": "apply", "yes": "apply", "n": "skip", "no": "skip", "s": "skip_rest"}


def _skill_propose(rest: str, console: Console, cwd: str, agent: Agent) -> None:
    """`/skill propose <name> [--report hermetic|real]` — eval-report-driven
    improvement proposals, landed only through per-item human review.

    Honest boundary (spec §2.1): the report is the *evidence base for
    proposing*, not a verifier of landed effect — the hermetic track runs with
    skill injection off, so the record keeps the pre-landing report digest for
    human comparison only.
    """
    name, _, flag_text = rest.partition(" ")
    name = name.strip()
    if not name:
        console.print(_PROPOSE_USAGE)
        return
    try:
        track = _parse_report_track(flag_text)
    except ValueError as exc:
        console.print(f"{_PROPOSE_USAGE} ({exc})")
        return

    store = SkillEvolutionStore()
    try:
        skill_path = store.skill_path(name)
        skill_text = skill_path.read_text(encoding="utf-8")
    except ValueError as exc:
        # An invalid slug (e.g. "BadName") fails the name validation inside
        # skill_path() before any file is touched — report it like evolve and
        # rollback do instead of letting it bubble into the REPL's generic
        # error fallback.
        console.print(f"propose failed: {exc}")
        return
    except FileNotFoundError:
        console.print(f'Skill "{name}" not found in user skills ({skill_path.parent}).')
        return

    # Idle case 1: no report for the track. Read-only consumption of the eval
    # module's output files; this module never writes either of them.
    try:
        report, report_path = load_report(cwd, track)
    except ReportUnavailableError as exc:
        console.print(f"skill propose: {exc}")
        return
    digest = summarize_report(report, track=track)
    # Idle case 2: report exists but carries no proposal signal.
    if not digest_has_signal(digest):
        console.print(
            f"skill propose: {report_path} has no failed scenarios and no cost "
            "warnings — nothing to propose from"
        )
        return

    # The REPL's live client is the injected seam; without one (tests, odd
    # embeddings) the generator builds through the normal config chain and a
    # missing key fails loudly (idle case 3).
    generator = ProposalGenerator(llm_client=getattr(agent, "llm_client", None), cwd=cwd)
    try:
        proposal = generator.generate(skill_text, digest)
    except (ProposeCredentialsError, ProposalParseError) as exc:
        console.print(f"skill propose: {exc}")
        return
    if not proposal.items:
        dropped = f" ({proposal.dropped} dropped)" if proposal.dropped else ""
        console.print(
            f'skill propose: no evidence-backed proposals for "{name}"{dropped}; nothing to review'
        )
        return

    interactive = sys.stdin.isatty()
    # Fail-closed gate for non-interactive callers (no TTY): the proposal is
    # generated and archived, but nothing lands without a human answering.
    # Today no production path runs /skill propose without a TTY (-p only
    # expands custom slash commands; piped stdin is refused at the REPL
    # entry) — the gate is defense-in-depth and the tests' injection point.
    console.print(
        f'skill propose: {len(proposal.items)} proposal item(s) for "{name}" ({track} report):'
    )
    decisions: list[dict[str, Any]] = []
    applied_versions: list[int] = []
    skip_rest = False
    for number, item in enumerate(proposal.items, start=1):
        entry: dict[str, Any] = {
            "rule": item.rule,
            "evidence": item.evidence,
            "decision": "pending",
            "version": None,
        }
        if not interactive:
            # Fail-closed: nobody can answer here, so nothing is applied and
            # every item stays "pending" in the archived record.
            decisions.append(entry)
            continue
        if skip_rest:
            entry["decision"] = "skipped"
            decisions.append(entry)
            continue
        console.print(f"[bold]{number}. {item.rule}[/bold]")
        # The reason line follows the approval-prompt convention: stacked
        # questions must say which gate is asking and on what grounds.
        console.print(f"[dim]Reason: {item.evidence}[/dim]")
        answer = _interpret_review_answer(_ask_review())
        if answer == "apply":
            try:
                version = store.save_versioned(name, item.rule, reason="propose")
                entry["decision"] = "applied"
                entry["version"] = version
                applied_versions.append(version)
                console.print(f"[green]Applied; pre-change state kept as v{version}.[/green]")
            except (FileNotFoundError, ValueError) as exc:
                entry["decision"] = "failed"
                console.print(f"[red]apply failed: {exc}[/red]")
        else:
            entry["decision"] = "skipped"
            if answer == "skip_rest":
                skip_rest = True
                console.print("[yellow]Skipping the remaining items.[/yellow]")
        decisions.append(entry)

    record = {
        "ts": _utc_now(),
        "skill": name,
        "track": track,
        "report_path": str(report_path),
        # Pre-landing digest: for human comparison only, never an effect
        # verifier (hermetic eval runs with skill injection off).
        "report_summary": digest,
        "proposal": proposal.raw_reply,
        "parse_notes": proposal.parse_notes,
        "items": decisions,
        "applied_versions": applied_versions,
        "interactive": interactive,
    }
    record_path = save_proposal_record(store.root, name, record)
    if not interactive:
        console.print(
            f"[yellow]Proposal archived without landing ({len(decisions)} item(s) "
            f"pending): {record_path}. Review it in the REPL with "
            f"/skill propose {name}.[/yellow]"
        )
        return
    applied = len(applied_versions)
    if applied:
        versions = ", ".join(f"v{version}" for version in applied_versions)
        console.print(
            f'\n[green]Applied {applied}/{len(decisions)} rule(s) to "{name}" '
            f"({versions}); record: {record_path}[/green]"
        )
    else:
        console.print(
            f"\n[yellow]No rules applied ({len(decisions)} skipped); record: {record_path}[/yellow]"
        )


def _skill_proposals(rest: str, console: Console) -> None:
    """`/skill proposals <name>` — archived proposal records (time, items,
    applied count)."""
    name = rest.strip()
    if not name:
        console.print(_PROPOSALS_USAGE)
        return
    store = SkillEvolutionStore()
    try:
        records = list_proposal_records(store.root, name)
    except ValueError as exc:
        console.print(f"proposals failed: {exc}")
        return
    if not records:
        console.print(
            f'No proposal records for "{name}" ({store.root}). Generate one with /skill propose.'
        )
        return
    for path, record in records:
        items = record.get("items") or []
        applied = sum(1 for item in items if item.get("decision") == "applied")
        ts = str(record.get("ts") or path.stem)
        console.print(
            f"{ts}\titems={len(items)}\tapplied={applied}"
            f"\ttrack={record.get('track') or '-'}\t{path}"
        )


def _parse_report_track(flag_text: str) -> str:
    """`--report hermetic|real` (space or = form); anything else is a usage error."""
    tokens = flag_text.split()
    track = "hermetic"
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "--report":
            if index + 1 >= len(tokens):
                raise ValueError("--report needs a value")
            track = tokens[index + 1]
            index += 2
            continue
        if token.startswith("--report="):
            track = token.split("=", 1)[1]
            index += 1
            continue
        raise ValueError(f"unknown argument {token!r}")
    if track not in {"hermetic", "real"}:
        raise ValueError(f"--report must be hermetic or real, got {track!r}")
    return track


def _ask_review() -> str:
    """One review answer. Ctrl+C/Ctrl+D (EOF) skip the rest — fail-closed,
    the same convention as the approval prompt's EOF deny. Esc is NOT
    intercepted: rich's Prompt.ask submits the default answer ``n`` for it
    (skip this item only). Both paths are fail-closed; nothing lands unless
    ``y`` is typed."""
    try:
        return Prompt.ask(_REVIEW_HELP, default="n")
    except (EOFError, KeyboardInterrupt):
        return "s"


def _interpret_review_answer(raw: str) -> str:
    return _REVIEW_ANSWERS.get(raw.strip().lower(), "skip_rest")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
