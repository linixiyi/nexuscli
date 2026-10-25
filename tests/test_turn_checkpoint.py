"""回合级 checkpoint 测试（spec 2026-10-25_add-turn-resume-engine-contract，F10）。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent import Agent
from nexuscli.agent.turn_checkpoint import TurnCheckpointStore
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.journal import ToolJournal


class _DieAfterToolCallClient:
    """第一轮请求一个真实写文件调用；第二轮若被调用则正常结束。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if getattr(self, "_called", False):
            yield {"type": "text_delta", "text": "continued"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        self._called = True
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": "call_orig_1",
                "function": {
                    "name": "write_file",
                    "arguments": '{"path":"resumed.txt","content":"v1"}',
                },
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}


def _crash_on_first_dispatch(monkeypatch) -> None:
    """快照 (a) 落盘之后、journal claim 之前让分发炸掉——精确命中 (a) 窗口。"""

    original = ToolExecutor.execute_all
    state = {"crashed": False}

    async def crash_first(self, calls, context):  # noqa: ANN001
        if state["crashed"]:
            return await original(self, calls, context)
        state["crashed"] = True
        raise RuntimeError("simulated dispatch-time crash")

    monkeypatch.setattr(ToolExecutor, "execute_all", crash_first)


class _NaturalEndClient:
    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        yield {"type": "text_delta", "text": "all done"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


@pytest.fixture()
def agent_config(monkeypatch, tmp_path: Path) -> NexusCliConfig:
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    config.features.context_compression = False
    return config


def _agent(config: NexusCliConfig, cwd: Path, **kwargs: Any) -> Agent:
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    return Agent(
        llm_client=kwargs.pop("llm_client"),
        tool_registry=registry,
        system_prompt="test",
        cwd=str(cwd),
        config=config,
        **kwargs,
    )


def test_store_save_load_overwrite_list(tmp_path: Path) -> None:
    store = TurnCheckpointStore(directory=tmp_path / "tc")
    saved = store.save(
        run_id="runA",
        cwd=str(tmp_path),
        model="m",
        provider="p",
        turn=1,
        messages=[{"role": "user", "content": "hi"}],
        pending_tool_calls=[{"id": "c1", "name": "bash", "arguments": {}}],
        usage_so_far={"total_tokens": 10},
        cost_so_far={},
    )
    assert saved == "turn_runA"

    checkpoint = store.load(saved)
    assert checkpoint is not None
    created_first = checkpoint.created_at
    assert checkpoint.pending_tool_calls and checkpoint.pending_tool_calls[0]["id"] == "c1"

    # 同 run 覆盖：created_at 保留、turn 推进、pending 清空
    store.save(
        run_id="runA",
        cwd=str(tmp_path),
        model="m",
        provider="p",
        turn=1,
        messages=[{"role": "user", "content": "hi"}, {"role": "tool", "content": "done"}],
        pending_tool_calls=[],
        usage_so_far={"total_tokens": 30},
        cost_so_far={"usd": 0.1},
    )
    overwritten = store.load(saved)
    assert overwritten is not None
    assert overwritten.created_at == created_first
    assert overwritten.turn == 1 and overwritten.pending_tool_calls == []

    # cwd 过滤（realpath 归一化）
    assert len(store.list_checkpoints(cwd=str(tmp_path))) == 1
    assert store.list_checkpoints(cwd="/nonexistent-project") == []

    # 删除
    assert store.delete(saved)
    assert store.load(saved) is None


def test_store_corrupt_and_invalid_ids(tmp_path: Path) -> None:
    store = TurnCheckpointStore(directory=tmp_path / "tc")
    (tmp_path / "tc").mkdir(parents=True)
    (tmp_path / "tc" / "turn_broken.json").write_text("{not json", encoding="utf-8")
    assert store.load("turn_broken") is None
    assert store.load("../escape") is None
    assert store.load("") is None
    assert store.load("turn_missing") is None


def test_inflight_snapshot_survives_crash_and_journal_replay(
    tmp_path: Path, agent_config: NexusCliConfig, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _crash_on_first_dispatch(monkeypatch)
    agent = _agent(
        agent_config,
        tmp_path,
        llm_client=_DieAfterToolCallClient(),
        use_journal=True,
        turn_checkpoints=True,
    )
    with suppress(RuntimeError):
        asyncio.run(_collect(agent, "write the file"))  # dispatch 时刻崩溃（快照 (a) 已落盘）

    store = TurnCheckpointStore()
    checkpoint = store.load("turn_" + agent._run_id)
    assert checkpoint is not None
    assert checkpoint.run_id == agent._run_id
    assert len(checkpoint.pending_tool_calls) == 1
    assert checkpoint.pending_tool_calls[0]["id"] == "call_orig_1"

    # /turn-resume 的重放语义：原 id 经 executor+journal 重派——无记录 → 执行
    journal = ToolJournal()
    registry = ToolRegistry()
    for tool in get_builtin_tools():
        registry.register(tool)
    context = ToolContext(
        cwd=str(tmp_path),
        config=agent_config,
        approval_callback=lambda _request: "approve",
        journal=journal,
        run_id=checkpoint.run_id,  # 沿用原 run_id（两级去重前提）
    )
    executor = ToolExecutor(registry)
    results = asyncio.run(executor.execute_all(checkpoint.pending_tool_calls, context))
    assert not results[0].is_error
    assert (tmp_path / "resumed.txt").read_text() == "v1"

    # 再次重放同 id → journal reuse（幂等：不重执行）
    replay = asyncio.run(executor.execute_all(checkpoint.pending_tool_calls, context))
    assert replay[0].error_type == "journal_reuse"


def test_continue_run_appends_no_user_message(
    tmp_path: Path, agent_config: NexusCliConfig, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.txt").write_text("content")
    agent = _agent(
        agent_config,
        tmp_path,
        llm_client=_RepeatedToolCallThenEndClient(),
        use_journal=True,
        turn_checkpoints=True,
    )
    history_before = len(agent.history)
    events = asyncio.run(_collect(agent, "read the note"))
    assert any(event.get("type") == "done" for event in events)
    # 轮末快照覆盖：pending 为空
    store = TurnCheckpointStore()
    checkpoint = store.load("turn_" + agent._run_id)
    assert checkpoint is not None
    assert checkpoint.pending_tool_calls == []
    # 最后完整轮：read_file 那一轮（下一轮 end_turn 自然结束，不另写快照）
    assert checkpoint.turn >= 1
    assert len(agent.history) > history_before

    # continue_run 不追加用户消息：从既有历史直接续跑
    restored = _agent(
        agent_config,
        tmp_path,
        llm_client=_NaturalEndClient(),
        use_journal=True,
        turn_checkpoints=True,
        run_id=agent._run_id,
    )
    restored.history = list(agent.history)
    events = asyncio.run(_collect_continue(restored))
    assert any(event.get("type") == "done" for event in events)
    # 历史没有新增 user 行（continue_run 不注入用户消息）
    user_rows = [m for m in restored.history if m.role == "user"]
    assert len(user_rows) == len([m for m in agent.history if m.role == "user"])


def test_continue_run_requires_checkpoint_agent(
    agent_config: NexusCliConfig, tmp_path: Path
) -> None:
    agent = _agent(agent_config, tmp_path, llm_client=_NaturalEndClient())
    with pytest.raises(RuntimeError, match="turn_checkpoints"):
        asyncio.run(_collect_continue(agent))


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


async def _collect_continue(agent: Agent) -> list[dict[str, Any]]:
    return [event async for event in agent.continue_run()]


class _RepeatedToolCallThenEndClient:
    """第一轮请求 read_file 真实工具调用，第二轮自然结束。"""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        if getattr(self, "_called", False):
            yield {"type": "text_delta", "text": "done reading"}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        self._called = True
        yield {
            "type": "tool_call_delta",
            "tool_call": {
                "index": 0,
                "id": "call_read_1",
                "function": {"name": "read_file", "arguments": '{"path":"note.txt"}'},
            },
        }
        yield {"type": "message_end", "stop_reason": "tool_use"}
