"""Native Google Gemini API client (provider="google").

Minimal slice mapping the existing OpenAI-shaped internals onto Gemini's
``generateContent`` / ``streamGenerateContent`` wire format (REST, SSE via
``?alt=sse``):

- the system prompt becomes the top-level ``systemInstruction`` field
  (Gemini semantics — system is not a chat message);
- ``{"type": "function", "function": {...}}`` tool definitions become
  ``functionDeclarations`` entries wrapped in one ``tools`` entry;
- assistant ``tool_calls`` become ``functionCall`` parts and ``role="tool"``
  messages become ``user`` contents carrying ``functionResponse`` parts
  (consecutive tool results merge into one user content). Gemini assigns no
  call ids, so ``functionResponse.name`` is resolved from the paired
  assistant ``tool_calls`` entry by id;
- image parts ride on the tri-state capability field (``vision_capable``,
  from ``ModelProfile.vision`` / ``llm.vision``) — never a model-name guess;
  ``None`` (unknown model) is conservatively image-less. Supported images
  travel as ``inlineData`` (data URLs from the @image: flow); remote http(s)
  URLs degrade to a text note because the REST API has no remote-image
  input outside GCS/YouTube ``fileData``;
- usage comes from ``usageMetadata`` (promptTokenCount /
  candidatesTokenCount / cachedContentTokenCount / thoughtsTokenCount).
  Streamed usage is a running total, so only the final value is emitted —
  consumers (agent.py) sum every usage event they see.

Credential discipline: the API key travels only in the ``x-goog-api-key``
header (never a ``?key=`` query parameter, which leaks into logs). Source
and tests contain the ``<api-key>`` placeholder only.

Pricing: there are no built-in Gemini price profiles; cost calculation
requires ``llm.prices`` overrides in the NexusCLI config (same boundary as
the Anthropic client).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

# Shared SSE parsing and HTTP error plumbing with the other chat backends:
# the Gemini SSE stream is standard "data:" framing, and the error text must
# match the phrasing downstream classifiers (is_transient_api_error /
# is_context_overflow_error) already pattern-match.
from nexuscli.llm.openai_compatible import (
    _http_error_message,
    _iter_sse,
    _raise_for_status_with_body,
    httpx_client_kwargs,
)
from nexuscli.llm.pricing import CostBreakdown, ModelPriceProfile, calculate_cost
from nexuscli.types import Message, Usage

# Data URLs only — the inlineData part cannot reference a remote http(s)
# URL, and this client performs no secondary fetches.
_DATA_URL_PATTERN = re.compile(r"^data:(?P<mime>[^;,]+);base64,(?P<data>.+)$", re.DOTALL)

# Finish reasons that mean the model was cut short by a policy filter, mapped
# onto the nexus "stop_sequence" exit (same slot the OpenAI-compatible client
# uses for content_filter). Everything else unknown falls back to end_turn.
_FILTER_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "BLOCK_LIST", "PROHIBITED_CONTENT", "SPII"}
)


@dataclass(slots=True)
class GoogleGeminiClient:
    # model/api_key carry no defaults, so they must precede the defaulted
    # fields (dataclass ordering rule); construct with keyword arguments.
    model: str
    api_key: str
    provider_name: str = "google"
    base_url: str = "https://generativelanguage.googleapis.com"
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0
    max_context_window: int = 128_000
    price_profile: ModelPriceProfile | None = None
    # Tri-state multimodal capability from an explicit decision
    # (ModelProfile.vision via /model, or llm.vision in config). None means
    # the capability is unknown → conservatively no images. There is no
    # model-name heuristic on this provider by design.
    vision_capable: bool | None = None

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def supports_images(self) -> bool:
        return self.vision_capable is True

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
        """Stream one Gemini response as the fixed event sequence consumers
        (agent.py / query.py) parse: ``message_start`` → ``thinking_delta`` /
        ``text_delta`` / ``tool_call_delta`` → ``message_end`` → ``usage``.
        """
        if not self.api_key:
            yield {
                "type": "error",
                "error": RuntimeError(
                    "GEMINI_API_KEY is not configured. Set it in env (GEMINI_API_KEY or "
                    "GOOGLE_API_KEY) or ~/.nexuscli/config.json llm.api_key."
                ),
            }
            return

        payload = self._build_payload(messages, tools, system_prompt=system_prompt)
        headers = {
            "x-goog-api-key": self.api_key,
            "content-type": "application/json",
            "user-agent": "NexusCLI-Python/0.1.0",
        }
        url = (
            f"{self.base_url.rstrip('/')}/v1beta/models/{self.model}:streamGenerateContent?alt=sse"
        )

        # Emitted before the request so consumers always open with exactly one
        # message_start — including connect-phase failures (same seam as the
        # OpenAI-compatible client; the Gemini SSE stream has no start event
        # of its own to translate).
        yield {"type": "message_start", "model": self.model}
        # Streamed usageMetadata is a running total, not a delta — keep only
        # the last one and emit it once after the stream ends so consumers
        # don't multiply tokens.
        pending_usage: dict[str, Any] | None = None
        # Parsing state shared across chunks: functionCall parts carry no call
        # ids or ordering, so the parser hands them deterministic ids/indices.
        state: dict[str, Any] = {"saw_function_call": False, "next_index": 0}
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
                    async for parsed in self._parse_chunk(chunk, state):
                        if parsed.get("type") == "usage":
                            pending_usage = parsed
                        else:
                            yield parsed
            if pending_usage is not None:
                yield pending_usage
        except httpx.TimeoutException:
            yield {
                "type": "error",
                "error": RuntimeError(
                    f"{self.provider_name} request timed out after {self.timeout:g}s. "
                    "Check the network and retry."
                ),
            }
        except httpx.HTTPStatusError as exc:
            # Same text (with the gateway body detail) as the other chat
            # backends — the planner / orchestrator retry logic classifies
            # identical phrasing.
            yield {
                "type": "error",
                "error": RuntimeError(_http_error_message(self.provider_name, exc)),
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
            "contents": self._format_messages(messages),
            # System prompt is a top-level field, not a chat message.
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_tokens,
            },
        }
        if tools:
            # Tools arrive in the OpenAI shape the agent already builds
            # ({"type": "function", "function": {...}}); convert them to
            # Gemini's single functionDeclarations entry.
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["function"]["name"],
                            "description": tool["function"].get("description", ""),
                            "parameters": tool["function"].get("parameters", {}),
                        }
                        for tool in tools
                    ]
                }
            ]
        return payload

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        # tool_call_id → function name, resolved from earlier assistant
        # messages: Gemini's functionResponse parts name the function, not
        # the call id the nexus history carries.
        call_names: dict[str, str] = {}
        formatted: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                name = call_names.get(message.tool_call_id or "", "") or message.tool_call_id or ""
                part: dict[str, Any] = {
                    "functionResponse": {
                        "name": name,
                        "response": {"result": str(message.content)},
                    }
                }
                # Consecutive tool results merge into one user content; a
                # plain user text turn in between starts a new one.
                previous = formatted[-1] if formatted else None
                if (
                    previous is not None
                    and previous["role"] == "user"
                    and previous["parts"]
                    and "functionResponse" in previous["parts"][-1]
                ):
                    previous["parts"].append(part)
                else:
                    formatted.append({"role": "user", "parts": [part]})
            elif message.role == "assistant":
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": str(message.content)})
                for call in message.tool_calls or []:
                    call_id = str(call.get("id") or "")
                    function = call.get("function") or {}
                    name = str(function.get("name") or "")
                    if call_id:
                        call_names[call_id] = name
                    parts.append(
                        {
                            "functionCall": {
                                "name": name,
                                # OpenAI-shaped arguments is a JSON string;
                                # Gemini wants the already-parsed args object.
                                "args": _args_object(function.get("arguments")),
                            }
                        }
                    )
                if not parts:
                    parts = [{"text": ""}]
                formatted.append({"role": "model", "parts": parts})
            else:
                # "system" rides on the top-level systemInstruction field; a
                # stray system message in history degrades to a user turn.
                formatted.append({"role": "user", "parts": self._format_parts(message.content)})
        return formatted

    def _format_parts(self, content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content}]
        parts: list[dict[str, Any]] = []
        for part in content:
            part_type = part.get("type")
            if part_type == "text":
                text = str(part.get("text") or "")
                if text:
                    parts.append({"text": text})
            elif part_type == "image_url":
                converted = self._image_part(part)
                if converted is not None:
                    parts.append(converted)
        return parts or [{"text": ""}]

    def _image_part(self, part: dict[str, Any]) -> dict[str, Any] | None:
        url = str((part.get("image_url") or {}).get("url") or "")
        if not self.supports_images:
            # Capability field said no (or unknown): degrade to a text note,
            # mirroring the OpenAI-compatible client's non-vision path.
            metadata = part.get("metadata") or {}
            source = metadata.get("source", "remote image")
            width = metadata.get("width", "?")
            height = metadata.get("height", "?")
            return {"text": f"[Image omitted: {source}, {width}x{height}]"}
        match = _DATA_URL_PATTERN.match(url)
        if match is None:
            # Remote http(s) URLs have no inlineData form (fileData is
            # GCS/YouTube only) and this client performs no fetches.
            return {"text": "[Image omitted: remote image URLs are not supported here]"}
        return {
            "inlineData": {
                "mimeType": match.group("mime"),
                "data": match.group("data"),
            }
        }

    async def _parse_chunk(
        self,
        chunk: dict[str, Any],
        state: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Translate one Gemini SSE chunk into the nexus event protocol.

        ``state`` carries the function-call flag (finishReason "STOP" after a
        functionCall means tool_use, matching how the OpenAI-compatible
        client maps finish_reason) and the running tool-call index.
        """
        candidates = chunk.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            function_call = part.get("functionCall")
            if isinstance(function_call, dict):
                index = int(state["next_index"])
                state["next_index"] = index + 1
                state["saw_function_call"] = True
                yield {
                    "type": "tool_call_delta",
                    "tool_call": {
                        "index": index,
                        # Gemini sends no call id; a deterministic one keeps
                        # the tool_result round-trip resolvable.
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {
                            "name": str(function_call.get("name") or ""),
                            # Pre-parsed argument object — _merge_tool_delta
                            # serializes dicts into the arguments string.
                            "arguments": _args_object(function_call.get("args")),
                        },
                    },
                }
            elif part.get("text"):
                if part.get("thought") is True:
                    yield {"type": "thinking_delta", "thinking": str(part["text"])}
                else:
                    yield {"type": "text_delta", "text": str(part["text"])}
        finish_reason = candidate.get("finishReason")
        if finish_reason:
            yield {
                "type": "message_end",
                "stop_reason": _map_finish_reason(
                    str(finish_reason), bool(state["saw_function_call"])
                ),
            }
        usage = chunk.get("usageMetadata")
        if isinstance(usage, dict):
            yield {"type": "usage", "usage": _usage_from_metadata(usage).to_dict()}


def _args_object(raw: Any) -> dict[str, Any]:
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


def _usage_from_metadata(usage: dict[str, Any]) -> Usage:
    return Usage(
        input_tokens=_non_negative_int(usage.get("promptTokenCount")),
        output_tokens=_non_negative_int(usage.get("candidatesTokenCount")),
        cache_hit_tokens=_non_negative_int(usage.get("cachedContentTokenCount")),
        reasoning_tokens=_non_negative_int(usage.get("thoughtsTokenCount")),
        total_tokens=_non_negative_int(usage.get("totalTokenCount")),
    )


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _map_finish_reason(reason: str, saw_function_call: bool) -> str:
    """Map Gemini finishReason onto the nexus exit enum (types.StopReason)."""
    if reason == "MAX_TOKENS":
        return "max_tokens"
    if reason.upper() in _FILTER_FINISH_REASONS:
        return "stop_sequence"
    # Gemini ends a tool-call turn with plain "STOP"; the function-call flag
    # from the parsed parts upgrades it to tool_use.
    if reason == "STOP" and saw_function_call:
        return "tool_use"
    return "end_turn"
