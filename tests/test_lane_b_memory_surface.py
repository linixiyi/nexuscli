"""Lane B machine verification: the `/memory` slash-command surface.

The testing matrix had this row at 机器验证 0. These assertions pin what a
user actually sees in the REPL — the empty-state line, the `#id content`
list, the search/stats/delete/clear subcommand outputs — by driving
`_handle_slash` with a real Console over a captured buffer, the same way
test_repl_ux.py does.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.context import ContextBudget, ContextWindowManager
from nexuscli.entrypoints.repl import _handle_slash
from nexuscli.memory import MemoryManager
from nexuscli.types import Message


def _config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


def _console() -> Console:
    return Console(file=io.StringIO(), width=120)


def _run(command: str, tmp_path: Path, config: NexusCliConfig) -> str:
    console = _console()
    asyncio.run(
        _handle_slash(command, console, str(tmp_path), config, None, None, None, None, None)
    )
    return console.file.getvalue()


def test_memory_empty_state_tells_the_user_there_is_nothing(tmp_path: Path) -> None:
    config = _config(tmp_path)

    out = _run("/memory", tmp_path, config)

    assert "(no memories)" in out
    assert "Traceback" not in out


def test_memory_lists_saved_entries_as_readable_lines(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    memory_id = manager.save("User prefers uv over pip for Python dependency management")

    out = _run("/memory", tmp_path, config)

    assert f"#{memory_id} User prefers uv over pip" in out
    assert "(no memories)" not in out


def test_memory_search_subcommand_reports_matches_and_misses(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    memory_id = manager.save("User prefers uv over pip for Python dependency management")

    hit = _run("/memory search uv", tmp_path, config)
    miss = _run("/memory search kubernetes-clusters", tmp_path, config)

    assert f"#{memory_id} User prefers uv" in hit
    assert "(no matches)" in miss


def test_memory_stats_prints_a_json_summary(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    manager.save("User prefers uv over pip")

    out = _run("/memory stats", tmp_path, config)

    payload = json.loads(out)
    assert payload["total"] == 1
    assert payload["scope"] == str(tmp_path)


def test_memory_delete_subcommand_removes_the_entry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    memory_id = manager.save("Temporary note to delete")

    out = _run(f"/memory delete {memory_id}", tmp_path, config)
    assert "Deleted: True" in out

    after = _run("/memory", tmp_path, config)
    assert "(no memories)" in after


def test_memory_clear_subcommand_reports_the_count(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = MemoryManager(config.memory.long_term_db_path, scope=str(tmp_path))
    manager.save("note one")
    manager.save("note two")

    out = _run("/memory clear", tmp_path, config)

    assert "Cleared 2 memories." in out
    assert "(no memories)" in _run("/memory", tmp_path, config)


# ---------------------------------------------------------------------------
# 7.4 auto-compression regression (real-gateway finding, 2026-10-18)
# ---------------------------------------------------------------------------
# A real run against the GLM gateway hit HTTP 400 code 11133 right after the
# 80%-threshold auto-compression fired: `_build_compacted` used to emit the
# rolling summary as an assistant message, and the retained recent side starts
# with an assistant tool-call turn — strict-alternation providers reject both
# the assistant-first request and the consecutive assistant pair. The summary
# now rides in a user message.


def _react_loop_history() -> list[Message]:
    messages = [Message(role="user", content="read the six part files")]
    for i in range(1, 7):
        messages.append(
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": f'{{"path": "p{i}.txt"}}'},
                    }
                ],
            )
        )
        messages.append(
            Message(
                role="tool",
                content=f"filler payload alpha bravo charlie delta part{i} " * 300,
                tool_call_id=f"call_{i}",
            )
        )
    return messages


def _small_budget_manager() -> ContextWindowManager:
    return ContextWindowManager(
        ContextBudget(
            context_window=30_000,
            max_output_tokens=2_000,
            compression_threshold=0.8,
            compression_target=0.55,
            reserve_tokens=1_024,
        ),
        max_history_messages=100,
        min_recent_messages=6,
        summary_max_chars=6_000,
    )


def test_compacted_history_never_violates_role_alternation() -> None:
    messages = _react_loop_history()

    result = _small_budget_manager().prepare(
        messages, system_prompt="system prompt", tool_definitions=[{"tool": "def"}] * 27
    )

    assert result.compressed
    roles = [message.role for message in result.messages]
    assert roles[0] == "user"
    assert "conversation-summary" in str(result.messages[0].content)
    for previous, current in zip(roles, roles[1:], strict=False):
        assert not (previous == "assistant" and current == "assistant"), roles


def test_force_trim_also_opens_with_a_user_message() -> None:
    messages = _react_loop_history()
    messages.append(Message(role="user", content="now answer the question"))

    trimmed = _small_budget_manager().force_trim(messages)

    assert trimmed is not None
    assert trimmed[0].role == "user"
    assert "conversation-summary" in str(trimmed[0].content)
    assert trimmed[-1].content == "now answer the question"


def test_force_trim_keeps_a_trailing_tool_turn_instead_of_giving_up() -> None:
    # A react-loop request ends with tool results; trimming used to return
    # None here (the results were stripped as orphans), which disabled
    # overflow recovery on exactly the histories that need it most.
    messages = _react_loop_history()

    trimmed = _small_budget_manager().force_trim(messages)

    assert trimmed is not None
    assert trimmed[0].role == "user"
    roles = [message.role for message in trimmed]
    assert roles[-2:] == ["assistant", "tool"]
    assert trimmed[-1].tool_call_id == "call_6"
    for previous, current in zip(roles, roles[1:], strict=False):
        assert not (previous == "assistant" and current == "assistant"), roles
