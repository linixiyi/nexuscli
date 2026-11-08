"""Local LLM gateway clients (provider="ollama" / "llamacpp" / "local").

Machine-local inference servers (Ollama, llama.cpp's ``llama-server``, vLLM,
LM Studio, ...) speak the OpenAI chat-completions + SSE protocol that
:class:`OpenAICompatibleClient` already implements, so payload shape, streaming,
tool calls, reasoning deltas, usage parsing and the built-in non-streaming
fallback are inherited unchanged. The subclass exists for the two places the
cloud parent assumes a hosted, key-gated API:

- local servers usually run without credentials, so a missing API key is NOT
  an error (the cloud parent yields an error event), and the ``Authorization``
  header is only sent when a key was actually configured — an empty or
  placeholder bearer token is rejected by some gateways and would otherwise
  leak into local audit logs and proxies;
- the model-listing probe (:meth:`LocalProviderClient.list_models`) reuses the
  shared :func:`fetch_available_models` (no-key tolerant, capability-aware)
  but turns a failed probe into a :class:`ModelFetchError` whose reason names
  the likely fix — ``ollama serve`` / ``llama-server`` — instead of the cloud
  parent's silent ``[]``, which for a local server without a key is the normal
  state and must still be able to list models.

The ``chat`` override is deliberately explicit rather than parameterized: the
parent's no-key guard and unconditional Authorization header live inline in
``chat``. Wire constants (default base URLs) live in the factory next to the
other providers' endpoints; this module owns client behaviour only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from nexuscli.llm.openai_compatible import (
    ModelFetchError,
    OpenAICompatibleClient,
    _append_dump,
    _http_error_message,
    _redact_headers,
    fetch_available_models,
)
from nexuscli.observability import get_trace_id
from nexuscli.telemetry import chat_span
from nexuscli.types import Message

# How to start each known local server, injected into connection-failure
# wording so "server not started" is actionable. The unified "local" alias and
# custom gateways fall back to the generic hint.
LOCAL_SERVER_HINTS = {
    "ollama": "`ollama serve`",
    "llamacpp": "`llama-server`",
}


class LocalProviderClient(OpenAICompatibleClient):
    """OpenAI-compatible client for machine-local gateways — no key required."""

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

        Local override of the cloud parent: no API key is required, the
        Authorization header is only sent when one was configured, and
        connection failures carry a "start the server" hint. Everything else
        — payload building, SSE parsing, tool-call mapping, usage buffering,
        the non-streaming fallback — is the parent's machinery (see
        ``_stream_with_fallback``), called with the same fixed event contract.
        """
        payload = self._build_payload(messages, tools, system_prompt=system_prompt)
        headers = {
            "content-type": "application/json",
            "user-agent": "NexusCLI-Python/0.1.0",
        }
        # Authorization only when a key was actually configured: local
        # gateways reject empty bearer tokens and a placeholder header would
        # leak into local audit logs / proxies.
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        # Correlate the request with the session's audit trail (same seam as
        # the cloud parent).
        trace_id = get_trace_id()
        if trace_id:
            headers["x-request-id"] = trace_id
        url = self.base_url.rstrip("/") + "/chat/completions"

        # Debug-dump sidecar (opt-in), mirroring the cloud parent's records.
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
        text_parts: list[str] = []
        tool_calls: list[Any] = []
        dump_error: str | None = None
        with chat_span(self.telemetry_enabled, provider=self.provider_name, model=self.model):
            try:
                async for parsed in self._stream_with_fallback(url, headers, payload):
                    # Streamed usage is a running total, not a delta — keep
                    # only the last one and emit it once (same as the parent).
                    if parsed.get("type") == "usage":
                        pending_usage = parsed
                    else:
                        if parsed.get("type") == "text_delta":
                            text_parts.append(str(parsed.get("text") or ""))
                        elif parsed.get("type") == "tool_call_delta":
                            tool_calls.append(parsed.get("tool_call"))
                        yield parsed
            except httpx.TimeoutException:
                dump_error = f"{self.provider_name} request timed out after {self.timeout:g}s"
                yield {
                    "type": "error",
                    "error": RuntimeError(
                        f"{self.provider_name} request timed out after {self.timeout:g}s. "
                        "Check that the local server is responsive, then retry."
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
                        f"{_connection_hint(self.provider_name)}"
                    ),
                }
                return
            if pending_usage is not None:
                yield pending_usage

        # Debug-dump sidecar response record (same shape as the parent; the
        # early-returning connection-error branch skips it by design there too).
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

    async def list_models(self) -> list[str]:
        """Fetch available model IDs from the local gateway's ``/models`` endpoint.

        Delegates to the shared :func:`fetch_available_models` — which needs no
        API key and picks up capability blocks from the listing — instead of
        the cloud parent, which returns ``[]`` whenever the key is empty and
        swallows every failure. A failed probe raises :class:`ModelFetchError`
        whose reason is user-readable: connection failures carry the
        start-server hint, so /model can say why the listing failed.
        """
        try:
            listings = await asyncio.to_thread(fetch_available_models, self.base_url, self.api_key)
        except ModelFetchError as exc:
            raise ModelFetchError(_listing_failure_reason(self.provider_name, exc.reason)) from exc
        # Sorted like the cloud parent's list_models, so the contract stays
        # interchangeable even though the shared prober preserves gateway order.
        return sorted(listing.model for listing in listings)


def _connection_hint(provider_name: str) -> str:
    """Readable tail for a chat connection failure against a local server."""
    hint = LOCAL_SERVER_HINTS.get(provider_name.lower())
    if not hint:
        return "Check that the local server is running and the port is correct, then retry."
    return f"Is the local server running? Start it with {hint}, then retry."


def _listing_failure_reason(provider_name: str, reason: str) -> str:
    """Append the start-server hint to a model-listing connection failure.

    Only connection failures get the hint (the shared prober's reason starts
    with its ``无法连接`` wording); HTTP-status and payload-shape reasons are
    already specific and pass through unchanged.
    """
    if not reason.startswith("无法连接"):
        return reason
    hint = LOCAL_SERVER_HINTS.get(provider_name.lower(), "先启动本地推理服务")
    return f"{reason}——本地服务未启动？先运行 {hint}，再重试"
