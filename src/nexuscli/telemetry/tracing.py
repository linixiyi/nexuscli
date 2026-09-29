"""Minimal OpenTelemetry spans for the ``llm.chat`` and ``tool.call`` paths.

Optional-dependency semantics: nothing in this module (or its importers)
requires ``opentelemetry`` to be installed. Every opentelemetry import is
lazy; when the SDK is missing — or ``telemetry.enabled`` is false — every
helper returns a no-op :class:`SpanHandle` and the call sites stay
branch-free. That mirrors ZCode's ``safeCreate(...)`` telemetry calls which
fall back to the ``NOOP_TURN_WRITER`` no-op writer when telemetry is not
ready (apps/zcode-cli/packages/telemetry/src/agent-trace-runtime.ts:154-191;
read-only reference, not ported verbatim).

Deliberately NOT ported from ZCode (minimum slice): the OTLP exporter and any
network export (otlp-exporter.ts), the agent_turn/agent_step/compaction
multi-layer span hierarchy, causation links and cross-process span
propagation, sampling strategies, and the error-sanitizer. Hosts that want
exported spans can install their own global TracerProvider first: without an
injection this module defers to ``opentelemetry.trace.get_tracer(...)``
(default NonRecordingSpan), the standard etiquette for reusable OTel
instrumentations.

Span contract: :meth:`SpanHandle.__exit__` ends the span, so
:meth:`SpanHandle.record_outcome` must be called *inside* the ``with``
block. The SDK silently drops ``set_attribute`` on an already-ended span
(warning "Setting attribute on ended span."; verified on opentelemetry-sdk
1.20 and 1.45).
"""

from __future__ import annotations

from typing import Any

from nexuscli import observability

_TRACER_NAME = "nexuscli"
_OUTCOME_ATTRIBUTE = "nexuscli.tool.outcome"

# Test-only injection point (see configure_tracer_provider). Production code
# never touches it; when it is None the tracer comes from the OTel API global.
_PROVIDER: object | None = None


def _sdk_available() -> bool:
    """Probe for the optional opentelemetry SDK on every call.

    Per-call probing (instead of an import-time cache) keeps this function a
    stable monkeypatch seam: tests patch
    ``nexuscli.telemetry.tracing._sdk_available`` to simulate the SDK being
    absent without touching sys.modules. The failed import is cached inside
    sys.modules by Python itself, so the repeated cost is negligible.
    """
    try:
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401
    except ImportError:
        return False
    return True


def configure_tracer_provider(provider: object) -> None:
    """Test-only seam: inject a tracer provider for span assertions.

    Lets tests attach an SDK TracerProvider wired to an InMemorySpanProcessor
    without touching the process-global ``opentelemetry.trace.
    set_tracer_provider`` (which OTel only allows once per process).
    Production code must never call this.
    """
    global _PROVIDER
    _PROVIDER = provider


def reset_tracer_provider() -> None:
    """Test-only seam: drop the injected provider again (fixture teardown)."""
    global _PROVIDER
    _PROVIDER = None


def _tracer() -> Any:
    if _PROVIDER is not None:
        return _PROVIDER.get_tracer(_TRACER_NAME)
    from opentelemetry import trace

    return trace.get_tracer(_TRACER_NAME)


class SpanHandle:
    """Context manager around one span (or a no-op when telemetry is off).

    ``record_outcome`` must be called inside the ``with`` block: the span
    ends on ``__exit__``, and attributes set afterwards are dropped by the
    SDK (see the module docstring). Inactive handles (telemetry disabled or
    SDK missing) make every operation a silent no-op — no exceptions, no
    behavioural difference for the caller.
    """

    def __init__(self, span_context: Any | None = None) -> None:
        self._span_context = span_context
        self._span: Any = None

    def __enter__(self) -> SpanHandle:
        if self._span_context is not None:
            self._span = self._span_context.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._span_context is not None:
            self._span_context.__exit__(exc_type, exc, tb)
        self._span = None

    def record_outcome(self, outcome: str) -> None:
        """Attach ``nexuscli.tool.outcome`` to the live span; no-op when off."""
        if self._span is not None:
            self._span.set_attribute(_OUTCOME_ATTRIBUTE, outcome)


def chat_span(enabled: bool, *, provider: str, model: str) -> SpanHandle:
    """Open an ``llm.chat`` span around one chat() request/response cycle.

    No-op unless ``enabled`` is True *and* the opentelemetry SDK is
    installed. Attributes: ``nexuscli.trace_id`` (session trace id),
    ``nexuscli.llm.model``, ``nexuscli.llm.provider``.
    """
    if not enabled or not _sdk_available():
        return SpanHandle()
    attributes = {
        "nexuscli.trace_id": observability.get_trace_id(),
        "nexuscli.llm.model": model,
        "nexuscli.llm.provider": provider,
    }
    return SpanHandle(_tracer().start_as_current_span("llm.chat", attributes=attributes))


def tool_call_span(enabled: bool, *, tool_name: str) -> SpanHandle:
    """Open a ``tool.call`` span around one tool execution.

    No-op unless ``enabled`` is True *and* the opentelemetry SDK is
    installed. Attributes: ``nexuscli.trace_id``, ``nexuscli.tool.name``;
    record the result inside the ``with`` block via
    ``handle.record_outcome("ok" | "error")`` (attribute
    ``nexuscli.tool.outcome``). Silent no-op on inactive handles.
    """
    if not enabled or not _sdk_available():
        return SpanHandle()
    attributes = {
        "nexuscli.trace_id": observability.get_trace_id(),
        "nexuscli.tool.name": tool_name,
    }
    return SpanHandle(_tracer().start_as_current_span("tool.call", attributes=attributes))
