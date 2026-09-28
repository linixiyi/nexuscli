"""subagent.py — Delegated subagent runner behind the ``task`` tool.

A subagent is an independent Agent session with its own history, skill buffer,
and tool registry, spawned to complete one delegated task and report back.
Built-in ``general-purpose`` and ``explore`` agents always exist; users can
define more as markdown files with a simple ``key: value`` frontmatter in
``~/.nexuscli/agents/`` (user scope) or ``<cwd>/.nexuscli/agents/`` (project
scope, which wins on name conflicts). The file body becomes the subagent's
system prompt; ``model`` is parsed but ignored (reserved for future per-agent
model routing).

Recursion is bounded twice: :func:`run_subagent` refuses to start when the
calling context has already reached the depth limit, and the ``task`` tool is
removed from every subagent registry (default deny, mirroring opencode).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nexuscli.agent.agent import Agent
from nexuscli.tools.base import ToolContext
from nexuscli.tools.builtins import get_builtin_tools
from nexuscli.tools.registry import ToolRegistry

# Subagents may not spawn further subagents; the task handler converts the
# raised ValueError into an error tool result for the model.
SUBAGENT_DEPTH_LIMIT = 1

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
                    pass  # Reserved for per-agent model routing; parsed but ignored.
    if not name or not description or not body.strip():
        return None
    return AgentDefinition(name=name, description=description, prompt=body.strip(), tools=tools)


def load_subagents(cwd: str, home: Path | None = None) -> dict[str, AgentDefinition]:
    """Load built-in plus custom subagents; custom names override built-ins."""
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
    return definitions


def build_subagent_registry(agent_def: AgentDefinition) -> ToolRegistry:
    """Build a subagent's registry from builtin tools only (MCP tools excluded).

    When the agent defines a tool whitelist, only those tools are registered.
    The ``task`` tool is removed unconditionally — a whitelisted ``task`` must
    not reopen the recursion door, so the depth guard in :func:`run_subagent`
    is never the only barrier (mirrors opencode's default subagent permissions).
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
    return registry


async def run_subagent(agent_def: AgentDefinition, prompt: str, context: ToolContext) -> str:
    """Run *agent_def* on *prompt* in an isolated session; return its final report.

    Raises :class:`ValueError` when the calling context is already at the
    subagent depth limit; the ``task`` tool handler turns that into an error
    tool result for the model.
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
        llm_client=create_llm_client(context.config.llm),
        tool_registry=build_subagent_registry(agent_def),
        config=context.config,
        cwd=context.cwd,
        approval_callback=context.approval_callback,
        system_prompt=agent_def.prompt,
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
    return buffer.strip() or _EMPTY_REPORT
