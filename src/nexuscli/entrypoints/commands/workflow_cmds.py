"""workflow_cmds.py — /workflow and /expert execution: serial subagent graph dispatch."""

from __future__ import annotations

from rich.console import Console

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.slash_commands import build_step_dispatch_prompt
from nexuscli.tools.base import ToolContext
from nexuscli.workflow import WorkflowGraph


async def _run_workflow_graph(
    graph: WorkflowGraph,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
) -> None:
    """Dispatch subagents over the graph serially in topological order.

    Minimal slice (spec C8): no parallel executor — steps run one at a time
    and each step's report is collected into its dependents' prompts. A step
    failure aborts the remaining steps (their upstream reports would be
    missing), the minimal analogue of ZCode's pause-on-failure in
    workflow/expert/run-loop.ts:172-186. Steps never declare an agent type;
    they always run as the built-in ``general-purpose`` subagent.
    """
    # Imported lazily: nexuscli.agent.subagent transitively imports
    # tools.builtins — same anti-cycle reason as _announce_bg_subagents above.
    from nexuscli.agent.subagent import BUILTIN_AGENTS, run_subagent

    context = ToolContext(cwd=cwd, config=config, approval_callback=agent.approval_callback)
    order = graph.topological_order()
    reports: dict[str, str] = {}
    # The bracket prefix is rich-markup-escaped (\[) so it renders literally.
    prefix = "\\[workflow]"
    for index, step_id in enumerate(order):
        step = graph.steps[step_id]
        prompt = build_step_dispatch_prompt(
            graph,
            step_id,
            {dep: reports[dep] for dep in step.depends_on},
        )
        console.print(f"{prefix} ({index + 1}/{len(order)}) dispatching step {step_id}")
        try:
            report = await run_subagent(BUILTIN_AGENTS["general-purpose"], prompt, context)
        except Exception as exc:  # noqa: BLE001 - one failing step must not kill the REPL
            message = str(exc).strip() or exc.__class__.__name__
            console.print(f"{prefix} Step {step_id} failed: {message.splitlines()[0]}")
            console.print(f"{prefix} 中止剩余步骤：{', '.join(order[index + 1 :])}")
            return
        reports[step_id] = report
    console.print(f"{prefix} Workflow complete — {len(reports)} step reports:")
    for step_id in order:
        console.print(f"=== {step_id} ===")
        console.print(reports[step_id])
