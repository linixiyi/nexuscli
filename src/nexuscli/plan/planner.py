from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from nexuscli.llm.base import LlmClient, is_transient_api_error
from nexuscli.plan.models import ExecutionPlan, Task, TaskType
from nexuscli.types import Message, Usage

PLANNER_PROMPT = """你是 NexusCLI 的任务规划器。
请为用户任务创建一个简洁、可执行的 DAG，并仅返回以下结构的 JSON：
{
  "summary": "short summary",
  "tasks": [
    {
      "id": "stable_source_id",
      "description": "concrete executable step",
      "type": "FILE_READ|FILE_WRITE|COMMAND|ANALYSIS|VERIFICATION",
      "dependencies": ["stable_source_id"]
    }
  ]
}
可以并行的独立任务应放在同一执行批次中。
summary 和 description 必须使用与用户目标相同的语言；用户目标包含中文时，必须使用中文。
JSON 字段名、任务 id 和 type 枚举值保持上述英文格式。
"""

# One repair round after the first attempt: some models answer the planner
# prompt with prose (or pseudo tool-call markers) instead of JSON.
PLANNER_ATTEMPTS = 2
PLANNER_REPAIR_PROMPT = (
    "你上一次的输出不是合法的执行计划 JSON。请重新输出，"
    "只返回一个符合上述结构的 JSON 对象，"
    "不要包含任何解释性文字、Markdown 代码块或工具调用标记。"
)


class Planner:
    def __init__(self, llm_client: LlmClient):
        self.llm_client = llm_client
        self.last_usage = Usage()

    async def create_plan(self, goal: str) -> ExecutionPlan:
        plan: ExecutionPlan | None = None
        async for event in self.stream_plan(goal):
            if event.get("type") == "plan_created":
                plan = event["plan"]
        if plan is None:
            raise ValueError("planner did not produce an execution plan")
        return plan

    async def stream_plan(self, goal: str) -> AsyncIterator[dict[str, Any]]:
        """Create a plan while preserving provider reasoning and usage events."""
        self.last_usage = Usage()
        if _is_simple_goal(goal):
            yield {"type": "plan_created", "plan": _minimal_plan(goal)}
            return

        messages = [Message(role="user", content=f"请为以下目标创建执行计划：\n{goal}")]
        text = ""
        last_error: Exception | None = None
        for attempt in range(PLANNER_ATTEMPTS):
            text = ""
            try:
                async for event in self.llm_client.chat(messages, [], system_prompt=PLANNER_PROMPT):
                    event_type = event.get("type")
                    if event_type == "text_delta":
                        # Planner text is machine-readable JSON. Keep it out of the user-facing
                        # stream and expose the parsed plan below instead.
                        text += str(event.get("text") or "")
                    elif event_type == "thinking_delta":
                        yield {
                            "type": "thinking_delta",
                            "thinking": str(event.get("thinking") or ""),
                            "phase": "planning",
                        }
                    elif event_type == "usage":
                        usage = Usage.from_mapping(event.get("usage") or {})
                        self.last_usage = self.last_usage + usage
                        yield {"type": "usage", "usage": usage.to_dict(), "phase": "planning"}
                    elif event_type == "error":
                        raise event["error"]
            except RuntimeError as exc:
                # Transient provider failures (rate limit / gateway 5xx) get
                # one clean retry without the repair conversation.
                if attempt < PLANNER_ATTEMPTS - 1 and is_transient_api_error(exc):
                    last_error = exc
                    continue
                raise
            try:
                plan = self.parse_plan(goal, text)
            except ValueError as exc:
                last_error = exc
                messages = [
                    *messages,
                    Message(role="assistant", content=text),
                    Message(role="user", content=PLANNER_REPAIR_PROMPT),
                ]
                continue
            yield {"type": "plan_created", "plan": plan}
            return

        raise ValueError(
            f"planner output could not be parsed after {PLANNER_ATTEMPTS} attempts "
            f"({last_error}): {_preview(text)}"
        ) from last_error

    def parse_plan(self, goal: str, plan_json: str) -> ExecutionPlan:
        data = _parse_json_object(plan_json)
        task_nodes = data.get("tasks") or data.get("steps") or []
        if not isinstance(task_nodes, list) or not task_nodes:
            raise ValueError("planner output did not contain a non-empty tasks/steps array")

        plan = ExecutionPlan(id=f"plan_{int(time.time() * 1000)}", goal=goal)
        plan.summary = str(data.get("summary") or "")
        id_mapping: dict[str, str] = {}

        for index, node in enumerate(task_nodes, start=1):
            if not isinstance(node, dict):
                continue
            original_id = str(node.get("id") or f"task_{index}")
            new_id = f"task_{index}"
            id_mapping[original_id] = new_id
            plan.add_task(
                Task(
                    id=new_id,
                    description=str(node.get("description") or original_id),
                    type=_parse_task_type(str(node.get("type") or "ANALYSIS")),
                )
            )

        for index, node in enumerate(task_nodes, start=1):
            if not isinstance(node, dict):
                continue
            task = plan.get_task(f"task_{index}")
            if not task:
                continue
            dependencies = node.get("dependencies") or []
            if not isinstance(dependencies, list):
                continue
            for raw_dep in dependencies:
                dep_id = id_mapping.get(str(raw_dep), str(raw_dep))
                if dep_id in plan.tasks:
                    task.add_dependency(dep_id)
                    plan.tasks[dep_id].add_dependent(task.id)

        if not plan.compute_execution_order():
            raise ValueError("plan contains a cyclic dependency")
        return plan


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?\s*", "", text or "").replace("```", "").strip()
    if not cleaned:
        raise ValueError("empty planner output")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Models sometimes wrap the JSON object in prose or pseudo tool-call
        # markers; recover the first balanced object instead of failing.
        data = extract_json_object(cleaned)
    if not isinstance(data, dict):
        raise ValueError("planner output is not a JSON object")
    return data


def extract_json_object(text: str) -> Any:
    """Best-effort extraction of the first balanced JSON object from prose."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text[match.start() :])
            return data
        except json.JSONDecodeError:
            continue
    raise ValueError("planner output contained no JSON object")


def _preview(text: str, max_len: int = 160) -> str:
    value = (text or "").replace("\r\n", "\n").strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _parse_task_type(value: str) -> TaskType:
    normalized = value.upper()
    try:
        return TaskType(normalized)
    except ValueError:
        return TaskType.ANALYSIS


def _is_simple_goal(goal: str | None) -> bool:
    normalized = (goal or "").strip()
    if not normalized or len(normalized) > 30:
        return False
    multi_step_cues = ["然后", "并且", "再", "最后", "同时", "先", "之后", "接着", "以及"]
    if any(cue in normalized for cue in multi_step_cues):
        return False
    simple_cues = ["列出", "查看", "读取", "显示", "执行", "运行", "搜索", "当前目录", "文件"]
    return any(cue in normalized for cue in simple_cues)


def _minimal_plan(goal: str) -> ExecutionPlan:
    normalized = goal.strip()
    plan = ExecutionPlan(id=f"plan_{int(time.time() * 1000)}", goal=normalized)
    plan.summary = f"直接执行简单任务：{normalized}"
    plan.add_task(Task(id="task_1", description=normalized, type=_infer_simple_type(normalized)))
    plan.compute_execution_order()
    return plan


def _infer_simple_type(goal: str) -> TaskType:
    if any(token in goal for token in ["读取", "打开", "查看"]) and "文件" in goal:
        return TaskType.FILE_READ
    if any(token in goal for token in ["写入", "修改", "创建文件"]):
        return TaskType.FILE_WRITE
    if any(token in goal for token in ["分析", "总结", "解释"]):
        return TaskType.ANALYSIS
    if any(token in goal for token in ["验证", "检查"]):
        return TaskType.VERIFICATION
    return TaskType.COMMAND
