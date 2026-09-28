from __future__ import annotations

from nexuscli.context import ContextBudget, ContextWindowManager, estimate_text_tokens
from nexuscli.types import Message


def test_context_under_threshold_is_unchanged():
    messages = [Message(role="user", content="hello"), Message(role="assistant", content="hi")]
    manager = ContextWindowManager(ContextBudget(10_000, 1_000))

    result = manager.prepare(messages, system_prompt="system")

    assert not result.compressed
    assert result.messages == messages


def test_context_compresses_old_turns_and_keeps_latest_user_message():
    messages = []
    for index in range(8):
        messages.extend(
            [
                Message(role="user", content=f"request {index} " + "x" * 240),
                Message(role="assistant", content=f"answer {index} " + "y" * 240),
            ]
        )
    manager = ContextWindowManager(
        ContextBudget(
            context_window=900,
            max_output_tokens=150,
            compression_threshold=0.6,
            compression_target=0.4,
            reserve_tokens=50,
        ),
        min_recent_messages=4,
    )

    result = manager.prepare(messages, system_prompt="system")

    assert result.compressed
    assert result.summarized_messages > 0
    assert result.messages[0].role == "assistant"
    assert "conversation-summary" in str(result.messages[0].content)
    assert any(
        message.content.startswith("request 7")
        for message in result.messages
        if message.role == "user"
    )
    assert result.estimated_tokens_after < result.estimated_tokens_before


def test_context_boundary_keeps_tool_call_and_result_together():
    messages = [
        Message(role="user", content="old" * 300),
        Message(role="assistant", content="old answer" * 200),
        Message(role="user", content="read file"),
        Message(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        ),
        Message(role="tool", content="result", tool_call_id="call_1"),
        Message(role="assistant", content="done"),
    ]
    manager = ContextWindowManager(ContextBudget(700, 100, 0.5, 0.35, 25), min_recent_messages=3)

    result = manager.prepare(messages)

    roles = [message.role for message in result.messages]
    assert roles[-4:] == ["user", "assistant", "tool", "assistant"]
    assert result.messages[-2].tool_call_id == "call_1"


def test_context_truncates_oversized_tool_payload():
    messages = [
        Message(role="user", content="inspect"),
        Message(role="assistant", content="", tool_calls=[{"id": "call_1"}]),
        Message(role="tool", content="x" * 10_000, tool_call_id="call_1"),
        Message(role="assistant", content="done"),
    ]
    manager = ContextWindowManager(
        ContextBudget(1_000, 100, 0.5, 0.35, 20), tool_result_max_chars=300
    )

    result = manager.prepare(messages)

    tool_message = next(message for message in result.messages if message.role == "tool")
    assert "tool result truncated" in str(tool_message.content)


def _long_history(count: int) -> list[Message]:
    messages: list[Message] = []
    for index in range(count):
        if index % 2 == 0:
            messages.append(Message(role="user", content=f"request {index} " + "x" * 600))
        else:
            messages.append(Message(role="assistant", content=f"answer {index} " + "y" * 600))
    return messages


def test_compact_now_compresses_regardless_of_threshold():
    messages = _long_history(32)
    manager = ContextWindowManager(ContextBudget(100_000, 1_000), min_recent_messages=4)

    result = manager.compact_now(messages)

    assert result.compressed
    assert result.summarized_messages == len(messages) - manager.min_recent_messages
    assert len(result.messages) == manager.min_recent_messages + 1
    assert len(result.messages) < len(messages)
    assert result.estimated_tokens_before > 0
    assert result.estimated_tokens_after < result.estimated_tokens_before
    assert "conversation-summary" in str(result.messages[0].content)


def test_compact_now_keeps_recent_messages_verbatim():
    messages = _long_history(32)
    manager = ContextWindowManager(ContextBudget(100_000, 1_000), min_recent_messages=4)

    result = manager.compact_now(messages)

    assert result.messages[1:] == messages[-manager.min_recent_messages :]


def test_compact_now_injects_focus_directive_into_summary():
    messages = _long_history(32)
    manager = ContextWindowManager(ContextBudget(100_000, 1_000), min_recent_messages=4)

    result = manager.compact_now(messages, focus="数据库迁移方案")

    summary = str(result.messages[0].content)
    lines = summary.splitlines()
    assert lines[2] == "Focus: 数据库迁移方案"
    assert lines[3] == "Preserve information related to this focus first."
    assert "数据库迁移方案" in summary
    assert result.estimated_tokens_after < result.estimated_tokens_before


def test_compact_now_handles_short_history_safely():
    manager = ContextWindowManager(ContextBudget(10_000, 1_000))

    empty = manager.compact_now([])
    assert not empty.compressed
    assert empty.messages == []
    assert empty.estimated_tokens_before == 0
    assert empty.estimated_tokens_after == 0

    only = Message(role="user", content="only turn")
    single = manager.compact_now([only])
    assert not single.compressed
    assert single.messages == [only]
    assert single.summarized_messages == 0
    assert single.estimated_tokens_before == 0
    assert single.estimated_tokens_after == 0


def test_compact_now_truncates_oversized_tool_payload():
    messages = _long_history(10)
    messages.extend(
        [
            Message(role="user", content="inspect"),
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            Message(role="tool", content="x" * 10_000, tool_call_id="call_1"),
            Message(role="assistant", content="done"),
        ]
    )
    manager = ContextWindowManager(
        ContextBudget(100_000, 1_000), min_recent_messages=4, tool_result_max_chars=300
    )

    result = manager.compact_now(messages)

    tool_message = next(message for message in result.messages if message.role == "tool")
    assert "tool result truncated" in str(tool_message.content)


def test_mixed_language_token_estimator_is_nonzero_and_conservative():
    assert estimate_text_tokens("你好 world()") >= 5
