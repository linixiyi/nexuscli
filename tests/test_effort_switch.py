"""Tests for `/effort minimal|low|medium|high`: session-level reasoning effort.

The REPL command sets ``reasoning_effort`` live on the current client instance
(no /model re-creation involved). ``OpenAICompatibleClient._build_payload``
attaches the OpenAI-style ``reasoning_effort`` field only for whitelisted
providers ({openai, openai-compatible, compatible}); anything else — and an
unset effort — never sees the field (absent, not blank). A client without the
attribute (AnthropicClient is a slots dataclass) gets a polite refusal from
the command via the hasattr capability probe.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.repl import PermissionModeController, _handle_slash
from nexuscli.llm.openai_compatible import OpenAICompatibleClient


def _client(provider_name: str, effort: str | None = None) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name=provider_name,
        model="test-model",
        api_key="key",
        base_url="https://example.com/v1",
        reasoning_effort=effort,
    )


def _payload(client: OpenAICompatibleClient) -> dict:
    return client._build_payload([], [], system_prompt="s")


def _test_config(tmp_path: Path) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "key"
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    config.memory.long_term_db_path = str(tmp_path / "memory.db")
    return config


def _console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _run_slash(raw: str, console: Console, tmp_path: Path, config, agent) -> bool:
    return asyncio.run(
        _handle_slash(
            raw,
            console,
            str(tmp_path),
            config,
            agent,
            None,
            PermissionModeController(config),
            None,
            None,
            custom_commands=None,
        )
    )


@pytest.mark.parametrize("provider_name", ["openai", "openai-compatible", "compatible"])
def test_build_payload_attaches_effort_for_whitelisted_providers(provider_name: str) -> None:
    client = _client(provider_name, effort="high")

    payload = _payload(client)

    assert payload["reasoning_effort"] == "high"


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high"])
def test_build_payload_attaches_all_four_levels(level: str) -> None:
    client = _client("openai-compatible", effort=level)

    payload = _payload(client)

    assert payload["reasoning_effort"] == level


def test_build_payload_omits_effort_when_unset() -> None:
    client = _client("openai-compatible")

    assert client.reasoning_effort is None

    payload = _payload(client)

    assert "reasoning_effort" not in payload


@pytest.mark.parametrize("provider_name", ["deepseek", "glm", "kimi", "anthropic"])
def test_build_payload_omits_effort_for_unsupported_providers(provider_name: str) -> None:
    client = _client(provider_name, effort="high")

    payload = _payload(client)

    assert "reasoning_effort" not in payload


def test_effort_command_set_show_invalid_and_unsupported(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    console = _console()
    client = _client("openai-compatible")
    agent = SimpleNamespace(llm_client=client)

    _run_slash("/effort high", console, tmp_path, config, agent)

    assert client.reasoning_effort == "high"
    assert "Reasoning effort set" in console.file.getvalue()

    marker = len(console.file.getvalue())
    _run_slash("/effort", console, tmp_path, config, agent)

    assert "Reasoning effort: high" in console.file.getvalue()[marker:]

    marker = len(console.file.getvalue())
    _run_slash("/effort bogus", console, tmp_path, config, agent)

    assert "Usage:" in console.file.getvalue()[marker:]
    assert client.reasoning_effort == "high"

    unsupported = SimpleNamespace(model_name="m", provider_name="anthropic")
    agent2 = SimpleNamespace(llm_client=unsupported)
    marker = len(console.file.getvalue())
    _run_slash("/effort low", console, tmp_path, config, agent2)

    assert "does not support" in console.file.getvalue()[marker:]
    assert not hasattr(unsupported, "reasoning_effort")

    _run_slash("/effort MINIMAL", console, tmp_path, config, agent)

    assert client.reasoning_effort == "minimal"
