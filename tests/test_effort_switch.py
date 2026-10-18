"""Tests for pi-style `/effort`: seven thinking levels, cycle on empty arg.

The REPL command sets ``reasoning_effort`` live on the current client instance
(no /model re-creation involved). ``OpenAICompatibleClient._build_payload``
attaches the OpenAI-style ``reasoning_effort`` field only for whitelisted
providers ({openai, openai-compatible, compatible}); anything else — and an
unset or ``off`` effort — never sees the field (absent, not blank). A client
without the attribute (AnthropicClient is a slots dataclass) gets a polite
refusal from the command via the hasattr capability probe.

Levels follow pi: ``off minimal low medium high xhigh max``. ``off`` maps to
``None`` (no field on the wire); empty arg cycles one step forward from the
current level, and an unset level starts the cycle at ``low``.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from nexuscli.config import NexusCliConfig
from nexuscli.entrypoints.commands.effort import EFFORT_LEVELS, next_effort_level
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


def test_effort_levels_cover_pi_seven():
    assert EFFORT_LEVELS == ("off", "minimal", "low", "medium", "high", "xhigh", "max")


def test_cycle_from_unset_starts_at_low():
    assert next_effort_level(None) == "low"
    assert next_effort_level("") == "low"


def test_cycle_advances_through_all_levels_and_wraps():
    order = []
    level = "off"
    for _ in range(len(EFFORT_LEVELS) + 1):
        level = next_effort_level(level)
        order.append(level)
    assert order == ["minimal", "low", "medium", "high", "xhigh", "max", "off", "minimal"]


@pytest.mark.parametrize("provider_name", ["openai", "openai-compatible", "compatible"])
def test_build_payload_attaches_effort_for_whitelisted_providers(provider_name: str) -> None:
    client = _client(provider_name, effort="high")

    payload = _payload(client)

    assert payload["reasoning_effort"] == "high"


@pytest.mark.parametrize("level", ["minimal", "low", "medium", "high", "xhigh", "max"])
def test_build_payload_attaches_all_six_on_levels(level: str) -> None:
    client = _client("openai-compatible", effort=level)

    payload = _payload(client)

    assert payload["reasoning_effort"] == level


def test_build_payload_omits_effort_when_unset_or_off() -> None:
    client = _client("openai-compatible")

    assert client.reasoning_effort is None
    assert "reasoning_effort" not in _payload(client)

    client.reasoning_effort = None  # /effort off lands here
    assert "reasoning_effort" not in _payload(client)


@pytest.mark.parametrize("provider_name", ["deepseek", "glm", "kimi", "anthropic"])
def test_build_payload_omits_effort_for_unsupported_providers(provider_name: str) -> None:
    client = _client(provider_name, effort="high")

    payload = _payload(client)

    assert "reasoning_effort" not in payload


def test_effort_command_set_all_levels_cycle_off_and_invalid(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    console = _console()
    client = _client("openai-compatible")
    agent = SimpleNamespace(llm_client=client)

    for level in ("minimal", "low", "medium", "high", "xhigh", "max"):
        marker = len(console.file.getvalue())
        _run_slash(f"/effort {level}", console, tmp_path, config, agent)
        assert client.reasoning_effort == level
        assert f"set to {level}" in console.file.getvalue()[marker:]

    # /effort off sends no field at all.
    _run_slash("/effort off", console, tmp_path, config, agent)
    assert client.reasoning_effort is None
    assert "set to off" in console.file.getvalue()

    # Empty arg cycles: unset -> low -> medium.
    client.reasoning_effort = None
    _run_slash("/effort", console, tmp_path, config, agent)
    assert client.reasoning_effort == "low"
    assert "cycled to low" in console.file.getvalue()
    _run_slash("/effort", console, tmp_path, config, agent)
    assert client.reasoning_effort == "medium"
    assert "cycled to medium" in console.file.getvalue()

    # /effort with no arg also shows the level line.
    marker = len(console.file.getvalue())
    _run_slash("/effort high", console, tmp_path, config, agent)
    marker = len(console.file.getvalue())
    _run_slash("/effort clear", console, tmp_path, config, agent)
    assert client.reasoning_effort is None

    # Invalid level keeps the current one and prints usage.
    _run_slash("/effort bogus", console, tmp_path, config, agent)
    assert "Usage:" in console.file.getvalue()
    assert client.reasoning_effort is None


def test_effort_command_refuses_unsupported_client(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    console = _console()
    unsupported = SimpleNamespace(model_name="m", provider_name="anthropic")
    agent = SimpleNamespace(llm_client=unsupported)

    _run_slash("/effort low", console, tmp_path, config, agent)

    assert "does not support" in console.file.getvalue()
    assert not hasattr(unsupported, "reasoning_effort")

    marker = len(console.file.getvalue())
    _run_slash("/effort", console, tmp_path, config, agent)

    assert "does not support" in console.file.getvalue()[marker:]


def test_effort_command_says_when_the_provider_drops_the_field(tmp_path: Path) -> None:
    # The client object carries reasoning_effort for any chat-completions
    # backend, but _build_payload only forwards it for the whitelisted ones.
    # Reporting a bare "set to high" on deepseek would claim an effect the
    # request never has, so the command must name the drop.
    config = _test_config(tmp_path)
    console = _console()
    client = _client("deepseek")
    agent = SimpleNamespace(llm_client=client)

    _run_slash("/effort high", console, tmp_path, config, agent)

    out = console.file.getvalue()
    assert client.reasoning_effort == "high"
    assert "set to high" in out
    assert "Not sent:" in out
    assert "does not forward" in out
    assert "reasoning_effort" not in _payload(client)


def test_effort_command_stays_quiet_when_the_field_reaches_the_wire(tmp_path: Path) -> None:
    config = _test_config(tmp_path)
    console = _console()
    client = _client("openai-compatible")
    agent = SimpleNamespace(llm_client=client)

    _run_slash("/effort high", console, tmp_path, config, agent)

    out = console.file.getvalue()
    assert "set to high" in out
    assert "Not sent:" not in out
    assert _payload(client)["reasoning_effort"] == "high"
