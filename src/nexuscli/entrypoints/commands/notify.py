"""notify.py — One-shot completion notices for finished background subagents."""

from __future__ import annotations

from rich.console import Console


def _announce_bg_subagents(console: Console) -> None:
    """Drain finished background subagent tasks and print a one-shot summary.

    Purely synchronous and non-blocking: only tasks that already reached a
    terminal state are announced, all of the current batch in one call;
    still-running subagents stay silent and are picked up by a later turn.
    Notices are for the user only — they are never injected into the model
    context.
    """
    # Imported lazily: nexuscli.agent.bg_tasks transitively imports the
    # subagent runner, which imports tools.builtins — same anti-cycle reason
    # as the task handler there.
    from nexuscli.agent.bg_tasks import bg_subagent_registry

    # The bracket prefix is rich-markup-escaped (\[) so it renders literally.
    prefix = "\\[bg-subagent]"
    for note in bg_subagent_registry.drain_notifications():
        agent_type = note["agent_type"]
        description = note["description"]
        task_id = note["task_id"]
        if note["status"] == "completed":
            console.print(
                f'{prefix} completed {agent_type} "{description}" '
                f"(task {task_id}) — report: {note['report_path']}"
            )
        else:
            console.print(
                f'{prefix} failed {agent_type} "{description}" (task {task_id}): {note["error"]}'
            )
