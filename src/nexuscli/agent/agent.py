"""agent.py — Core terminal Agent inspired by Claude Code.

Integrates LLM API, parses natural-language instructions, schedules command
and file operations via the tool system, and generates streaming responses.

Supports three execution modes:
    - react  : Standard ReAct loop (thought → tool → observation → answer)
    - plan   : Plan-then-execute with a DAG of sub-tasks
    - team   : Multi-agent orchestrator (planner → workers → reviewer)

All modes share the same streaming event protocol so callers can render
progress incrementally.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from typing import Any, Literal
from uuid import uuid4

from nexuscli.agent.loop_guard import (
    LOOP_ABORT_MESSAGE,
    LoopGuard,
    loop_abort_warning,
    tool_call_fingerprint,
)
from nexuscli.agent.orchestrator import AgentOrchestrator
from nexuscli.agent.plan_execute import PlanExecuteAgent
from nexuscli.agent.turn_checkpoint import TurnCheckpointStore
from nexuscli.config import NexusCliConfig
from nexuscli.context import ContextBudget, ContextWindowManager
from nexuscli.hooks import fire_event, has_hooks
from nexuscli.image import parse_image_references
from nexuscli.llm.base import LlmClient, is_context_overflow_error
from nexuscli.prompt import PromptAssembler
from nexuscli.skill import SkillContextBuffer, SkillRegistry
from nexuscli.snapshot import SnapshotService
from nexuscli.tools.base import ToolContext, ToolResult
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.journal import get_tool_journal
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, Usage

AgentMode = Literal["react", "plan", "team"]

# F12: synthetic tool_result for a `max_tokens` cutoff that landed inside a
# tool_use block — the call's arguments JSON is incomplete, so the call is
# never executed. Wording follows kitagent's loop synthesis (loop.py): tell
# the model to split the task into smaller steps and retry.
MAX_TOKENS_TOOL_RESULT_MESSAGE = (
    "Error: output token limit reached before this tool call could be "
    "completed. Please break the task into smaller steps and try again."
)


def _message_snapshot(message: Message) -> dict[str, Any]:
    """Message → JSON 安全 dict（dataclass 字段过滤，tool_calls 本就是 dict 列表）。"""

    from dataclasses import asdict, is_dataclass

    if is_dataclass(message):
        return asdict(message)
    return {"role": str(getattr(message, "role", "")), "content": message.content}


class Agent:
    """A terminal AI agent that connects an LLM to tools for task execution.

    The Agent owns the conversation history, manages context compression, and
    delegates the actual LLM-tool interaction loop to mode-specific runners.
    All ``run()`` methods yield the same streaming event protocol so that UI
    layers (REPL, CLI, or programmatic) can render progress uniformly.

    Typical usage::

        agent = Agent(
            llm_client=my_client,
            tool_registry=my_registry,
            config=my_config,
            cwd="/workspace",
        )
        async for event in agent.run("list all Python files"):
            if event["type"] == "text_delta":
                print(event["text"], end="")
    """

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        tool_registry: ToolRegistry,
        config: NexusCliConfig,
        cwd: str,
        approval_callback: Callable | None = None,
        mode: AgentMode = "react",
        system_prompt: str | None = None,
        max_turns: int = 200,
        max_plan_depth: int = 1,
        subagent_depth: int = 0,
        session_id: str | None = None,
        use_journal: bool = False,
        run_id: str | None = None,
        turn_checkpoints: bool = False,
    ) -> None:
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.config = config
        self.cwd = cwd
        self.approval_callback = approval_callback
        self.mode = mode
        self.max_turns = max_turns
        # F9 工具副作用日志：默认关闭（eval 直构 Agent 的离线承诺不破）；
        # REPL/引擎路径显式开启。run_id 每次 Agent 实例一份（一次 run 一份）。
        self._journal = get_tool_journal() if use_journal else None
        self._run_id = run_id or uuid4().hex
        # F10 回合级 checkpoint：opt-in（REPL 开启；eval 直构默认关闭保离线承诺）
        self._turn_ckpt_store = TurnCheckpointStore() if turn_checkpoints else None
        self.max_plan_depth = max_plan_depth
        # Recursion guard threaded into every ToolContext this agent builds
        # (see run_subagent / SUBAGENT_DEPTH_LIMIT).
        self.subagent_depth = subagent_depth

        # Runtime session stamp, mirrored into PolicyConfig so hook payloads
        # and tools can correlate executions (same live-rewrite pattern as
        # policy.hitl_mode in the permission-mode controller). Subagents pass
        # the parent's session_id down, so the shared config keeps the main
        # session's stamp instead of being rewritten on every task call.
        self.session_id = session_id or uuid4().hex[:12]
        if not self.config.policy.session_id:
            self.config.policy.session_id = self.session_id

        # Build the base system prompt from personality profile and config.
        self.system_prompt = (
            system_prompt
            or PromptAssembler(
                config=config,
                cwd=cwd,
                tool_names=tool_registry.list_names(),
                model=llm_client.model_name,
                provider=llm_client.provider_name,
            ).build_static()
        )

        # Conversation state.
        self.history: list[Message] = []
        self.skill_context_buffer = SkillContextBuffer()

        # Accumulated usage / cost across all turns of the session.
        self.last_usage = Usage()
        self.last_cost: dict[str, Any] = {}

        # Validate configuration.
        self._validate_config()

    # ------------------------------------------------------------------
    # Public API — run the agent
    # ------------------------------------------------------------------

    async def run(self, message: str) -> AsyncIterator[dict[str, Any]]:
        """Execute *message* and yield streaming events.

        Events follow this protocol (``type`` field determines the shape):

        ``text_delta``
            {"type": "text_delta", "text": "..."}
        ``thinking_delta``
            {"type": "thinking_delta", "thinking": "..."}
        ``tool_call``
            {"type": "tool_call", "name": "...", "input": {...}}
        ``tool_result``
            {"type": "tool_result", "name": "...", "result": "...", "is_error": bool}
        ``usage``
            {"type": "usage", "usage": {...}}
        ``turn_complete``
            {"type": "turn_complete", "turn": int, "stop_reason": str}
        ``context_compressed``
            {"type": "context_compressed", "before_tokens": ..., "after_tokens": ...,
             "summarized_messages": int}
        ``warning``
            {"type": "warning", "message": "..."} — emitted (once, before
            ``done``) when the react loop hit ``max_turns`` while the model
            still wanted to keep the tool loop running, or when the
            loop-detection guard terminated the turn because the model kept
            repeating the same tool call after a warning.
        ``error``
            {"type": "error", "error": Exception}

        Provider-reported context-length overflows are not surfaced as
        ``error`` right away: the react loop compacts the history with the
        manual compressor and retries the same turn once (a notice is
        streamed as ``text_delta`` first). Only a second failure — or a
        history too short to compact — surfaces the provider error.
        ``done``
            {"type": "done", "total_turns": int, "total_tokens": int,
             "usage": {...}, "cost": {...}, "messages": [Message, ...]}
        """
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

        try:
            # UserPromptSubmit hooks gate the message before it reaches any
            # runner: a blocked hook refuses the input without calling the LLM.
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
            if self.mode == "plan":
                async for event in self._run_plan(message):
                    yield event
            elif self.mode == "team":
                async for event in self._run_team(message):
                    yield event
            else:
                async for event in self._run_react(message):
                    yield event
        finally:
            if snapshot is not None:
                with suppress(Exception):
                    snapshot.create("post-turn")

        # Stop hooks fire once the turn loop ended normally; a block is only a
        # notice to the user, it never forces the agent to keep running.
        if has_hooks(self.config, "Stop"):
            stop = await fire_event(self.config, "Stop", {}, self.cwd)
            if stop.blocked:
                yield {
                    "type": "text_delta",
                    "text": f"\nStop hook: {stop.reason or 'blocked by hook'}\n",
                }

    # ------------------------------------------------------------------
    # History management
    # ------------------------------------------------------------------

    def clear_history(self) -> None:
        """Reset conversation history and skill context buffer."""
        self.history = []
        self.skill_context_buffer.clear()
        self.last_usage = Usage()
        self.last_cost = {}

    # ------------------------------------------------------------------
    # Mode runners
    # ------------------------------------------------------------------

    async def continue_run(self) -> AsyncIterator[dict[str, Any]]:
        """F10：从已恢复的历史继续循环（不追加用户消息、不注入技能候选）。

        /turn-resume 重建历史并重放在途调用后，以此从本轮继续。仅限构造时开启
        turn_checkpoints 的 agent（恢复路径的护栏）。
        """

        if self._turn_ckpt_store is None:
            raise RuntimeError("continue_run requires an agent built with turn_checkpoints=True")
        async for event in self._run_react(None):
            yield event

    async def _run_react(self, message: str | None) -> AsyncIterator[dict[str, Any]]:
        """Standard ReAct loop (the default and most common mode)."""
        # Prepend skill candidates that match the user message.
        original = ""
        if message is None:
            # continue_run：历史已就位（含重放的工具结果），直接进入下一模型轮。
            messages = [*(self.history or [])]
        else:
            original = message
            message = _prepend_skill_candidates(message, self.cwd, self.config)
            message = _prepend_skill_context(message, self.skill_context_buffer)

            messages = [
                *(self.history or []),
                Message(role="user", content=parse_image_references(message, self.cwd)),
            ]

        tool_defs = self.tool_registry.definitions()
        executor = ToolExecutor(self.tool_registry)
        context = ToolContext(
            cwd=self.cwd,
            config=self.config,
            approval_callback=self.approval_callback,
            skill_context_buffer=self.skill_context_buffer,
            journal=self._journal,
            run_id=self._run_id,
            subagent_depth=self.subagent_depth,
        )

        # Build the dynamic part of the system prompt (tool list, cwd, etc.).
        dynamic_prompt = PromptAssembler(
            config=self.config,
            cwd=self.cwd,
            tool_names=self.tool_registry.list_names(),
            model=self.llm_client.model_name,
            provider=self.llm_client.provider_name,
        ).build_dynamic(original)

        effective_system = f"{self.system_prompt}\n\n{dynamic_prompt}".strip()

        # Context-window manager for history compression.
        context_manager = ContextWindowManager(
            ContextBudget(
                context_window=self.llm_client.max_context_window,
                max_output_tokens=self.config.llm.max_tokens,
                compression_threshold=self.config.memory.compression_threshold,
                compression_target=self.config.memory.compression_target,
                reserve_tokens=self.config.memory.compression_reserve_tokens,
            ),
            max_history_messages=self.config.memory.max_conversation_history,
            min_recent_messages=self.config.memory.min_recent_messages,
            summary_max_chars=self.config.memory.summary_max_chars,
        )

        total_usage = Usage()
        turn = 0
        # Set when the loop exits because max_turns ran out while the model
        # still wanted to continue the tool loop; surfaced as an explicit
        # warning event instead of a silent truncation.
        hit_limit = False
        # React-loop repetition detection: consecutive identical tool calls
        # (same tool + same normalized arguments) past
        # agent.loop_detection_threshold are intercepted — see loop_guard.py.
        # A threshold of 0 disables detection inside the guard.
        loop_guard = LoopGuard(threshold=self.config.agent.loop_detection_threshold)
        loop_aborted = False

        while turn < self.max_turns:
            turn += 1
            text = ""
            stop_reason = "end_turn"
            turn_usage = Usage()
            tool_states: dict[int, dict[str, Any]] = {}

            # Compress if needed.
            if self.config.features.context_compression:
                compression = context_manager.prepare(
                    messages,
                    system_prompt=effective_system,
                    tool_definitions=tool_defs,
                )
                messages = compression.messages
                if compression.compressed:
                    yield {
                        "type": "context_compressed",
                        "before_tokens": compression.estimated_tokens_before,
                        "after_tokens": compression.estimated_tokens_after,
                        "summarized_messages": compression.summarized_messages,
                    }

            # Call the LLM. A provider-reported context overflow (the request
            # itself exceeded the window — e.g. a resumed session or tool
            # results that pushed past the estimate-based threshold) is
            # compacted once with the manual compressor and retried instead
            # of failing the whole turn. Everything else surfaces as before.
            text = ""
            stop_reason = "end_turn"
            turn_usage = Usage()
            tool_states: dict[int, dict[str, Any]] = {}
            stream_error: BaseException | None = None
            for attempt in range(2):
                text = ""
                stop_reason = "end_turn"
                turn_usage = Usage()
                tool_states = {}
                stream_error = None
                async for event in self.llm_client.chat(
                    messages,
                    tool_defs,
                    system_prompt=effective_system,
                ):
                    event_type = event.get("type")
                    if event_type == "text_delta":
                        delta = str(event.get("text") or "")
                        text += delta
                        yield {"type": "text_delta", "text": delta}
                    elif event_type == "thinking_delta":
                        yield {
                            "type": "thinking_delta",
                            "thinking": str(event.get("thinking") or ""),
                        }
                    elif event_type == "tool_call_delta":
                        _merge_tool_delta(tool_states, event["tool_call"])
                    elif event_type == "message_end":
                        stop_reason = str(event.get("stop_reason") or "end_turn")
                    elif event_type == "usage":
                        usage = Usage.from_mapping(event.get("usage") or {})
                        turn_usage = turn_usage + usage
                        yield {"type": "usage", "usage": usage.to_dict()}
                    elif event_type == "error":
                        stream_error = event["error"]
                        break
                if stream_error is None:
                    break
                overflow_recoverable = (
                    attempt == 0
                    and self.config.features.context_compression
                    and is_context_overflow_error(stream_error)
                )
                if not overflow_recoverable:
                    break
                compression = context_manager.compact_now(messages)
                if not compression.compressed:
                    # compact_now refuses to hand back a history that grew (the
                    # summary block can cost more than the turns it replaces on
                    # a short session). That refusal must not disable overflow
                    # recovery: the provider already said the request is too
                    # big, so a hard trim is the remaining lever. Drop the
                    # oldest turns and retry once; if nothing is left to drop,
                    # surface the provider error as-is.
                    trimmed = context_manager.force_trim(messages)
                    if trimmed is None:
                        break
                    messages = trimmed
                else:
                    messages = compression.messages
                yield {
                    "type": "text_delta",
                    "text": (
                        "\nProvider reported a context-length overflow; compacting the "
                        "conversation history and retrying once.\n"
                    ),
                }
            if stream_error is not None:
                yield {"type": "error", "error": stream_error, "error_code": "llm_error"}
                return

            total_usage = total_usage + turn_usage
            tool_calls = _finalize_tool_calls(tool_states)
            messages.append(Message(role="assistant", content=text or "", tool_calls=tool_calls))
            yield {"type": "turn_complete", "turn": turn, "stop_reason": stop_reason}

            # If the model didn't request any tools, we're done.
            if stop_reason != "tool_use" and not tool_calls:
                break

            # Final allowed turn and the model still wants to keep the tool
            # loop going — flag it so the stream ends with an explicit
            # warning instead of a silent hard stop.
            if turn == self.max_turns and (stop_reason == "tool_use" or tool_calls):
                hit_limit = True

            blocked_results: dict[str, ToolResult] = {}
            if stop_reason == "max_tokens" and tool_calls:
                # F12: a `max_tokens` cutoff landing inside a tool_use block
                # truncates the call's arguments JSON — executing the
                # degraded `{"raw": ...}` input would act on garbage. Answer
                # every call with a synthetic is_error tool_result so the
                # assistant tool_use / tool_result pairing invariant holds
                # and execute nothing; the next turn lets the model retry
                # with smaller steps. Loop-guard fingerprints are not
                # recorded: these calls never ran, so they are not
                # repetitions.
                loop_aborted = False
                for call in tool_calls:
                    call_id = str(call.get("id") or "")
                    blocked_results[call_id] = ToolResult(
                        tool_use_id=call_id,
                        content=MAX_TOKENS_TOOL_RESULT_MESSAGE,
                        is_error=True,
                    )
            else:
                # Loop guard: record every call's fingerprint. When the same
                # fingerprint reaches the threshold the call is NOT executed and
                # a nudge message is injected instead; if the very next turn
                # repeats the fingerprint again, the whole turn aborts.
                decisions = [
                    loop_guard.record(
                        tool_call_fingerprint(
                            call.get("function", {}).get("name", "unknown"), _tool_input(call)
                        )
                    )
                    for call in tool_calls
                ]
                loop_aborted = any(decision.abort for decision in decisions)
                if loop_aborted:
                    # Answer every tool call of the aborting turn with the abort
                    # message so the assistant tool_calls stay paired in history.
                    for call in tool_calls:
                        call_id = str(call.get("id") or "")
                        blocked_results[call_id] = ToolResult(
                            tool_use_id=call_id,
                            content=LOOP_ABORT_MESSAGE,
                            is_error=True,
                        )
                else:
                    for call, decision in zip(tool_calls, decisions, strict=True):
                        if decision.blocked:
                            call_id = str(call.get("id") or "")
                            blocked_results[call_id] = ToolResult(
                                tool_use_id=call_id,
                                content=loop_guard.format_nudge(decision),
                            )

            # Run tools (calls intercepted by the loop guard are skipped).
            for call in tool_calls:
                name = call.get("function", {}).get("name", "unknown")
                yield {"type": "tool_call", "name": name, "input": _tool_input(call)}

            pending_calls = [
                call for call in tool_calls if str(call.get("id") or "") not in blocked_results
            ]
            if self._turn_ckpt_store is not None:
                # (a) in-flight 快照：assistant tool_use 已入 messages、分发未开始。
                # 崩溃后 /turn-resume 按原始 id 经 journal 重派（05 F10 双快照点定稿）。
                self._turn_ckpt_store.save(
                    cwd=self.cwd,
                    run_id=self._run_id,
                    model=self.llm_client.model_name,
                    provider=self.llm_client.provider_name,
                    turn=turn,
                    messages=[_message_snapshot(item) for item in messages],
                    pending_tool_calls=list(pending_calls),
                    usage_so_far=total_usage.to_dict(),
                    cost_so_far=_calculate_costs(self.llm_client, total_usage) or {},
                )
            tool_results = await executor.execute_all(pending_calls, context)
            tool_results = [*tool_results, *blocked_results.values()]
            loaded_skill_context = _drain_skill_context(self.skill_context_buffer)
            load_skill_ids = [
                str(call.get("id") or "")
                for call in tool_calls
                if str(call.get("function", {}).get("name") or "") == "load_skill"
            ]
            injection_target = load_skill_ids[-1] if load_skill_ids else ""
            injected = False

            for result in tool_results:
                yield {
                    "type": "tool_result",
                    "name": _tool_name_by_id(tool_calls, result.tool_use_id or ""),
                    "result": result.content,
                    "is_error": result.is_error,
                }
                model_content = result.content
                if loaded_skill_context and result.tool_use_id == injection_target:
                    model_content = f"{model_content}\n\n{loaded_skill_context}"
                    injected = True
                messages.append(
                    Message(
                        role="tool",
                        content=model_content,
                        tool_call_id=result.tool_use_id,
                    )
                )

            if loaded_skill_context and not injected:
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"{loaded_skill_context}\n\n"
                            "Use these loaded instructions to continue the current request."
                        ),
                    )
                )

            if self._turn_ckpt_store is not None:
                # (b) 轮末快照：pending 为空，覆盖 (a)——该轮完整。
                self._turn_ckpt_store.save(
                    cwd=self.cwd,
                    run_id=self._run_id,
                    model=self.llm_client.model_name,
                    provider=self.llm_client.provider_name,
                    turn=turn,
                    messages=[_message_snapshot(item) for item in messages],
                    pending_tool_calls=[],
                    usage_so_far=total_usage.to_dict(),
                    cost_so_far=_calculate_costs(self.llm_client, total_usage) or {},
                )

            if loop_aborted:
                break

        # Persist history for next user message and report final usage.
        self.history = list(messages)
        self.last_usage = total_usage

        if hit_limit and not loop_aborted:
            yield {
                "type": "warning",
                "kind": "budget_exhausted",  # F11：类型化预算耗尽标记（折叠层不做字符串匹配）
                "message": (
                    f"Reached the agent.max_turns limit ({self.max_turns}); the task "
                    "may be unfinished. Raise agent.max_turns in config.json to "
                    "continue longer."
                ),
            }

        if loop_aborted:
            yield {
                "type": "warning",
                "kind": "budget_exhausted",  # F11：loop guard 中止同为预算耗尽语义
                "message": loop_abort_warning(turn, self.config.agent.loop_detection_threshold),
            }

        done_event: dict[str, Any] = {
            "type": "done",
            "total_turns": turn,
            "total_tokens": total_usage.total_tokens,
            "usage": total_usage.to_dict(),
            "messages": self.history,
        }
        costs = _calculate_costs(self.llm_client, total_usage)
        if costs:
            done_event["cost"] = costs
            self.last_cost = costs
        yield done_event

    async def _run_plan(self, message: str) -> AsyncIterator[dict[str, Any]]:
        """Plan-then-execute mode: the planner creates a DAG, then workers run it."""
        agent = PlanExecuteAgent(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            config=self.config,
            cwd=self.cwd,
            approval_callback=self.approval_callback,
            max_task_turns=self.max_turns,
        )
        agent.history = list(self.history)

        async for event in agent.run(message):
            if event.get("type") == "done":
                self.history = list(event.get("messages") or [])
                self.last_usage = Usage.from_mapping(event.get("usage") or {})
                self.last_cost = dict(event.get("cost") or {})
            yield event

    async def _run_team(self, message: str) -> AsyncIterator[dict[str, Any]]:
        """Multi-agent team mode: planner → parallel workers → reviewer."""
        orchestrator = AgentOrchestrator(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            config=self.config,
            cwd=self.cwd,
            approval_callback=self.approval_callback,
            default_worker_mode="react",
        )
        orchestrator.history = list(self.history)
        async for event in orchestrator.run(message):
            if event.get("type") == "done":
                self.history = list(event.get("messages") or [])
                self.last_usage = Usage.from_mapping(event.get("usage") or {})
                self.last_cost = dict(event.get("cost") or {})
            yield event

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _validate_config(self) -> None:
        """Raise early if the configuration is obviously wrong."""
        if not self.config.llm.api_key:
            raise ValueError(
                "LLM API key is not configured. "
                "Set NEXUSCLI_API_KEY (or DEEPSEEK_API_KEY / GLM_API_KEY / etc.) "
                "in the environment or in ~/.nexuscli/config.json."
            )
        if not self.llm_client.max_context_window:
            raise ValueError(
                "LLM context window is not configured. "
                "Set a NEXUSCLI_CONTEXT_WINDOW environment variable or configure "
                "it in ~/.nexuscli/config.json."
            )


# ------------------------------------------------------------------
# Shared helpers (also used by query.py)
# ------------------------------------------------------------------


def _merge_tool_delta(tool_states: dict[int, dict[str, Any]], delta: dict[str, Any]) -> None:
    index = int(delta.get("index") or 0)
    state = tool_states.setdefault(
        index,
        {
            "id": delta.get("id") or f"tool_{index}",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )
    if delta.get("id"):
        state["id"] = delta["id"]
    function = delta.get("function") or {}
    if function.get("name"):
        state["function"]["name"] = function["name"]
    arguments = function.get("arguments")
    if arguments:
        if isinstance(arguments, str):
            state["function"]["arguments"] += arguments
        else:
            # Some gateways send pre-parsed argument objects, not string deltas.
            import json

            state["function"]["arguments"] = json.dumps(arguments, ensure_ascii=False)


def _finalize_tool_calls(tool_states: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    for index in sorted(tool_states):
        state = tool_states[index]
        if state["function"]["name"]:
            calls.append(state)
    return calls


def _tool_input(call: dict[str, Any]) -> dict[str, Any]:
    import json

    raw = call.get("function", {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _tool_name_by_id(calls: list[dict[str, Any]], tool_call_id: str) -> str:
    for call in calls:
        if call.get("id") == tool_call_id:
            return str(call.get("function", {}).get("name") or "unknown")
    return "unknown"


def _prepend_skill_candidates(user_message: str, cwd: str, config: NexusCliConfig) -> str:
    if not config.features.skill:
        return user_message
    candidates = SkillRegistry(cwd).match(user_message, top_k=5)
    if not candidates:
        return user_message
    lines = [
        "Relevant skill candidates for this request:",
        "Call load_skill(name) before proceeding when a candidate applies.",
    ]
    for skill in candidates:
        description = " ".join(skill.description.split())
        if len(description) > 300:
            description = description[:297] + "..."
        tags = f" [tags: {', '.join(skill.tags)}]" if skill.tags else ""
        lines.append(f"- {skill.name}: {description}{tags}")
    candidate_text = "\n".join(lines)
    return f"{candidate_text}\n\n---\nUser request:\n{user_message}"


def _prepend_skill_context(user_message: str, skill_context_buffer: SkillContextBuffer) -> str:
    if not skill_context_buffer or skill_context_buffer.is_empty():
        return user_message
    drained = skill_context_buffer.drain()
    if not drained:
        return user_message
    return f"{drained}\n\n---\nUser request:\n{user_message}"


def _drain_skill_context(skill_context_buffer: SkillContextBuffer) -> str:
    if not skill_context_buffer or skill_context_buffer.is_empty():
        return ""
    return skill_context_buffer.drain()


def _calculate_costs(llm_client: LlmClient, usage: Usage) -> dict[str, Any]:
    calculator = getattr(llm_client, "calculate_cost", None)
    if not callable(calculator):
        return {}
    result: dict[str, Any] = {}
    for currency in ("usd", "cny"):
        try:
            breakdown = calculator(usage, currency=currency)
        except (KeyError, TypeError, ValueError):
            continue
        result[currency] = breakdown.to_dict()
    return result
