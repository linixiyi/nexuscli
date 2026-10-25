"""OfflineDemoProvider — deterministic scripted stand-in for the LLM.

Ported from kitagent (MIT, zed123214/kitagent)
``src/agent_runtime/web/demo.py:18-152``: a scripted provider that drives the
REAL agent loop, real tools and the real approval gate while making every
model decision deterministic — no API key, no network, no randomness.

Scripted decision chain (kitagent demo.py:54-96, adapted):

1. read the input file (real ``read_file``);
2. write the report (real ``write_file`` — passes the executor's real HITL
   approval gate, ``tools/builtins.py`` ``requires_approval=True``);
3. read the report back for verification;
4. end with a summary. After the script is exhausted (or a tool call was
   denied / failed), answer with text only and ``end_turn``.

Tokens stream in fixed 8-character chunks (kitagent demo.py:97-102).

Adaptation notes (deviations from the original, stated not silent):

- kitagent's CoreApp passes an explicit ``step`` counter into ``chat``;
  NexusCLI's :class:`nexuscli.llm.base.LlmClient` protocol has no step
  parameter, so the step is inferred from the conversation history (the last
  assistant ``tool_calls`` block plus the tool results that follow it).
- kitagent reads ``is_error`` flags off the tool-result content blocks;
  NexusCLI history stores tool results as plain ``Message(role="tool")``
  strings (``nexuscli/types.py`` — no is_error field survives into history),
  so the failure branch recognizes the executor's deterministic refusal
  phrasings (``tools/executor.py`` denial/execution-error wrappers and
  ``tools/file_ops.py`` read/write failure prefixes) instead.
- kitagent streams via an event bus (``LlmTokenEvent``); here the same chunks
  are yielded as ``text_delta`` events on NexusCLI's streaming protocol.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

from nexuscli.llm.pricing import CostBreakdown
from nexuscli.types import Message

#: Fixed streaming granularity — kitagent demo.py:97 ``range(0, len(text), 8)``.
CHUNK_SIZE = 8

#: Environment knob for the per-chunk pacing (kitagent's
#: ``AGENTRT_DEMO_INITIAL_DELAY``); default 0 keeps tests instant.
INITIAL_DELAY_ENV = "NEXUSCLI_OFFLINE_DEMO_INITIAL_DELAY"

# Deterministic failure phrasings flattened into tool-result history (see
# module docstring: Message(role="tool") carries no is_error flag).
_FAILURE_MARKERS = (
    "was denied by approval policy",  # tools/executor.py _denial_message
    "was denied by permission rule",  # tools/executor.py deny branch
    "was denied by pre-tool-use hook",
    "was denied by permission-request hook",
    "execution error: ",  # tools/executor.py exception wrapper
    "is not allowed in plan mode",  # tools/executor.py plan-mode gate
    "Not a file: ",  # tools/file_ops.read_file
    "Failed to read ",  # tools/file_ops.read_file
    "Failed to write ",  # tools/file_ops.write_file
    "write_file rejected: ",  # tools/file_ops.write_file (size cap)
)

_LINE_NUMBER_PREFIX = re.compile(r"^\d+: ")


class OfflineDemoProvider:
    """Scripted LLM client compatible with :class:`nexuscli.llm.base.LlmClient`.

    Same shape as the fake clients in ``tests/test_agent_turns.py``
    (``_RepeatedToolUseClient`` etc.), but driving the full real chain:
    real ``ToolRegistry`` tools, real executor gates, real approval callback.
    """

    model_name = "offline-demo"
    provider_name = "offline-demo"
    max_context_window = 128_000
    price_profile = None

    def __init__(
        self,
        *,
        input_path: str = "README.md",
        report_path: str = "demo-report.md",
        chunk_size: int = CHUNK_SIZE,
        initial_delay: float | None = None,
        run_id: str | None = None,
    ) -> None:
        self.input_path = input_path
        self.report_path = report_path
        self.chunk_size = chunk_size
        if initial_delay is None:
            try:
                initial_delay = float(os.environ.get(INITIAL_DELAY_ENV, "0") or 0)
            except ValueError:
                initial_delay = 0.0
        self.initial_delay = initial_delay
        # Run identity stamped into the report (kitagent demo.py:39/89 used the
        # CoreApp run_id; a per-provider uuid is the same role here — the
        # provider instance lives for one session).
        self.run_id = run_id or uuid4().hex[:8]

    # ------------------------------------------------------------------
    # LlmClient protocol
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],  # noqa: ARG002 - script ignores schemas
        *,
        system_prompt: str,  # noqa: ARG002 - deterministic, prompt-free
    ) -> AsyncIterator[dict[str, Any]]:
        text, call = self._decide(messages)
        for index in range(0, len(text), self.chunk_size):
            yield {"type": "text_delta", "text": text[index : index + self.chunk_size]}
            if self.initial_delay:
                await asyncio.sleep(self.initial_delay)
        if call is not None:
            yield {"type": "tool_call_delta", "tool_call": call}
        yield {
            "type": "message_end",
            "stop_reason": "tool_use" if call is not None else "end_turn",
        }

    def calculate_cost(
        self,
        usage: Any,  # noqa: ARG002 - offline: nothing was spent
        *,
        currency: str = "usd",
    ) -> CostBreakdown:
        """Zero-cost breakdown: the offline provider consumes no tokens."""
        return CostBreakdown(
            currency=currency,
            input_cache_hit_tokens=0,
            input_cache_miss_tokens=0,
            input_uncategorized_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            input_cache_hit_cost=0.0,
            input_cache_miss_cost=0.0,
            input_uncategorized_cost=0.0,
            output_cost=0.0,
            total_cost=0.0,
        )

    # ------------------------------------------------------------------
    # Scripted decisions
    # ------------------------------------------------------------------

    def _decide(self, messages: list[Message]) -> tuple[str, dict[str, Any] | None]:
        """Return (announcement text, tool call or None) for this turn.

        Mirrors kitagent demo.py:54-96's branch order: failure short-circuit
        first, then the step ladder, then the script-exhausted fallback.
        """
        name, args, failed, result_content = self._last_exchange(messages)
        if name is None:
            return (
                "我是离线演示模型。我会先读取输入文件，再写入检查报告并读回验证。",
                self._tool_call("demo_read_input", "read_file", {"path": self.input_path}),
            )
        if failed:
            # kitagent demo.py:56-57 (denied branch): a refused or failed call
            # ends the demo honestly — no report is claimed as complete.
            return "本轮工具未获准或未完成。已保留执行记录；检查报告未被确认为完成。", None
        if name == "read_file" and args.get("path") == self.input_path:
            return (
                "输入文件已通过真实 read_file 读取。现在请求写入检查报告（将经过审批门）。",
                self._tool_call(
                    "demo_write_report",
                    "write_file",
                    {"path": self.report_path, "content": self._report_body(result_content)},
                ),
            )
        if name == "write_file":
            return (
                "检查报告已通过真实 write_file 写入。现在读回验证。",
                self._tool_call("demo_verify_report", "read_file", {"path": self.report_path}),
            )
        if name == "read_file" and args.get("path") == self.report_path:
            # kitagent demo.py:95-96: final summary names the report path.
            return (
                "## 检查报告\n\n已完成输入读取、报告写入与读回验证。\n\n"
                f"报告路径：`{self.report_path}`。读回内容与写入一致，检查报告确认为完成。",
                None,
            )
        return "离线演示脚本已用完，本轮结束。", None

    def _report_body(self, read_result: str) -> str:
        """Report content, embedding a line of the real read result.

        kitagent demo.py:88-89 wrote a static report; here the first line of
        the actual read_file output is embedded so the report verifiably
        derives from the tool round-trip.
        """
        excerpt = ""
        for line in read_result.splitlines():
            stripped = _LINE_NUMBER_PREFIX.sub("", line).strip()
            if stripped:
                excerpt = stripped[:80]
                break
        return (
            "# 离线演示检查报告\n\n"
            "- 输入文件已通过真实 read_file 工具读取。\n"
            "- 本文件由真实 write_file 工具写入（经过审批门）。\n\n"
            f"输入摘要：{excerpt or '（输入文件无文本内容）'}\n"
            f"运行标识：{self.run_id}\n"
        )

    def _tool_call(self, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Event shape consumed by ``agent._merge_tool_delta`` (agent.py:691)."""
        return {
            "index": 0,
            "id": call_id,
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
        }

    def _last_exchange(
        self, messages: list[Message]
    ) -> tuple[str | None, dict[str, Any], bool, str]:
        """Inspect the tail of the history: the last scripted call and its result.

        Returns ``(tool_name, arguments, failed, result_content)``; the tool
        name is None when the history has no completed tool round yet (the
        very first model turn).
        """
        index = len(messages) - 1
        results: list[str] = []
        while index >= 0 and messages[index].role == "tool":
            content = messages[index].content
            results.append(content if isinstance(content, str) else str(content))
            index -= 1
        if not results or index < 0:
            return None, {}, False, ""
        assistant = messages[index]
        if assistant.role != "assistant" or not assistant.tool_calls:
            return None, {}, False, ""
        call = assistant.tool_calls[0]  # the demo emits exactly one call per turn
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        failed = any(marker in content for content in results for marker in _FAILURE_MARKERS)
        return name, arguments, failed, results[-1]
