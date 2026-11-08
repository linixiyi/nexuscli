from __future__ import annotations

import os

from nexuscli.config import LlmConfig
from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.google import GoogleGeminiClient
from nexuscli.llm.local import LocalProviderClient
from nexuscli.llm.model_profiles import DEFAULT_MODEL_PROFILES
from nexuscli.llm.offline_demo import OfflineDemoProvider
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.llm.pricing import resolve_price_profile

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com"
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
LLAMACPP_BASE_URL = "http://127.0.0.1:8080/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "kimi": "https://api.moonshot.cn/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "step": "https://api.stepfun.com/v1",
}
# Machine-local gateways (LocalProviderClient: no key required, readable
# start-server hints). Kept separate from PROVIDER_BASE_URLS — those entries
# are key-gated cloud endpoints sharing the generic dispatch branch below,
# while local providers need their own credential-free client. "local" is the
# unified alias; it is endpoint-ambiguous (Ollama vs llama.cpp listen on
# different ports), so it always requires an explicit base_url.
LOCAL_PROVIDER_BASE_URLS = {
    "ollama": OLLAMA_BASE_URL,
    "llamacpp": LLAMACPP_BASE_URL,
}

# Context-window fallback for local models. Local servers default to small
# windows (Ollama's num_ctx and llama.cpp's -c default to a few thousand
# tokens) — far below the 128K cloud fallback, which would let the context
# manager oversubscribe a default-configured server. Users should set
# llm.context_window to match their server's configured window.
LOCAL_FALLBACK_CONTEXT_WINDOW = 32_000

# Context-window fallback for models with no ModelProfile entry. Deliberately
# conservative: the known-model windows come from DEFAULT_MODEL_PROFILES, not
# from a name-keyed dict here, so a new Gemini listing needs no factory edit.
GOOGLE_FALLBACK_CONTEXT_WINDOW = 128_000

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


# Offline deterministic demo provider injection (Ported from kitagent (MIT,
# zed123214/kitagent) src/agent_runtime/web/demo.py): set NEXUSCLI_OFFLINE_DEMO=1
# to make the factory return the scripted OfflineDemoProvider. The switch is
# env-only and opt-in on purpose — no config default, no provider name — so the
# default path (real network clients) is byte-for-byte unchanged.
OFFLINE_DEMO_ENV_VAR = "NEXUSCLI_OFFLINE_DEMO"


def create_llm_client(
    config: LlmConfig,
    *,
    # The root-level telemetry switch rides on this keyword because the
    # factory only receives the llm section (LlmConfig), which does not carry
    # it. Default False keeps every existing caller unchanged; production
    # call sites pass config.telemetry.enabled (repl/cli/runtime/subagent/sdk).
    telemetry_enabled: bool = False,
) -> (
    OpenAICompatibleClient
    | AnthropicClient
    | GoogleGeminiClient
    | LocalProviderClient
    | OfflineDemoProvider
):
    if os.environ.get(OFFLINE_DEMO_ENV_VAR, "") == "1":
        return OfflineDemoProvider()
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
            vision_capable=config.vision,
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
            vision_capable=config.vision,
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
            vision_capable=config.vision,
        )
    if provider in LOCAL_PROVIDER_BASE_URLS or provider == "local":
        # Local gateways usually run without credentials: no key is not an
        # error (see LocalProviderClient), so config.api_key flows through
        # as-is and stays empty when unset.
        base_url = config.base_url or LOCAL_PROVIDER_BASE_URLS.get(provider)
        if not base_url:
            raise ValueError(
                'provider "local" requires llm.base_url '
                '(e.g. "http://127.0.0.1:8080/v1" for llama-server); '
                'or use provider "ollama" / "llamacpp" for their default endpoints'
            )
        context = config.context_window or LOCAL_FALLBACK_CONTEXT_WINDOW
        # include_builtin=False on purpose: a local model that happens to
        # share a cloud model's name (e.g. "deepseek-coder" on Ollama) must
        # not silently inherit that vendor's prices — local inference is
        # free unless llm.prices says otherwise.
        return LocalProviderClient(
            provider_name=provider,
            model=config.model,
            api_key=config.api_key,
            base_url=base_url,
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
            vision_capable=config.vision,
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
    if provider == "google":
        # Env fallback lives here on purpose, same seam as the anthropic
        # branch: config._apply_env's provider key map is outside this
        # change's boundary. GEMINI_API_KEY is the documented name; the
        # Google-SDK spelling GOOGLE_API_KEY is honored as a fallback.
        api_key = (
            config.api_key
            or os.environ.get("GEMINI_API_KEY", "")
            or os.environ.get("GOOGLE_API_KEY", "")
        )
        # Known-model windows come from the ModelProfile table (the single
        # capability/context source this provider uses — no name-keyed dict
        # here); unknown models take the conservative fallback.
        context = config.context_window or _google_profile_context_window(config.model)
        return GoogleGeminiClient(
            model=config.model,
            api_key=api_key,
            base_url=config.base_url or GOOGLE_BASE_URL,
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
            vision_capable=config.vision,
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
            vision_capable=config.vision,
        )
    valid = ", ".join(
        [
            "anthropic",
            "deepseek",
            "google",
            "llamacpp",
            "local",
            "ollama",
            "openai",
            "openai-compatible",
            *PROVIDER_BASE_URLS,
        ]
    )
    raise ValueError(
        f"unknown LLM provider {config.provider!r} without a base_url. "
        f"Set llm.base_url or use one of: {valid}"
    )


def _google_profile_context_window(model: str) -> int:
    """Context window for *model* from the ModelProfile table, else conservative."""
    lowered = model.lower()
    for profile in DEFAULT_MODEL_PROFILES:
        if profile.provider == "google" and profile.model.lower() == lowered:
            return profile.context_window
    return GOOGLE_FALLBACK_CONTEXT_WINDOW
