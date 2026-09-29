from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from nexuscli.llm.base import is_transient_api_error
from nexuscli.llm.pricing import CostBreakdown, ModelPriceProfile, calculate_cost
from nexuscli.observability import get_trace_id
from nexuscli.telemetry import chat_span
from nexuscli.types import Message, Usage


@dataclass(slots=True)
class OpenAICompatibleClient:
    provider_name: str
    model: str
    api_key: str
    base_url: str
    max_tokens: int = 8192
    temperature: float = 0.7
    timeout: float = 120.0
    max_context_window: int = 128_000
    prompt_cache: bool = False
    price_profile: ModelPriceProfile | None = None
    # Spans are only produced when telemetry.enabled AND the optional
    # opentelemetry SDK are both present; chat_span no-ops otherwise.
    telemetry_enabled: bool = False
    # Opt-in debug dump: when True, every turn appends one request and one
    # response JSON line to ~/.nexuscli/debug/llm/<date>.jsonl. Dump failures
    # are swallowed and never interrupt the chat.
    debug_dump: bool = False
    # Session-level reasoning effort ("minimal"|"low"|"medium"|"high"). Set
    # live by the REPL /effort command; None (default) means the field is
    # never sent. Applied only for whitelisted providers — see
    # _REASONING_EFFORT_PROVIDERS below.
    reasoning_effort: str | None = None

    @property
    def model_name(self) -> str:
        return self.model

    @property
    def supports_images(self) -> bool:
        model = self.model.lower()
        provider = self.provider_name.lower()
        return any(marker in model for marker in ("vision", "image", "5v", "vl")) or (
            provider in {"glm", "zhipu"} and "5v" in model
        )

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
        """Stream one chat completion as the fixed event sequence consumers
        (agent.py / query.py) parse: ``message_start`` → ``thinking_delta`` /
        ``text_delta`` / ``tool_call_delta`` → ``message_end`` → ``usage``.

        Stream fallback (built-in behaviour, no config switch): when the
        streaming request fails with a transient HTTP error (429 / gateway
        5xx, matched by ``is_transient_api_error``) before any event has been
        produced — the connect / first-byte phase, where local gateways
        intermittently reject the streaming path — the turn is replayed once
        as a non-streaming request (``stream: false``) parsed into exactly the
        same event sequence (see ``_stream_with_fallback``). A failure after
        deltas were already emitted is deliberately never replayed: the event
        stream is polluted and a retry would duplicate what consumers already
        saw, so the error surfaces as usual.
        """
        if not self.api_key:
            yield {
                "type": "error",
                "error": RuntimeError(
                    "NEXUSCLI_API_KEY is not configured. Set it in env, ~/.nexuscli/config.json, "
                    "or project .nexuscli/config.json."
                ),
            }
            return

        payload = self._build_payload(messages, tools, system_prompt=system_prompt)

        headers = {
            "authorization": f"Bearer {self.api_key}",
            "content-type": "application/json",
            "user-agent": "NexusCLI-Python/0.1.0",
        }
        # Correlate the request with the session's audit trail. list_models
        # stays untraced on purpose: only the chat path is in the slice.
        trace_id = get_trace_id()
        if trace_id:
            headers["x-request-id"] = trace_id
        url = self.base_url.rstrip("/") + "/chat/completions"

        # Debug-dump sidecar (opt-in): the request line goes out before the
        # first event, so a turn always leaves at least the request on disk.
        # Headers are redacted; the payload only holds model/messages/tools
        # (no credentials), so it is written as-is.
        if self.debug_dump:
            _append_dump(
                {
                    "ts": datetime.now().isoformat(),
                    "direction": "request",
                    "provider": self.provider_name,
                    "model": self.model,
                    "url": url,
                    "headers": _redact_headers(headers),
                    "payload": payload,
                }
            )

        # Emitted before the request so consumers always open with exactly one
        # message_start — including connect-phase failures and fallback replays.
        yield {"type": "message_start", "model": self.model}
        pending_usage: dict[str, Any] | None = None
        # Debug-dump sidecar buffers: mirror the emitted deltas so the
        # response line can aggregate them once the stream ends.
        text_parts: list[str] = []
        tool_calls: list[Any] = []
        dump_error: str | None = None
        # The span covers the whole request/stream-processing section below
        # (including the error branches and the trailing usage event). It is
        # an opt-in sidecar: the yield sequence and the exception handling are
        # unchanged, and chat_span no-ops unless telemetry is enabled.
        with chat_span(self.telemetry_enabled, provider=self.provider_name, model=self.model):
            try:
                async for parsed in self._stream_with_fallback(url, headers, payload):
                    # Streamed usage is a running total, not a delta, and some
                    # gateways attach it to every chunk — keep only the last
                    # one and emit it once so consumers don't multiply tokens.
                    if parsed.get("type") == "usage":
                        pending_usage = parsed
                    else:
                        # Debug-dump sidecar: mirror the delta into the
                        # response buffers; the yielded event is untouched.
                        if parsed.get("type") == "text_delta":
                            text_parts.append(str(parsed.get("text") or ""))
                        elif parsed.get("type") == "tool_call_delta":
                            tool_calls.append(parsed.get("tool_call"))
                        yield parsed
            except httpx.TimeoutException:
                # Debug-dump sidecar: keep a summary for the response line
                # written after the span block; the yielded error is unchanged.
                dump_error = f"{self.provider_name} request timed out after {self.timeout:g}s"
                yield {
                    "type": "error",
                    "error": RuntimeError(
                        f"{self.provider_name} request timed out after {self.timeout:g}s. "
                        "Check the network and retry."
                    ),
                }
            except httpx.HTTPStatusError as exc:
                dump_error = f"{self.provider_name} API returned HTTP {exc.response.status_code}"
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
                return
            if pending_usage is not None:
                yield pending_usage

        # Debug-dump sidecar (opt-in): the response line lands after the span
        # block. Turns that failed with a timeout / HTTP status error dump an
        # error record instead of the aggregated response. The early-returning
        # connection-error branch above never reaches this point, which is
        # accepted: every turn already left at least the request line.
        if self.debug_dump:
            if dump_error is not None:
                record: dict[str, Any] = {
                    "ts": datetime.now().isoformat(),
                    "direction": "response",
                    "model": self.model,
                    "error": dump_error,
                }
            else:
                record = {
                    "ts": datetime.now().isoformat(),
                    "direction": "response",
                    "model": self.model,
                    "text": "".join(text_parts),
                    "tool_calls": tool_calls,
                    "usage": pending_usage.get("usage") if pending_usage is not None else None,
                }
            _append_dump(record)

    async def _stream_with_fallback(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """One streaming attempt, plus at most one built-in non-stream replay.

        A transient HTTP error (429 / gateway 5xx per ``is_transient_api_error``)
        raised before any event was produced — the connect / first-byte phase —
        is answered by ``_non_stream_events`` so gateways that intermittently
        reject the streaming path don't take the whole turn down. A failure
        after deltas were already yielded re-raises as-is: the event stream is
        polluted and replaying it would duplicate what consumers saw. httpx
        exceptions propagate to ``chat``'s error-event mapping unchanged.
        """
        produced_delta = False
        try:
            async with (
                httpx.AsyncClient(timeout=self.timeout, http2=False) as client,
                client.stream("POST", url, headers=headers, json=payload) as response,
            ):
                await _raise_for_status_with_body(response)
                async for event in _iter_sse(response):
                    if event == "[DONE]":
                        break
                    try:
                        chunk = json.loads(event)
                    except json.JSONDecodeError:
                        continue
                    async for parsed in self._parse_chunk(chunk):
                        # Usage-only events are buffered totals, not replayable
                        # content — they don't count as stream progress.
                        if parsed.get("type") != "usage":
                            produced_delta = True
                        yield parsed
        except httpx.HTTPStatusError as exc:
            if produced_delta or not is_transient_api_error(
                _http_error_message(self.provider_name, exc)
            ):
                raise
            async for parsed in self._non_stream_events(url, headers, payload):
                yield parsed

    async def _non_stream_events(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay one failed streaming turn as a single non-streaming request.

        The completion JSON is re-shaped into ``_parse_chunk`` input form (the
        ``message`` fields masquerade as one ``delta``), so the existing
        thinking / text / tool_calls / usage parsing is reused verbatim and
        the yielded event sequence is identical to the streaming path's.
        httpx exceptions propagate to ``chat``'s error-event mapping unchanged.
        """
        replay_payload = dict(payload)
        replay_payload["stream"] = False
        replay_payload.pop("stream_options", None)
        async with httpx.AsyncClient(timeout=self.timeout, http2=False) as client:
            response = await client.post(url, headers=headers, json=replay_payload)
            await _raise_for_status_with_body(response)
            completion = response.json()
        choices = completion.get("choices") or []
        first = choices[0] if choices else {}
        chunk = {
            "choices": [
                {
                    "delta": first.get("message") or {},
                    "finish_reason": first.get("finish_reason"),
                }
            ],
            "usage": completion.get("usage"),
        }
        async for parsed in self._parse_chunk(chunk):
            yield parsed

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        *,
        system_prompt: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._format_messages(messages, system_prompt),
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        # Reasoning effort is opt-in per provider: only whitelisted
        # chat-completions backends ever see the field (never a blank value).
        if self.reasoning_effort and self.provider_name.lower() in _REASONING_EFFORT_PROVIDERS:
            payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _format_messages(self, messages: list[Message], system_prompt: str) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for message in messages:
            if message.role == "tool":
                formatted.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "",
                        "content": str(message.content),
                    }
                )
            elif message.role == "assistant":
                item: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
                if message.tool_calls:
                    item["tool_calls"] = message.tool_calls
                formatted.append(item)
            else:
                formatted.append(
                    {"role": message.role, "content": self._format_content(message.content)}
                )
        return formatted

    def _format_content(self, content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
        if isinstance(content, str):
            return content
        if self.supports_images:
            cleaned = []
            for part in content:
                item = {key: value for key, value in part.items() if key != "metadata"}
                cleaned.append(item)
            return cleaned
        text_parts = []
        for part in content:
            if part.get("type") == "text":
                text_parts.append(str(part.get("text") or ""))
            elif part.get("type") == "image_url":
                metadata = part.get("metadata") or {}
                source = metadata.get("source", "remote image")
                width = metadata.get("width", "?")
                height = metadata.get("height", "?")
                text_parts.append(f"[Image omitted: {source}, {width}x{height}]")
        return "\n".join(text_parts)

    async def list_models(self) -> list[str]:
        """Fetch available model IDs from the API's /v1/models endpoint.

        Returns an empty list if the API key is missing or the request fails.
        """
        if not self.api_key:
            return []
        headers = {
            "authorization": f"Bearer {self.api_key}",
            "user-agent": "NexusCLI-Python/0.1.0",
        }
        url = self.base_url.rstrip("/") + "/models"
        async with httpx.AsyncClient(timeout=15.0, http2=False) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                return sorted(item["id"] for item in data.get("data", []))
            except Exception:
                return []

    async def _parse_chunk(self, chunk: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        choices = chunk.get("choices") or []
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}

            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                yield {"type": "thinking_delta", "thinking": reasoning}

            content = delta.get("content")
            if isinstance(content, str) and content:
                yield {"type": "text_delta", "text": content}

            tool_calls = delta.get("tool_calls") or []
            for tool_call in tool_calls:
                yield {"type": "tool_call_delta", "tool_call": tool_call}

            finish_reason = choice.get("finish_reason")
            if finish_reason:
                yield {
                    "type": "message_end",
                    "stop_reason": _map_finish_reason(str(finish_reason)),
                }

        usage = chunk.get("usage")
        if isinstance(usage, dict):
            yield {"type": "usage", "usage": Usage.from_mapping(usage).to_dict()}


_REDACTED = "<redacted>"
# Header keys whose values must never reach the debug dump. Keys are matched
# lower-cased and their values replaced by the _REDACTED placeholder.
_SENSITIVE_HEADER_KEYS = frozenset(
    {"authorization", "proxy-authorization", "x-api-key", "cookie", "set-cookie"}
)
# Providers whose chat-completions endpoint accepts the OpenAI-style
# "reasoning_effort" field. ZCode gates this per model via the provider
# registry's optionSpecs.reasoningLevel whitelist
# (provider-registry-selection.ts:173-182); nexusCLI has no model
# capability registry, so the gate is provider-level and deliberately
# narrow: anything not listed here never sees the field.
_REASONING_EFFORT_PROVIDERS = frozenset({"openai", "openai-compatible", "compatible"})


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Copy ``headers`` with every sensitive value replaced by ``_REDACTED``."""
    return {
        key: _REDACTED if key.lower() in _SENSITIVE_HEADER_KEYS else value
        for key, value in headers.items()
    }


def _append_dump(record: dict[str, Any]) -> None:
    """Append one JSON line to ~/.nexuscli/debug/llm/<date>.jsonl.

    Both the home directory and the date are resolved per call (same seam as
    ``bg_tasks.BgSubagentRegistry._bg_dir``) so tests can redirect Path.home()
    via HOME/USERPROFILE. Best-effort by design: a dump failure must never
    interrupt a chat turn, so every OSError is swallowed (mirrors
    ``config._read_json``'s tolerance).
    """
    try:
        path = (
            Path.home()
            / ".nexuscli"
            / "debug"
            / "llm"
            / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


async def _iter_sse(response: httpx.Response) -> AsyncIterator[str]:
    buffer = ""
    async for text in response.aiter_text():
        # SSE allows \r\n, \r, or \n separators; normalize so CRLF streams split.
        buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            data_lines = []
            for line in event.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            if data_lines:
                yield "\n".join(data_lines)
    if buffer.strip():
        data_lines = []
        for line in buffer.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            yield "\n".join(data_lines)


def _map_finish_reason(reason: str) -> str:
    if reason in {"tool_calls", "tool_use"}:
        return "tool_use"
    if reason == "length":
        return "max_tokens"
    if reason == "content_filter":
        return "stop_sequence"
    return "end_turn"


def _http_error_message(provider_name: str, exc: httpx.HTTPStatusError) -> str:
    """Error text for a non-2xx chat response.

    Single source for both the transient-fallback classification in
    ``_stream_with_fallback`` and the yielded error event in ``chat``:
    downstream retry logic (planner / orchestrator) classifies the yielded
    text via ``is_transient_api_error``, so the two must stay identical.

    On top of the fixed prefix, appends a one-line summary of the response
    body (see ``_response_body_summary``): the status code alone hides the
    actual cause (2026-09-30 real-gateway smoke test: a wrong model name
    surfaced as a bare 404 while the body carried "model_not_found: No
    active credentials for provider: openai"). The summary is appended
    AFTER the "API returned HTTP <code>" token, so ``is_transient_api_error``'s
    substring match is untouched, and the 300-char cap sits far above the
    prefix length, so truncation can never cut the token away either.
    """
    base = (
        f"{provider_name} API returned HTTP {exc.response.status_code}. "
        "Check the API key, model access, account balance, and provider status."
    )
    summary = _response_body_summary(exc.response)
    if not summary:
        return base
    return _one_line(f"{base} Response body: {summary}")[:_HTTP_ERROR_MESSAGE_LIMIT]


async def _raise_for_status_with_body(response: httpx.Response) -> None:
    """``response.raise_for_status()``, but with the error body attached.

    On a rejected status the SSE body has not been consumed yet, and the
    enclosing ``async with`` closes the stream on the way out — so this reads
    the body here, while the stream is still open, before re-raising. The
    handlers outside can then feed ``response.text`` into
    ``_http_error_message``. A failed body read is suppressed: the
    status-code error must surface either way, just without the detail.
    """
    try:
        response.raise_for_status()
        return
    except httpx.HTTPStatusError:
        with contextlib.suppress(Exception):
            await response.aread()
        raise


_HTTP_ERROR_MESSAGE_LIMIT = 300
_RESPONSE_BODY_SUMMARY_LIMIT = 200


def _response_body_summary(response: httpx.Response) -> str:
    """Best-effort one-line summary of an error response body.

    Prefers the gateway's structured ``error.code`` + ``error.message``
    fields ("model_not_found: No active credentials ..."); anything else —
    non-JSON bodies, JSON without that shape — degrades to the first 200
    characters of the raw text. Every failure (unread or undecodable body)
    yields "" so the summary can never mask the status error itself.
    """
    try:
        raw = response.text
    except Exception:
        return ""
    if not raw:
        return ""
    detail = ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
        error = parsed["error"]
        parts = [str(part) for part in (error.get("code"), error.get("message")) if part]
        detail = ": ".join(parts)
    if not detail:
        detail = raw.strip()
    return _one_line(detail)[:_RESPONSE_BODY_SUMMARY_LIMIT]


def _one_line(text: str) -> str:
    """Replace line breaks (CRLF / CR / LF) with spaces, mirroring _iter_sse."""
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ")
