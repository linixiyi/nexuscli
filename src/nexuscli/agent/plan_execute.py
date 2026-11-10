from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from nexuscli.agent.query import query
from nexuscli.config import NexusCliConfig
from nexuscli.hooks.runner import fire_event, has_hooks
from nexuscli.llm.base import LlmClient
from nexuscli.plan import ExecutionPlan, PlanCheckpointStore, Planner, Task, TaskStatus
from nexuscli.prompt import PromptAssembler
from nexuscli.skill import SkillContextBuffer
from nexuscli.snapshot import SnapshotService
from nexuscli.tools.base import Tool, ToolResult, object_schema
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, Usage

# Task 0034: the one string the plan-mode read-only gate (tools/executor.py)
# puts in every rejected tool result. A worker whose tool calls were rejected
# by the gate cannot have completed its task, so plan_execute matches this
# snippet on the tool_result events query() forwards and blocks the task
# instead of marking it COMPLETED.
PLAN_MODE_REJECTION_SNIPPET = "is not allowed in plan mode"

# Task 0034, clause 1: the read-only planning directive appended to the
# planner's user turns in plan mode — write-file / disk-writing tasks must not
# be scheduled into the executable plan. The planner (plan/planner.py) is
# outside this change's boundary, so the instruction rides the one seam
# plan_execute owns: its client proxy. Obedience is the model's job; write
# work that still slips into a plan is stopped by the executor's read-only
# gate and reported as blocked, never COMPLETED.
_PLAN_MODE_PLANNING_DIRECTIVE = (
    "注意：当前处于 plan 只读模式。计划任务只允许调用这六个只读工具："
    "read_file、glob、grep、search_code、vfs_ls、vfs_read；"
    "禁止安排 bash、execute_command 任务——即使命令看起来只读也不行；"
    "禁止安排 pytest、npm、uv run、安装依赖、构建、写盘类任务。"
    "执行计划只能包含读文件、检索、分析类任务——"
    "执行阶段任何写入或命令操作都会被只读闸拒绝，"
    "请把写入动作留给用户切回默认模式后执行。"
    "统计、查询类诉求一律用只读工具实现，不得排成命令任务："
    "统计行数或文件大小用 read_file 读后自行计数，列目录用 glob 或 vfs_ls，搜索文本用 grep；"
    "例如「统计某文件总行数」= read_file 读取该文件后由你自己数，不得用 wc -l；"
    "任何 bash/execute_command 调用都会被只读闸拒绝并导致任务 blocked。"
)

# Footer of the plan-mode failure summary: the read-only gate's own hint,
# repeated where the run actually ends so the user knows how to proceed.
_PLAN_MODE_FOOTER = (
    "plan 模式是只读模式：请切回默认模式（Shift+Tab，或 -p 时去掉 --mode plan）后重新运行该计划。"
)

# Task 0001 (2026-11-10 plan-worker-readonly): the read-only tools a plan-mode
# task worker may see. This is the runtime half of the double constraint — the
# planner directive (above) narrows what gets scheduled, this whitelist narrows
# what a worker can even see: hidden tools never appear in its tool schemas or
# runtime context. The executor's plan-mode read-only gate (tools/executor.py)
# stays untouched as the enforcement backstop: a worker that nevertheless
# attempts a hidden tool is rejected there and the task is reported as
# blocked, never COMPLETED.
PLAN_MODE_WORKER_TOOLS: frozenset[str] = frozenset(
    {"read_file", "glob", "grep", "search_code", "vfs_ls", "vfs_read"}
)

# Task 0001: the per-worker read-only constraint appended to the worker system
# prompt in plan mode. Names the whitelisted tools explicitly (the filtered
# registry's names also ride the runtime context inside query()), bans the
# write/command tools by name, and tells the worker not to escalate a
# read-only task description into writes or shell commands on its own.
_PLAN_MODE_WORKER_CONSTRAINT = (
    "当前是 plan 只读模式，只能使用上述只读工具"
    "（read_file、glob、grep、search_code、vfs_ls、vfs_read）；"
    "不要调用 bash/execute_command/write_file/edit_file；"
    "任务描述要求只读时不要自行改成写入或命令。"
)


class PlanWorkerToolRegistry(ToolRegistry):
    """Registry view for plan-mode task workers: whitelisted exposure only.

    ``list_names()`` / ``definitions()`` see exactly what was registered into
    the view (the six whitelisted read-only tools), so hidden tools never
    reach the model's tool schemas or runtime context. ``get()`` falls back to
    the base registry: a hidden tool resolves to a non-read-only stub whose
    only job is to land the attempt in the executor's plan-mode read-only
    gate; an unknown name resolves to None (plain unknown-tool error).
    """

    def __init__(self, base: ToolRegistry):
        super().__init__()
        self._base = base

    def get(self, name: str) -> Tool | None:
        tool = super().get(name)
        if tool is not None:
            return tool
        if self._base.get(name) is not None:
            return _hidden_tool_stub(name)
        return None


def _hidden_tool_stub(name: str) -> Tool:
    """Stand-in for a whitelisted-hidden tool the worker attempted anyway.

    ``is_read_only=False`` routes the call into the executor's plan-mode
    read-only gate, which rejects it with the canonical refusal before any
    permission rule, hook, approval or execution can run — the gate stays the
    enforcement backstop (blocked semantics unchanged), and the refusal is
    what the model sees so it can react instead of guessing. The handler is a
    defensive backstop only: on the plan worker's plan-mode config the gate
    always fires first, so it is unreachable.
    """

    async def handler(payload, context):
        _ = payload, context
        return ToolResult(f'Tool "{name}" is not allowed in plan mode.', is_error=True)

    return Tool(
        name=name,
        description="",
        parameters=object_schema({}),
        handler=handler,
        is_read_only=False,
        requires_approval=False,
    )


def filter_registry_for_plan_mode(registry: ToolRegistry) -> ToolRegistry:
    """A plan-mode worker registry view exposing only the whitelisted tools.

    The model-facing face — ``list_names()`` / ``definitions()`` — is the
    six-tool whitelist, so bash/write_file and every other non-whitelisted
    tool never appear in the request's tool schemas or the runtime "Available
    tools" context. ``get()`` still resolves any tool the base registry
    carries: a worker that nevertheless attempts a hidden tool reaches the
    executor's plan-mode read-only gate and is refused there (canonical
    message, task blocked) — the double constraint the docs describe, with
    the gate as the second layer. A name the base registry does not know
    either is a plain unknown-tool error the worker may recover from.
    Whitelisted names missing from the base registry simply do not show up
    (degrades gracefully); the input registry is never mutated.
    """
    view = PlanWorkerToolRegistry(registry)
    for name in sorted(PLAN_MODE_WORKER_TOOLS):
        tool = registry.get(name)
        if tool is not None:
            view.register(tool)
    return view


@dataclass(slots=True)
class TaskRunResult:
    task: Task
    text: str
    usage: Usage
    turns: int
    error: Exception | None = None
    # Task 0013: the messages this task produced beyond the history snapshot
    # it started from; the plan-level history absorbs them in task order.
    messages: list[Message] = field(default_factory=list)
    # Task 0034: set when plan mode's read-only gate rejected this task's tool
    # calls. Such a task is blocked — plan/models.py is outside this change's
    # boundary, so there is no BLOCKED enum value: the status becomes FAILED
    # with the rejection reason in Task.error, and it must never be COMPLETED.
    blocked_reason: str | None = None

    @property
    def tokens(self) -> int:
        return self.usage.total_tokens


class PlanExecuteAgent:
    def __init__(
        self,
        *,
        llm_client: LlmClient,
        tool_registry: ToolRegistry,
        config: NexusCliConfig,
        cwd: str,
        approval_callback=None,
        planner: Planner | None = None,
        max_task_turns: int | None = None,
        checkpoint_store: PlanCheckpointStore | None = None,
        use_journal: bool = False,
        run_id: str | None = None,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.config = config
        self.cwd = cwd
        self.approval_callback = approval_callback
        # F9：plan 自身的 run_id（checkpoint 持久化）；任务 worker 的 query() 共享
        # 这本 journal 与 plan.run_id，恢复路径的副作用去重以此为前提。
        from nexuscli.tools.journal import get_tool_journal

        self._journal = get_tool_journal() if use_journal else None
        self._run_id = run_id or uuid4().hex
        self.planner = planner or Planner(llm_client)
        # Task 0013: an injected planner owns its own context contract; the
        # default one gets the session history seeded per run (run()).
        self._planner_injected = planner is not None
        # Plan task workers share the single agent.max_turns budget with the
        # react loop and team workers; an explicit argument still wins for
        # callers that need a different budget.
        self.max_task_turns = (
            max_task_turns if max_task_turns is not None else self.config.agent.max_turns
        )
        # Checkpointing is on by default (store injectable for tests); team
        # workers that assemble this agent inherit it without any changes.
        self.checkpoint_store = checkpoint_store or PlanCheckpointStore()
        self.history: list[Message] = []
        self._interrupt_requested = asyncio.Event()

    def request_interrupt(self) -> None:
        """Ask the execution loop to stop cooperatively between batches."""
        self._interrupt_requested.set()

    async def run(self, message: str) -> AsyncIterator[dict[str, Any]]:
        # UserPromptSubmit hooks gate the prompt before any planning work: a
        # blocked hook refuses the input without calling the LLM. The react
        # loop runs the same gate in Agent.run, so every entry path is covered
        # exactly once.
        if has_hooks(self.config, "UserPromptSubmit"):
            gate = await fire_event(
                self.config,
                "UserPromptSubmit",
                {"prompt": message},
                self.cwd,
            )
            if gate.blocked:
                yield {
                    "type": "error",
                    "error": RuntimeError(
                        f"Prompt rejected by user-prompt-submit hook: {gate.reason}"
                    ),
                }
                return
            if gate.additional_context:
                message = f"{message}\n\n{gate.additional_context}"
        # Automatic turn snapshots are opt-in (features.auto_snapshot): a full
        # workspace copy per turn is too slow on some disks. Keeping the service
        # unconstructed also skips its pointless mkdir. Manual /snapshot and
        # /restore commands are not gated by this.
        snapshot: SnapshotService | None = (
            SnapshotService(self.cwd) if self.config.features.auto_snapshot else None
        )
        if snapshot is not None:
            with suppress(Exception):
                snapshot.create("pre-turn")
        self._interrupt_requested.clear()
        total_usage = Usage()
        total_turns = 0
        final_text = ""
        try:
            yield {"type": "text_delta", "text": f"正在规划任务：{message}\n\n"}
            yield {"type": "plan_status", "phase": "planning"}
            plan: ExecutionPlan | None = None
            async for event in self._planner_for_turn().stream_plan(message):
                if event.get("type") == "plan_created":
                    plan = event["plan"]
                    continue
                if event.get("type") == "usage":
                    total_usage = total_usage + Usage.from_mapping(event.get("usage") or {})
                yield event
            if plan is None:
                raise ValueError("planner did not produce an execution plan")
            # F9：plan 的 run_id = 创建 run 的 run_id；/plan --resume 载入后沿用，
            # 工具日志的 hash 级去重以此为前提。
            plan.run_id = self._run_id
            self._save_checkpoint(plan)
            yield {"type": "plan_checkpointed", "plan_id": plan.id}
            yield {
                "type": "text_delta",
                "text": f"（checkpoint 已保存：{plan.id}，可用 /plan --resume {plan.id} 续跑）\n\n",
            }
            yield {"type": "text_delta", "text": plan.summarize() + "\n\n"}
            async for event in self._execute_plan(plan):
                if event.get("type") == "usage":
                    total_usage = total_usage + Usage.from_mapping(event.get("usage") or {})
                elif event.get("type") == "plan_task_done":
                    total_turns += int(event.get("turns") or 0)
                    continue
                elif event.get("type") == "text_delta":
                    final_text += str(event.get("text") or "")
                yield event
            self.history = [
                *self.history,
                Message(role="user", content=message),
                Message(role="assistant", content=final_text),
            ]
        except Exception as exc:  # noqa: BLE001
            from nexuscli.agent.results import classify_error

            yield {"type": "error", "error": exc, "error_code": classify_error(exc)}
            return
        finally:
            if snapshot is not None:
                with suppress(Exception):
                    snapshot.create("post-turn")
        done: dict[str, Any] = {
            "type": "done",
            "total_turns": total_turns,
            "total_tokens": total_usage.total_tokens,
            "usage": total_usage.to_dict(),
            "messages": self.history,
        }
        costs = _calculate_costs(self.llm_client, total_usage)
        if costs:
            done["cost"] = costs
        yield done
        # Stop hooks fire once the plan loop ended normally; a block is only a
        # notice to the user, never a forced continuation (same semantics as
        # the react loop).
        if has_hooks(self.config, "Stop"):
            stop = await fire_event(self.config, "Stop", {}, self.cwd)
            if stop.blocked:
                yield {
                    "type": "text_delta",
                    "text": f"\nStop hook: {stop.reason or 'blocked by hook'}\n",
                }

    async def run_resume(self, plan_id: str) -> AsyncIterator[dict[str, Any]]:
        """Resume a checkpointed plan: skip planning, re-enter execution.

        The plan is restored from the checkpoint store; completed tasks keep
        their stored results, and ``executable_tasks()`` only hands out the
        remaining PENDING tasks, so the while-loop below picks up exactly
        where the interrupted run stopped.
        """
        self._interrupt_requested.clear()
        plan = self.checkpoint_store.load(plan_id)
        if plan is None:
            from nexuscli.agent.results import PlanCheckpointMissing

            yield {
                "type": "error",
                "error": PlanCheckpointMissing(f"no plan checkpoint found for id {plan_id!r}"),
                "error_code": "plan_no_checkpoint",
            }
            return
        total_usage = Usage()
        total_turns = 0
        final_text = ""
        try:
            yield {"type": "text_delta", "text": f"恢复计划 {plan.id}：{plan.summarize()}\n\n"}
            async for event in self._execute_plan(plan):
                if event.get("type") == "usage":
                    total_usage = total_usage + Usage.from_mapping(event.get("usage") or {})
                elif event.get("type") == "plan_task_done":
                    total_turns += int(event.get("turns") or 0)
                    continue
                elif event.get("type") == "text_delta":
                    final_text += str(event.get("text") or "")
                yield event
        except Exception as exc:  # noqa: BLE001 - same error-event contract as run()
            from nexuscli.agent.results import classify_error

            yield {"type": "error", "error": exc, "error_code": classify_error(exc)}
            return
        done: dict[str, Any] = {
            "type": "done",
            "total_turns": total_turns,
            "total_tokens": total_usage.total_tokens,
            "usage": total_usage.to_dict(),
            "messages": self.history,
        }
        costs = _calculate_costs(self.llm_client, total_usage)
        if costs:
            done["cost"] = costs
        yield done

    def _save_checkpoint(self, plan: ExecutionPlan) -> None:
        """Persist plan state; a checkpoint failure must never kill the run."""
        with suppress(Exception):
            self.checkpoint_store.save(plan)

    def _planner_for_turn(self) -> Planner:
        """The planner plans on top of the current session history.

        plan/planner.py builds its planning messages itself and is outside this
        change's boundary, so the history rides in through the one seam the
        planner exposes — its llm_client: a thin proxy prefixes the history
        snapshot onto every chat() the planner makes (the planning turn and its
        repair round alike). An injected planner keeps its own contract.

        Task 0034: in plan mode the proxy also appends a read-only planning
        directive to the planner's user turn — the planner must not schedule
        write-file / disk-writing tasks into the executable plan. Whether the
        model obeys is up to the model; a plan that still carries write work
        is caught at execution time by the read-only gate and reported as
        blocked (see _apply_task_result).
        """
        if self._planner_injected:
            return self.planner
        directive = _PLAN_MODE_PLANNING_DIRECTIVE if self.config.policy.plan_mode else ""
        return Planner(_PlannerHistoryClient(self.llm_client, self.history, directive))

    async def _execute_plan(self, plan: ExecutionPlan) -> AsyncIterator[dict[str, Any]]:
        # Task 0034: task id -> rejection reason for every task whose worker
        # ran into the plan-mode read-only gate. Filled as results are applied;
        # drives the non-success terminal summary and the plan_failed event.
        blocked: dict[str, str] = {}
        yield {"type": "text_delta", "text": "开始执行计划……\n\n"}
        yield {"type": "plan_status", "phase": "execution"}
        plan.mark_started()
        while True:
            if self._interrupt_requested.is_set():
                # Cooperative stop between batches: the checkpoint already
                # holds every finished task, so run_resume() picks up here.
                self._save_checkpoint(plan)
                yield {"type": "plan_interrupted", "plan_id": plan.id}
                yield {
                    "type": "text_delta",
                    "text": (
                        f"计划已中断并保存 checkpoint，可用 /plan --resume {plan.id} 续跑。\n\n"
                    ),
                }
                return
            executable = _executable_tasks_in_order(plan)
            if not executable:
                break
            if len(executable) == 1:
                result: TaskRunResult | None = None
                async for event in self._execute_task_stream(plan, executable[0]):
                    if event.get("type") == "plan_task_result":
                        result = event["result"]
                    else:
                        yield event
                if result is None:
                    raise RuntimeError(f"task {executable[0].id} did not produce a result")
                async for event in self._apply_task_result(plan, result, blocked):
                    yield event
                continue
            yield {
                "type": "text_delta",
                "text": (f"正在并行执行：{', '.join(task.id for task in executable)}\n\n"),
            }
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            runners = [
                asyncio.create_task(self._pump_task_events(plan, task, queue))
                for task in executable
            ]
            results: dict[str, TaskRunResult] = {}
            finished = 0
            try:
                while finished < len(runners):
                    event = await queue.get()
                    event_type = event.get("type")
                    if event_type == "plan_task_stream_done":
                        finished += 1
                    elif event_type == "plan_task_result":
                        result = event["result"]
                        results[result.task.id] = result
                    else:
                        yield event
            finally:
                if finished < len(runners):
                    for runner in runners:
                        if not runner.done():
                            runner.cancel()
                await asyncio.gather(*runners, return_exceptions=True)

            for task in executable:
                result = results.get(task.id)
                if result is None:
                    result = TaskRunResult(
                        task=task,
                        text="",
                        usage=Usage(),
                        turns=0,
                        error=RuntimeError(f"task {task.id} did not produce a result"),
                    )
                async for event in self._apply_task_result(plan, result, blocked):
                    yield event

        if plan.has_failed():
            plan.mark_failed()
            if self.config.policy.plan_mode:
                # Task 0034: a plan-mode run that ended with blocked or failed
                # tasks must not report success — the summary says so, and the
                # plan_failed event lets the CLI (-p --mode plan) exit
                # non-zero instead of pretending the run completed.
                yield {
                    "type": "plan_failed",
                    "plan_id": plan.id,
                    "blocked_tasks": dict(blocked),
                }
                yield {
                    "type": "text_delta",
                    "text": _build_plan_result(
                        plan,
                        header=_plan_mode_failure_header(blocked),
                        footer=_PLAN_MODE_FOOTER,
                    ),
                }
            else:
                yield {"type": "text_delta", "text": "计划部分完成，但有任务执行失败。\n\n"}
        elif plan.is_all_completed():
            plan.mark_completed()
            yield {"type": "text_delta", "text": _build_plan_result(plan)}
        else:
            plan.mark_failed()
            yield {
                "type": "text_delta",
                "text": "计划已停滞，因为任务依赖未满足。\n\n",
            }
        # Terminal state is checkpointed too, so a finished plan's store entry
        # reflects completion instead of a stale mid-run state.
        self._save_checkpoint(plan)

    async def _apply_task_result(
        self,
        plan: ExecutionPlan,
        result: TaskRunResult,
        blocked: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
        # Task 0013: the task's own messages join the plan-level history in
        # task order (sequential batches apply immediately; a parallel batch
        # applies in execution order once every worker finished). Later tasks
        # snapshot them, and the final done event carries them exactly once.
        self.history = [*self.history, *result.messages]
        if result.blocked_reason:
            # Task 0034: the worker ran into the plan-mode read-only gate —
            # the task is blocked with the rejection reason, never COMPLETED,
            # even when the worker's own text claimed success. The worker's
            # own output (partial findings, or its false completion claim)
            # is still reported, labeled as output rather than as a result.
            reason = result.blocked_reason
            if result.error:
                reason = f"{reason}（任务随后异常：{result.error}）"
            result.task.mark_failed(reason)
            blocked[result.task.id] = reason
            blocked_line = f"已阻断 [{result.task.id}]：{_preview(reason)}"
            worker_output = result.text.strip()
            if worker_output:
                blocked_line += f"\n任务输出：{_preview(worker_output)}"
            yield {
                "type": "text_delta",
                "text": blocked_line + "\n\n",
            }
        elif result.error:
            result.task.mark_failed(str(result.error))
            yield {"type": "text_delta", "text": f"失败 [{result.task.id}]：{result.error}\n\n"}
        else:
            result.task.mark_completed(result.text)
            yield {
                "type": "text_delta",
                "text": f"已完成 [{result.task.id}]：{_preview(result.text)}\n\n",
            }
        # Checkpoint right after the task state is mutated, before the events
        # reach consumers: an interrupted run never replays a finished task.
        self._save_checkpoint(plan)
        # Failed tasks still consumed tokens and turns — always report accounting.
        yield {
            "type": "usage",
            "usage": result.usage.to_dict(),
        }
        yield {"type": "plan_task_done", "turns": result.turns, "tokens": result.tokens}

    async def _pump_task_events(
        self,
        plan: ExecutionPlan,
        task: Task,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        try:
            async for event in self._execute_task_stream(plan, task):
                await queue.put(event)
        finally:
            await queue.put({"type": "plan_task_stream_done", "task_id": task.id})

    async def _execute_task_stream(
        self,
        plan: ExecutionPlan,
        task: Task,
    ) -> AsyncIterator[dict[str, Any]]:
        task.mark_started()
        yield {
            "type": "plan_task_started",
            "task_id": task.id,
            "task_description": task.description,
        }
        # Task 0013: every worker starts from a snapshot of the plan-level
        # history — the session history plus the messages earlier tasks
        # appended — instead of a blank slate. Parallel tasks all capture the
        # same pre-batch snapshot because increments are only appended when a
        # result is applied, after the whole batch has finished.
        history_snapshot = list(self.history)
        task_user_message = _task_context(plan, task)
        text = ""
        tool_results: list[str] = []
        # Task 0034: tool results rejected by the plan-mode read-only gate,
        # collected verbatim as the task's rejection reason.
        gate_rejections: list[str] = []
        usage = Usage()
        turns = 0
        task_messages: list[Message] = []
        worker_registry = self._worker_registry()
        try:
            async for event in query(
                llm_client=self.llm_client,
                tool_registry=worker_registry,
                system_prompt=self._task_system_prompt(plan, task),
                user_message=task_user_message,
                history=history_snapshot,
                cwd=self.cwd,
                config=self.config,
                approval_callback=self.approval_callback,
                # Parallel plan tasks must never share one-shot Skill context.
                skill_context_buffer=SkillContextBuffer(),
                max_turns=self.max_task_turns,
                # Task 0015: task workers claim side-effectful calls in the
                # plan's tool journal, keyed on the plan's persisted run_id.
                # A resumed run loads the same run_id from the checkpoint, so
                # the executor's claim/hash dedup replays completed results
                # instead of repeating the side effect (empty run_id covers
                # checkpoints written before the field existed).
                journal=self._journal,
                run_id=plan.run_id or self._run_id,
            ):
                if event.get("type") == "text_delta":
                    text += str(event.get("text") or "")
                elif event.get("type") == "tool_result":
                    content = str(event.get("result") or "")
                    if content:
                        tool_results.append(content)
                    if event.get("is_error") and PLAN_MODE_REJECTION_SNIPPET in content:
                        gate_rejections.append(content)
                    yield _with_task_context(event, task)
                elif event.get("type") in {
                    "thinking_delta",
                    "tool_call",
                    "context_compressed",
                }:
                    yield _with_task_context(event, task)
                elif event.get("type") == "done":
                    turns += int(event.get("total_turns") or 0)
                    usage = usage + Usage.from_mapping(event.get("usage") or {})
                    task_messages = list(event.get("messages") or [])
                elif event.get("type") == "error":
                    raise event["error"]
            result_text = text.strip() or "\n".join(tool_results).strip()
            yield {
                "type": "plan_task_result",
                "result": TaskRunResult(
                    task=task,
                    text=result_text,
                    usage=usage,
                    turns=turns,
                    blocked_reason=_plan_gate_rejection_reason(gate_rejections),
                    messages=_history_increment(history_snapshot, task_messages, task_user_message),
                ),
            }
        except Exception as exc:  # noqa: BLE001
            yield {
                "type": "plan_task_result",
                "result": TaskRunResult(
                    task=task,
                    text="",
                    usage=usage,
                    turns=turns,
                    error=exc,
                    blocked_reason=_plan_gate_rejection_reason(gate_rejections),
                ),
            }

    def _worker_registry(self) -> ToolRegistry:
        """The registry one task worker runs against.

        Task 0001: outside plan mode this is the full registry, unchanged (the
        very same object, so non-plan behavior is bit-identical). In plan mode
        it is the whitelisted view — the worker only ever SEES the six
        read-only tools (schemas, runtime context, system prompt), while an
        attempted hidden tool still resolves through ``get()`` into the
        executor's plan-mode read-only gate and is refused there. The gate
        stays the second layer behind the exposure filter.
        """
        if not self.config.policy.plan_mode:
            return self.tool_registry
        return filter_registry_for_plan_mode(self.tool_registry)

    def _task_system_prompt(self, plan: ExecutionPlan, task: Task) -> str:
        worker_registry = self._worker_registry()
        base = PromptAssembler(
            config=self.config,
            cwd=self.cwd,
            tool_names=worker_registry.list_names(),
            model=self.llm_client.model_name,
            provider=self.llm_client.provider_name,
        ).build_static()
        prompt = (
            base
            + "\n\n你正在执行 Plan-and-Execute DAG 中的一个任务。\n"
            + f"任务 id：{task.id}\n任务类型：{task.type.value}\n"
            + "请具体完成任务，并在需要时使用工具。"
            + _task_language_instruction(plan.goal)
        )
        if self.config.policy.plan_mode:
            prompt += f"\n\n{_PLAN_MODE_WORKER_CONSTRAINT}"
        return prompt


def _with_task_context(event: dict[str, Any], task: Task) -> dict[str, Any]:
    return {
        **event,
        "phase": "execution",
        "task_id": task.id,
        "task_description": task.description,
    }


def _history_increment(
    snapshot: list[Message],
    messages: list[Message],
    task_user_content: str,
) -> list[Message]:
    """The messages one task produced beyond the history it started from.

    query() returns the whole conversation — the snapshot prefix plus the
    task's own turns — so the prefix must be stripped before the plan-level
    history absorbs them: appending wholesale would duplicate every earlier
    message once per task. The task's turns are anchored by the snapshot
    prefix when it survived verbatim, otherwise by the task's own user
    message (context compression may have summarized the older snapshot turns
    away, but the current task's turns stay in the recent side). When neither
    anchor matches, nothing is appended: a lost suffix is recoverable,
    duplicated or torn history is not.
    """
    if messages[: len(snapshot)] == snapshot:
        return list(messages[len(snapshot) :])
    start = next(
        (
            index
            for index, message in enumerate(messages)
            if message.role == "user" and message.content == task_user_content
        ),
        None,
    )
    if start is None:
        return []
    return list(messages[start:])


class _PlannerHistoryClient:
    """LLM client proxy that prefixes the session history onto planner chats.

    The planner builds its own ``[Message(role="user", content="请为以下目标
    创建执行计划：…")]`` turn and plan/planner.py is outside this change's
    boundary, so the history is injected at the client seam: every chat() the
    planner makes (the planning turn and its repair round alike) starts with
    the history snapshot; everything else forwards to the wrapped client.

    Task 0034: a non-empty ``plan_mode_directive`` is appended to the planner's
    own user turn (never to the session history) so a plan-mode run tells the
    planner — on the planning turn and the repair round alike — not to
    schedule write work. The directive rides on the user role: strict
    alternating gateways reject assistant-side injected turns (AGENTS.md).
    """

    def __init__(
        self,
        inner: LlmClient,
        history: list[Message],
        plan_mode_directive: str = "",
    ):
        self._inner = inner
        self._history = list(history)
        self._directive = plan_mode_directive

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes this class does not define (chat is
        # defined), so after __init__ has bound _inner there is no recursion.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        forwarded = [*self._history, *messages]
        if self._directive and forwarded and forwarded[-1].role == "user":
            last = forwarded[-1]
            forwarded[-1] = Message(role="user", content=f"{last.content}\n\n{self._directive}")
        return self._inner.chat(
            forwarded,
            tools,
            system_prompt=system_prompt,
        )


def _executable_tasks_in_order(plan: ExecutionPlan) -> list[Task]:
    executable_ids = {task.id for task in plan.executable_tasks()}
    return [plan.tasks[task_id] for task_id in plan.execution_order() if task_id in executable_ids]


def _task_context(plan: ExecutionPlan, task: Task) -> str:
    lines = [
        f"目标：{plan.goal}",
        f"当前任务 [{task.id}]：{task.description}",
        "",
        "已完成的依赖任务结果：",
    ]
    for dep_id in task.dependencies:
        dep = plan.get_task(dep_id)
        if dep and dep.status == TaskStatus.COMPLETED:
            lines.append(f"- [{dep.id}] {dep.description}: {_preview(dep.result, 800)}")
    return "\n".join(lines)


def _build_plan_result(
    plan: ExecutionPlan,
    *,
    header: str = "计划执行完成。",
    footer: str | None = None,
) -> str:
    status_labels = {
        TaskStatus.PENDING: "待执行",
        TaskStatus.RUNNING: "执行中",
        TaskStatus.COMPLETED: "已完成",
        TaskStatus.FAILED: "失败",
        TaskStatus.SKIPPED: "已跳过",
    }
    lines = [header, "", "任务摘要："]
    for task in plan.all_tasks():
        lines.append(f"- [{task.id}] {status_labels[task.status]}：{task.description}")
        if task.result:
            lines.append(f"  结果：{_preview(task.result)}")
        if task.status is TaskStatus.FAILED and task.error:
            lines.append(f"  原因：{_preview(task.error)}")
    if footer:
        lines.append("")
        lines.append(footer)
    return "\n".join(lines) + "\n"


def _plan_mode_failure_header(blocked: dict[str, str]) -> str:
    """Task 0034: the plan-mode failure header — never claims success."""
    if blocked:
        return f"计划未完成：{len(blocked)} 个任务被 plan 只读闸拒绝，未执行。"
    return "计划未完成：有任务执行失败。"


def _plan_gate_rejection_reason(rejections: list[str]) -> str | None:
    """The task's plan-gate rejection reason, deduplicated and joined."""
    unique = list(dict.fromkeys(item for item in rejections if item))
    if not unique:
        return None
    return "；".join(unique)


def _task_language_instruction(goal: str) -> str:
    if any("\u4e00" <= char <= "\u9fff" for char in goal):
        return "\n用户目标包含中文；所有进度说明、分析和最终结果都必须使用中文。"
    return "\nUse the same language as the user's goal for all progress and results."


def _preview(text: str, max_len: int = 160) -> str:
    value = (text or "").replace("\r\n", "\n").strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _calculate_costs(llm_client: LlmClient, usage: Usage) -> dict[str, Any]:
    calculator = getattr(llm_client, "calculate_cost", None)
    if not callable(calculator):
        return {}
    result: dict[str, Any] = {}
    for currency in ("usd", "cny"):
        try:
            result[currency] = calculator(usage, currency=currency).to_dict()
        except (KeyError, TypeError, ValueError):
            continue
    return result
