"""Tests for surfacing gateway error bodies in HTTP error messages.

Background (2026-09-30 real-gateway smoke test): a wrong model name made the
gateway answer 404 with ``{"error": {"message": "No active credentials for
provider: openai", "code": "model_not_found"}}``, but the CLI only reported
"API returned HTTP 404. Check the API key..." — the actual cause stayed in
the body. Both chat backends now read the error body while the stream is
still open (``_raise_for_status_with_body``) and append a one-line summary
(``error.code``/``error.message``, else the raw body head) after the fixed
prefix, capped at 300 chars.

The stubs serve REAL ``httpx.Response`` objects with an unread async body
stream, mirroring the genuine flow: ``raise_for_status`` fires before the
body is consumed, so ``response.text`` raises ``ResponseNotRead`` unless the
client read it in time. No test performs a real network request (same seam
as tests/test_stream_fallback.py).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest

from nexuscli.llm.anthropic import AnthropicClient
from nexuscli.llm.base import is_transient_api_error
from nexuscli.llm.openai_compatible import (
    OpenAICompatibleClient,
    _http_error_message,
    _raise_for_status_with_body,
)
from nexuscli.types import Message


def _placeholder_api_key() -> str:
    """Placeholder credential for tests; env-overridable, never a real secret."""
    return os.environ.get("NEXUSCLI_UNITTEST_LLM_CREDENTIAL") or "unit-test-key-123"


_CHAT_URL = "https://api.deepseek.com/v1/chat/completions"

# The smoke-test gateway body: error.code + error.message, verbatim shape.
_MODEL_NOT_FOUND_BODY = json.dumps(
    {
        "error": {
            "message": "No active credentials for provider: openai",
            "type": "invalid_request_error",
            "code": "model_not_found",
        }
    }
)

_COMPLETION_SINGLE_TEXT = {
    "choices": [
        {
            "message": {"role": "assistant", "content": "Hello world"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


class _ErrorBodyStream(httpx.AsyncByteStream):
    """Async byte stream serving one body chunk (or raising, to simulate a
    connection that dies before the error body arrives)."""

    def __init__(self, body: str, error: Exception | None = None) -> None:
        self._body = body.encode("utf-8")
        self._error = error

    async def __aiter__(self):
        if self._error is not None:
            raise self._error
        yield self._body


class _FakeStream:
    """Async context manager mirroring client.stream(...)."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> httpx.Response:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _Transport:
    """Stub for httpx.AsyncClient.stream/.post — records calls, never a network.

    ``stream_status``/``stream_body`` shape the rejected streaming response;
    ``post_response`` is what a non-stream replay would get back (absent =
    replay is unexpected and fails the test).
    """

    def __init__(
        self,
        *,
        stream_status: int,
        stream_body: str,
        stream_body_error: Exception | None = None,
        post_response: httpx.Response | None = None,
    ) -> None:
        self.stream_calls = 0
        self.post_calls = 0
        self._stream_status = stream_status
        self._stream_body = stream_body
        self._stream_body_error = stream_body_error
        self._post_response = post_response

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        transport = self

        def fake_stream(client, *args, **kwargs):
            transport.stream_calls += 1
            response = httpx.Response(
                transport._stream_status,
                headers={"content-type": "application/json"},
                stream=_ErrorBodyStream(transport._stream_body, transport._stream_body_error),
                request=httpx.Request("POST", _CHAT_URL),
            )
            return _FakeStream(response)

        async def fake_post(client, *args, **kwargs):
            transport.post_calls += 1
            if transport._post_response is not None:
                return transport._post_response
            raise AssertionError("unexpected non-stream POST")

        monkeypatch.setattr(httpx.AsyncClient, "stream", fake_stream)
        monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


def _openai_client() -> OpenAICompatibleClient:
    # Placeholder credential routed through the established env-fallback
    # helper pattern (see tests/test_llm_io_dump.py) — never a real key.
    return OpenAICompatibleClient(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        api_key=_placeholder_api_key(),
        base_url="https://api.deepseek.com/v1",
    )


def _anthropic_client() -> AnthropicClient:
    # `<api-key>` is the mandated placeholder — never a real credential.
    return AnthropicClient(model="claude-sonnet-4-5", api_key="<api-key>")


async def _collect_chat_events(client: Any) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content="hello")],
            [],
            system_prompt="system",
        )
    ]


def _last_error_text(events: list[dict[str, Any]]) -> str:
    assert events[-1]["type"] == "error"
    return str(events[-1]["error"])


def _read_status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    """HTTPStatusError raised the production way, via _raise_for_status_with_body."""
    response = httpx.Response(
        status_code,
        headers={"content-type": "application/json"},
        stream=_ErrorBodyStream(body),
        request=httpx.Request("POST", _CHAT_URL),
    )

    async def raise_it() -> None:
        await _raise_for_status_with_body(response)

    try:
        asyncio.run(raise_it())
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError(f"HTTP {status_code} did not raise")


# ---------------------------------------------------------------------------
# openai_compatible: the yielded error event carries the body detail.


def test_404_json_body_surfaces_code_and_message(monkeypatch) -> None:
    """a) The smoke-test 404: message keeps the hints and gains the body cause."""
    transport = _Transport(stream_status=404, stream_body=_MODEL_NOT_FOUND_BODY)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 0  # 404 is not transient → no replay
    assert [event["type"] for event in events] == ["message_start", "error"]
    message = _last_error_text(events)
    assert "deepseek API returned HTTP 404." in message
    assert "Check the API key, model access, account balance, and provider status." in message
    assert "model_not_found: No active credentials for provider: openai" in message


def test_non_json_body_appends_raw_head_single_lined(monkeypatch) -> None:
    """b) Unparseable body → the raw head is appended, line breaks flattened.

    400 is deliberately non-transient: the error event is reached directly
    instead of through the non-stream replay.
    """
    body = "<html>\r\n  <h1>request rejected</h1>\n  <p>fix your query</p>\r\n</html>"
    transport = _Transport(stream_status=400, stream_body=body)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    message = _last_error_text(events)
    # Only line breaks become spaces; the body's own indentation survives.
    assert (
        "Response body: <html>   <h1>request rejected</h1>   <p>fix your query</p> </html>"
        in message
    )
    assert "\n" not in message and "\r" not in message


def test_json_without_error_shape_degrades_to_raw_head(monkeypatch) -> None:
    """c) JSON lacking error.code/error.message is treated as raw text."""
    transport = _Transport(stream_status=400, stream_body='{"detail": "boom"}')
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    message = _last_error_text(events)
    assert "Response body: " + '{"detail": "boom"}' in message


def test_message_is_single_line_and_capped_at_300(monkeypatch) -> None:
    """d) Huge padded body: message stays one line, within the 300-char cap."""
    body = "x" * 1000 + "\n" + "y" * 1000
    transport = _Transport(stream_status=400, stream_body=body)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    message = _last_error_text(events)
    assert len(message) <= 300
    assert "\n" not in message
    assert message.startswith("deepseek API returned HTTP 400.")


def test_unreadable_error_body_degrades_to_status_only(monkeypatch) -> None:
    """e) Body read failing mid-stream: status error still surfaces, no crash."""
    transport = _Transport(
        stream_status=404,
        stream_body=_MODEL_NOT_FOUND_BODY,
        stream_body_error=httpx.ReadError("connection reset while reading body"),
    )
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    assert [event["type"] for event in events] == ["message_start", "error"]
    message = _last_error_text(events)
    assert "deepseek API returned HTTP 404." in message
    assert "model_not_found" not in message


def test_transient_503_with_body_still_replays_non_stream(monkeypatch) -> None:
    """f) The appended body must not break the transient fallback: a 503 whose
    body carries no HTTP token still classifies transient and replays once."""
    transport = _Transport(
        stream_status=503,
        stream_body=_MODEL_NOT_FOUND_BODY,
        post_response=httpx.Response(
            200, json=_COMPLETION_SINGLE_TEXT, request=httpx.Request("POST", _CHAT_URL)
        ),
    )
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_openai_client()))

    assert transport.stream_calls == 1
    assert transport.post_calls == 1
    assert [event["type"] for event in events] == [
        "message_start",
        "text_delta",
        "message_end",
        "usage",
    ]


# ---------------------------------------------------------------------------
# Transient classification on the new, body-appended text.


def test_is_transient_api_error_still_matches_appended_body_text() -> None:
    """g) The 'HTTP 4xx/5xx' prefix matching survives the appended summary."""
    assert is_transient_api_error(
        _http_error_message("deepseek", _read_status_error(503, _MODEL_NOT_FOUND_BODY))
    )
    assert is_transient_api_error(
        _http_error_message("deepseek", _read_status_error(429, "rate limited"))
    )

    assert not is_transient_api_error(
        _http_error_message("deepseek", _read_status_error(404, _MODEL_NOT_FOUND_BODY))
    )
    assert not is_transient_api_error(
        _http_error_message("deepseek", _read_status_error(401, "bad key"))
    )


# ---------------------------------------------------------------------------
# anthropic: the same enhancement on its HTTPStatusError handler. Note the
# Anthropic client opens with no event of its own, so the error is the only
# event on a rejected request.


def test_anthropic_404_json_body_surfaces_code_and_message(monkeypatch) -> None:
    transport = _Transport(stream_status=404, stream_body=_MODEL_NOT_FOUND_BODY)
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_anthropic_client()))

    assert transport.stream_calls == 1
    assert [event["type"] for event in events] == ["error"]
    message = _last_error_text(events)
    assert "anthropic API returned HTTP 404." in message
    assert "model_not_found: No active credentials for provider: openai" in message


def test_anthropic_non_json_body_appends_raw_head(monkeypatch) -> None:
    transport = _Transport(stream_status=529, stream_body="site is overloaded\nplease retry")
    transport.install(monkeypatch)

    events = asyncio.run(_collect_chat_events(_anthropic_client()))

    message = _last_error_text(events)
    assert "anthropic API returned HTTP 529." in message
    assert "Response body: site is overloaded please retry" in message
    assert "\n" not in message
