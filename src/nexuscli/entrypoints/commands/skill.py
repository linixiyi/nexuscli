"""skill.py — /skill subcommand handler (list, show, on/off, reload, one-shot load)."""

from __future__ import annotations

from rich.console import Console

from nexuscli.agent import Agent
from nexuscli.entrypoints.commands.goal import _push_turn_context
from nexuscli.skill import SkillRegistry


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
    rows = registry.all_skills()
    lines = [
        f"{item.name}\t{item.source}\t{'on' if item.enabled else 'off'}\t{item.description}"
        for item in rows
    ]
    console.print("\n".join(lines) or "(no skills)")
