"""Tests for the provider context-overflow auto-compact retry.

Covers:
- ``is_context_overflow_error`` classification (OpenAI / Anthropic phrasing,
  negative cases: transient HTTP statuses and unrelated provider errors);
- react loop: a provider-reported context overflow compacts the history and
  retries the same turn once, then completes normally (no ``error`` event);
- the retry receives the compacted history (first message is the rolling
  summary), not the pre-compaction history;
- a history too short to compact surfaces the provider error unchanged;
- non-overflow errors surface immediately with a single chat call;
- the retry happens exactly once: a second overflow surfaces the error;
- with ``features.context_compression`` disabled the error surfaces directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nexuscli.agent import Agent
from nexuscli.config import NexusCliConfig, load_config
from nexuscli.llm.base import is_context_overflow_error, is_transient_api_error
from nexuscli.tools import ToolRegistry
from nexuscli.types import Message


class _OverflowThenOkClient:
    """Fails the first chat with provider overflow phrasing, then succeeds."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self, error_text: str = "maximum context length exceeded") -> None:
        self._error_text = error_text
        self.calls = 0
        self.call_message_counts: list[int] = []
        self.call_first_contents: list[Any] = []

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        self.call_message_counts.append(len(messages))
        self.call_first_contents.append(messages[0].content if messages else None)
        if self.calls == 1:
            yield {"type": "error", "error": RuntimeError(self._error_text)}
            return
        yield {"type": "text_delta", "text": "recovered"}
        yield {"type": "message_end", "stop_reason": "end_turn"}


class _AlwaysOverflowClient:
    """Fails every chat with provider overflow phrasing (retry must stop)."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        yield {
            "type": "error",
            "error": RuntimeError("prompt is too long: 900 tokens > 800 maximum"),
        }


class _BrokenClient:
    """Fails every chat with a non-overflow provider error."""

    model_name = "fake-model"
    provider_name = "fake-provider"
    max_context_window = 1000

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, tools, *, system_prompt):  # noqa: ARG002
        self.calls += 1
        yield {"type": "error", "error": RuntimeError("model_not_found: no such model")}


def _agent_config(tmp_path, monkeypatch) -> NexusCliConfig:
    monkeypatch.setenv("NEXUSCLI_API_KEY", "test-key")
    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("nexuscli.config._home", lambda: home)
    config = load_config(project_root=tmp_path)
    assert config.features.context_compression  # required by the retry path
    return config


def _seeded_agent(config: NexusCliConfig, client: Any) -> Agent:
    agent = Agent(
        llm_client=client,
        tool_registry=ToolRegistry(),
        config=config,
        cwd=str(Path.cwd()),
    )
    # A short-but-multi-turn history: enough for the manual compressor to
    # summarize the older side (the react turn adds one user message on top).
    agent.history = [
        Message(role="user", content=f"turn {i} of a long working session") for i in range(4)
    ]
    return agent


async def _collect(agent: Agent, message: str) -> list[dict[str, Any]]:
    return [event async for event in agent.run(message)]


# ----------------------------------------------------------------------
# Classification (llm/base.py)
# ----------------------------------------------------------------------


def test_is_context_overflow_error_matches_provider_phrasing():
    assert is_context_overflow_error("maximum context length is 4097 tokens")
    assert is_context_overflow_error("context_length_exceeded")
    assert is_context_overflow_error("prompt is too long: 900 tokens > 800 maximum")
    assert is_context_overflow_error("too many input tokens for this model")
    assert is_context_overflow_error(
        RuntimeError(
            "deepseek API returned HTTP 400. Response body: "
            "context_length_exceeded: This model's maximum context length is 4096 tokens"
        )
    )


def test_is_context_overflow_error_rejects_unrelated_errors():
    assert not is_context_overflow_error("HTTP 429 rate limited")
    assert not is_context_overflow_error("model_not_found: no such model")
    assert not is_context_overflow_error("connection reset by peer")
    # Transient classification is untouched by the new classifier.
    assert is_transient_api_error("HTTP 429")
    assert not is_transient_api_error("maximum context length exceeded")


# ----------------------------------------------------------------------
# React-loop behaviour
# ----------------------------------------------------------------------


def test_overflow_error_compacts_and_retries_once(tmp_path, monkeypatch):
    config = _agent_config(tmp_path, monkeypatch)
    client = _OverflowThenOkClient()
    agent = _seeded_agent(config, client)

    events = asyncio.run(_collect(agent, "continue the session"))

    assert client.calls == 2
    types = [event["type"] for event in events]
    assert "error" not in types
    assert "done" in types
    # The notice is streamed so the user sees why the turn stalled.
    notices = [
        event["text"]
        for event in events
        if event["type"] == "text_delta" and "compacting" in event["text"]
    ]
    assert len(notices) == 1
    assert "retrying once" in notices[0]
    # The retried call starts with the rolling summary, not the raw history.
    assert "conversation-summary" in str(client.call_first_contents[1])
    assert client.call_message_counts[1] < client.call_message_counts[0]
    # The turn completed with the post-retry reply.
    assert agent.history[-1].content == "recovered"


def test_overflow_error_with_uncompactable_history_surfaces(tmp_path, monkeypatch):
    config = _agent_config(tmp_path, monkeypatch)
    client = _OverflowThenOkClient()
    agent = _seeded_agent(config, client)
    agent.history = []  # only the new user message -> nothing to compact

    events = asyncio.run(_collect(agent, "hello"))

    assert client.calls == 1
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert "maximum context length" in str(errors[0]["error"])
    assert [event["type"] for event in events if event["type"] == "done"] == []


def test_non_overflow_error_surfaces_immediately(tmp_path, monkeypatch):
    config = _agent_config(tmp_path, monkeypatch)
    client = _BrokenClient()
    agent = _seeded_agent(config, client)

    events = asyncio.run(_collect(agent, "continue"))

    assert client.calls == 1
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert "model_not_found" in str(errors[0]["error"])


def test_overflow_retry_happens_only_once(tmp_path, monkeypatch):
    config = _agent_config(tmp_path, monkeypatch)
    client = _AlwaysOverflowClient()
    agent = _seeded_agent(config, client)

    events = asyncio.run(_collect(agent, "continue"))

    assert client.calls == 2
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert "prompt is too long" in str(errors[0]["error"])


def test_overflow_retry_disabled_without_compression(tmp_path, monkeypatch):
    config = _agent_config(tmp_path, monkeypatch)
    config.features.context_compression = False
    client = _OverflowThenOkClient()
    agent = _seeded_agent(config, client)

    events = asyncio.run(_collect(agent, "continue"))

    assert client.calls == 1
    errors = [event for event in events if event["type"] == "error"]
    assert len(errors) == 1
    assert "maximum context length" in str(errors[0]["error"])
