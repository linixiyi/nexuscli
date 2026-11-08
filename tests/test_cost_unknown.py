"""-p --json 未知模型价格语义（spec 2026-11-07 task_0037）。

模型不在内置价格表且用户未配置 ``llm.prices`` 时：
- ``cost`` 为 ``null``，附 ``cost_note``，不再是空对象/伪造的 0.0；
- ``session_usage`` 两个口径的 ``cost_usd``/``cost_cny`` 为 ``null``；
- 已知模型（内置价格表命中或用户已配置价格）输出契约不变。

fake 客户端用真实的 ``resolve_price_profile`` 按 config 解析 ``price_profile``，
与 factory 的 deepseek 分支（include_builtin=True）同口径；事件里的 cost 由
真实 agent 在价格已知时才会产生，未知时 done 事件本就无 cost 键。
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import nexuscli.entrypoints.cli as cli
from nexuscli.agent import QueryEngine
from nexuscli.config import NexusCliConfig
from nexuscli.llm.pricing import UNKNOWN_MODEL_PRICE_NOTE, resolve_price_profile


class _WriteCapture:
    """stdout replacement recording each write() call separately."""

    # click's echo treats a stdout without ``encoding`` as binary; declare
    # this a text stream so typer.echo lands here as str.
    encoding = "utf-8"

    def __init__(self):
        self.writes: list[str] = []

    def write(self, s: str) -> int:
        if isinstance(s, bytes):
            raise TypeError("write() argument must be str, not bytes")
        self.writes.append(s)
        return len(s)

    def flush(self) -> None:
        pass


class StubQueryEngine:
    """Scripted QueryEngine double reusing the real event aggregation helper."""

    _complete_from_events = QueryEngine._complete_from_events

    def __init__(self, events, **_kwargs):
        self._events = events

    async def ask(self, message, history=None):
        for event in self._events:
            yield event


def _install_stub_engine(monkeypatch, events, llm_overrides: dict) -> None:
    async def fake_build_registry(config, cwd):
        return None, None

    def fake_create_llm_client(llm, telemetry_enabled):
        # Mirror the factory's deepseek branch: built-in table consulted,
        # llm.prices overrides merged. The client carries the resolved
        # profile, exactly what cli._cost_unknown inspects.
        return SimpleNamespace(
            model_name=llm.model,
            provider_name=llm.provider,
            price_profile=resolve_price_profile(
                llm.model,
                context_window=64_000,
                overrides=llm.prices,
            ),
        )

    monkeypatch.setattr(cli, "build_tool_registry", fake_build_registry)
    monkeypatch.setattr(cli, "create_llm_client", fake_create_llm_client)
    monkeypatch.setattr(cli, "QueryEngine", lambda **kwargs: StubQueryEngine(events))


def _test_config(**llm_overrides) -> NexusCliConfig:
    config = NexusCliConfig()
    config.llm.api_key = "unit-test-value"
    for key, value in llm_overrides.items():
        setattr(config.llm, key, value)
    return config


def _run_json_prompt(tmp_path, monkeypatch, events, **llm_overrides) -> dict:
    capture = _WriteCapture()
    _install_stub_engine(monkeypatch, events, llm_overrides)
    monkeypatch.setattr(sys, "stdout", capture)
    asyncio.run(
        cli._run_prompt(
            "hi",
            str(tmp_path),
            _test_config(**llm_overrides),
            mode="react",
            worker_mode="react",
            json_output=True,
        )
    )
    return json.loads("".join(capture.writes))


# The cost dict a price-resolving agent attaches to the done event.
_COST_EVENT = {
    "type": "done",
    "total_turns": 1,
    "total_tokens": 10,
    "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
    "cost": {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}},
}

# Unknown price: the agent's _calculate_costs swallows the profile-miss
# ValueError, so the done event carries usage but no cost at all.
_NO_COST_EVENT = {
    "type": "done",
    "total_turns": 1,
    "total_tokens": 10,
    "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10},
}


def test_unknown_model_price_reports_null_cost_with_note(tmp_path, monkeypatch):
    payload = _run_json_prompt(tmp_path, monkeypatch, [_NO_COST_EVENT], model="local-qwen")

    assert payload["cost"] is None
    assert payload["cost_note"] == UNKNOWN_MODEL_PRICE_NOTE
    for scope in ("historical_total", "current_process"):
        session_usage = payload["session_usage"][scope]
        assert session_usage["cost_usd"] is None
        assert session_usage["cost_cny"] is None
        # Token accounting stays intact — only the price is unknown.
        assert session_usage["total_tokens"] == 10
        assert session_usage["turns"] == 1


def test_known_builtin_model_keeps_cost_contract(tmp_path, monkeypatch):
    payload = _run_json_prompt(tmp_path, monkeypatch, [_COST_EVENT])  # default deepseek model

    assert payload["cost"] == {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}}
    assert "cost_note" not in payload
    this_run = payload["session_usage"]["current_process"]
    assert this_run["cost_usd"] == 0.01
    assert this_run["cost_cny"] == 0.07


def test_known_model_without_produced_cost_stays_empty_object(tmp_path, monkeypatch):
    # Built-in profile present but no cost produced (e.g. calculator absent):
    # the previous contract — empty dict, zeros in session_usage — is kept.
    payload = _run_json_prompt(tmp_path, monkeypatch, [_NO_COST_EVENT])

    assert payload["cost"] == {}
    assert "cost_note" not in payload
    this_run = payload["session_usage"]["current_process"]
    assert this_run["cost_usd"] == 0.0
    assert this_run["cost_cny"] == 0.0


def test_user_configured_prices_count_as_known(tmp_path, monkeypatch):
    payload = _run_json_prompt(
        tmp_path,
        monkeypatch,
        [_COST_EVENT],
        model="qwen2.5-coder",
        prices={"usd": {"input_cache_hit": 0.0, "input_cache_miss": 0.05, "output": 0.1}},
    )

    assert payload["cost"] == {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}}
    assert "cost_note" not in payload
    this_run = payload["session_usage"]["current_process"]
    assert this_run["cost_usd"] == 0.01
    assert this_run["cost_cny"] == 0.07


def test_produced_cost_beats_missing_profile(tmp_path, monkeypatch):
    # The offline demo provider resolves no profile but its scripted
    # calculate_cost returns honest zeros: pass-through, never "unknown".
    payload = _run_json_prompt(tmp_path, monkeypatch, [_COST_EVENT], model="local-qwen")

    assert payload["cost"] == {"usd": {"total_cost": 0.01}, "cny": {"total_cost": 0.07}}
    assert "cost_note" not in payload
    this_run = payload["session_usage"]["current_process"]
    assert this_run["cost_usd"] == 0.01
