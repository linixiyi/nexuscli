"""graph.py — JSON step-graph schema for user-defined multi-step workflows.

Pure schema engine: standard library only, no network, no execution. A
workflow is a JSON object ``{"steps": [{"id", "prompt", "depends_on"}]}``;
:func:`load_workflow` validates it and :class:`WorkflowGraph` computes ready
steps and a topological order over it.

DAG semantics mirror ``nexuscli/plan/models.py``: ``WorkflowGraph.ready_steps``
is the same "all dependencies completed" predicate as ``Task.is_executable``
(models.py:68-74), and ``WorkflowGraph.topological_order`` is the same DFS
post-order as ``ExecutionPlan.compute_execution_order`` (models.py:108-132) —
tests/test_workflow.py pins the two side by side.

Minimal-slice trade-off (spec C8): serial dispatch only. There is NO parallel
executor in this slice — ``ready_steps`` is the semantic anchor a future
concurrent frontier would build on, while the current scheduler (repl
``_run_workflow_graph``) walks ``topological_order`` one step at a time. Also
not ported from ZCode (core/src/workflow/): artifact persistence and snapshot
storage, the concurrent scheduler frontier (``maxConcurrentLoops``), critic
iteration loops, clarify rounds, resume/pause semantics, and strategy
configuration — this engine is an in-memory graph with console-side report
collection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class WorkflowError(ValueError):
    """Raised for any workflow load/validation failure; caught as ValueError."""


@dataclass(slots=True)
class WorkflowStep:
    """One node of the step graph: a prompt to dispatch plus its dependencies."""

    id: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)


@dataclass(slots=True)
class WorkflowGraph:
    """Step graph keyed by step id; the dict keeps definition order."""

    steps: dict[str, WorkflowStep]

    def ready_steps(self, completed: set[str]) -> list[str]:
        """Steps whose dependencies are all completed and that are not done.

        Same "dependencies fully completed" predicate as ``Task.is_executable``
        in plan/models.py — the anchor for a future parallel scheduler.
        """
        return [
            step.id
            for step in self.steps.values()
            if step.id not in completed and set(step.depends_on) <= completed
        ]

    def topological_order(self) -> list[str]:
        """DFS post-order (dependencies first), stable within a level.

        Mirrors ``ExecutionPlan.compute_execution_order`` (plan/models.py):
        same visiting/visited two-set walk, but cycles are already rejected at
        load time, so this always returns a complete order.
        """
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visited:
                return
            visiting.add(step_id)
            for dep_id in self.steps[step_id].depends_on:
                if dep_id in self.steps:
                    visit(dep_id)
            visiting.remove(step_id)
            visited.add(step_id)
            order.append(step_id)

        for step_id in self.steps:
            visit(step_id)
        return order


def load_workflow(payload: object) -> WorkflowGraph:
    """Validate an already-parsed JSON object into a :class:`WorkflowGraph`.

    Every failure raises :class:`WorkflowError` with the offending position and
    value. Unknown top-level keys are silently ignored (same lenient stance as
    the plugin manifest loader).
    """
    if not isinstance(payload, dict):
        raise WorkflowError(f"workflow payload must be a JSON object, got {type(payload).__name__}")
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list):
        raise WorkflowError(f"workflow 'steps' must be a list, got {raw_steps!r}")
    if not raw_steps:
        raise WorkflowError("workflow must contain at least one step ('steps' is empty)")

    steps: dict[str, WorkflowStep] = {}
    for index, raw_step in enumerate(raw_steps):
        if not isinstance(raw_step, dict):
            raise WorkflowError(f"workflow step #{index} must be an object, got {raw_step!r}")
        step_id = raw_step.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise WorkflowError(
                f"workflow step #{index} needs a non-empty string 'id', got {step_id!r}"
            )
        step_id = step_id.strip()
        if step_id in steps:
            raise WorkflowError(f"workflow step id '{step_id}' is duplicated")
        prompt = raw_step.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise WorkflowError(
                f"workflow step '{step_id}' needs a non-empty string 'prompt', got {prompt!r}"
            )
        raw_depends = raw_step.get("depends_on", [])
        if not isinstance(raw_depends, list) or not all(
            isinstance(dep, str) for dep in raw_depends
        ):
            raise WorkflowError(
                f"workflow step '{step_id}' needs 'depends_on' to be a list of strings, "
                f"got {raw_depends!r}"
            )
        steps[step_id] = WorkflowStep(
            id=step_id,
            prompt=prompt,
            depends_on=[dep.strip() for dep in raw_depends],
        )

    # Dangling references first — this also covers a self-dependency before
    # the cycle walk would report it as a one-node cycle.
    for step in steps.values():
        for dep_id in step.depends_on:
            if dep_id not in steps:
                raise WorkflowError(f"workflow step '{step.id}' depends on unknown id '{dep_id}'")
    _reject_cycles(steps)
    return WorkflowGraph(steps=steps)


def load_workflow_file(path: Path) -> WorkflowGraph:
    """Read and validate a workflow JSON file; all failures become WorkflowError."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkflowError(f"cannot read workflow file {path}: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"workflow file is not valid JSON: {exc}") from exc
    return load_workflow(payload)


def _reject_cycles(steps: dict[str, WorkflowStep]) -> None:
    """DFS cycle check; a visiting-set hit means a cycle (self-dep included)."""
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visiting:
            raise WorkflowError(f"workflow dependency graph contains a cycle at step '{step_id}'")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dep_id in steps[step_id].depends_on:
            visit(dep_id)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in steps:
        visit(step_id)
