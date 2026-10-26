"""Hooks 三路径覆盖（夜间② P1-2 回归锚定）。

UserPromptSubmit/Stop hooks 此前只在 agent.py 的 react 循环触发，/plan 与
/team 的全部公开入口直接构造 PlanExecuteAgent/AgentOrchestrator，完全绕过
UserPromptSubmit 阻断门禁。下沉后两条 runner 必须与 react 同效：exit 2 的
hook 在计划/编排入口直接拒绝输入，不调用 LLM。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from nexuscli.agent.orchestrator import AgentOrchestrator
from nexuscli.agent.plan_execute import PlanExecuteAgent
from nexuscli.config import (
    HOOK_EVENT_FIELDS,
    HookCommandConfig,
    HookMatcherConfig,
    load_config,
)
from nexuscli.tools import ToolRegistry, get_builtin_tools

_EXIT_TWO_BODY = (
    "import sys\nsys.stderr.write('no pushing allowed')\nsys.stderr.flush()\nsys.exit(2)\n"
)


def _blocking_prompt_hook(config, tmp_path: Path) -> None:
    script = tmp_path / "block_prompt.py"
    script.write_text(_EXIT_TWO_BODY, encoding="utf-8")
    setattr(
        config.hooks,
        HOOK_EVENT_FIELDS["UserPromptSubmit"],
        [
            HookMatcherConfig(
                matcher="*",
                hooks=[HookCommandConfig(command=f'"{sys.executable}" "{script}"')],
            )
        ],
    )


class _UnusedClient:
    """门禁必须先于任何 LLM 调用触发；此 client 若被调用即测试失败。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"LLM must not be reached when the prompt gate blocks: {name}")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry


def _collect(agen) -> list[dict[str, Any]]:
    async def _run():
        return [event async for event in agen]

    return asyncio.run(_run())


def test_plan_execute_prompt_hook_blocks_before_llm(tmp_path: Path) -> None:
    config = load_config(project_root=tmp_path)
    _blocking_prompt_hook(config, tmp_path)
    agent = PlanExecuteAgent(
        llm_client=_UnusedClient(),
        tool_registry=_registry(),
        config=config,
        cwd=str(tmp_path),
    )
    events = _collect(agent.run("hello plan"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "user-prompt-submit hook" in str(events[0]["error"])


def test_orchestrator_prompt_hook_blocks_before_llm(tmp_path: Path) -> None:
    config = load_config(project_root=tmp_path)
    _blocking_prompt_hook(config, tmp_path)
    agent = AgentOrchestrator(
        llm_client=_UnusedClient(),
        tool_registry=_registry(),
        config=config,
        cwd=str(tmp_path),
    )
    events = _collect(agent.run("hello team"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "user-prompt-submit hook" in str(events[0]["error"])


def test_plan_execute_no_hooks_runs_normally(tmp_path: Path) -> None:
    """无 hooks 时门禁零开销路径：run() 能走到规划阶段（首个事件非 error）。"""
    config = load_config(project_root=tmp_path)
    agent = PlanExecuteAgent(
        llm_client=_UnusedClient(),
        tool_registry=_registry(),
        config=config,
        cwd=str(tmp_path),
    )
    # _UnusedClient 在任何 LLM 访问时抛 AssertionError——只要门禁没拦、
    # 流程进到 planner 就会触发它，这正好证明门禁没有误拦。
    try:
        events = _collect(agent.run("hello"))
    except AssertionError:
        return  # 门禁放行、进入 planner：符合预期
    assert events and events[0].get("type") != "error", events[:2]
