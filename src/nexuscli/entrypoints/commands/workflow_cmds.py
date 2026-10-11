"""workflow_cmds.py — /workflow and /expert execution: parallel subagent graph dispatch."""

from __future__ import annotations

import asyncio

from rich.console import Console

from nexuscli.agent import Agent
from nexuscli.agent.mailbox import mailbox_root
from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.slash_commands import build_step_dispatch_prompt
from nexuscli.tools.base import ToolContext
from nexuscli.workflow import WorkflowGraph

# Workflow steps never declare an agent type; they always run as the built-in
# general-purpose subagent. Its mailbox identity gets a per-step suffix.
_WORKFLOW_AGENT = "general-purpose"


async def _run_workflow_graph(
    graph: WorkflowGraph,
    console: Console,
    cwd: str,
    config: NexusCliConfig,
    agent: Agent,
) -> None:
    """Dispatch subagents over the graph in dependency-batched parallel waves.

    Each round takes the current ready set (all dependencies completed) and
    dispatches up to ``workflow.max_parallel_steps`` of it concurrently —
    ``asyncio.gather`` over :func:`run_subagent`, ordered by the graph's
    topological order so the progress lines stay deterministic. With
    ``max_parallel_steps=1`` this degenerates to the old serial dispatch.
    Upstream reports are injected into dependent prompts exactly as before:
    a step's prompt is built only once *all* of its ``depends_on`` reports
    have been collected.

    Failure semantics (chosen variant, simplest and testable): when any step
    of a batch fails, the batch's already-running steps are awaited to
    completion — nothing is cancelled, so a sibling step's report is still
    collected — but no further batch is dispatched, the failed step is
    reported and the not-yet-run steps are listed as aborted, then the run
    stops (their upstream inputs would be missing). This is the parallel
    analogue of ZCode's pause-on-failure in workflow/expert/run-loop.ts:172-186.

    Concurrency safety of the dispatched subagents: :func:`run_subagent`
    builds a fresh :class:`Agent`, a fresh :class:`ToolRegistry` and a fresh
    LLM client per call (subagent.py:315-327), the skill context buffer is a
    per-Agent instance attribute (agent.py:117), and each mailbox message is
    one appended JSONL line with OSError swallowed (mailbox.py:108-116).
    Because concurrent same-type siblings would otherwise share — and race
    on — one drain-only inbox, every step is dispatched with a per-step
    ``mailbox_name`` (``general-purpose.<sanitized step id>``, see
    :func:`_step_mailbox_name`), its leftover inbox from earlier runs in the
    same session is removed first (:func:`_reset_mailbox_inbox`), and the
    dispatch prompt advertises the step's own mailbox name plus its batch
    siblings'. The only cross-subagent surface is the shared ``config``
    object, whose ``policy.session_id`` stamping in ``Agent.__init__`` is
    synchronous (agent.py:99-101), so two concurrent subagent constructions
    cannot interleave there.

    Usage coverage: the scheduler itself records nothing — each step's real
    token usage is persisted by ``subagent.run_subagent``'s done-event
    recorder as a ``source=subagent`` row (and runs that produced no usage,
    e.g. scripted stub clients, record nothing rather than a zero-token row).
    """  # noqa: E501 - line references kept for traceability
    # Imported lazily: nexuscli.agent.subagent transitively imports
    # tools.builtins — same anti-cycle reason as _announce_bg_subagents above.
    from nexuscli.agent.subagent import BUILTIN_AGENTS, run_subagent

    context = ToolContext(cwd=cwd, config=config, approval_callback=agent.approval_callback)
    max_parallel = config.workflow.max_parallel_steps
    # The bracket prefix is rich-markup-escaped (\[) so it renders literally.
    prefix = "\\[workflow]"
    order = graph.topological_order()
    reports: dict[str, str] = {}
    # Every step that has already been dispatched (successfully or not) must
    # never be re-selected by ready_steps — a failed step whose dependencies
    # are complete would otherwise loop forever.
    dispatched: set[str] = set()
    failed: dict[str, str] = {}
    # Mailbox names are allocated run-wide so distinct step ids that sanitize
    # to the same base (e.g. "a b" and "a.b") still get distinct inboxes.
    used_mailbox_names: set[str] = set()
    while True:
        ready = graph.ready_steps(dispatched)
        batch = [step_id for step_id in order if step_id in ready][:max_parallel]
        if not batch:
            break
        for step_id in batch:
            position = order.index(step_id) + 1
            console.print(f"{prefix} ({position}/{len(order)}) dispatching step {step_id}")
        mailbox_names = {
            step_id: _step_mailbox_name(step_id, used_mailbox_names) for step_id in batch
        }
        for mailbox_name in mailbox_names.values():
            _reset_mailbox_inbox(context, mailbox_name)
        prompts = {
            step_id: build_step_dispatch_prompt(
                graph,
                step_id,
                {dep: reports[dep] for dep in graph.steps[step_id].depends_on},
                mailbox_name=mailbox_names[step_id],
                peer_mailbox_names=[
                    mailbox_names[sibling] for sibling in batch if sibling != step_id
                ],
            )
            for step_id in batch
        }
        # return_exceptions=True keeps sibling steps running to completion:
        # the failure check happens only after the whole batch has settled.
        coros = [
            run_subagent(
                BUILTIN_AGENTS[_WORKFLOW_AGENT],
                prompts[step_id],
                context,
                mailbox_name=mailbox_names[step_id],
            )
            for step_id in batch
        ]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for step_id, result in zip(batch, results, strict=True):
            dispatched.add(step_id)
            if isinstance(result, BaseException):
                message = str(result).strip() or result.__class__.__name__
                failed[step_id] = message.splitlines()[0]
                console.print(f"{prefix} Step {step_id} failed: {failed[step_id]}")
                continue
            reports[step_id] = result
        if failed:
            remaining = [step_id for step_id in order if step_id not in dispatched]
            if remaining:
                console.print(f"{prefix} 中止剩余步骤：{', '.join(remaining)}")
            return
    console.print(f"{prefix} Workflow complete — {len(reports)} step reports:")
    for step_id in order:
        console.print(f"=== {step_id} ===")
        console.print(reports[step_id])


def _step_mailbox_name(step_id: str, used: set[str]) -> str:
    """Sanitize a step id into a unique per-step mailbox name for this run.

    Mailbox agent names must match ``[A-Za-z0-9][A-Za-z0-9._-]*``
    (mailbox._MAILBOX_NAME_RE), while workflow step ids are free-form
    non-empty strings: characters outside the whitelist (spaces, CJK, path
    separators) collapse to ``_``, and sanitized collisions get a numeric
    suffix so every step of the run keeps its own inbox. The fixed
    ``general-purpose.`` prefix also guarantees a valid first character.
    """
    base = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in step_id
    ).strip("._-")
    name = f"{_WORKFLOW_AGENT}.{base or 'step'}"
    candidate, suffix = name, 1
    while candidate in used:
        suffix += 1
        candidate = f"{name}-{suffix}"
    used.add(candidate)
    return candidate


def _reset_mailbox_inbox(context: ToolContext, mailbox_name: str) -> None:
    """Delete a step's leftover inbox so each dispatch starts with an empty one.

    Mailbox reads drain (truncate) rather than mark-read, so a stale inbox
    from an earlier workflow run in the same session would be
    indistinguishable from live messages. Best-effort like every mailbox
    operation: an OSError leaves the old file in place and the run continues.
    """
    try:
        inbox = mailbox_root(context) / f"{mailbox_name}.jsonl"
        inbox.unlink(missing_ok=True)
    except OSError:
        return
