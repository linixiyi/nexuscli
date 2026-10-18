"""skill.py — /skill subcommand handler (list, show, on/off, reload, one-shot
load, evolve, stats, rollback)."""

from __future__ import annotations

import contextlib

from rich.console import Console

from nexuscli.agent import Agent
from nexuscli.entrypoints.commands.goal import _push_turn_context
from nexuscli.skill import SkillRegistry, SkillStateStore
from nexuscli.skill.evolve import SkillEvolutionStore


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
