from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from nexuscli.llm.pricing import CostBreakdown, ModelPriceProfile
from nexuscli.types import Message, Usage

# Transient provider-side failures worth one automatic retry: rate limiting
# and gateway/overload 5xx. Matched against the error text because both chat
# backends raise plain RuntimeErrors with this phrasing
# (openai_compatible.py / anthropic.py "API returned HTTP <code>.").
_TRANSIENT_HTTP_STATUSES = ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504")


def is_transient_api_error(error: BaseException | str) -> bool:
    """True when the LLM client error looks retryable (429 / gateway 5xx)."""
    text = error if isinstance(error, str) else str(error)
    return any(status in text for status in _TRANSIENT_HTTP_STATUSES)


class LlmClient(Protocol):
    model_name: str
    provider_name: str
    max_context_window: int
    price_profile: ModelPriceProfile | None

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> AsyncIterator[dict[str, Any]]: ...

    def calculate_cost(
        self,
        usage: Usage | dict[str, Any],
        *,
        currency: str = "usd",
    ) -> CostBreakdown: ...
