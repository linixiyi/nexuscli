"""Optional OTLP/HTTP span export behind ``telemetry.otlp`` (Langfuse et al).

Optional-dependency semantics per AGENTS.md「可选依赖」四步套路:

- extras: the exporter lives in the second-tier ``telemetry-otlp`` group
  (``telemetry`` = spans can be produced, ``telemetry-otlp`` = spans can be
  exported); this module is its only import boundary — the core tracing
  module keeps zero top-level references.
- lazy import: nothing from ``opentelemetry`` is imported until
  ``telemetry.otlp.endpoint`` is configured *and* ``telemetry.enabled`` is
  on; with the defaults the whole face is dead code (zero awareness, zero
  cost, nothing in ``sys.modules``).
- typed degradation: a failed import whose ``exc.name`` lands in the
  opentelemetry family becomes :class:`OtlpDependencyError` carrying the
  ``uv sync --extra telemetry-otlp`` guidance; every other ImportError
  propagates unchanged (internal bugs must not be masked by a friendly
  message).
- bypass discipline: export is a side channel. ``ensure_otlp_export``
  converts the typed error — and any other wiring failure — into at most ONE
  ``warnings.warn`` per process; export failures after wiring go through the
  delegating exporter shell in :func:`_build_processor` with the same
  once-per-process budget. Nothing here may change caller behaviour or the
  CLI exit code.
"""

from __future__ import annotations

import logging
import threading
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from nexuscli.config import (
    TelemetryConfig,
    get_resolved_telemetry_config,
    load_telemetry_config,
    set_resolved_telemetry_config,
)

_EXTRA_GROUP = "telemetry-otlp"
_MISSING_DEPENDENCY_MESSAGE = (
    "OTLP span export is configured (telemetry.otlp.endpoint) but the "
    "opentelemetry exporter dependency is missing. Install the telemetry-otlp "
    f"extra: uv sync --extra {_EXTRA_GROUP}"
)


class OtlpDependencyError(RuntimeError):
    """``telemetry.otlp`` is configured but the opentelemetry exporter is missing.

    Raised by :func:`_import_otlp_dependencies` when the failed import's
    ``exc.name`` lands in the opentelemetry family. Production call sites
    (:func:`ensure_otlp_export`) catch it and degrade to a single warning —
    export is a bypass and must never break the span call sites.
    """


@dataclass
class _State:
    """Process-lifetime one-shot state for the wiring and the noise budget."""

    attempted: bool = False
    wired: bool = False
    warned: bool = False


_state = _State()
_lock = threading.RLock()


def _load_config() -> TelemetryConfig:
    """Self-read the process config's telemetry face (test seam).

    Resolution order: the face last resolved by :func:`load_config` (the
    authoritative in-process source — the CLI may resolve its project root
    from ``--cwd`` to a directory other than the process cwd, so re-reading
    from ``Path.cwd()`` could blend ``telemetry.enabled`` of one project with
    an ``otlp.endpoint`` of another); otherwise, for embedders that never call
    load_config, a fresh read of user config plus the project config at
    ``Path.cwd()``. Tests monkeypatch this module attribute (same discipline
    as ``tracing._sdk_available``) so suites never depend on real config files.
    """
    resolved = get_resolved_telemetry_config()
    if resolved is not None:
        return resolved
    return load_telemetry_config(project_root=Path.cwd())


def warn_once(message: str) -> None:
    """Emit one ``warnings.warn`` per process at most (the noise budget)."""
    with _lock:
        if _state.warned:
            return
        _state.warned = True
    warnings.warn(f"[nexuscli:telemetry] {message}", stacklevel=3)


def _is_otel_family(exc: ImportError) -> bool:
    name = exc.name or ""
    return name.split(".")[0] == "opentelemetry"


def _raise_translated(exc: ImportError) -> NoReturn:
    """Translate opentelemetry-family import failures, re-raise the rest raw."""
    if _is_otel_family(exc):
        raise OtlpDependencyError(_MISSING_DEPENDENCY_MESSAGE) from exc
    raise exc


def _import_otlp_dependencies() -> tuple[type, type, type]:
    """Lazily import (OTLPSpanExporter, BatchSpanProcessor, SpanExportResult).

    The SDK pieces come from the ``telemetry`` extra, the exporter from
    ``telemetry-otlp`` (which pulls the SDK transitively, so one guidance
    message covers both). A missing opentelemetry-family dependency becomes
    :class:`OtlpDependencyError`; any other ImportError propagates raw.
    """
    try:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
    except ImportError as exc:
        _raise_translated(exc)
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError as exc:
        _raise_translated(exc)
    return OTLPSpanExporter, BatchSpanProcessor, SpanExportResult  # noqa: F821


def _resolve_provider() -> Any | None:
    """Find the existing TracerProvider the export processor attaches to.

    Order: the test-injected provider (``tracing._PROVIDER``), then a
    host-installed global provider (anything exposing
    ``add_span_processor`` — the SDK TracerProvider does, the default
    ``ProxyTracerProvider`` does not), then a fresh SDK TracerProvider
    installed as the global. OTel's global slot can only be set while it
    still holds the default proxy, so a host provider without
    ``add_span_processor`` leaves nothing to attach to and None is returned.
    """
    # Lazy: tracing imports this module at package load time.
    from nexuscli.telemetry import tracing

    if tracing._PROVIDER is not None:
        return tracing._PROVIDER
    from opentelemetry import trace

    current = trace.get_tracer_provider()
    if hasattr(current, "add_span_processor"):
        return current
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is not provider:
        return None
    return provider


def _build_processor(
    config_otlp: Any,
    exporter_cls: Any,
    batch_processor_cls: Any,
    span_export_result: Any,
) -> Any:
    """Build a BatchSpanProcessor wrapping the exporter in the once-warn shell.

    The OTLP exporter reports network failures as a ``SpanExportResult``
    return value (it does not raise), so the shell inspects the result of
    every batch and spends the process-lifetime warning budget exactly once.
    The config ``timeout`` is in seconds, matching the exporter's own unit.
    """
    kwargs: dict[str, Any] = {
        "endpoint": config_otlp.endpoint,
        "headers": dict(config_otlp.headers),
    }
    if config_otlp.timeout is not None:
        kwargs["timeout"] = config_otlp.timeout
    exporter = exporter_cls(**kwargs)

    class _OnceWarningExporter:
        """Delegating exporter shell: batch failure → at most one warning."""

        def export(self, spans: Any) -> Any:
            result = exporter.export(spans)
            if result is not span_export_result.SUCCESS:
                warn_once(
                    "OTLP span export to telemetry.otlp.endpoint="
                    f"{config_otlp.endpoint} failed; spans and CLI behaviour "
                    "are unaffected and this process will not warn again."
                )
            return result

        def shutdown(self) -> Any:
            return exporter.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> Any:
            return exporter.force_flush(timeout_millis)

    return batch_processor_cls(_OnceWarningExporter())


def ensure_otlp_export(telemetry: TelemetryConfig | None = None) -> bool:
    """Wire the OTLP exporter onto the existing provider once per process.

    Returns True when a BatchSpanProcessor exporting to ``telemetry.otlp.
    endpoint`` is attached. Nothing is imported or wired unless
    ``telemetry.enabled`` is on AND the endpoint is set (the default face is
    off). Missing exporter dependencies and any other wiring failure degrade
    to at most one warning per process — never an exception reaching the
    span call sites, never an effect on the CLI exit code.
    """
    with _lock:
        if _state.attempted:
            return _state.wired
        _state.attempted = True
        config = telemetry if telemetry is not None else _load_config()
        if not config.enabled or not config.otlp.endpoint:
            return False
        try:
            exporter_cls, batch_cls, result_cls = _import_otlp_dependencies()
            provider = _resolve_provider()
            if provider is None:
                warn_once(
                    "OTLP span export requested but no attachable "
                    "TracerProvider is available (the global provider slot is "
                    "held by a provider without add_span_processor); export "
                    "stays off for this process."
                )
                return False
            provider.add_span_processor(
                _build_processor(config.otlp, exporter_cls, batch_cls, result_cls)
            )
        except OtlpDependencyError as exc:
            warn_once(str(exc))
            return False
        except Exception as exc:  # bypass discipline: a broken install is not a crash
            warn_once(
                f"OTLP span export wiring failed unexpectedly ({exc!r}); "
                "export stays off for this process."
            )
            return False
        # The exporter/BSP log their own per-batch retry and drop noise through
        # the ``opentelemetry`` logger, which reaches stderr via Python's
        # lastResort handler when the host configures no logging. The spec's
        # degradation budget is ONE warning per process — ours, not the
        # library's — so silence the library channel once wiring succeeded.
        logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
        _state.wired = True
        return True


def _reset_state_for_tests() -> None:
    """Test-only seam: drop the one-shot state AND the resolved-face slot.

    Both are process globals: without clearing the config-side face a test
    that called ``load_config`` with an ``otlp.endpoint`` would leak that
    endpoint into any later span-enabling test in the same pytest process
    (the wiring consults the face before the cwd fallback).
    """
    global _state
    with _lock:
        _state = _State()
        set_resolved_telemetry_config(None)
