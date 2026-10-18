"""Vision-capability detection is capability-driven, not name-guess-only.

Round-1 feedback (spec 2026-10-18_verify-zero-and-yellow): the real gateway
marks ``capabilities.vision: true`` on models whose names carry no vision
marker, so the @image: flow was unreachable end to end — the only name-matched
model had a dead upstream channel, and the models that actually accepted
images were filtered out. An explicit tri-state (config ``llm.vision`` /
profile ``vision`` / gateway capability) now wins; the name heuristic stays
the fallback for gateways that declare nothing.
"""

from __future__ import annotations

from nexuscli.config import LlmConfig
from nexuscli.llm.factory import create_llm_client
from nexuscli.llm.model_profiles import ModelProfile
from nexuscli.llm.openai_compatible import (
    OpenAICompatibleClient,
    _parse_models_payload,
)


def _client(**kwargs) -> OpenAICompatibleClient:
    fields = {
        "provider_name": "openai-compatible",
        "model": "cbcn/glm-5.3-flash",
        "api_key": "k",
        "base_url": "https://example.invalid/v1",
        **kwargs,
    }
    return OpenAICompatibleClient(**fields)


def test_explicit_vision_true_beats_name_heuristic() -> None:
    assert _client(vision_capable=True).supports_images is True


def test_explicit_vision_false_beats_name_heuristic() -> None:
    assert _client(model="glm-5v-turbo", vision_capable=False).supports_images is False


def test_name_heuristic_still_fallback_when_undeclared() -> None:
    assert _client(model="some-vision-model").supports_images is True
    assert _client().supports_images is False


def test_models_payload_parses_vision_capability() -> None:
    listings = _parse_models_payload(
        {
            "data": [
                {"id": "cbcn/glm-5.3-flash", "capabilities": {"vision": True}},
                {"id": "text-only", "capabilities": {"vision": False}},
                {"id": "no-caps"},
            ]
        }
    )
    assert [item.vision for item in listings] == [True, False, None]


def test_factory_passes_config_vision_through() -> None:
    config = LlmConfig(
        provider="openai-compatible",
        model="cbcn/glm-5.3-flash",
        api_key="k",
        base_url="https://example.invalid/v1",
        vision=True,
    )
    client = create_llm_client(config)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.supports_images is True


def test_custom_profile_carries_vision() -> None:
    profile = ModelProfile.custom_profile(
        name="gateway vision",
        provider="openai-compatible",
        model="cbcn/glm-5.3-flash",
        base_url="https://example.invalid/v1",
        context_window=128_000,
        vision=True,
    )
    assert profile.vision is True
