"""Tests for the opt-in LLM request/response debug dump (llm.debug_dump).

Every test drives :meth:`OpenAICompatibleClient.chat` against a stubbed httpx
transport — no real network. The dump is off by default, so the suite pins:
the zero-write default, the JSONL structure (one request line + one response
line per turn), append behaviour across turns, header redaction (the
Authorization header must never reach the file in plaintext), and the
silent-failure guarantee (a broken dump path must not interrupt the chat).
Home is redirected via HOME/USERPROFILE — win32 Path.home() prefers
USERPROFILE, so both variables are set (same pattern as tests/test_hooks.py).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from nexuscli.config import LlmConfig, load_config
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.types import Message


def _placeholder_api_key() -> str:
    """Placeholder credential for tests; env-overridable, never a real secret."""
    return os.environ.get("NEXUSCLI_UNITTEST_LLM_CREDENTIAL") or "unit-test-key-123"


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point Path.home() at a temp directory for the dump path.

    win32 Path.home() prefers USERPROFILE over HOME, so both variables are
    redirected (same pattern as tests/test_hooks.py).
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def _dump_path(home: Path) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    return home / ".nexuscli" / "debug" / "llm" / f"{date}.jsonl"


def _read_dump_records(home: Path) -> list[dict[str, Any]]:
    raw = _dump_path(home).read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


class _FakeResponse:
    """Minimal stand-in for httpx.Response: SSE frames via aiter_text()."""

    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        yield 'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        yield 'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
        yield (
            'data: {"choices": [{"delta": {}, "finish_reason": "stop"}],'
            ' "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}\n\n'
        )
        yield "data: [DONE]\n\n"


class _FakeStream:
    """Async context manager returning the fake response, as client.stream does."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _stub_httpx_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse()

    def fake_stream(self, *args, **kwargs):
        return _FakeStream(response)

    monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)


def _client(**overrides: Any) -> OpenAICompatibleClient:
    kwargs: dict[str, Any] = {
        "provider_name": "deepseek",
        "model": "deepseek-v4-flash",
        "api_key": _placeholder_api_key(),
        "base_url": "https://api.deepseek.com/v1",
    }
    kwargs.update(overrides)
    return OpenAICompatibleClient(**kwargs)


async def _collect_chat_events(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    messages = [Message(role="user", content="hello")]
    tools: list[dict[str, Any]] = []
    return [event async for event in client.chat(messages, tools, system_prompt="system")]


def test_dump_disabled_by_default_writes_nothing(tmp_path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    _stub_httpx_stream(monkeypatch)
    client = _client()

    events = asyncio.run(_collect_chat_events(client))

    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "text_delta",
        "message_end",
        "usage",
    ]
    assert not (home / ".nexuscli" / "debug" / "llm").exists()


def test_dump_writes_request_and_response_lines(tmp_path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    _stub_httpx_stream(monkeypatch)
    client = _client(debug_dump=True)

    asyncio.run(_collect_chat_events(client))

    dump_path = _dump_path(home)
    assert dump_path.exists()
    records = _read_dump_records(home)
    assert len(records) == 2

    request_line = records[0]
    assert request_line["direction"] == "request"
    assert request_line["model"] == "deepseek-v4-flash"
    assert request_line["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert {"model", "url", "headers", "payload"} <= request_line.keys()
    assert request_line["payload"]["model"] == "deepseek-v4-flash"

    response_line = records[1]
    assert response_line["direction"] == "response"
    assert response_line["text"] == "Hello world"
    assert response_line["tool_calls"] == []
    assert response_line["usage"]["total_tokens"] == 15


def test_dump_appends_across_calls(tmp_path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    _stub_httpx_stream(monkeypatch)
    client = _client(debug_dump=True)

    asyncio.run(_collect_chat_events(client))
    asyncio.run(_collect_chat_events(client))

    records = _read_dump_records(home)
    assert len(records) == 4
    assert [record["direction"] for record in records] == [
        "request",
        "response",
        "request",
        "response",
    ]


def test_dump_redacts_authorization(tmp_path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    _stub_httpx_stream(monkeypatch)
    fake_key = _placeholder_api_key()
    client = _client(debug_dump=True)

    asyncio.run(_collect_chat_events(client))

    records = _read_dump_records(home)
    request_line = records[0]
    assert request_line["headers"]["authorization"] == "<redacted>"

    raw = _dump_path(home).read_text(encoding="utf-8")
    assert "Bearer" not in raw
    assert fake_key not in raw


def test_dump_failure_is_silent(tmp_path, monkeypatch) -> None:
    home = _isolate_home(monkeypatch, tmp_path)
    # Make the dump root unwritable: <home>/.nexuscli/debug exists as a plain
    # file, so the dump's mkdir(parents=True, exist_ok=True) raises an OSError
    # subclass (FileExistsError on win32) that _append_dump must swallow.
    debug_root = home / ".nexuscli" / "debug"
    debug_root.parent.mkdir(parents=True)
    debug_root.write_text("not a directory", encoding="utf-8")
    _stub_httpx_stream(monkeypatch)
    client = _client(debug_dump=True)

    events = asyncio.run(_collect_chat_events(client))

    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "text_delta",
        "message_end",
        "usage",
    ]
    assert not (home / ".nexuscli" / "debug" / "llm").exists()


def test_config_debug_dump_default_and_parse() -> None:
    assert LlmConfig().debug_dump is False

    overrides = {"llm": {"debug_dump": True}}
    config = load_config(overrides=overrides, env={})

    assert config.llm.debug_dump is True
