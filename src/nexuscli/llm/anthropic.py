"""Native Anthropic Messages API client (provider="anthropic").

Minimal slice compared to ZCode's model-execution layer: no coding-plan
gateway attribution header family and no reasoning-metadata normalization.
We map the existing OpenAI-shaped internals onto Anthropic's wire format:

- the system prompt becomes the top-level ``system`` field as a single
  ``text`` block tagged ``cache_control: ephemeral`` (Anthropic semantics —
  system is not a chat message), and the last converted tool entry is
  tagged the same way, so system + tool schema form one cached prefix
  (05 F12 prompt caching);
- ``{"type": "function", "function": {...}}`` tool definitions become
  ``{name, description, input_schema}`` entries;
- assistant ``tool_calls`` become ``tool_use`` blocks and ``role="tool"``
  messages become ``user`` messages carrying ``tool_result`` blocks;
- assistant ``thinking_blocks`` (when the caller carries them on the
  message) are passed back verbatim — ``signature`` included — at the head
  of the assistant content, as extended thinking requires;
- images are not supported in this slice (no ``supports_images``) —
  list content is flattened to its string form.

Stream retry: transport-level drops (``RemoteProtocolError`` /
``ReadError`` / ``ConnectError``) are retried with a 1/2/4s backoff across
4 attempts. The first attempt streams live; every retry attempt collects
its events silently and only the final successful attempt's sequence is
replayed — a retry that fails again never publishes (no duplicate token
events), and after an already-published attempt-1 prefix the replayed
complete message shows up as a display jump (prefix followed directly by
the full message). This is an intentional tradeoff, unlike the
OpenAI-compatible client which never replays once a delta was produced.
Semantic errors (HTTP status errors, timeouts) are never retried.

Credential discipline: the API key is only ever taken from the
``ANTHROPIC_API_KEY`` environment variable (fallback wiring lives in
``llm/factory.py``) or the existing ``config.llm.api_key`` path. Source
and tests must contain the ``<api-key>`` placeholder only — never a real
credential literal.

Pricing: there are no built-in Anthropic price profiles; cost calculation
requires ``llm.prices`` overrides in the NexusCLI config.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

# Single sources shared with the OpenAI-compatible client: the SSE frame
# parsing (the Anthropic stream is standard "data:" framing), and the
# HTTP error text plus the status-raise-with-body read, so both chat
# backends surface gateway error bodies identically and downstream code
# (is_transient_api_error) classifies identical phrasing.
from nexuscli.llm.openai_compatible import (
    _http_error_message,
    _iter_sse,
    _raise_for_status_with_body,
    httpx_client_kwargs,
)
from nexuscli.llm.pricing import CostBreakdown, ModelPriceProfile, calculate_cost
from nexuscli.types import Message, Usage

ANTHROPIC_API_VERSION = "2023-06-01"

# F12 stream retry parameters (05 doc review ruling): 4 attempts with 3
# backoffs of 1/2/4s. kitagent's ``_MAX_STREAM_RETRIES = 3`` only produces
# two backoffs, so its 4s tier was unreachable; the reviewed count makes
# the documented 1/2/4 sequence real.
_MAX_STREAM_ATTEMPTS = 4
_STREAM_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)
# Only connection/stream drops retry. Semantic errors (HTTP status errors,
# timeouts) and any other httpx failure surface immediately.
_RETRYABLE_STREAM_ERRORS = (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError)


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
        # turns them into the final message_end + usage events. It is
        # reset per attempt so published usage always belongs to the
        # attempt whose events are actually emitted.
        for attempt in range(1, _MAX_STREAM_ATTEMPTS + 1):
            state: dict[str, Any] = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "stop_reason": "",
            }
            # Retry attempts collect silently: their parsed events are only
            # replayed when the attempt turns out to be the final success.
            # A retry that drops again discards its buffer, so nothing a
            # failed attempt produced is ever published twice.
            buffered: list[dict[str, Any]] = []
            try:
                async with (
                    httpx.AsyncClient(
                        timeout=self.timeout, http2=False, **httpx_client_kwargs(self.base_url)
                    ) as client,
                    client.stream("POST", url, headers=headers, json=payload) as response,
                ):
                    await _raise_for_status_with_body(response)
                    async for event in _iter_sse(response):
                        try:
                            chunk = json.loads(event)
                        except json.JSONDecodeError:
                            continue
                        async for parsed in self._parse_event(chunk, state):
                            if attempt == 1:
                                yield parsed
                            else:
                                buffered.append(parsed)
                if attempt > 1:
                    for parsed in buffered:
                        yield parsed
                return
            except _RETRYABLE_STREAM_ERRORS:
                if attempt == _MAX_STREAM_ATTEMPTS:
                    yield {
                        "type": "error",
                        "error": RuntimeError(
                            f"Could not connect to {self.provider_name} at {self.base_url}. "
                            "Check the network, VPN/proxy, and provider status, then retry."
                        ),
                    }
                    return
                await asyncio.sleep(_STREAM_RETRY_BACKOFF_S[attempt - 1])
            except httpx.TimeoutException:
                yield {
                    "type": "error",
                    "error": RuntimeError(
                        f"{self.provider_name} request timed out after {self.timeout:g}s. "
                        "Check the network and retry."
                    ),
                }
                return
            except httpx.HTTPStatusError as exc:
                # Same text (with the gateway body detail) as the OpenAI-compatible
                # client — the planner / orchestrator retry logic classifies both.
                yield {
                    "type": "error",
                    "error": RuntimeError(_http_error_message(self.provider_name, exc)),
                }
                return
            except httpx.RequestError:
                yield {
                    "type": "error",
                    "error": RuntimeError(
                        f"Could not connect to {self.provider_name} at {self.base_url}. "
                        "Check the network, VPN/proxy, and provider status, then retry."
                    ),
                }
                return

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
            # F12 prompt caching: a single text block tagged ephemeral so the
            # (stable) system prompt prefix hits the prompt cache.
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": self._format_messages(messages),
        }
        if tools:
            # Tools arrive in the OpenAI shape the agent already builds
            # ({"type": "function", "function": {...}}); convert them to
            # Anthropic's {name, description, input_schema} form.
            converted = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {}),
                }
                for tool in tools
            ]
            # F12 prompt caching: the tool schema list is stable across
            # turns — tag the last entry so system + tools cache as one
            # prefix (Anthropic only allows cache_control on the final
            # tool definition).
            converted[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = converted
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
            elif message.role == "assistant" and (
                message.tool_calls or _assistant_thinking_blocks(message)
            ):
                blocks: list[dict[str, Any]] = []
                # Extended thinking: thinking blocks (signature included)
                # must be passed back verbatim at the head of the assistant
                # content, before text and tool_use blocks.
                blocks.extend(_assistant_thinking_blocks(message))
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
            state["cache_creation_tokens"] = _non_negative_int(
                usage.get("cache_creation_input_tokens")
            )
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
                    # cache_read_input_tokens maps onto nexus cache_hit_tokens;
                    # cache_creation_input_tokens (cache writes from the
                    # ephemeral breakpoints above) maps onto cache_miss_tokens.
                    cache_hit_tokens=int(state.get("cache_read_tokens") or 0),
                    cache_miss_tokens=int(state.get("cache_creation_tokens") or 0),
                ).to_dict(),
            }
        # "ping" and unknown event types are ignored.


def _assistant_thinking_blocks(message: Any) -> list[dict[str, Any]]:
    """Verbatim thinking blocks carried on an assistant message, if any.

    ``Message`` grows a ``thinking_blocks`` field for the F12 round-trip;
    the client reads it defensively so plain messages (without the field)
    keep formatting exactly as before. Non-mapping entries are dropped —
    everything else is returned as-is, signature included.
    """
    blocks = getattr(message, "thinking_blocks", None)
    if not blocks:
        return []
    return [block for block in blocks if isinstance(block, dict)]


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
