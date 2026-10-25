from __future__ import annotations

import asyncio
from typing import Any

from nexuscli import observability
from nexuscli.hooks import fire_event, has_hooks
from nexuscli.policy import (
    AuditLog,
    PermissionDecision,
    evaluate_permissions,
    is_readonly_bash_command,
)
from nexuscli.telemetry import tool_call_span
from nexuscli.tools.base import Tool, ToolContext, ToolDecision, ToolResult
from nexuscli.tools.commands import classify_command
from nexuscli.tools.registry import ToolRegistry

# Tools whose command string can be statically judged read-only (see
# nexuscli.policy.bash_readonly): such commands bypass the HITL prompt.
_COMMAND_TOOLS = {"bash", "execute_command"}

# F9 工具副作用日志的接入范围（05-execution-durability.md 定稿）：三类有副作用工具。
# edit_file 的 dry_run 调用与 bash 的只读直通本身只读，不进日志；只读工具一律不进。
_JOURNALED_TOOLS = {"write_file", "edit_file", "bash", "execute_command"}


class ToolExecutor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def _journal_claim(
        self, context: ToolContext, tool: Tool, tool_call_id: str, data: dict[str, Any]
    ):
        """F9：三类有副作用工具的 claim（含 hash 级尽力去重）；不接入则返回 None。"""

        journal = context.journal
        if journal is None or tool.name not in _JOURNALED_TOOLS:
            return None
        if tool.name == "edit_file" and data.get("dry_run") is True:
            return None
        if tool.name in _COMMAND_TOOLS and is_readonly_bash_command(str(data.get("command") or "")):
            return None
        claim = journal.claim_tool(
            scope=context.cwd,
            run_id=context.run_id,
            tool_use_id=tool_call_id,
            tool_name=tool.name,
            tool_input=data,
        )
        if claim.action == "execute":
            hit = journal.lookup_by_hash(
                scope=context.cwd,
                run_id=context.run_id,
                tool_name=tool.name,
                tool_input=data,
            )
            if hit is not None and hit.action == "reuse" and hit.tool_use_id != tool_call_id:
                # 同 run 内同工具同参已完成：复用结果，并把本次新 id 的行落成 completed
                # （否则该行永远停在 started，后续同 id 认领会误判 fail-closed）。
                serialized = hit.serialized_result or {}
                journal.complete_tool(
                    scope=context.cwd,
                    run_id=context.run_id,
                    tool_use_id=tool_call_id,
                    content=str(serialized.get("content", "")),
                    is_error=bool(serialized.get("is_error", False)),
                )
                return hit
        return claim

    async def execute_all(
        self,
        calls: list[dict[str, Any]],
        context: ToolContext,
    ) -> list[ToolResult]:
        read_calls: list[tuple[dict[str, Any], Tool]] = []
        sequential_calls: list[tuple[dict[str, Any], Tool | None]] = []

        for call in calls:
            name = _tool_call_name(call)
            tool = self.registry.get(name)
            if tool and tool.is_read_only and tool.is_concurrency_safe:
                read_calls.append((call, tool))
            else:
                sequential_calls.append((call, tool))

        results: list[ToolResult] = []
        if read_calls:
            semaphore = asyncio.Semaphore(context.config.tools.max_concurrent_read)

            async def run_read(call: dict[str, Any], tool: Tool) -> ToolResult:
                async with semaphore:
                    return await self._execute_single(call, tool, context)

            results.extend(
                await asyncio.gather(*(run_read(call, tool) for call, tool in read_calls))
            )

        for call, tool in sequential_calls:
            results.append(await self._execute_single(call, tool, context))

        return results

    async def _execute_single(
        self,
        call: dict[str, Any],
        tool: Tool | None,
        context: ToolContext,
    ) -> ToolResult:
        """Run one tool call through the gate order.

        Gates run in sequence: unknown tool → plan-mode read-only stop →
        permission rules → PreToolUse hooks → PermissionRequest hooks →
        HITL approval → execute. The plan-mode stop short-circuits before
        rules, hooks, audit and approval: no approval decision ever
        happened, so nothing is audited and no hook (pre, permission or
        post) fires for a rejected call. A failed execution fires
        PostToolUseFailure instead of PostToolUse; the exception path fires
        neither event.

        For shell tools a statically read-only command string (see
        ``nexuscli.policy.bash_readonly``) counts as read-only for the plan
        gate and the HITL prompt, while the call is still audited and
        deny/ask rules and hook hints keep their priority. The audit record
        names ``readonly-rule`` as approver when the static judgment bypassed
        the prompt (no human was involved) and ``hitl`` only when a person
        actually decided.

        A per-run approval ("approve_run" returned by the approval callback,
        the prompt's "r" option) stores the tool name in
        ``context.run_scope_approved``; later calls to the same tool skip the
        prompt and are audited as ``run-scope``. Ask rules and hook ask hints
        keep forcing the prompt even for a run-approved tool.
        """
        tool_call_id = str(call.get("id") or "")
        # One trace id per call: every audit record below (deny/hitl/
        # allow/error) carries it so a session's trail stays correlatable.
        trace_id = observability.get_trace_id()
        name = _tool_call_name(call)
        payload = _tool_call_arguments(call)

        if not tool:
            return ToolResult(
                tool_use_id=tool_call_id,
                content=(
                    f'Tool "{name}" not found. Available tools: '
                    f"{', '.join(self.registry.list_names())}"
                ),
                is_error=True,
            )

        audit = AuditLog(context.config.policy.audit_log_path)
        approver = "none"
        try:
            journal_claim: object | None = None  # F9：validate 可能在赋值前抛错，先置 None
            data = tool.validate(payload)
            # Statically read-only shell commands (git status, ls, ...) count
            # as read-only for the plan gate and the HITL prompt below. The
            # permission chain still weighs in: deny/ask rules and PreToolUse
            # hooks keep their priority, and the call is still audited as a
            # non-read-only tool — pass-through is not trace-free.
            command_readonly = tool.name in _COMMAND_TOOLS and is_readonly_bash_command(
                str(data.get("command") or "")
            )
            # Plan mode is a hard read-only gate: tools that are not
            # read-only (statically or per command) are rejected before
            # permission rules, hooks or approval can weigh in. Read-only
            # tools keep flowing through the normal chain, so deny rules
            # still apply to them in plan mode.
            if context.config.policy.plan_mode and not (tool.is_read_only or command_readonly):
                return ToolResult(
                    tool_use_id=tool_call_id,
                    content=(
                        f'Tool "{tool.name}" is not allowed in plan mode. '
                        "Only read-only tools are permitted. "
                        "Press Shift+Tab to switch back to default mode and run the plan."
                    ),
                    is_error=True,
                )
            permission = evaluate_permissions(context.config.permissions, tool.name, data)
            if permission and permission.action == "deny":
                if context.config.features.audit_log:
                    audit.record(
                        tool_name=tool.name,
                        input_data=data,
                        outcome="deny",
                        approver="permission-rule",
                        cwd=context.cwd,
                        trace_id=trace_id,
                    )
                return ToolResult(
                    tool_use_id=tool_call_id,
                    content=f'Tool "{tool.name}" was denied by permission rule: {permission.rule}',
                    is_error=True,
                )
            # PreToolUse hooks: a blocked hook denies the call exactly like a
            # permission rule; an "ask" hint forces the approval prompt even
            # when rules/mode would have auto-approved. ("allow" is recorded
            # on the outcome but intentionally never bypasses approval.)
            force_prompt = False
            if has_hooks(context.config, "PreToolUse"):
                pre = await fire_event(
                    context.config,
                    "PreToolUse",
                    {"tool_name": tool.name, "tool_input": data},
                    context.cwd,
                )
                if pre.blocked:
                    if context.config.features.audit_log:
                        audit.record(
                            tool_name=tool.name,
                            input_data=data,
                            outcome="deny",
                            approver="hook",
                            cwd=context.cwd,
                            trace_id=trace_id,
                        )
                    return ToolResult(
                        tool_use_id=tool_call_id,
                        content=(
                            f'Tool "{tool.name}" was denied by pre-tool-use hook: {pre.reason}'
                        ),
                        is_error=True,
                    )
                force_prompt = pre.permission_hint == "ask"
            # PermissionRequest hooks: the observation/interception point
            # right before the approval decision, reusing the PreToolUse
            # protocol semantics exactly (deny blocks and is audited, "ask"
            # forces the prompt, "allow" is recorded only). It fires before
            # the read-only bypass below is computed so an "ask" hint also
            # suppresses that fast path.
            if has_hooks(context.config, "PermissionRequest"):
                req = await fire_event(
                    context.config,
                    "PermissionRequest",
                    {"tool_name": tool.name, "tool_input": data},
                    context.cwd,
                )
                if req.blocked:
                    if context.config.features.audit_log:
                        audit.record(
                            tool_name=tool.name,
                            input_data=data,
                            outcome="deny",
                            approver="hook",
                            cwd=context.cwd,
                            trace_id=trace_id,
                        )
                    return ToolResult(
                        tool_use_id=tool_call_id,
                        content=(
                            f'Tool "{tool.name}" was denied by '
                            f"permission-request hook: {req.reason}"
                        ),
                        is_error=True,
                    )
                force_prompt = force_prompt or req.permission_hint == "ask"
            readonly_passthrough = _readonly_passthrough(
                command_readonly, force_prompt, permission, context.config.policy.hitl_mode
            )
            # Computed before the decision: on the first "r" press the grant
            # is not in the set yet (the human decided), while on later calls
            # the run-scope grant is what bypassed the prompt.
            run_scope_bypass = _run_scope_bypass(tool.name, context, permission, force_prompt)
            decision = await self._approval_decision(
                tool,
                data,
                context,
                permission,
                force_prompt=force_prompt,
                readonly_command=command_readonly,
            )
            if decision == "approve_run":
                context.run_scope_approved.add(tool.name)
            if decision in {"deny", "skip"}:
                approver = "hitl"
                audit.record(
                    tool_name=tool.name,
                    input_data=data,
                    outcome=decision,
                    approver=approver,
                    cwd=context.cwd,
                    trace_id=trace_id,
                )
                return ToolResult(
                    tool_use_id=tool_call_id,
                    content=_denial_message(tool.name, decision, context),
                    is_error=True,
                )
            if (
                permission
                and permission.action == "allow"
                and context.config.policy.hitl_mode != "always"
            ):
                approver = "permission-rule"
            elif run_scope_bypass and decision == "approve":
                # The run-scope grant bypassed the prompt: the earlier "r"
                # decision authorized this tool for the whole run, so this
                # call must not be attributed to a fresh human approval.
                approver = "run-scope"
            elif readonly_passthrough:
                # The static read-only judgment bypassed the prompt: no human
                # approved, so the audit must not attribute the call to "hitl".
                approver = "readonly-rule"
            elif tool.requires_approval or context.config.policy.hitl_mode == "always":
                approver = "hitl"

            # F9 工具副作用日志：审批门之后 claim（门链先行，日志不改变任何审批语义）。
            journal_claim = self._journal_claim(context, tool, tool_call_id, data)
            if journal_claim is not None and journal_claim.action == "reuse":
                serialized = journal_claim.serialized_result or {}
                return ToolResult(
                    tool_use_id=tool_call_id,
                    content=str(serialized.get("content", "")),
                    is_error=bool(serialized.get("is_error", False)),
                    error_type="journal_reuse",
                )
            if journal_claim is not None and journal_claim.action == "outcome_unknown":
                return ToolResult(
                    tool_use_id=tool_call_id,
                    content=(
                        "This side-effectful call has an unknown outcome from a previous "
                        "interrupted attempt (tool journal). It will not be replayed "
                        "automatically; inspect the workspace or start a fresh call."
                    ),
                    is_error=True,
                    error_type="outcome_unknown",
                )

            # The span covers only the tool execution and its outcome record.
            # The gate chain above (deny rules / hooks / approval / audit)
            # stays outside the span on purpose: gate order and audit
            # semantics must not move.
            with tool_call_span(context.config.telemetry.enabled, tool_name=tool.name) as span:
                result = await tool.execute(
                    data,
                    context,
                )
                if journal_claim is not None:
                    # 副作用已发生：落库结果。写失败必须原样上抛（不得触发重执行）。
                    context.journal.complete_tool(
                        scope=context.cwd,
                        run_id=context.run_id,
                        tool_use_id=tool_call_id,
                        content=result.content,
                        is_error=result.is_error,
                    )
                # Must stay inside the with-block: start_as_current_span ends the span on
                # __exit__, and the SDK drops set_attribute on an ended span (warning
                # "Setting attribute on ended span."; verified on opentelemetry-sdk 1.45/1.20).
                span.record_outcome("error" if result.is_error else "ok")
            result.tool_use_id = tool_call_id
            if not tool.is_read_only and context.config.features.audit_log:
                audit.record(
                    tool_name=tool.name,
                    input_data=data,
                    outcome="allow" if not result.is_error else "error",
                    approver=approver,
                    cwd=context.cwd,
                    trace_id=trace_id,
                )
            # Post-tool hooks are informational only: they never change the
            # result that has already been produced. A failed call fires
            # PostToolUseFailure instead of PostToolUse — the failure event
            # replaces the success event — and a blocked post-hook is
            # audited; errors would only be surfaced to the user. The bare
            # ``except`` below fires neither event: exception paths keep the
            # minimal slice (ZCode's isInterrupt semantics are intentionally
            # not ported).
            if result.is_error:
                post_event = "PostToolUseFailure"
                post_payload = {
                    "tool_name": tool.name,
                    "tool_input": data,
                    "error_message": result.content,
                }
            else:
                post_event = "PostToolUse"
                post_payload = {
                    "tool_name": tool.name,
                    "tool_input": data,
                    "tool_response": result.content,
                }
            if has_hooks(context.config, post_event):
                post = await fire_event(context.config, post_event, post_payload, context.cwd)
                if post.blocked and context.config.features.audit_log:
                    audit.record(
                        tool_name=tool.name,
                        input_data=data,
                        outcome="deny",
                        approver="hook",
                        cwd=context.cwd,
                        trace_id=trace_id,
                    )
            return result
        except Exception as exc:  # noqa: BLE001 - tool errors must flow back to the model
            if journal_claim is not None and journal_claim.action == "execute":
                # 工具执行异常：结果未知，fail-closed（不提供 serialized_result）。
                # mark 自身的失败原样上抛——吞掉它会把「副作用可能已发生」伪装成干净失败。
                context.journal.mark_outcome_unknown(
                    scope=context.cwd,
                    run_id=context.run_id,
                    tool_use_id=tool_call_id,
                )
            if context.config.features.audit_log and tool and not tool.is_read_only:
                audit.record(
                    tool_name=tool.name,
                    input_data=payload,
                    outcome="error",
                    approver=approver,
                    cwd=context.cwd,
                    trace_id=trace_id,
                )
            return ToolResult(
                tool_use_id=tool_call_id,
                content=f'Tool "{name}" execution error: {exc}',
                is_error=True,
            )

    async def _approval_decision(
        self,
        tool: Tool,
        payload: dict[str, Any],
        context: ToolContext,
        permission: PermissionDecision | None = None,
        force_prompt: bool = False,
        readonly_command: bool = False,
    ) -> ToolDecision:
        mode = context.config.policy.hitl_mode
        # A statically read-only command skips the prompt — but only while no
        # explicit interceptor disagrees: an ask rule, a PreToolUse hook that
        # forces prompting, or hitl_mode "always" all keep their right of way.
        if _readonly_passthrough(readonly_command, force_prompt, permission, mode):
            return "approve"
        action = permission.action if permission is not None else None
        if not force_prompt:
            if action == "ask":
                if mode == "never":
                    # Fail closed: no human is available to ask in never mode.
                    return "deny"
                # Any other mode: an explicit ask falls through to the
                # approval prompt below — it must never be silently
                # auto-approved, even when the tool is not requires_approval.
            elif action == "allow":
                # Allow rules skip the prompt in auto/never; hitl_mode ==
                # "always" is the explicit confirm-everything mode and still
                # prompts.
                if mode != "always":
                    return "approve"
            elif mode == "never" or mode == "auto" and not tool.requires_approval:
                return "approve"
        if not context.approval_callback:
            return "deny"
        # Run-scope approval: the user granted this tool for the rest of the
        # run earlier, so the prompt is skipped — but only where the run scope
        # owns the decision. An ask rule or an "ask" hook hint keeps its right
        # of way and the prompt still shows (same guard as the readonly
        # passthrough above).
        if _run_scope_bypass(tool.name, context, permission, force_prompt):
            return "approve"
        result = context.approval_callback(
            {
                "tool_name": tool.name,
                "input": payload,
                "danger_level": _display_danger_level(tool, payload),
                "description": tool.description,
            }
        )
        if asyncio.iscoroutine(result):
            result = await result
        return result


_DECISION_PAST_TENSE = {"deny": "denied", "skip": "skipped"}


def _denial_message(tool_name: str, decision: ToolDecision, context: ToolContext) -> str:
    """Human-readable refusal that names how the call could have been allowed.

    The bare f-string used to render the decision verb by appending "ed",
    which produced "denyed" / "skiped". Single-prompt (-p) runs have no
    approval callback, so every non-read-only call lands here; without a
    pointer at the two real escape hatches the model just retries blindly.
    """
    verb = _DECISION_PAST_TENSE.get(decision, decision)
    message = f'Tool "{tool_name}" was {verb} by approval policy.'
    if context.approval_callback is None:
        return (
            f"{message} This run has no interactive approver (single-prompt mode), "
            "so no human could be asked. Allow the tool beforehand with "
            f'permissions.allow (for example {{"allow": ["{tool_name}"]}}) in '
            "config.json, or run the interactive REPL for approval prompts."
        )
    return message


def _readonly_passthrough(
    readonly_command: bool,
    force_prompt: bool,
    permission: PermissionDecision | None,
    mode: str,
) -> bool:
    """True when a statically read-only command skips the approval prompt.

    Single source of truth for two consumers: the approval decision (the
    prompt is bypassed) and the audit attribution — under this exact
    condition no human approved the call, so the audit record must name
    ``readonly-rule`` instead of ``hitl``.
    """
    action = permission.action if permission is not None else None
    return readonly_command and not force_prompt and action != "ask" and mode != "always"


def _run_scope_bypass(
    tool_name: str,
    context: ToolContext,
    permission: PermissionDecision | None,
    force_prompt: bool,
) -> bool:
    """True when a per-run grant (the prompt's "r") skips the approval prompt.

    Mirror of :func:`_readonly_passthrough` for the run-scoped grant: the
    bypass only applies where the approval prompt itself would have been the
    deciding gate — an ask permission rule or an "ask" hook hint keeps its
    right of way and the prompt still shows, so the run grant never overrides
    an explicit interceptor. It is consumed by the approval decision (prompt
    bypassed) and by the audit attribution: under this exact condition no
    human approved *this* call, so the record names ``run-scope`` instead of
    ``hitl``.
    """
    action = permission.action if permission is not None else None
    return tool_name in context.run_scope_approved and not force_prompt and action != "ask"


def _display_danger_level(tool: Tool, payload: dict[str, Any]) -> str:
    """Refine the static tool danger level with the actual command content.

    The static level stays the default; shell tools get a per-command rating so
    the approval prompt distinguishes ``ls`` from ``rm -rf`` or ``curl | sh``.
    """
    if tool.name in {"bash", "execute_command"}:
        command = str(payload.get("command") or "")
        if command.strip():
            return classify_command(command)
    return tool.danger_level


def _tool_call_name(call: dict[str, Any]) -> str:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    return str(function.get("name") or call.get("name") or "")


def _tool_call_arguments(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    arguments = function.get("arguments", call.get("arguments", {}))
    if isinstance(arguments, str):
        import json

        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": arguments}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return arguments if isinstance(arguments, dict) else {}
