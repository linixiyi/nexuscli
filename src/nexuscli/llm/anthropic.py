"""Native Anthropic Messages API client (provider="anthropic").

Minimal slice compared to ZCode's model-execution layer: no coding-plan
gateway attribution header family, no cache-control block injection, and
no reasoning-metadata normalization. We map the existing OpenAI-shaped
internals onto Anthropic's wire format:

- the system prompt becomes the top-level ``system`` string field
  (Anthropic semantics — system is not a chat message);
- ``{"type": "function", "function": {...}}`` tool definitions become
  ``{name, description, input_schema}`` entries;
- assistant ``tool_calls`` become ``tool_use`` blocks and ``role="tool"``
  messages become ``user`` messages carrying ``tool_result`` blocks;
- images are not supported in this slice (no ``supports_images``) —
  list content is flattened to its string form.

Credential discipline: the API key is only ever taken from the
``ANTHROPIC_API_KEY`` environment variable (fallback wiring lives in
``llm/factory.py``) or the existing ``config.llm.api_key`` path. Source
and tests must contain the ``<api-key>`` placeholder only — never a real
credential literal.

Pricing: there are no built-in Anthropic price profiles; cost calculation
requires ``llm.prices`` overrides in the NexusCLI config.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

# Single source of truth for SSE frame parsing: the Anthropic stream is
# standard "data:" framing, identical to the OpenAI-compatible one.
from nexuscli.llm.openai_compatible import _iter_sse
from nexuscli.llm.pricing import CostBreakdown, ModelPriceProfile, calculate_cost
from nexuscli.types import Message, Usage

ANTHROPIC_API_VERSION = "2023-06-01"


@dataclass(slots=True)
class AnthropicClient:
    # model/api_key carry no defaults, so they must precede the defaulted
    # fields (dataclass ordering rule); construct with keyword arguments.
    model: str
    api_key: str
    provider_name: str = "anthropic"
    base_url: str = "https://api.anthropic.com"
    # max_tokens is mandatory on every Anthropic Messages API request.
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0
    max_context_window: int = 200_000
    price_profile: ModelPriceProfile | None = None

    @property
    def model_name(self) -> str:
        return self.model

    def calculate_cost(
        self,
        usage: Usage | dict[str, Any],
        *,
        currency: str = "usd",
    ) -> CostBreakdown:
        if self.price_profile is None:
            raise ValueError(
                f'No price profile is configured for model "{self.model}". '
                "Set llm.prices in NexusCLI config."
            )
        return calculate_cost(usage, self.price_profile, currency=currency)

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.api_key:
            yield {
                "type": "error",
                "error": RuntimeError(
                    "ANTHROPIC_API_KEY is not configured. Set it in env (ANTHROPIC_API_KEY) "
                    "or ~/.nexuscli/config.json llm.api_key."
                ),
            }
            return

        payload = self._build_payload(messages, tools, system_prompt=system_prompt)

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
            "user-agent": "NexusCLI-Python/0.1.0",
        }
        url = self.base_url.rstrip("/") + "/v1/messages"

        # Conversion state shared by the per-event parser below: usage
        # counters arrive on message_start/message_delta, message_stop
        # turns them into the final message_end + usage events.
        state: dict[str, Any] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "stop_reason": "",
        }
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout, http2=False) as client,
                client.stream("POST", url, headers=headers, json=payload) as response,
            ):
                response.raise_for_status()
                async for event in _iter_sse(response):
                    try:
                        chunk = json.loads(event)
                    except json.JSONDecodeError:
                        continue
                    async for parsed in self._parse_event(chunk, state):
                        yield parsed
        except httpx.TimeoutException:
            yield {
                "type": "error",
                "error": RuntimeError(
                    f"{self.provider_name} request timed out after {self.timeout:g}s. "
                    "Check the network and retry."
                ),
            }
        except httpx.HTTPStatusError as exc:
            yield {
                "type": "error",
                "error": RuntimeError(
                    f"{self.provider_name} API returned HTTP {exc.response.status_code}. "
                    "Check the API key, model access, account balance, and provider status."
                ),
            }
        except httpx.RequestError:
            yield {
                "type": "error",
                "error": RuntimeError(
                    f"Could not connect to {self.provider_name} at {self.base_url}. "
                    "Check the network, VPN/proxy, and provider status, then retry."
                ),
            }

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            # Anthropic requires max_tokens on every request (no default).
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
            # System prompt is a top-level field, not a system message.
            "system": system_prompt,
            "messages": self._format_messages(messages),
        }
        if tools:
            # Tools arrive in the OpenAI shape the agent already builds
            # ({"type": "function", "function": {...}}); convert them to
            # Anthropic's {name, description, input_schema} form.
            payload["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {}),
                }
                for tool in tools
            ]
        return payload

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                # Tool results travel as user messages with tool_result blocks.
                formatted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.tool_call_id or "",
                                "content": [{"type": "text", "text": str(message.content)}],
                            }
                        ],
                    }
                )
            elif message.role == "assistant" and message.tool_calls:
                blocks: list[dict[str, Any]] = []
                if message.content:
                    blocks.append({"type": "text", "text": str(message.content)})
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": call.get("function", {}).get("name", ""),
                        # OpenAI-shaped arguments is a JSON string; Anthropic
                        # wants the already-parsed input object.
                        "input": _json_loads_or_empty(call.get("function", {}).get("arguments")),
                    }
                    for call in message.tool_calls
                )
                formatted.append({"role": "assistant", "content": blocks})
            else:
                # No image input in this slice: list content degrades to str.
                formatted.append({"role": message.role, "content": str(message.content)})
        return formatted

    async def _parse_event(
        self,
        event: dict[str, Any],
        state: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Translate one Anthropic SSE event into the nexus event protocol.

        ``state`` carries the usage counters and last stop_reason across
        events; ``message_stop`` emits the final message_end and usage.
        """
        event_type = event.get("type")
        if event_type == "message_start":
            message = event.get("message") or {}
            usage = message.get("usage") or {}
            state["input_tokens"] = _non_negative_int(usage.get("input_tokens"))
            state["cache_read_tokens"] = _non_negative_int(usage.get("cache_read_input_tokens"))
            yield {"type": "message_start", "model": str(message.get("model") or self.model)}
        elif event_type == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                yield {
                    "type": "tool_call_delta",
                    "tool_call": {
                        "index": event.get("index", 0),
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {"name": block.get("name", ""), "arguments": ""},
                    },
                }
        elif event_type == "content_block_delta":
            delta = event.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                yield {"type": "text_delta", "text": str(delta.get("text") or "")}
            elif delta_type == "thinking_delta":
                yield {"type": "thinking_delta", "thinking": str(delta.get("thinking") or "")}
            elif delta_type == "input_json_delta":
                yield {
                    "type": "tool_call_delta",
                    "tool_call": {
                        "index": event.get("index", 0),
                        "function": {"arguments": str(delta.get("partial_json") or "")},
                    },
                }
        elif event_type == "message_delta":
            delta = event.get("delta") or {}
            if delta.get("stop_reason"):
                state["stop_reason"] = str(delta["stop_reason"])
            usage = event.get("usage") or {}
            if usage.get("output_tokens") is not None:
                # Streamed usage is cumulative; the last value wins.
                state["output_tokens"] = _non_negative_int(usage.get("output_tokens"))
        elif event_type == "message_stop":
            yield {
                "type": "message_end",
                "stop_reason": _map_stop_reason(str(state.get("stop_reason") or "")),
            }
            yield {
                "type": "usage",
                "usage": Usage(
                    input_tokens=int(state.get("input_tokens") or 0),
                    output_tokens=int(state.get("output_tokens") or 0),
                    # cache_read_input_tokens maps onto nexus cache_hit_tokens.
                    cache_hit_tokens=int(state.get("cache_read_tokens") or 0),
                ).to_dict(),
            }
        # "ping" and unknown event types are ignored.


def _json_loads_or_empty(raw: Any) -> dict[str, Any]:
    """Parse tool-call arguments; malformed or non-object JSON yields {}."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _map_stop_reason(reason: str) -> str:
    """Map Anthropic stop_reason onto the nexus exit enum (types.StopReason)."""
    if reason == "tool_use":
        return "tool_use"
    if reason == "max_tokens":
        return "max_tokens"
    # "end_turn" and "stop_sequence" both mean the model finished its turn.
    return "end_turn"
