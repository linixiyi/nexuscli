"""``/plan --resume``'s summary must not present completed tasks as upcoming.

Round-1 feedback (spec 2026-10-18_verify-zero-and-yellow): after a resume the
first batch is often all COMPLETED, but the summary's 首批任务 line still
listed it — the user reads that as "task_1 will run again" when execution
actually continues from task_2.
"""

from __future__ import annotations

from nexuscli.plan.models import ExecutionPlan, Task


def test_summary_points_at_first_pending_batch() -> None:
    plan = ExecutionPlan(id="p1", goal="demo")
    first = Task(id="task_1", description="first")
    first.mark_completed("done")
    second = Task(id="task_2", description="second")
    third = Task(id="task_3", description="third")
    plan.add_task(first)
    plan.add_task(second)
    plan.add_task(third)
    plan.tasks["task_2"].add_dependency("task_1")
    plan.tasks["task_3"].add_dependency("task_2")

    text = plan.summarize()
    assert "task_2" in text.split("首批任务：")[1].splitlines()[0]
    assert "task_1" not in text.split("首批任务：")[1].splitlines()[0]


def test_summary_all_completed_reports_done() -> None:
    plan = ExecutionPlan(id="p1", goal="demo")
    for index in (1, 2):
        task = Task(id=f"task_{index}", description=f"t{index}")
        task.mark_completed("done")
        plan.add_task(task)

    assert "无(全部完成)" in plan.summarize()
