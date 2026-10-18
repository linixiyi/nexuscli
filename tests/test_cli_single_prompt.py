from __future__ import annotations

import asyncio
import json
import os
import sys

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import QueryEngine
from nexuscli.config import NexusCliConfig


def _placeholder_llm_key() -> str:
    """Placeholder credential for tests; env-overridable, never a real secret."""
    return os.environ.get("NEXUSCLI_UNITTEST_LLM_CREDENTIAL") or "unit-test-value"


# One controlled event sequence shared by both branches under test: two
# text_delta chunks followed by the terminal done event.
_DELTA_EVENTS = [
    {"type": "text_delta", "text": "Hello"},
    {"type": "text_delta", "text": ", world"},
    {
        "type": "done",
        "total_turns": 2,
        "total_tokens": 42,
        "usage": {"input_tokens": 30, "output_tokens": 12, "total_tokens": 42},
        "cost": {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
    },
]


class _WriteCapture:
    """stdout replacement recording each write() call separately."""

    # click's echo treats a stdout without ``encoding`` as binary; declare
    # this a text stream so typer.echo lands here as str.
    encoding = "utf-8"

    def __init__(self, timeline: list | None = None):
        self.writes: list[str] = []
        self._timeline = timeline

    def write(self, s: str) -> int:
        # Reject bytes so click's binary-writer probe (it trial-writes b"")
        # classifies this capture as a text stream, like real stdout.
        if isinstance(s, bytes):
            raise TypeError("write() argument must be str, not bytes")
        self.writes.append(s)
        if self._timeline is not None:
            self._timeline.append(("write", s))
        return len(s)

    def flush(self) -> None:
        pass


class StubQueryEngine:
    """Scripted QueryEngine double reusing the real event aggregation helper."""

    _complete_from_events = QueryEngine._complete_from_events

    def __init__(self, events, timeline=None, **_kwargs):
        self._events = events
        self._timeline = timeline
        self.ask_calls: list[tuple[str, list]] = []

    async def ask(self, message, history=None):
        self.ask_calls.append((message, list(history or [])))
        for event in self._events:
            if self._timeline is not None:
                self._timeline.append(("yield", event.get("type")))
            yield event


def _install_stub_engine(monkeypatch, events, timeline=None) -> StubQueryEngine:
    async def fake_build_registry(config, cwd):
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())
    stub = StubQueryEngine(events, timeline=timeline)
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: stub)
    return stub


def _test_config() -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = _placeholder_llm_key()
    return config


def test_react_mode_streams_each_delta_as_it_arrives(tmp_path, monkeypatch):
    timeline: list = []
    capture = _WriteCapture(timeline=timeline)
    stub = _install_stub_engine(monkeypatch, _DELTA_EVENTS, timeline=timeline)
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(
        cli._run_prompt(
            "hello",
            str(tmp_path),
            _test_config(),
            mode="react",
            worker_mode="react",
            json_output=False,
        )
    )

    # Each delta reaches stdout as it is produced — its write lands right
    # after that event's yield and before the next event, instead of one
    # buffered print after the run. The last write is only the trailing
    # newline that the old typer.echo(result.text) used to add.
    assert timeline == [
        ("yield", "text_delta"),
        ("write", "Hello"),
        ("yield", "text_delta"),
        ("write", ", world"),
        ("yield", "done"),
        ("write", "\n"),
    ]
    assert "".join(capture.writes) == "Hello, world\n"
    assert stub.ask_calls == [("hello", [])]


def test_json_mode_emits_single_object_and_no_stream_output(tmp_path, monkeypatch):
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, _DELTA_EVENTS)
    monkeypatch.setattr(sys, "stdout", capture)

    asyncio.run(
        cli._run_prompt(
            "hello",
            str(tmp_path),
            _test_config(),
            mode="react",
            worker_mode="react",
            json_output=True,
        )
    )

    # The entire stdout payload is exactly one JSON object plus its trailing
    # newline — no delta text leaked around it while the stream was consumed.
    raw = "".join(capture.writes)
    payload = json.loads(raw)
    assert raw == json.dumps(payload, ensure_ascii=False) + "\n"

    # Same JSON contract as the buffered implementation, including the
    # aggregated text/turns/usage/cost and the dual-scope session_usage.
    assert payload["text"] == "Hello, world"
    assert payload["mode"] == "react"
    assert payload["worker_mode"] is None
    assert payload["turns"] == 2
    assert payload["total_tokens"] == 42
    assert payload["usage"] == {
        "input_tokens": 30,
        "output_tokens": 12,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 42,
    }
    assert payload["cost"] == {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}}
    this_run = {
        "turns": 1,
        "input_tokens": 30,
        "output_tokens": 12,
        "total_tokens": 42,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "cost_usd": 0.01,
        "cost_cny": 0.07,
    }
    assert payload["session_usage"] == {
        "historical_total": this_run,
        "current_process": this_run,
        "sources": {
            "historical_total": "resumed session usage baseline + this run (not persisted)",
            "current_process": "this run only",
        },
    }


def test_yes_flag_installs_an_approval_callback(tmp_path, monkeypatch):
    # -p used to build QueryEngine with no approval_callback, so every tool
    # that needs approval was refused (executor fails closed) and scripted
    # work could not write files or run commands at all. --yes is the
    # explicit opt-in that says nobody will be asked.
    captured = {}

    async def fake_build_registry(config, cwd):
        return None, None

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", lambda llm, telemetry_enabled: object())

    def fake_engine(**kwargs):
        captured["approval_callback"] = kwargs.get("approval_callback")
        return StubQueryEngine(_DELTA_EVENTS)

    monkeypatch.setattr(cli, "QueryEngine", fake_engine)
    monkeypatch.setattr(sys, "stdout", _WriteCapture())

    asyncio.run(cli._run_prompt("hi", str(tmp_path), _test_config(), approve_all=True))
    assert captured["approval_callback"] is cli._auto_approver

    asyncio.run(cli._run_prompt("hi", str(tmp_path), _test_config(), approve_all=False))
    assert captured["approval_callback"] is None


def test_auto_approver_answers_approve():
    assert cli._auto_approver({"tool_name": "bash"}) == "approve"


def test_yes_and_no_approve_are_mutually_exclusive(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    result = CliRunner().invoke(cli.app, ["-p", "hi", "--yes", "--no-approve"])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
