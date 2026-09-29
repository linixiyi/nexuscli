from __future__ import annotations

import os

from nexuscli.config import LlmConfig
from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.llm.pricing import resolve_price_profile

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "kimi": "https://api.moonshot.cn/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "step": "https://api.stepfun.com/v1",
}

MODEL_CONTEXT_WINDOWS = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 1_000_000,
    "deepseek-reasoner": 1_000_000,
    "deepseek-coder": 128_000,
    "glm-5.2": 200_000,
    "glm-5.1": 200_000,
    "glm-4.7": 200_000,
}


def create_llm_client(
    config: LlmConfig,
    *,
    # The root-level telemetry switch rides on this keyword because the
    # factory only receives the llm section (LlmConfig), which does not carry
    # it. Default False keeps every existing caller unchanged; production
    # call sites pass config.telemetry.enabled (repl/cli/runtime/subagent/sdk).
    telemetry_enabled: bool = False,
) -> OpenAICompatibleClient | AnthropicClient:
    provider = config.provider.lower()
    if provider == "deepseek":
        base_url = config.base_url or DEEPSEEK_BASE_URL
        context = config.context_window or MODEL_CONTEXT_WINDOWS.get(config.model.lower(), 64_000)
        return OpenAICompatibleClient(
            provider_name="deepseek",
            model=config.model,
            api_key=config.api_key,
            base_url=base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            max_context_window=context,
            prompt_cache=True,
            price_profile=resolve_price_profile(
                config.model,
                context_window=context,
                overrides=config.prices,
            ),
            telemetry_enabled=telemetry_enabled,
            debug_dump=config.debug_dump,
        )
    if provider in {"openai", "openai-compatible", "compatible"}:
        context = config.context_window or 128_000
        return OpenAICompatibleClient(
            provider_name=provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url or OPENAI_BASE_URL,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            max_context_window=context,
            prompt_cache=False,
            price_profile=resolve_price_profile(
                config.model,
                context_window=context,
                overrides=config.prices,
                include_builtin=False,
            ),
            telemetry_enabled=telemetry_enabled,
            debug_dump=config.debug_dump,
        )
    if provider in PROVIDER_BASE_URLS:
        context = config.context_window or MODEL_CONTEXT_WINDOWS.get(config.model.lower(), 128_000)
        return OpenAICompatibleClient(
            provider_name=provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url or PROVIDER_BASE_URLS[provider],
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            max_context_window=context,
            prompt_cache=False,
            price_profile=resolve_price_profile(
                config.model,
                context_window=context,
                overrides=config.prices,
                include_builtin=False,
            ),
            telemetry_enabled=telemetry_enabled,
            debug_dump=config.debug_dump,
        )
    if provider == "anthropic":
        # Env fallback lives here on purpose; config._apply_env's provider
        # key map is outside this change's boundary.
        api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        context = config.context_window or 200_000
        return AnthropicClient(
            model=config.model,
            api_key=api_key,
            base_url=config.base_url or ANTHROPIC_BASE_URL,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            max_context_window=context,
            price_profile=resolve_price_profile(
                config.model,
                context_window=context,
                overrides=config.prices,
                include_builtin=False,
            ),
        )
    # Unknown provider name: only honor it when an explicit base_url says where
    # to send credentials. Defaulting to a known vendor endpoint would silently
    # transmit the user's API key to a third party on a typo like "oepnai".
    if config.base_url:
        context = config.context_window or 64_000
        return OpenAICompatibleClient(
            provider_name=provider,
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            timeout=config.timeout,
            max_context_window=context,
            prompt_cache=False,
            price_profile=resolve_price_profile(
                config.model,
                context_window=context,
                overrides=config.prices,
                include_builtin=False,
            ),
            telemetry_enabled=telemetry_enabled,
            debug_dump=config.debug_dump,
        )
    valid = ", ".join(["anthropic", "deepseek", "openai", "openai-compatible", *PROVIDER_BASE_URLS])
    raise ValueError(
        f"unknown LLM provider {config.provider!r} without a base_url. "
        f"Set llm.base_url or use one of: {valid}"
    )
