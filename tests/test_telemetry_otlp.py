"""Tests for the optional OTLP/HTTP export (``telemetry.otlp``).

The opentelemetry-exporter-otlp-proto-http package ships in the dev extra:
the export tests drive the REAL exporter against an in-process fake OTLP/HTTP
receiver (``http.server`` thread bound to 127.0.0.1 on an ephemeral port) —
the only network involved is loopback to the test's own socket. Degradation
paths are exercised without any real network: the missing-dependency case
blocks the lazy import through a ``None`` ``sys.modules`` entry (from-import
then raises ModuleNotFoundError with the module as ``exc.name``), and the
unreachable-endpoint case points at a just-closed port.

The config self-read is replaced through the
``nexuscli.telemetry.otlp._load_config`` seam (same discipline as
``tracing._sdk_available``) so these tests never depend on the repo's or the
machine's real config files. The httpx stub mirrors
tests/test_telemetry.py:30-99 — no real LLM calls, no non-loopback traffic.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import threading
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import nexuscli.telemetry.otlp as otlp_module
from nexuscli import observability
from nexuscli.config import (
    NexusCliConfig,
    TelemetryConfig,
    TelemetryOtlpConfig,
    get_resolved_telemetry_config,
    load_config,
    load_telemetry_config,
)
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.telemetry import (
    OtlpDependencyError,
    chat_span,
    configure_tracer_provider,
    reset_tracer_provider,
    tool_call_span,
)
from nexuscli.tools.base import Tool, ToolContext, ToolResult, object_schema
from nexuscli.tools.executor import ToolExecutor
from nexuscli.tools.registry import ToolRegistry
from nexuscli.types import Message

_EXPORTER_MODULE = "opentelemetry.exporter.otlp.proto.http.trace_exporter"


# ---------------------------------------------------------------------------
# In-process fake OTLP/HTTP receiver (loopback only, ephemeral port)
# ---------------------------------------------------------------------------


class _FakeOtlpReceiver:
    """Threaded HTTP receiver recording POST bodies and request headers."""

    def __init__(self) -> None:
        self.requests: list[tuple[bytes, dict[str, str]]] = []
        self._lock = threading.Lock()
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                with receiver._lock:
                    receiver.requests.append((body, dict(self.headers)))
                # An empty ExportTraceServiceResponse parses as a valid ack.
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()
                self.wfile.write(b"")

            def log_message(self, *_args: Any) -> None:
                return None

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1/traces"

    def parsed_spans(self) -> list[Any]:
        """Decode every recorded POST body into a flat list of proto spans."""
        with self._lock:
            bodies = [body for body, _headers in self.requests]
        spans: list[Any] = []
        for body in bodies:
            request = ExportTraceServiceRequest()
            request.ParseFromString(body)
            for resource_spans in request.resource_spans:
                for scope_spans in resource_spans.scope_spans:
                    spans.extend(scope_spans.spans)
        return spans

    def header(self, name: str) -> str | None:
        """Case-insensitive lookup over recorded request headers."""
        with self._lock:
            headers = [entry for _body, entry in self.requests]
        for entry in headers:
            for key, value in entry.items():
                if key.lower() == name.lower():
                    return value
        return None

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


class _SpanSurface:
    """Injected provider plus the in-memory exporter for span-semantics checks."""

    def __init__(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))


@pytest.fixture
def surface() -> Any:
    """Inject the test provider and give the OTLP wiring a pristine state."""
    state = _SpanSurface()
    configure_tracer_provider(state.provider)
    otlp_module._reset_state_for_tests()
    previous_trace_id = observability.get_trace_id()
    try:
        yield state
    finally:
        reset_tracer_provider()
        otlp_module._reset_state_for_tests()
        observability.set_trace_id(previous_trace_id)
        # Shutdown unregisters the provider's atexit hook and drains the
        # BatchSpanProcessor so no export thread outlives the test.
        with contextlib.suppress(Exception):
            state.provider.shutdown()


@pytest.fixture
def receiver() -> Any:
    fake = _FakeOtlpReceiver()
    yield fake
    fake.close()


def _otlp_config(endpoint: str, *, headers: dict[str, str] | None = None, timeout: float = 0.5):
    return TelemetryConfig(
        enabled=True,
        otlp=TelemetryOtlpConfig(
            endpoint=endpoint,
            headers=headers if headers is not None else {},
            timeout=timeout,
        ),
    )


def _stub_otlp_config(monkeypatch: pytest.MonkeyPatch, config: TelemetryConfig) -> None:
    """Route the one-shot self-read through the seam (never real config files)."""
    monkeypatch.setattr(otlp_module, "_load_config", lambda: config)


def _telemetry_warnings(caught: Any) -> list[Any]:
    """Only the degradation warnings this suite asserts on.

    ``catch_warnings(record=True)``/``pytest.warns`` capture EVERY warning
    routed through the module — in a full-suite run that includes unrelated
    Resource/DeprecationWarnings from other tests' objects garbage-collected
    inside this test's window. The once-per-process budget assertions must
    count only the ``[nexuscli:telemetry]`` messages.
    """
    return [
        warning
        for warning in caught
        if warning.category is UserWarning and "nexuscli:telemetry" in str(warning.message)
    ]


@pytest.fixture
def closed_endpoint() -> Any:
    """An endpoint that cannot be reached, without a TOCTOU window.

    The socket stays bound for the whole test but is never ``listen()``ed, so
    the port cannot be claimed by another process (no bind→close→use race)
    and every connect attempt fails for as long as the test runs: the kernel
    answers the SYN without a listener by dropping it (connect times out —
    observed on darwin 27) or resetting it (ECONNREFUSED — Linux), bounded by
    the exporter's own configured timeout either way.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    yield f"http://127.0.0.1:{sock.getsockname()[1]}/v1/traces"
    sock.close()


def _echo_registry() -> ToolRegistry:
    async def echo_ok(data, context):
        return ToolResult("echo ok")

    registry = ToolRegistry()
    registry.register(
        Tool(
            name="echo_ok",
            description="echo tool",
            parameters=object_schema({}, []),
            handler=echo_ok,
            is_read_only=True,
        )
    )
    return registry


def _run_one_tool_call(tmp_path, *, telemetry_config: TelemetryConfig) -> list[ToolResult]:
    config = NexusCliConfig()
    config.telemetry = telemetry_config
    config.policy.audit_log_path = str(tmp_path / "audit.jsonl")
    executor = ToolExecutor(_echo_registry())
    calls = [{"id": "call-1", "name": "echo_ok", "arguments": {}}]
    context = ToolContext(cwd=str(tmp_path), config=config)
    return asyncio.run(executor.execute_all(calls, context))


# httpx stub (local copy of the tests/test_telemetry.py:67-99 patch point)
_SSE_FRAMES = [
    'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
    'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
    "data: [DONE]\n\n",
]


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    async def aiter_text(self):
        for frame in _SSE_FRAMES:
            yield frame


class _FakeStream:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _fake_stream(*_args: Any, **_kwargs: Any) -> _FakeStream:
    return _FakeStream(_FakeResponse())


def _client(telemetry_enabled: bool) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="deepseek",
        model="deepseek-v4-flash",
        api_key="<api-key>",  # placeholder discipline: never a real credential
        base_url="https://api.example.test/v1",
        telemetry_enabled=telemetry_enabled,
    )


async def _collect_chat_events(client: OpenAICompatibleClient) -> list[dict[str, Any]]:
    return [
        event
        async for event in client.chat(
            [Message(role="user", content="hello")],
            [],
            system_prompt="system",
        )
    ]


# ---------------------------------------------------------------------------
# Config face (telemetry.otlp three keys, default off)
# ---------------------------------------------------------------------------


def test_otlp_config_face_defaults_and_round_trip() -> None:
    assert TelemetryConfig().otlp == TelemetryOtlpConfig()
    assert TelemetryOtlpConfig().endpoint == ""
    assert TelemetryOtlpConfig().headers == {}
    assert TelemetryOtlpConfig().timeout is None
    assert NexusCliConfig().telemetry.otlp.endpoint == ""
    assert load_config(overrides={}, env={}).telemetry.otlp.endpoint == ""

    # Unknown keys inside the otlp section are silently dropped, bad value
    # types fall back to the default (one bad entry cannot break the CLI).
    bogus = load_config(
        overrides={"telemetry": {"otlp": {"endpoint": 5, "headers": "nope", "timeout": "x"}}},
        env={},
    )
    assert bogus.telemetry.otlp == TelemetryOtlpConfig()
    dropped = load_config(
        overrides={"telemetry": {"enabled": True, "otlp": {"endpoint": "http://x/", "bogus": 1}}},
        env={},
    )
    assert dropped.telemetry.otlp.endpoint == "http://x/"
    assert tuple(dropped.telemetry.otlp.__dataclass_fields__) == ("endpoint", "headers", "timeout")

    full = load_config(
        overrides={
            "telemetry": {
                "otlp": {
                    "endpoint": "https://cloud.langfuse.example/api/public/otel/v1/traces",
                    "headers": {"Authorization": "Basic dGVzdA=="},
                    "timeout": 2.5,
                }
            }
        },
        env={},
    )
    assert (
        full.telemetry.otlp.endpoint == "https://cloud.langfuse.example/api/public/otel/v1/traces"
    )
    assert full.telemetry.otlp.headers == {"Authorization": "Basic dGVzdA=="}
    assert full.telemetry.otlp.timeout == 2.5


def test_load_telemetry_config_layers_like_load_config(tmp_path) -> None:
    (tmp_path / ".nexuscli").mkdir()
    (tmp_path / ".nexuscli" / "config.json").write_text(
        json.dumps(
            {
                "telemetry": {
                    "enabled": True,
                    "otlp": {
                        "endpoint": "https://project.example/v1/traces",
                        "headers": {"Authorization": "Basic project"},
                        "timeout": 3,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    face = load_telemetry_config(project_root=tmp_path)
    assert face.enabled is True
    assert face.otlp.endpoint == "https://project.example/v1/traces"
    assert face.otlp.headers == {"Authorization": "Basic project"}
    assert face.otlp.timeout == 3.0
    # Parity guard: the full loader resolves the very same telemetry face.
    assert load_config(project_root=tmp_path, env={}).telemetry == face


def test_wiring_consumes_load_config_resolved_face(
    surface, receiver, monkeypatch, tmp_path
) -> None:
    """The CLI may resolve its project root from --cwd (cli.py:
    (cwd or Path.cwd()).resolve()), so the lazy wiring must consume the face
    load_config resolved — never a fresh read at the process cwd, which could
    silently export to an endpoint configured in ANOTHER directory's project
    config while missing the intended one."""
    # The project the CLI points at (--cwd analogue): the intended endpoint.
    real_root = tmp_path / "real"
    (real_root / ".nexuscli").mkdir(parents=True)
    (real_root / ".nexuscli" / "config.json").write_text(
        json.dumps(
            {
                "telemetry": {
                    "enabled": True,
                    "otlp": {"endpoint": receiver.endpoint, "timeout": 0.5},
                }
            }
        ),
        encoding="utf-8",
    )
    # The process-cwd project that must NOT leak into the wiring.
    cwd_root = tmp_path / "leak"
    (cwd_root / ".nexuscli").mkdir(parents=True)
    (cwd_root / ".nexuscli" / "config.json").write_text(
        json.dumps({"telemetry": {"otlp": {"endpoint": "http://cwd-leak.example/v1/traces"}}}),
        encoding="utf-8",
    )
    # Snapshot the resolved-face slot so whatever earlier suite tests left
    # there is restored after this one.
    monkeypatch.setattr(
        "nexuscli.config._resolved_telemetry",
        get_resolved_telemetry_config(),
    )
    monkeypatch.chdir(cwd_root)  # process cwd = the leak project
    resolved = load_config(project_root=real_root, env={})  # --cwd analogue
    assert resolved.telemetry.otlp.endpoint == receiver.endpoint

    # No seam stub here: production shape — the wiring must go through the
    # resolved face. (A cwd read would wire the leak project's DNS-dead
    # domain instead, fail the export and warn.) Zero warnings + export
    # arriving at the real receiver prove the resolved face won.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = _run_one_tool_call(tmp_path, telemetry_config=resolved.telemetry)
        assert [result.is_error for result in results] == [False]
        assert surface.provider.force_flush() is True
    assert _telemetry_warnings(caught) == []
    exported = receiver.parsed_spans()
    assert [span.name for span in exported] == ["tool.call"]
    attributes = {attr.key: attr.value.string_value for attr in exported[0].attributes}
    assert attributes["nexuscli.tool.name"] == "echo_ok"


# ---------------------------------------------------------------------------
# Default off: zero awareness, zero cost
# ---------------------------------------------------------------------------


def test_default_off_is_zero_cost(surface, monkeypatch, tmp_path) -> None:
    # Master on but otlp.endpoint empty (the default face): the wiring must
    # return without importing the exporter at all.
    _stub_otlp_config(monkeypatch, TelemetryConfig(enabled=True))
    monkeypatch.delitem(sys.modules, _EXPORTER_MODULE, raising=False)
    assert otlp_module.ensure_otlp_export() is False
    assert _EXPORTER_MODULE not in sys.modules

    # Master off: chat_span never even consults the wiring.
    _stub_otlp_config(
        monkeypatch,
        _otlp_config("http://127.0.0.1:9/v1/traces"),  # endpoint configured...
    )
    with chat_span(False, provider="deepseek", model="deepseek-v4-flash"):
        pass
    with tool_call_span(False, tool_name="echo_ok"):
        pass
    # ...yet nothing was imported or recorded: the runtime switch wins.
    assert _EXPORTER_MODULE not in sys.modules
    assert len(surface.exporter.get_finished_spans()) == 0


# ---------------------------------------------------------------------------
# Export path: real exporter → fake collector
# ---------------------------------------------------------------------------


def test_tool_span_export_reaches_fake_collector(surface, receiver, monkeypatch, tmp_path) -> None:
    config = _otlp_config(receiver.endpoint, headers={"Authorization": "Basic test-key"})
    _stub_otlp_config(monkeypatch, config)
    observability.set_trace_id("trace-otlp-1")

    results = _run_one_tool_call(tmp_path, telemetry_config=config)

    assert [result.is_error for result in results] == [False]
    # Span semantics unchanged: the same tool.call span the in-memory
    # processor has always seen still exists, with the same attributes.
    memory_spans = surface.exporter.get_finished_spans()
    assert len(memory_spans) == 1
    assert memory_spans[0].name == "tool.call"
    assert memory_spans[0].attributes["nexuscli.trace_id"] == "trace-otlp-1"
    assert memory_spans[0].attributes["nexuscli.tool.name"] == "echo_ok"

    assert surface.provider.force_flush() is True
    exported = receiver.parsed_spans()
    assert [span.name for span in exported] == ["tool.call"]
    attributes = {attr.key: attr.value.string_value for attr in exported[0].attributes}
    assert attributes["nexuscli.trace_id"] == "trace-otlp-1"
    assert attributes["nexuscli.tool.name"] == "echo_ok"
    assert attributes["nexuscli.tool.outcome"] == "ok"
    # Auth headers from telemetry.otlp.headers travel with every request
    # (the OTLP exporter lowercases header names on the wire).
    assert receiver.header("Authorization") == "Basic test-key"


def test_chat_span_export_reaches_fake_collector(surface, receiver, monkeypatch, tmp_path) -> None:
    config = _otlp_config(receiver.endpoint)
    _stub_otlp_config(monkeypatch, config)
    observability.set_trace_id("trace-otlp-2")
    monkeypatch.setattr(httpx.AsyncClient, "stream", _fake_stream)

    events = asyncio.run(_collect_chat_events(_client(telemetry_enabled=True)))

    assert [event["type"] for event in events] == ["message_start", "text_delta", "usage"]
    assert surface.provider.force_flush() is True
    exported = receiver.parsed_spans()
    assert [span.name for span in exported] == ["llm.chat"]
    attributes = {attr.key: attr.value.string_value for attr in exported[0].attributes}
    assert attributes["nexuscli.trace_id"] == "trace-otlp-2"
    assert attributes["nexuscli.llm.model"] == "deepseek-v4-flash"
    assert attributes["nexuscli.llm.provider"] == "deepseek"


def test_timeout_is_configured_in_seconds(monkeypatch) -> None:
    """Pin the unit contract: telemetry.otlp.timeout (seconds) passes through."""
    seen: dict[str, Any] = {}

    class _RecordingExporter:
        """Records constructor kwargs; full no-op SpanExporter protocol so the
        BatchSpanProcessor's shutdown path stays clean at teardown."""

        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

        def export(self, spans: Any) -> Any:  # pragma: no cover - never flushed
            return None

        def shutdown(self) -> Any:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> Any:
            return True

    monkeypatch.setattr(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
        _RecordingExporter,
    )
    config = _otlp_config("http://127.0.0.1:9/v1/traces", timeout=2.5)
    _stub_otlp_config(monkeypatch, config)

    assert otlp_module.ensure_otlp_export() is True
    assert seen["endpoint"] == "http://127.0.0.1:9/v1/traces"
    assert seen["timeout"] == 2.5
    assert seen["headers"] == {}


# ---------------------------------------------------------------------------
# Degradation: missing exporter dependency
# ---------------------------------------------------------------------------


def test_missing_dependency_translates_to_typed_error(monkeypatch) -> None:
    # Unit boundary: the lazy import translates an opentelemetry-family
    # ImportError into the typed error with install guidance...
    monkeypatch.setitem(sys.modules, _EXPORTER_MODULE, None)
    with pytest.raises(OtlpDependencyError, match=r"uv sync --extra telemetry-otlp"):
        otlp_module._import_otlp_dependencies()
    # ...while non-opentelemetry import failures propagate unchanged.
    plain = ImportError("broken", name="urllib3")
    with pytest.raises(ImportError, match="broken") as excinfo:
        otlp_module._raise_translated(plain)
    assert type(excinfo.value) is ImportError
    assert not isinstance(excinfo.value, OtlpDependencyError)


def test_missing_dependency_degrades_to_single_warning(surface, monkeypatch, tmp_path) -> None:
    config = _otlp_config("http://127.0.0.1:9/v1/traces")
    _stub_otlp_config(monkeypatch, config)
    monkeypatch.setitem(sys.modules, _EXPORTER_MODULE, None)

    with pytest.warns(UserWarning, match=r"uv sync --extra telemetry-otlp") as caught:
        assert otlp_module.ensure_otlp_export() is False
        results = _run_one_tool_call(tmp_path, telemetry_config=config)

    # Exactly one process-lifetime warning, tool results unaffected, and the
    # span itself is still produced (only the export is missing).
    assert len(_telemetry_warnings(caught)) == 1
    assert [result.is_error for result in results] == [False]
    assert len(surface.exporter.get_finished_spans()) == 1
    # One-shot: a second wiring attempt stays silent.
    with warnings.catch_warnings(record=True) as caught_again:
        warnings.simplefilter("always")
        assert otlp_module.ensure_otlp_export() is False
    assert _telemetry_warnings(caught_again) == []


# ---------------------------------------------------------------------------
# Degradation: unreachable endpoint
# ---------------------------------------------------------------------------


def test_unreachable_endpoint_degrades_to_single_warning(
    surface, closed_endpoint, monkeypatch, tmp_path
) -> None:
    config = _otlp_config(closed_endpoint, timeout=0.5)
    _stub_otlp_config(monkeypatch, config)

    assert otlp_module.ensure_otlp_export() is True  # wired; export will fail

    with pytest.warns(UserWarning, match="OTLP span export") as caught:
        first = _run_one_tool_call(tmp_path, telemetry_config=config)
        assert surface.provider.force_flush() is True
        second = _run_one_tool_call(tmp_path, telemetry_config=config)
        assert surface.provider.force_flush() is True

    # Two failed export batches, exactly ONE warning for the whole process;
    # tool results and span semantics are untouched (exit-code neutrality is
    # implied by this test finishing green with the CLI-side chains intact).
    assert len(_telemetry_warnings(caught)) == 1
    assert [result.is_error for result in first] == [False]
    assert [result.is_error for result in second] == [False]
    assert len(surface.exporter.get_finished_spans()) == 2
