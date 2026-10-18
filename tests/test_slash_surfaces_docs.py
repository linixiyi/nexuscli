"""Slash-surface output contracts — matrix rows /context /tools /policy /usage
(were 🟠: real-run only, no automated assertion).

Each test direct-drives the same handler the REPL dispatches to
(``repl._handle_slash``, following the pattern of tests/test_repl_ux.py) and
captures through a recorded Console, asserting the output a user must be able
to read. The registry/agent are stubs, so nothing here can reach a gateway.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import _handle_slash
from nexuscli.types import Usage

TOOL_NAMES = ["bash", "read_file", "write_file"]


def _config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.model = "unit-model"
    config.llm.provider = "unit-provider"
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    return config


def _console() -> Console:
    return Console(file=io.StringIO(), width=120, record=True)


def _agent(**extra: object) -> SimpleNamespace:
    fields: dict = {
        "llm_client": SimpleNamespace(
            model_name="unit-model",
            provider_name="unit-provider",
            max_context_window=128_000,
        ),
        "history": [],
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _registry() -> SimpleNamespace:
    return SimpleNamespace(list_names=lambda: list(TOOL_NAMES))


CWD = "unit-test-cwd"


def _run_slash(command: str, console: Console, tmp_path: Path, config, agent) -> None:
    asyncio.run(
        _handle_slash(
            command,
            console,
            CWD,
            config,
            agent,
            _registry(),
            None,
            None,
            None,
        )
    )


def test_context_table_reports_window_and_workspace(tmp_path: Path) -> None:
    console = _console()
    _run_slash("/context", console, tmp_path, _config(tmp_path), _agent())

    out = console.file.getvalue()
    assert "NexusCLI Context" in out
    for label in ("cwd", "model", "context window", "render", "memory", "tools"):
        assert label in out, f"context table lost the {label!r} row"
    # The values a user checks before a run: workspace, model pairing, and the
    # window the context bar is measured against.
    assert CWD in out
    assert "unit-model (unit-provider)" in out
    assert "128000" in out
    assert "0 recent entries" in out


def test_tools_lists_each_tool_on_its_own_line(tmp_path: Path) -> None:
    console = _console()
    _run_slash("/tools", console, tmp_path, _config(tmp_path), _agent())

    lines = [line.strip() for line in console.file.getvalue().splitlines() if line.strip()]
    assert lines == TOOL_NAMES


def test_policy_summary_pins_the_guard_fields(tmp_path: Path) -> None:
    console = _console()
    _run_slash("/policy", console, tmp_path, _config(tmp_path), _agent())

    out = console.file.getvalue()
    assert "NexusCLI Policy" in out
    for label in (
        "hitl_mode",
        "path_guard",
        "command_guard",
        "env_sanitization",
        "plan_mode",
        "command_blacklist",
    ):
        assert label in out, f"policy table lost the {label!r} row"
    # Default posture must still read as enabled guards with an auto HITL
    # mode; the blacklist is summarized as a count, not dumped.
    assert "auto" in out
    assert "True" in out
    assert "entries" in out
    assert "permissions:" in out
    for scope in ("allow", "ask", "deny"):
        assert scope in out, f"permissions line lost {scope!r}"


def test_usage_renders_the_four_row_ledger(tmp_path: Path) -> None:
    console = _console()
    agent = _agent(
        last_usage=Usage(input_tokens=120, output_tokens=30),
        last_cost={"usd": 0.0123},
    )
    _run_slash("/usage", console, tmp_path, _config(tmp_path), agent)

    out = console.file.getvalue()
    # turn · session · cost · note — the four-row status table.
    assert "turn" in out
    assert "120 in / 30 out" in out
    assert "session" in out
    assert "0 turns · 0 tokens" in out
    assert "cost" in out
    assert "USD 0.012300" in out
    assert "note" in out
    assert "dated defaults" in out
