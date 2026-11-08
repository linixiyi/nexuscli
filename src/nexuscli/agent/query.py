from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from nexuscli.agent.loop_guard import (
    LOOP_ABORT_MESSAGE,
    LoopGuard,
    loop_abort_warning,
    max_turns_warning,
    tool_call_fingerprint,
)
from nexuscli.config import NexusCliConfig
from nexuscli.context import ContextBudget, ContextWindowManager
from nexuscli.image import parse_image_references
from nexuscli.llm.base import LlmClient, is_context_overflow_error
from nexuscli.prompt import PromptAssembler
from nexuscli.skill import SkillRegistry
from nexuscli.tools.base import ToolContext, ToolResult
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message, Usage

# ----------------------------------------------------------------------
# Shared turn helpers (also used by Agent._run_react) — provider
# context-overflow retry and the F12 max_tokens truncation protection.
# ----------------------------------------------------------------------

# F12: synthetic tool_result for a `max_tokens` cutoff that landed inside a
# tool_use block — the call's arguments JSON is incomplete, so the call is
# never executed. Wording follows kitagent's loop synthesis (loop.py): tell
# the model to split the task into smaller steps and retry.
MAX_TOKENS_TOOL_RESULT_MESSAGE = (
    "Error: output token limit reached before this tool call could be "
    "completed. Please break the task into smaller steps and try again."
)

# Stop reasons meaning "the output token budget ran out": the OpenAI-style
# ``max_tokens`` (openai_compatible maps finish_reason=length to it,
# anthropic reports max_tokens natively) plus the raw ``length`` finish
# reason itself, so a provider answering with the unnormalized spelling is
# protected the same way on every loop.
_TRUNCATED_STOP_REASONS = frozenset({"max_tokens", "length"})


def max_tokens_blocked_results(
    tool_calls: list[dict[str, Any]], stop_reason: str
) -> dict[str, ToolResult]:
    """F12: synthetic is_error results for tool calls cut off by a token limit.

    A ``max_tokens`` / ``length`` cutoff landing inside a tool_use block
    truncates the call's arguments JSON — executing the degraded
    ``{"raw": ...}`` input would act on garbage. Returns one synthetic
    ``MAX_TOKENS_TOOL_RESULT_MESSAGE`` result per call so the assistant
    tool_use / tool_result pairing invariant holds while nothing executes,
    or an empty dict when the turn was not truncated. The blocked calls
    never ran, so callers must not record loop-guard fingerprints for them.

    Shared by the react loop (``Agent._run_react``) and ``query()`` below.
    """
    if stop_reason not in _TRUNCATED_STOP_REASONS or not tool_calls:
        return {}
    return {
        str(call.get("id") or ""): ToolResult(
            tool_use_id=str(call.get("id") or ""),
            content=MAX_TOKENS_TOOL_RESULT_MESSAGE,
            is_error=True,
        )
        for call in tool_calls
    }


async def stream_llm_turn_with_overflow_retry(
    llm_client: LlmClient,
    messages: list[Message],
    tool_definitions: list[dict[str, Any]],
    *,
    system_prompt: str,
    context_manager: ContextWindowManager,
    compression_enabled: bool,
    outcome: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Stream one model turn; retry once on a provider context overflow.

    Shared by the react loop and ``query()`` so both recover the same way:

    - user-visible stream events (``text_delta`` / ``thinking_delta`` /
      ``usage``) are re-yielded as they arrive;
    - on a provider-reported context overflow (``is_context_overflow_error``)
      with compression enabled, the history is compacted once —
      ``compact_now`` first, ``force_trim`` when compaction refuses — and
      the same turn is retried exactly once. A notice is streamed first so
      the user sees why the turn stalled;
    - never a second retry: a second overflow, a non-overflow error, or a
      history with nothing left to trim surfaces the provider error through
      ``outcome["error"]`` and the caller fails the turn.

    When the generator finishes, *outcome* holds the turn state: ``text``,
    ``stop_reason``, ``usage``, ``tool_states``, ``error`` (None on success)
    and ``messages`` — the history to continue with, replaced by the
    compacted/trimmed list when a retry rewrote it.
    """
    current = messages
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
        async for event in llm_client.chat(
            current,
            tool_definitions,
            system_prompt=system_prompt,
        ):
            event_type = event.get("type")
            if event_type == "text_delta":
                delta = str(event.get("text") or "")
                text += delta
                yield {"type": "text_delta", "text": delta}
            elif event_type == "thinking_delta":
                yield {"type": "thinking_delta", "thinking": str(event.get("thinking") or "")}
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
            attempt == 0 and compression_enabled and is_context_overflow_error(stream_error)
        )
        if not overflow_recoverable:
            break
        compression = context_manager.compact_now(current)
        if not compression.compressed:
            # compact_now refuses to hand back a history that grew (the
            # summary block can cost more than the turns it replaces on
            # a short session). That refusal must not disable overflow
            # recovery: the provider already said the request is too
            # big, so a hard trim is the remaining lever. Drop the
            # oldest turns and retry once; if nothing is left to drop,
            # surface the provider error as-is.
            trimmed = context_manager.force_trim(current)
            if trimmed is None:
                break
            current = trimmed
        else:
            current = compression.messages
        yield {
            "type": "text_delta",
            "text": (
                "\nProvider reported a context-length overflow; compacting the "
                "conversation history and retrying once.\n"
            ),
        }
    outcome["text"] = text
    outcome["stop_reason"] = stop_reason
    outcome["usage"] = turn_usage
    outcome["tool_states"] = tool_states
    outcome["error"] = stream_error
    outcome["messages"] = current


async def query(
    *,
    llm_client: LlmClient,
    tool_registry: ToolRegistry,
    system_prompt: str,
    user_message: str,
    history: list[Message] | None,
    cwd: str,
    config: NexusCliConfig,
    approval_callback=None,
    skill_context_buffer=None,
    max_turns: int = 200,
    journal: Any | None = None,
    run_id: str = "",
) -> AsyncIterator[dict[str, Any]]:
    original_user_message = user_message
    user_message = _prepend_skill_candidates(user_message, cwd, config)
    user_message = _prepend_skill_context(user_message, skill_context_buffer)
    messages = [
        *(history or []),
        Message(role="user", content=parse_image_references(user_message, cwd)),
    ]
    tool_definitions = tool_registry.definitions()
    executor = ToolExecutor(tool_registry)
    context = ToolContext(
        cwd=cwd,
        config=config,
        approval_callback=approval_callback,
        skill_context_buffer=skill_context_buffer,
        journal=journal,
        run_id=run_id,
    )
    dynamic_prompt = PromptAssembler(
        config=config,
        cwd=cwd,
        tool_names=tool_registry.list_names(),
        model=llm_client.model_name,
        provider=llm_client.provider_name,
    ).build_dynamic(original_user_message)
    effective_system_prompt = f"{system_prompt}\n\n{dynamic_prompt}".strip()
    context_manager = ContextWindowManager(
        ContextBudget(
            context_window=llm_client.max_context_window,
            max_output_tokens=config.llm.max_tokens,
            compression_threshold=config.memory.compression_threshold,
            compression_target=config.memory.compression_target,
            reserve_tokens=config.memory.compression_reserve_tokens,
        ),
        max_history_messages=config.memory.max_conversation_history,
        min_recent_messages=config.memory.min_recent_messages,
        summary_max_chars=config.memory.summary_max_chars,
    )

    total_usage = Usage()
    turn = 0
    # Set when the loop exits because max_turns ran out while the model still
    # wanted to continue the tool loop; surfaced as an explicit warning event
    # instead of a silent truncation (same protocol as the react loop).
    hit_limit = False
    # Loop-guard repetition detection, identical to the react loop: consecutive
    # identical tool calls past agent.loop_detection_threshold are intercepted
    # — see loop_guard.py. A threshold of 0 disables detection inside the guard.
    loop_guard = LoopGuard(threshold=config.agent.loop_detection_threshold)
    loop_aborted = False

    while turn < max_turns:
        turn += 1

        if config.features.context_compression:
            compression = context_manager.prepare(
                messages,
                system_prompt=effective_system_prompt,
                tool_definitions=tool_definitions,
            )
            messages = compression.messages
            if compression.compressed:
                yield {
                    "type": "context_compressed",
                    "before_tokens": compression.estimated_tokens_before,
                    "after_tokens": compression.estimated_tokens_after,
                    "summarized_messages": compression.summarized_messages,
                }

        # Call the LLM through the shared turn streamer: a provider-reported
        # context overflow (the request itself exceeded the window) is
        # compacted once with the manual compressor and retried instead of
        # failing the whole turn — same handling as the react loop.
        turn_outcome: dict[str, Any] = {}
        async for event in stream_llm_turn_with_overflow_retry(
            llm_client,
            messages,
            tool_definitions,
            system_prompt=effective_system_prompt,
            context_manager=context_manager,
            compression_enabled=config.features.context_compression,
            outcome=turn_outcome,
        ):
            yield event
        messages = turn_outcome["messages"]
        if turn_outcome["error"] is not None:
            yield {"type": "error", "error": turn_outcome["error"], "error_code": "llm_error"}
            return

        text = turn_outcome["text"]
        stop_reason = turn_outcome["stop_reason"]
        total_usage = total_usage + turn_outcome["usage"]
        tool_calls = _finalize_tool_calls(turn_outcome["tool_states"])
        messages.append(Message(role="assistant", content=text, tool_calls=tool_calls))
        yield {"type": "turn_complete", "turn": turn, "stop_reason": stop_reason}

        if stop_reason != "tool_use" and not tool_calls:
            break

        # Final allowed turn and the model still wants to keep the tool loop
        # going — flag it so the stream ends with an explicit warning instead
        # of a silent hard stop (same as the react loop).
        if turn == max_turns and (stop_reason == "tool_use" or tool_calls):
            hit_limit = True

        # F12: a token-limit cutoff landing inside a tool_use block answers
        # every call with a synthetic is_error tool_result and executes
        # nothing — same handling as the react loop, for both the
        # OpenAI-style `max_tokens` and the raw `length` finish reason.
        blocked_results: dict[str, ToolResult] = max_tokens_blocked_results(tool_calls, stop_reason)
        if blocked_results:
            # The truncated calls never ran, so they are not loop-guard
            # repetitions: no fingerprints are recorded.
            loop_aborted = False
        else:
            # Loop guard: record every call's fingerprint. When the same
            # fingerprint reaches the threshold the call is NOT executed and a
            # nudge message is injected instead; if the very next turn repeats
            # the fingerprint again, the whole turn aborts.
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
        tool_results = await executor.execute_all(pending_calls, context)
        tool_results = [*tool_results, *blocked_results.values()]
        loaded_skill_context = _drain_skill_context(skill_context_buffer)
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

        if loop_aborted:
            break

    if hit_limit and not loop_aborted:
        yield {
            "type": "warning",
            "kind": "budget_exhausted",  # F11：类型化预算耗尽标记（折叠层不做字符串匹配）
            "message": max_turns_warning(max_turns),
        }

    if loop_aborted:
        yield {
            "type": "warning",
            "kind": "budget_exhausted",  # F11：loop guard 中止同为预算耗尽语义
            "message": loop_abort_warning(turn, config.agent.loop_detection_threshold),
        }

    done_event: dict[str, Any] = {
        "type": "done",
        "total_turns": turn,
        "total_tokens": total_usage.total_tokens,
        "usage": total_usage.to_dict(),
        "messages": messages,
    }
    costs = _calculate_costs(llm_client, total_usage)
    if costs:
        done_event["cost"] = costs
    yield done_event


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


def _prepend_skill_context(user_message: str, skill_context_buffer) -> str:
    if not skill_context_buffer or skill_context_buffer.is_empty():
        return user_message
    drained = skill_context_buffer.drain()
    if not drained:
        return user_message
    return f"{drained}\n\n---\nUser request:\n{user_message}"


def _drain_skill_context(skill_context_buffer) -> str:
    if not skill_context_buffer or skill_context_buffer.is_empty():
        return ""
    return skill_context_buffer.drain()


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
