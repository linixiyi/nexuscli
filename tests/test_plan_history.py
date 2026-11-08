"""tests/test_plan_history.py — plan 任务与会话历史（task 0013）。

覆盖：planner 在规划回合收到当前会话历史；每个 plan 任务以历史快照启动
（不再固定 ``history=[]``），第二个任务能看到第一个任务的用户/助手消息；
任务消息按任务顺序追加，done 时只追加一次，最终历史无重复——并行批次内
任务互不串线，上下文压缩改写快照前缀时增量仍能落账。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from nexuscli.agent import PlanExecuteAgent
from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.types import Message


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() into a temp home.

    Plan runs write graph checkpoints under ``~/.nexuscli/plan_runs`` and
    snapshots go to ``~/.nexuscli/snapshots``. A HOME-only patch leaks onto
    the real home on Windows (Path.home() reads USERPROFILE there), so triple
    patch — same convention as tests/test_plan.py.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


# 含"然后"的多步目标：绕开 _is_simple_goal 的直通路径，规划器必须真的
# 发起 LLM 规划回合，历史前置才有断言对象。
_GOAL = "先完成任务 A，然后执行任务 B"

_CHAIN_PLAN_JSON = (
    '{"summary":"chain","tasks":['
    '{"id":"a","description":"任务 A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"b","description":"任务 B","type":"ANALYSIS","dependencies":["a"]}]}'
)

_PARALLEL_PLAN_JSON = (
    '{"summary":"parallel","tasks":['
    '{"id":"a","description":"任务 A","type":"ANALYSIS","dependencies":[]},'
    '{"id":"b","description":"任务 B","type":"ANALYSIS","dependencies":[]}]}'
)

# 预置的"上一个回合"会话历史：planner 与每个任务worker都应能看到它。
_PRIOR_HISTORY = [
    Message(role="user", content="先前的问题：项目用什么测试框架？"),
    Message(role="assistant", content="先前的回答：pytest。"),
]


class _RecordingPlanClient:
    """按回合类型记录收到的完整消息序列的假客户端。

    规划器返回 task_1 -> task_2 链；子类可用 ``plan_json`` 换成并行计划。
    """

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    plan_json = _CHAIN_PLAN_JSON

    def __init__(self):
        self.planner_chats: list[list[Message]] = []
        self.task_chats: dict[str, list[list[Message]]] = {}

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        body = _message_text(messages[-1].content)
        if "请为以下目标创建执行计划" in body:
            self.planner_chats.append(list(messages))
            yield {"type": "text_delta", "text": self.plan_json}
            yield {"type": "message_end", "stop_reason": "end_turn"}
            return
        for task_id in ("task_1", "task_2"):
            if f"当前任务 [{task_id}]" in body:
                self.task_chats.setdefault(task_id, []).append(list(messages))
                yield {"type": "text_delta", "text": f"{task_id} 的结果"}
                yield {"type": "message_end", "stop_reason": "end_turn"}
                return
        yield {"type": "text_delta", "text": "fallback"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _ParallelPlanClient(_RecordingPlanClient):
    """两个互不依赖的任务：同一批次并行执行。"""

    plan_json = _PARALLEL_PLAN_JSON


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return str(content)


def _make_agent(client, tmp_path: Path, *, compression: bool) -> PlanExecuteAgent:
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    config.features.context_compression = compression
    return PlanExecuteAgent(
        llm_client=client,
        tool_registry=registry,
        config=config,
        cwd=str(tmp_path),
    )


async def _drain(events) -> list[dict[str, Any]]:
    collected = []
    async for event in events:
        if event.get("type") == "error":
            raise event["error"]
        collected.append(event)
    return collected


def _message_key(message: Message) -> tuple[str, str, str | None]:
    return (str(message.role), str(message.content), message.tool_call_id)


def _assert_no_duplicates(messages: list[Message]) -> None:
    keys = [_message_key(message) for message in messages]
    assert len(keys) == len(set(keys)), f"最终历史出现重复消息：{keys}"


def test_planner_receives_session_history(tmp_path, isolated_home):
    """规划回合的消息以会话历史快照开头，规划提示只出现一次。"""
    client = _RecordingPlanClient()
    agent = _make_agent(client, tmp_path, compression=False)
    agent.history = list(_PRIOR_HISTORY)

    asyncio.run(_drain(agent.run(_GOAL)))

    assert client.planner_chats, "多步目标必须经过规划器 LLM 回合"
    planner_messages = client.planner_chats[0]
    assert planner_messages[: len(_PRIOR_HISTORY)] == _PRIOR_HISTORY
    assert len(planner_messages) == len(_PRIOR_HISTORY) + 1
    assert "请为以下目标创建执行计划" in _message_text(planner_messages[-1].content)


def test_second_task_sees_first_task_messages(tmp_path, isolated_home):
    """第二个任务以含第一个任务用户/助手消息的快照启动，最终历史无重复。"""
    client = _RecordingPlanClient()
    agent = _make_agent(client, tmp_path, compression=False)
    agent.history = list(_PRIOR_HISTORY)

    events = asyncio.run(_drain(agent.run(_GOAL)))
    done = events[-1]
    assert done["type"] == "done"

    # 第一个任务以会话历史快照启动（不再固定 history=[]）。
    task_1_messages = client.task_chats["task_1"][0]
    assert task_1_messages[: len(_PRIOR_HISTORY)] == _PRIOR_HISTORY
    task_1_user = task_1_messages[-1]
    assert "当前任务 [task_1]" in _message_text(task_1_user.content)

    # 第二个任务能看到第一个任务的用户/助手消息，会话历史也保留。
    task_2_messages = client.task_chats["task_2"][0]
    task_2_user = task_2_messages[-1]
    contents = [_message_text(message.content) for message in task_2_messages]
    assert _message_text(task_1_user.content) in contents
    assert "task_1 的结果" in contents
    assert task_2_messages[: len(_PRIOR_HISTORY)] == _PRIOR_HISTORY
    assert "当前任务 [task_2]" in _message_text(task_2_user.content)

    # 最终历史：会话历史 + 两个任务的回合 + done 时只追加一次的收尾。
    messages = list(done["messages"])
    _assert_no_duplicates(messages)
    keys = [_message_key(message) for message in messages]
    assert keys.count(_message_key(_PRIOR_HISTORY[0])) == 1
    assert keys.count(_message_key(task_1_user)) == 1
    assert keys.count(("assistant", "task_1 的结果", None)) == 1
    assert keys.count(_message_key(task_2_user)) == 1
    assert keys.count(("assistant", "task_2 的结果", None)) == 1

    # 任务消息按任务顺序排列，收尾的 user/assistant 各只追加一次。
    positions = {
        key: index
        for index, key in enumerate(keys)
        if key
        in {
            _message_key(task_1_user),
            ("assistant", "task_1 的结果", None),
            _message_key(task_2_user),
            ("assistant", "task_2 的结果", None),
        }
    }
    assert positions[_message_key(task_1_user)] < positions[("assistant", "task_1 的结果", None)]
    assert positions[("assistant", "task_1 的结果", None)] < positions[_message_key(task_2_user)]
    assert positions[_message_key(task_2_user)] < positions[("assistant", "task_2 的结果", None)]
    assert messages[-2].role == "user" and _GOAL in _message_text(messages[-2].content)
    assert messages[-1].role == "assistant"
    assert "计划执行完成" in _message_text(messages[-1].content)
    assert keys.count(("user", _GOAL, None)) == 1


def test_parallel_tasks_share_pre_batch_snapshot(tmp_path, isolated_home):
    """并行批次内任务共享同一份批次前快照，互不串线，增量按任务顺序追加。"""
    client = _ParallelPlanClient()
    agent = _make_agent(client, tmp_path, compression=False)
    agent.history = list(_PRIOR_HISTORY)

    events = asyncio.run(_drain(agent.run(_GOAL)))
    done = events[-1]
    assert done["type"] == "done"

    # 两个任务拿到同一份批次前快照：会话历史 + 自己的任务上下文，
    # 看不到兄弟任务的任何消息。
    for task_id in ("task_1", "task_2"):
        chat = client.task_chats[task_id][0]
        assert chat[: len(_PRIOR_HISTORY)] == _PRIOR_HISTORY
        assert "当前任务 [" + task_id + "]" in _message_text(chat[-1].content)
        assert len(chat) == len(_PRIOR_HISTORY) + 1
    for task_id, chats in client.task_chats.items():
        sibling_replies = {f"{other} 的结果" for other in ("task_1", "task_2") if other != task_id}
        for chat in chats:
            contents = [_message_text(message.content) for message in chat]
            assert not sibling_replies.intersection(contents)

    # 增量按执行顺序落账：task_1 的消息在 task_2 之前，各自只出现一次。
    messages = list(done["messages"])
    _assert_no_duplicates(messages)
    keys = [_message_key(message) for message in messages]
    first_positions = {
        task_id: keys.index(("assistant", f"{task_id} 的结果", None))
        for task_id in ("task_1", "task_2")
    }
    assert first_positions["task_1"] < first_positions["task_2"]


def test_task_increment_survives_context_compression(tmp_path, isolated_home):
    """默认配置（压缩开启）下，快照前缀被压缩改写时增量仍完整落账、不重复。"""
    client = _RecordingPlanClient()
    agent = _make_agent(client, tmp_path, compression=True)
    agent.history = list(_PRIOR_HISTORY)

    events = asyncio.run(_drain(agent.run(_GOAL)))
    done = events[-1]
    assert done["type"] == "done"

    # 压缩会摘要旧历史，但第二个任务仍能看到第一个任务的用户/助手消息。
    task_1_user = client.task_chats["task_1"][0][-1]
    task_2_contents = [_message_text(message.content) for message in client.task_chats["task_2"][0]]
    assert _message_text(task_1_user.content) in task_2_contents
    assert "task_1 的结果" in task_2_contents

    # 最终历史无重复，任务回合与会话历史各恰好一份。
    messages = list(done["messages"])
    _assert_no_duplicates(messages)
    keys = [_message_key(message) for message in messages]
    for expected in (
        _message_key(_PRIOR_HISTORY[0]),
        _message_key(task_1_user),
        ("assistant", "task_1 的结果", None),
        ("assistant", "task_2 的结果", None),
    ):
        assert keys.count(expected) == 1, f"{expected} 应恰好出现一次"
    assert messages[-1].role == "assistant"
