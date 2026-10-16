"""Tests for the loopback proxy bypass in the httpx client kwargs helper.

A Windows system proxy (registry-published 127.0.0.1:7890) must never be
applied to a loopback LLM gateway: httpx's default ``trust_env`` routes the
request through it and a local gateway comes back as an empty-body 502.
Non-loopback hosts keep the default so corporate proxies keep working.
"""

from __future__ import annotations

import httpx
import pytest

from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.openai_compatible import OpenAICompatibleClient, httpx_client_kwargs


@pytest.mark.parametrize(
    ("base_url", "bypass"),
    [
        ("http://127.0.0.1:7864/v1", True),
        ("http://localhost:20128/v1", True),
        ("http://LOCALHOST:20128/v1", True),
        ("http://[::1]:7864/v1", True),
        ("http://127.20128.0.1/v1", False),  # 127. prefix match is dotted-quad only
        ("https://api.openai.com/v1", False),
        ("https://api.anthropic.com", False),
        ("not a url", False),
    ],
)
def test_httpx_client_kwargs_loopback_bypass(base_url: str, bypass: bool) -> None:
    kwargs = httpx_client_kwargs(base_url)

    if bypass:
        assert kwargs == {"trust_env": False}
    else:
        assert kwargs == {}


def test_real_clients_build_with_bypass_kwargs() -> None:
    """The exact kwargs each client passes construct a valid httpx client —
    guards against a typo'd keyword reaching every construction site."""
    openai_client = OpenAICompatibleClient(
        provider_name="openai-compatible",
        model="cn:glm-5.3-flash",
        api_key="k",
        base_url="http://127.0.0.1:7864/v1",
    )
    for kwargs in (
        httpx_client_kwargs(openai_client.base_url),
        httpx_client_kwargs(AnthropicClient(model="m", api_key="k").base_url),
    ):
        httpx.Client(timeout=1.0, **kwargs).close()
