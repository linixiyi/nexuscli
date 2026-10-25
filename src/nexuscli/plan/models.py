from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskType(StrEnum):
    PLANNING = "PLANNING"
    FILE_READ = "FILE_READ"
    FILE_WRITE = "FILE_WRITE"
    COMMAND = "COMMAND"
    ANALYSIS = "ANALYSIS"
    VERIFICATION = "VERIFICATION"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PlanStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class Task:
    id: str
    description: str
    type: TaskType = TaskType.ANALYSIS
    dependencies: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0

    def add_dependency(self, task_id: str) -> None:
        if task_id not in self.dependencies:
            self.dependencies.append(task_id)

    def add_dependent(self, task_id: str) -> None:
        if task_id not in self.dependents:
            self.dependents.append(task_id)

    def mark_started(self) -> None:
        self.status = TaskStatus.RUNNING
        self.start_time = time.time()

    def mark_completed(self, result: str) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.end_time = time.time()

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.error = error
        self.end_time = time.time()

    def is_executable(self, all_tasks: dict[str, Task]) -> bool:
        if self.status != TaskStatus.PENDING:
            return False
        return all(
            dep_id in all_tasks and all_tasks[dep_id].status == TaskStatus.COMPLETED
            for dep_id in self.dependencies
        )

    def to_dict(self) -> dict[str, Any]:
        """Hand-written serialization (slots dataclass): StrEnum fields by ``.value``."""
        return {
            "id": self.id,
            "description": self.description,
            "type": self.type.value,
            "dependencies": list(self.dependencies),
            "dependents": list(self.dependents),
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        return cls(
            id=str(data.get("id") or ""),
            description=str(data.get("description") or ""),
            type=_task_type_from(str(data.get("type") or TaskType.ANALYSIS.value)),
            dependencies=[str(dep) for dep in (data.get("dependencies") or [])],
            dependents=[str(dep) for dep in (data.get("dependents") or [])],
            status=_task_status_from(str(data.get("status") or TaskStatus.PENDING.value)),
            result=str(data.get("result") or ""),
            error=str(data.get("error") or ""),
            start_time=_float_field(data, "start_time"),
            end_time=_float_field(data, "end_time"),
        )


@dataclass(slots=True)
class ExecutionPlan:
    id: str
    goal: str
    tasks: dict[str, Task] = field(default_factory=dict)
    status: PlanStatus = PlanStatus.CREATED
    summary: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    # F9：创建该 plan 的 Agent.run() 的 run_id（两次 checkpoint 之间不变）；
    # /plan --resume 载入后沿用，工具日志的 hash 级去重以此为前提。
    run_id: str = ""
    _execution_order: list[str] = field(default_factory=list)

    def add_task(self, task: Task) -> None:
        self.tasks[task.id] = task
        for dep_id in task.dependencies:
            dep = self.tasks.get(dep_id)
            if dep:
                dep.add_dependent(task.id)
        for existing in self.tasks.values():
            if task.id in existing.dependencies:
                task.add_dependent(existing.id)
        self._execution_order.clear()

    def get_task(self, task_id: str) -> Task | None:
        return self.tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self.tasks.values())

    def executable_tasks(self) -> list[Task]:
        return [task for task in self.tasks.values() if task.is_executable(self.tasks)]

    def compute_execution_order(self) -> bool:
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(task: Task) -> bool:
            if task.id in visiting:
                return False
            if task.id in visited:
                return True
            visiting.add(task.id)
            for dep_id in task.dependencies:
                dep = self.tasks.get(dep_id)
                if dep and not visit(dep):
                    return False
            visiting.remove(task.id)
            visited.add(task.id)
            order.append(task.id)
            return True

        for task in self.tasks.values():
            if task.id not in visited and not visit(task):
                return False
        self._execution_order = order
        return True

    def execution_order(self) -> list[str]:
        if not self._execution_order:
            self.compute_execution_order()
        return list(self._execution_order)

    def execution_batches(self) -> list[list[Task]]:
        remaining = set(self.tasks)
        completed: set[str] = set()
        batches: list[list[Task]] = []
        while remaining:
            batch_ids = [
                task_id
                for task_id in self.execution_order()
                if task_id in remaining
                and all(dep_id in completed for dep_id in self.tasks[task_id].dependencies)
            ]
            if not batch_ids:
                break
            batch = [self.tasks[task_id] for task_id in batch_ids]
            batches.append(batch)
            completed.update(batch_ids)
            remaining.difference_update(batch_ids)
        return batches

    def is_all_completed(self) -> bool:
        return all(task.status == TaskStatus.COMPLETED for task in self.tasks.values())

    def has_failed(self) -> bool:
        return any(task.status == TaskStatus.FAILED for task in self.tasks.values())

    def mark_started(self) -> None:
        self.status = PlanStatus.RUNNING
        self.start_time = time.time()

    def mark_completed(self) -> None:
        self.status = PlanStatus.COMPLETED
        self.end_time = time.time()

    def mark_failed(self) -> None:
        self.status = PlanStatus.FAILED
        self.end_time = time.time()

    def summarize(self) -> str:
        batches = self.execution_batches()
        # After a --resume the first batch is often all COMPLETED; showing it
        # as 首批任务 reads like those tasks will run again. Point the line at
        # the first batch that still has pending work instead.
        pending_batches = [
            batch for batch in batches if any(task.status != TaskStatus.COMPLETED for task in batch)
        ]
        first_batch = (
            ", ".join(task.id for task in pending_batches[0]) if pending_batches else "无(全部完成)"
        )
        final_batch = ", ".join(task.id for task in batches[-1]) if batches else "无"
        return (
            f"计划 {self.id}：{self.summary or self.goal}\n"
            f"任务数：{len(self.tasks)} | 并行批次：{len(batches)} | "
            f"当前可执行：{len(self.executable_tasks())}\n"
            f"首批任务：{first_batch}\n"
            f"最终汇总：{final_batch}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Hand-written serialization (slots dataclass): StrEnum fields by ``.value``.

        ``_execution_order`` is derived state — recomputed by
        :meth:`compute_execution_order` on restore, never serialized.
        """
        return {
            "id": self.id,
            "goal": self.goal,
            "tasks": {task_id: task.to_dict() for task_id, task in self.tasks.items()},
            "status": self.status.value,
            "summary": self.summary,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        plan = cls(
            id=str(data.get("id") or ""),
            goal=str(data.get("goal") or ""),
        )
        plan.run_id = str(data.get("run_id") or "")
        plan.summary = str(data.get("summary") or "")
        plan.status = _plan_status_from(str(data.get("status") or PlanStatus.CREATED.value))
        plan.start_time = _float_field(data, "start_time")
        plan.end_time = _float_field(data, "end_time")
        tasks = data.get("tasks")
        # add_task re-wires dependents, so the graph edges survive restore.
        for task_data in (tasks or {}).values() if isinstance(tasks, dict) else ():
            if isinstance(task_data, dict):
                plan.add_task(Task.from_dict(task_data))
        plan.compute_execution_order()
        return plan


def _float_field(data: dict[str, Any], key: str, default: float = 0.0) -> float:
    """Garbage numeric fields degrade to the default instead of raising."""
    try:
        return float(data.get(key) or 0.0)
    except (TypeError, ValueError):
        return default


def _task_type_from(value: str) -> TaskType:
    try:
        return TaskType(value)
    except ValueError:
        return TaskType.ANALYSIS


def _task_status_from(value: str) -> TaskStatus:
    try:
        status = TaskStatus(value)
    except ValueError:
        return TaskStatus.PENDING
    if status is TaskStatus.RUNNING:
        # A crash inside a parallel apply window can persist RUNNING tasks.
        # On restore nothing is actually running, and ``is_executable`` only
        # accepts PENDING — a literal RUNNING restore would stall the plan
        # forever (the loop would break into the "stalled" FAILED path).
        # Normalize back to PENDING so resume re-executes the task.
        return TaskStatus.PENDING
    return status


def _plan_status_from(value: str) -> PlanStatus:
    try:
        return PlanStatus(value)
    except ValueError:
        return PlanStatus.CREATED
