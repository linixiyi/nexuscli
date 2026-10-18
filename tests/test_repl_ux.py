"""REPL surface fixes from the 2026-10-18 audit: placeholder syntax, /clear
reset, long-JSON summaries, /help guidance, unknown /skill names, compact
approval blocks.

Each case pins a defect that a real session hit; none of them are cosmetic.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.approval import _summarize_input
from nexuscli.entrypoints.commands.skill import _skill_command
from nexuscli.entrypoints.repl import ReplSessionState, _handle_slash
from nexuscli.policy.audit_log import AuditLog
from nexuscli.render import RichRenderer
from nexuscli.session import SessionStore


def _has_box_drawing(text: str) -> bool:
    """True when any Rich box/border glyph survives in the plain output."""
    return any(ch in text for ch in "─│┌┐└┘├┤┬┴┼═║╭╮╯╰")


def _config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


def _console() -> Console:
    return Console(file=io.StringIO(), width=120)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        llm_client=SimpleNamespace(
            model_name="m", provider_name="openai-compatible", max_context_window=1000
        ),
        history=[],
        clear_history=lambda: None,
    )


def test_placeholder_advertises_the_real_image_syntax() -> None:
    # The prompt used to say '@path/to/file', which is not a syntax the CLI
    # understands; the model received the literal string instead of a file.
    source = Path("src/nexuscli/entrypoints/repl.py").read_text(encoding="utf-8")
    assert "@image:path/to/file" in source
    assert "@path/to/file" not in source.replace("@image:path/to/file", "")


def test_clear_resets_the_context_bar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    console = _console()
    renderer = RichRenderer(console=console)
    renderer.handle({"type": "usage", "usage": {"input_tokens": 500, "output_tokens": 100}})
    renderer.handle({"type": "done", "total_turns": 1, "total_tokens": 600})
    assert renderer.toolbar_status()["has_usage"] is True

    store = SessionStore()
    state = ReplSessionState(store=store, writer=store.new_writer(cwd=str(tmp_path)))

    asyncio.run(
        _handle_slash(
            "/clear",
            console,
            str(tmp_path),
            config,
            _agent(),
            None,
            None,
            renderer,
            state,
        )
    )

    status = renderer.toolbar_status()
    assert status["has_usage"] is False
    assert status["context_ratio"] == 0


def test_config_summary_is_short_and_hides_the_key(tmp_path: Path) -> None:
    config = _config(tmp_path)
    console = _console()

    asyncio.run(
        _handle_slash("/config", console, str(tmp_path), config, _agent(), None, None, None, None)
    )

    out = console.file.getvalue()
    assert "provider" in out and "model" in out
    assert config.llm.api_key not in out
    assert len(out.splitlines()) < 20


def test_audit_prints_a_table_not_json(tmp_path: Path) -> None:
    config = _config(tmp_path)
    console = _console()

    asyncio.run(
        _handle_slash("/audit", console, str(tmp_path), config, _agent(), None, None, None, None)
    )

    out = console.file.getvalue()
    assert "(no audit records)" in out


def test_audit_with_records_is_a_frameless_four_column_summary(tmp_path: Path) -> None:
    """The records path of /audit: heading, four dim headers, no box glyphs."""
    config = _config(tmp_path)
    AuditLog(config.policy.audit_log_path).record(
        tool_name="bash",
        input_data={"command": "ls"},
        outcome="success",
        approver="auto",
        cwd=str(tmp_path),
    )
    console = _console()

    asyncio.run(
        _handle_slash("/audit", console, str(tmp_path), config, _agent(), None, None, None, None)
    )

    out = console.file.getvalue()
    assert "NexusCLI Audit" in out
    assert "— last 1" in out
    for header in ("time", "tool", "outcome", "approver"):
        assert header in out, f"audit summary lost the {header!r} column"
    assert "bash" in out and "success" in out and "auto" in out
    # Frameless pi-style listing: no Rich box/border characters.
    assert not _has_box_drawing(out)


def test_help_mentions_the_real_key_bindings(tmp_path: Path) -> None:
    config = _config(tmp_path)
    console = _console()

    asyncio.run(
        _handle_slash("/help", console, str(tmp_path), config, _agent(), None, None, None, None)
    )

    out = console.file.getvalue()
    assert "Shift+Tab" in out
    assert "Ctrl+C" in out
    assert "Ctrl+D" in out


def test_unknown_skill_name_is_named_instead_of_listing_everything(tmp_path: Path) -> None:
    console = _console()

    _skill_command("definitely-not-a-skill", console, str(tmp_path), _agent())

    out = console.file.getvalue()
    assert "Unknown skill" in out
    assert "definitely-not-a-skill" in out


def test_approval_input_is_folded_to_one_short_line() -> None:
    # A write_file payload used to print the whole file body above the
    # decision, pushing the prompt off screen.
    payload = {"path": "notes.txt", "content": "x" * 5_000}

    summary = _summarize_input(payload)

    assert "\n" not in summary
    assert "notes.txt" in summary
    assert len(summary) < 300
    assert "chars)" in summary


def test_approval_summary_keeps_short_values_intact() -> None:
    assert _summarize_input({"command": "git status"}) == "command=git status"
