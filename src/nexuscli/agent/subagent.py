"""subagent.py — Delegated subagent runner behind the ``task`` tool.

A subagent is an independent Agent session with its own history, skill buffer,
and tool registry, spawned to complete one delegated task and report back.
Built-in ``general-purpose`` and ``explore`` agents always exist; users can
define more as markdown files with a simple ``key: value`` frontmatter in
``~/.nexuscli/agents/`` (user scope) or ``<cwd>/.nexuscli/agents/`` (project
scope, which wins on name conflicts). The file body becomes the subagent's
system prompt; ``model`` selects that agent's LLM (per-agent model routing:
research agents can ride a small cheap model while implementation agents keep
the main config's strong model).

Recursion is bounded twice: :func:`run_subagent` refuses to start when the
calling context has already reached the depth limit, and the ``task`` tool is
removed from every subagent registry (default deny, mirroring opencode). The
turn loop is bounded too: every subagent runs with a fixed small
``SUBAGENT_MAX_TURNS`` (30) and does not scale up with the main agent's
``agent.max_turns`` configuration.

Usage coverage: a subagent run that produced tokens persists its own usage to
``~/.nexuscli/usage.db`` as a ``source=subagent`` row (best-effort, see
:func:`_record_subagent_usage`) so ``/usage stats`` includes delegated work.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from nexuscli.agent.agent import Agent
from nexuscli.agent.mailbox import get_mailbox_tools
from nexuscli.llm.usage_store import UsageStore
from nexuscli.plugins import load_plugins
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Usage

# Subagents may not spawn further subagents; the task handler converts the
# raised ValueError into an error tool result for the model.
SUBAGENT_DEPTH_LIMIT = 1

# Delegated tasks are short and scoped; capping the subagent loop
# independently keeps per-task cost bounded even though the main agent's
# default max_turns rose to 200 for long-running work (agent.max_turns).
SUBAGENT_MAX_TURNS = 30

_EMPTY_REPORT = "(subagent returned no text)"

_GENERAL_PURPOSE_PROMPT = """\
你是被主代理委派的独立子代理，不与最终用户直接交互：
- 一次性完成委派的任务，不要反问用户；缺少的信息按合理假设继续推进。
- 需要的事实自己检索，需要验证的结论自己运行只读检查确认。
- 最终产出一份自包含的报告：明确结论、关键文件路径（绝对路径）与验证结果。
"""

_EXPLORE_PROMPT = """\
你是只读代码探索子代理，擅长检索与定位，绝不修改任何内容：
- 绝不创建或写入文件，绝不编辑文件，绝不运行任何改变系统状态的命令。
- 先用目录树与 glob 缩小范围，再用 grep 精确定位，避免逐文件翻找。
- 以结构化清单返回发现：每条给出绝对路径（含行号）与一句话说明。
"""

_EXPLORE_TOOLS: tuple[str, ...] = (
    "read_file",
    "list_dir",
    "glob_files",
    "grep",
    "directory_tree",
    "get_file_info",
    "search_code",
    "web_search",
    "web_fetch",
)


@dataclass(slots=True)
class AgentDefinition:
    """One delegatable agent: identity, system prompt, and optional tool whitelist."""

    name: str
    description: str
    prompt: str
    tools: list[str] | None = None  # None = every builtin tool is available
    # Per-agent model routing (frontmatter ``model:``): None keeps the main
    # config's model; a value swaps the subagent's LLM client to that model.
    model: str | None = None


BUILTIN_AGENTS: dict[str, AgentDefinition] = {
    "general-purpose": AgentDefinition(
        name="general-purpose",
        description="通用任务求解代理，适合研究、多步实现与代码分析",
        prompt=_GENERAL_PURPOSE_PROMPT,
        tools=None,
    ),
    "explore": AgentDefinition(
        name="explore",
        description="只读代码探索代理，擅长检索与定位，不做任何修改",
        prompt=_EXPLORE_PROMPT,
        tools=list(_EXPLORE_TOOLS),
    ),
}


def _split_tools(value: str) -> list[str]:
    tools: list[str] = []
    for piece in value.split(","):
        tool = piece.strip()
        if tool and tool not in tools:
            tools.append(tool)
    return tools


def _parse_agent_file(path: Path) -> AgentDefinition | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    name = ""
    description = ""
    tools: list[str] | None = None
    model: str | None = None
    body = text
    if text.startswith("---"):
        segments = text.split("---", 2)
        if len(segments) == 3:
            frontmatter, body = segments[1].strip(), segments[2].lstrip("\n")
            for line in frontmatter.splitlines():
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key == "name":
                    name = value
                elif key == "description":
                    description = value
                elif key == "tools":
                    tools = _split_tools(value) or None
                elif key == "model":
                    model = value or None
    if not name or not description or not body.strip():
        return None
    return AgentDefinition(
        name=name, description=description, prompt=body.strip(), tools=tools, model=model
    )


def load_subagents(cwd: str, home: Path | None = None) -> dict[str, AgentDefinition]:
    """Load built-in plus custom subagents; custom names override built-ins.

    Plugin agents (``<cwd>/.nexuscli/plugins/<name>/agents/``) merge after the
    user/project scopes and win on name conflicts.
    """
    base = home if home is not None else Path.home()
    definitions = dict(BUILTIN_AGENTS)
    scopes = [
        base / ".nexuscli" / "agents",
        Path(cwd) / ".nexuscli" / "agents",
    ]
    for directory in scopes:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            definition = _parse_agent_file(path)
            if definition is not None:
                definitions[definition.name] = definition
    # Plugins merge last: a plugin agent overrides the project layer, which
    # overrides the user layer, which overrides built-ins — the same "last
    # scan wins" chain the scopes above already follow. No ZCode-style
    # ${plugin}:${component} namespace isolation in this MVP.
    for plugin in load_plugins(cwd):
        if plugin.agents_dir is None:
            continue
        for path in sorted(plugin.agents_dir.glob("*.md")):
            definition = _parse_agent_file(path)
            if definition is not None:
                definitions[definition.name] = definition
    return definitions


# Per-agent persistent memory lives under the user home, mirroring the path
# convention ``~/.nexuscli/agent-memory/<agent-name>/``. Only the prompt
# convention is defined here — the runner never creates the directory; the
# first write via the subagent's ``write_file`` tool does (file_ops.py's
# parent.mkdir), so delegation stays free of filesystem side effects.
_AGENT_MEMORY_ROOT_PARTS = (".nexuscli", "agent-memory")

# Model-facing convention appended to every subagent system prompt. Chinese on
# purpose: it sits next to the built-in agent prompts above. The degrade
# instruction is required because nexusCLI's file tools are confined to the
# workspace by default (``path_guard_enabled``), unlike ZCode's unguarded
# Write tool — the memory directory may legitimately be unwritable, in which
# case the agent folds its notes into the final report instead of retrying.
# Implicit concatenation keeps each source line under the 100-char limit while
# the rendered text stays on single lines.
_AGENT_MEMORY_SECTION_TEMPLATE = (
    "## 持久记忆目录\n"
    "\n"
    "你有一个跨委派保留的记忆目录：{memory_dir}"
    '（按你的代理名 "{agent_name}" 隔离，其他代理不共享）。\n'
    "- 委派开始时：若目录下已有 .md 笔记，先读取与本次任务相关的部分再动手。\n"
    "- 结束前：把跨委派有复用价值的结论（项目事实、验证过的做法、踩坑记录）"
    "写成 .md 笔记存入该目录；一次性的任务细节不存。\n"
    "- 用 read_file / write_file 直接读写"
    "（write_file 会自动创建缺失目录，见 file_ops.py:151 的 parent.mkdir）；"
    '若写入被路径守卫拒绝（"path escapes workspace"），'
    "把想记的备忘并入最终报告即可，不要重试。\n"
)


def agent_memory_dir(agent_name: str, home: Path | None = None) -> Path:
    """Return the per-agent memory directory ``<home>/.nexuscli/agent-memory/<name>``.

    *home* is resolved per call so tests can redirect the user scope by
    setting HOME/USERPROFILE — the same seam :func:`load_subagents` uses.
    """
    base = home if home is not None else Path.home()
    return base.joinpath(*_AGENT_MEMORY_ROOT_PARTS) / agent_name


def _is_safe_memory_name(agent_name: str) -> bool:
    """Only plain single-segment agent names get a memory directory section.

    Custom names come from untrusted frontmatter; anything containing a path
    separator (or the "."/".." special values) would inject an arbitrary
    relative path into the prompt, so it is refused.
    """
    return (
        bool(agent_name) and Path(agent_name).name == agent_name and agent_name not in {".", ".."}
    )


def build_subagent_system_prompt(agent_def: AgentDefinition, home: Path | None = None) -> str:
    """Return *agent_def*'s prompt plus its per-agent persistent memory section.

    Ported in spirit from ZCode's ``buildPersistentAgentMemoryPrompt``
    (packages/core/src/subagent/persistent-memory-prompt.ts): inject the memory
    root so the same agent name can persist and reuse notes across
    delegations. Key difference: ZCode's Write tool has no workspace path
    guard, while nexusCLI's file tools are workspace-confined by default
    (``path_guard_enabled``) — so the injected section carries a degrade
    instruction instead of promising the directory is always writable, and the
    runner neither pre-creates the directory nor loosens the guard.
    Unsafe names (path separators, "."/"..") get the bare prompt back.
    """
    if not _is_safe_memory_name(agent_def.name):
        return agent_def.prompt
    section = _AGENT_MEMORY_SECTION_TEMPLATE.format(
        memory_dir=agent_memory_dir(agent_def.name, home),
        agent_name=agent_def.name,
    )
    return f"{agent_def.prompt}\n\n{section}"


def subagent_llm_config(base_llm: Any, agent_def: AgentDefinition) -> Any:
    """Return the LlmConfig the subagent's client is built from.

    With no per-agent ``model`` the main config's LLM section is reused
    unchanged. With one, the model is swapped and the context window reset to
    ``None``: the main config's window was sized for the main model, so keeping
    it would mis-bound a different model's context — ``create_llm_client``
    falls back to the known-model table (or its provider default) instead.
    Typed loosely (``Any``) to avoid an import cycle with config for a pure
    dataclass copy.
    """
    if not agent_def.model:
        return base_llm
    return replace(base_llm, model=agent_def.model, context_window=None)


def build_subagent_registry(
    agent_def: AgentDefinition, mailbox_name: str | None = None
) -> ToolRegistry:
    """Build a subagent's registry from builtin tools only (MCP tools excluded).

    When the agent defines a tool whitelist, only those tools are registered.
    The ``task`` tool is removed unconditionally — a whitelisted ``task`` must
    not reopen the recursion door, so the depth guard in :func:`run_subagent`
    is never the only barrier (mirrors opencode's default subagent permissions).
    The mailbox primitives (``mailbox_post``/``mailbox_read``) are registered
    here only — the main agent's registry comes from ``get_builtin_tools()``
    alone, so the main agent never sees them. ``mailbox_name`` rebinds them to
    a name other than *agent_def*'s own (used by parallel workflow dispatch to
    isolate concurrent same-type siblings); ``None`` keeps the default.
    """
    allowed = set(agent_def.tools) if agent_def.tools is not None else None
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        if allowed is not None and tool.name not in allowed:
            continue
        if tool.name == "task":
            # Unconditional removal: a whitelisted ``task`` must not reopen the
            # recursion door — run_subagent's depth check stays as the second
            # barrier, never the only one (mirrors opencode's default).
            continue
        registry.register(tool)
    # Mailbox primitives are subagent-only: the main agent's registry is built
    # from get_builtin_tools() alone, so they never appear there. They honor
    # the same whitelist as builtin tools; the bound name defaults to the
    # agent's own name and can be overridden by parallel dispatchers.
    for tool in get_mailbox_tools(mailbox_name or agent_def.name):
        if allowed is not None and tool.name not in allowed:
            continue
        registry.register(tool)
    return registry


async def run_subagent(
    agent_def: AgentDefinition,
    prompt: str,
    context: ToolContext,
    mailbox_name: str | None = None,
) -> str:
    """Run *agent_def* on *prompt* in an isolated session; return its final report.

    Raises :class:`ValueError` when the calling context is already at the
    subagent depth limit; the ``task`` tool handler turns that into an error
    tool result for the model.

    ``mailbox_name`` overrides the agent name the mailbox tools are bound to
    (default: *agent_def*'s own name). Parallel dispatchers pass a per-step
    name so concurrent same-type siblings get distinct inbox files instead of
    sharing — and racing on — one drain-only inbox (see
    ``workflow_cmds._run_workflow_graph``).
    """
    if context.subagent_depth >= SUBAGENT_DEPTH_LIMIT:
        raise ValueError(
            f"Subagent depth limit reached ({SUBAGENT_DEPTH_LIMIT}); "
            "a subagent cannot delegate to another subagent."
        )

    # Imported lazily: keeps the LLM client replaceable in tests and avoids the
    # import cycle with tools.builtins. A fresh client from the same config is
    # equivalent to reusing the main agent's (httpx clients are stateless).
    from nexuscli.llm.factory import create_llm_client

    sub_agent = Agent(
        llm_client=create_llm_client(
            subagent_llm_config(context.config.llm, agent_def),
            telemetry_enabled=context.config.telemetry.enabled,
        ),
        tool_registry=build_subagent_registry(agent_def, mailbox_name=mailbox_name),
        config=context.config,
        cwd=context.cwd,
        approval_callback=context.approval_callback,
        system_prompt=build_subagent_system_prompt(agent_def),
        max_turns=SUBAGENT_MAX_TURNS,
        subagent_depth=context.subagent_depth + 1,
        session_id=context.config.policy.session_id or None,
    )

    buffer = ""
    async for event in sub_agent.run(prompt):
        event_type = event.get("type")
        if event_type == "text_delta":
            buffer += str(event.get("text") or "")
        elif event_type == "tool_call":
            # Only the text emitted after the last tool call is the report.
            buffer = ""
        elif event_type == "done":
            # The subagent's own usage lands in usage.db with source=subagent
            # so /usage stats covers delegated turns too. Best-effort: a store
            # failure must never break the delegation or its report. Runs that
            # produced no usage at all (e.g. scripted stub clients) are skipped
            # — a zero-token row is pure noise.
            _record_subagent_usage(event, sub_agent, context)
    return buffer.strip() or _EMPTY_REPORT


def _record_subagent_usage(
    done_event: dict[str, Any], sub_agent: Agent, context: ToolContext
) -> None:
    """Persist one subagent run's usage as a ``source=subagent`` turn row.

    Best-effort in the same sense as ``repl._record_turn_usage``: any failure
    (stub client without usage metadata, missing/wlocked db) is swallowed and
    the delegation continues. Model/provider identity comes from the
    subagent's own client; usage and cost come straight from the ``done``
    event the agent emitted (its run-level totals).
    """
    usage = Usage.from_mapping(done_event.get("usage") or {})
    if usage.total_tokens <= 0:
        return
    try:
        UsageStore().record_turn(
            model=sub_agent.llm_client.model_name,
            provider=sub_agent.llm_client.provider_name,
            session_id=context.config.policy.session_id or "",
            usage=usage,
            cost=done_event.get("cost"),
            source="subagent",
        )
    except Exception:  # noqa: BLE001 - best-effort; recording never breaks a run
        return
