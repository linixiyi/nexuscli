"""Tests for gateway model discovery, the /model fetch tab, and its wiring.

Covers the package contract: ``fetch_available_models`` payload/HTTP failure
handling, the selector state machine for the fetch tab, and the command-layer
orchestration that persists fetched listings and activates a model. Network
access is mocked; a live-gateway test is skipped unless the endpoint answers.

Credential discipline: no real key literal anywhere — test keys come from a
module constant that is obviously a fixture value (never a usable secret),
or from the environment when a real key is needed.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import httpx
import pytest

from nexuscli.entrypoints.model_selector import ModelSelectorState
from nexuscli.entrypoints.repl import _activate_model
from nexuscli.llm.model_profiles import CustomModelStore, ModelProfile
from nexuscli.llm.openai_compatible import (
    ModelFetchError,
    ModelListingProfile,
    fetch_available_models,
)

BASE_URL = "http://gateway.test/v1"
# Fixture credential: a syntactically-valid-looking but non-usable placeholder
# injected via env/monkeypatch, never a real secret value.
FIXTURE_KEY = os.environ.get("NEXUSCLI_TEST_FIXTURE_KEY", "fixture-key-not-a-real-credential")


def _respond(payload: object, status: int = 200) -> httpx.Response:
    import json

    return httpx.Response(
        status, content=json.dumps(payload).encode(), request=httpx.Request("GET", BASE_URL)
    )


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, payload: object, status: int = 200, raise_exc: Exception | None = None):
        self.payload = payload
        self.status = status
        self.raise_exc = raise_exc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.raise_exc is not None:
            raise self.raise_exc
        return _respond(self.payload, self.status)


def _fetch(monkeypatch, payload: object, status: int = 200, raise_exc: Exception | None = None):
    transport = _FakeTransport(payload, status, raise_exc)
    real_client = httpx.Client

    def _client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=transport, **kwargs)

    monkeypatch.setattr("nexuscli.llm.openai_compatible.httpx.Client", _client)
    return fetch_available_models(BASE_URL, FIXTURE_KEY)


def test_fetch_parses_standard_openai_listing(monkeypatch):
    payload = {
        "object": "list",
        "data": [
            {"id": "cbcn/glm-5.3-flash", "context_length": 1000000},
            {"id": "cbcn/kimi-k2.7", "context_length": 256000},
        ],
    }
    models = _fetch(monkeypatch, payload)
    assert [m.model for m in models] == ["cbcn/glm-5.3-flash", "cbcn/kimi-k2.7"]
    assert models[0].context_window == 1_000_000


def test_fetch_defaults_context_when_missing(monkeypatch):
    models = _fetch(monkeypatch, {"data": [{"id": "plain-model"}]})
    assert models[0].context_window == 128_000


def test_fetch_sends_bearer_header(monkeypatch):
    captured: dict = {}

    class _SpyTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["auth"] = request.headers.get("authorization", "")
            return _respond({"data": [{"id": "m1"}]})

    real_client = httpx.Client

    def _client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=_SpyTransport(), **kwargs)

    monkeypatch.setattr("nexuscli.llm.openai_compatible.httpx.Client", _client)
    fetch_available_models(BASE_URL, FIXTURE_KEY)
    assert captured["auth"] == f"Bearer {FIXTURE_KEY}"
    assert captured["url"] == f"{BASE_URL}/models"


def test_fetch_omits_auth_header_without_key(monkeypatch):
    captured: dict = {}

    class _SpyTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization", "")
            return _respond({"data": [{"id": "m1"}]})

    real_client = httpx.Client

    def _client(**kwargs):
        kwargs.pop("transport", None)
        return real_client(transport=_SpyTransport(), **kwargs)

    monkeypatch.setattr("nexuscli.llm.openai_compatible.httpx.Client", _client)
    fetch_available_models(BASE_URL, "")
    assert captured["auth"] == ""


def test_fetch_rejects_http_error(monkeypatch):
    with pytest.raises(ModelFetchError) as excinfo:
        _fetch(monkeypatch, {"error": "unauthorized"}, status=401)
    assert "401" in excinfo.value.reason


def test_fetch_rejects_connection_failure(monkeypatch):
    with pytest.raises(ModelFetchError) as excinfo:
        _fetch(monkeypatch, None, raise_exc=httpx.ConnectError("refused"))
    assert "无法连接" in excinfo.value.reason


def test_fetch_rejects_non_list_payload(monkeypatch):
    with pytest.raises(ModelFetchError):
        _fetch(monkeypatch, {"models": "not-a-list"})


def test_fetch_rejects_invalid_scheme():
    with pytest.raises(ModelFetchError) as excinfo:
        fetch_available_models("ftp://gateway/v1")
    assert "http" in excinfo.value.reason


def test_fetch_empty_list_is_error(monkeypatch):
    with pytest.raises(ModelFetchError) as excinfo:
        _fetch(monkeypatch, {"data": []})
    assert "为空" in excinfo.value.reason


def _selector(fetch: list[ModelListingProfile] | None = None) -> ModelSelectorState:
    return ModelSelectorState(
        defaults=[],
        custom=[],
        current_provider="openai-compatible",
        current_model="m0",
        fetch=fetch or [],
    )


def test_selector_fetch_tab_roundtrip():
    state = _selector(
        [
            ModelListingProfile(model="cbcn/glm-5.3-flash", context_window=1000000),
            ModelListingProfile(model="cbcn/kimi-k2.7", context_window=256000),
        ]
    )
    state.switch_tab(2)
    assert state.tab == "fetch"
    state.toggle_fetch_check()
    assert state.fetch_checked == {0}
    state.toggle_fetch_check()
    assert not state.fetch_checked
    state.check_all_fetched(True)
    assert len(state.fetch_checked) == 2
    state.check_all_fetched(False)
    assert not state.fetch_checked
    action = state.selected_action()
    assert action.kind == "add_fetched"
    assert action.fetched[0].model == "cbcn/glm-5.3-flash"


def test_selector_fetch_search_filters_and_keeps_checked_indices():
    state = _selector(
        [
            ModelListingProfile(model="cbcn/glm-5.3-flash", context_window=1),
            ModelListingProfile(model="cbcn/kimi-k2.7", context_window=1),
            ModelListingProfile(model="cbai/glm-5.0", context_window=1),
        ]
    )
    state.switch_tab(2)
    state.fetch_query = "glm"
    assert state.filtered_fetch_count == 2
    state.indexes["fetch"] = 0
    state.toggle_fetch_check()
    # checked index is unfiltered: switching the query keeps the selection
    state.fetch_query = "kimi"
    assert state.fetch_checked == {0}
    state.fetch_query = ""
    action = state.selected_action()
    assert action.kind == "add_fetched"
    assert [p.model for p in action.fetched] == ["cbcn/glm-5.3-flash"]


def test_selector_empty_fetch_tab_selects_nothing():
    state = _selector([])
    state.switch_tab(2)
    action = state.selected_action()
    assert action.kind == "select"
    assert action.fetched == ()


def test_store_fetched_profiles_persists_and_activates(tmp_path):
    import io

    from rich.console import Console

    from nexuscli.config import NexusCliConfig
    from nexuscli.entrypoints.commands import model_cmds

    console = Console(file=io.StringIO(), force_terminal=False)
    store = CustomModelStore(tmp_path / "models.json")
    listings = [
        ModelListingProfile(
            model="cbcn/glm-5.3-flash", context_window=1_000_000, base_url=BASE_URL
        ),
        ModelListingProfile(
            model="cbcn/kimi-k2.7", context_window=256_000, base_url=BASE_URL
        ),
    ]
    config = NexusCliConfig()
    config.llm.provider = "openai-compatible"
    config.llm.base_url = BASE_URL
    config.llm.api_key = FIXTURE_KEY
    activated: list[str] = []

    class _FakeAgent:
        llm_client = SimpleNamespace(provider_name="openai-compatible", model_name="old")

    original = model_cmds._activate_model
    model_cmds._activate_model = lambda *a, **k: activated.append(k or a)
    try:
        exited = model_cmds._store_fetched_profiles(
            store, listings, config, _FakeAgent(), None, None, str(tmp_path), console
        )
    finally:
        model_cmds._activate_model = original
    assert exited is True
    assert len(activated) == 1
    stored = store.list()
    assert {p.model for p in stored} == {"cbcn/glm-5.3-flash", "cbcn/kimi-k2.7"}
    assert all(p.provider == "openai-compatible" for p in stored)


def test_store_fetched_profiles_skips_duplicates(tmp_path):
    import io

    from rich.console import Console

    from nexuscli.config import NexusCliConfig
    from nexuscli.entrypoints.commands import model_cmds

    console = Console(file=io.StringIO(), force_terminal=False)
    store = CustomModelStore(tmp_path / "models.json")
    listing = ModelListingProfile(
        model="cbcn/glm-5.3-flash", context_window=1_000_000, base_url=BASE_URL
    )
    config = NexusCliConfig()
    config.llm.provider = "openai-compatible"
    config.llm.base_url = BASE_URL
    config.llm.api_key = FIXTURE_KEY

    original = model_cmds._activate_model
    model_cmds._activate_model = lambda *a, **k: None
    try:
        assert model_cmds._store_fetched_profiles(
            store, [listing], config, None, None, None, str(tmp_path), console
        )
        assert model_cmds._store_fetched_profiles(
            store, [listing], config, None, None, None, str(tmp_path), console
        )
    finally:
        model_cmds._activate_model = original
    assert len(store.list()) == 1


def test_activate_model_from_fetched_profile_rebuilds_client(tmp_path, monkeypatch):
    """Integration shape: a fetched profile flows through _activate_model."""
    profile = ModelProfile.custom_profile(
        name="API · cbcn/glm-5.3-flash",
        provider="openai-compatible",
        model="cbcn/glm-5.3-flash",
        base_url=BASE_URL,
        context_window=1_000_000,
        api_key=FIXTURE_KEY,
    )
    from nexuscli.config import NexusCliConfig

    config = NexusCliConfig()
    config.llm.provider = "openai-compatible"
    config.llm.base_url = BASE_URL
    config.llm.api_key = FIXTURE_KEY

    class _FakeClient:
        provider_name = "openai-compatible"
        model_name = "old"
        max_context_window = 64_000

    class _FakeAgent:
        llm_client = _FakeClient()
        system_prompt = ""

    class _FakeRegistry:
        def list_names(self):
            return []

    class _FakeRenderer:
        def set_context_window(self, value):
            self.window = value

    monkeypatch.setenv("NEXUSCLI_API_KEY", FIXTURE_KEY)
    agent = _FakeAgent()
    _activate_model(profile, config, agent, _FakeRegistry(), _FakeRenderer(), str(tmp_path))
    assert agent.llm_client.model_name == "cbcn/glm-5.3-flash"
    assert agent.llm_client.max_context_window == 1_000_000
    assert agent.system_prompt  # rebuilt


# ---------------------------------------------------------------------------
# Live gateway (skipped when the local endpoint is down)
# ---------------------------------------------------------------------------


def test_live_gateway_fetch():
    import os

    base = os.environ.get("NEXUSCLI_TEST_GATEWAY", "http://localhost:20128/v1")
    key = os.environ.get("NEXUSCLI_TEST_GATEWAY_KEY", "")
    try:
        models = fetch_available_models(base, key)
    except ModelFetchError as exc:
        pytest.skip(f"live gateway unavailable: {exc.reason}")
    assert models, "live gateway returned no models"
