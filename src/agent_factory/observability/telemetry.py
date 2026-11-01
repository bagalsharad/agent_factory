"""Telemetry abstraction over OpenTelemetry.

Default exporter is ``none``: spans are created (so trace/correlation ids exist) but nothing leaves the
process. ``console`` prints spans locally; ``otlp`` exports to a collector the operator configures
explicitly. Span attributes never contain prompt or tool content — only identifiers, sizes and timings.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor

from .. import __version__
from ..core.config import Settings
from ..core.logging import get_logger

log = get_logger(__name__)
_configured = False


def configure_telemetry(settings: Settings) -> None:
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "agent-factory",
                "service.version": __version__,
                "deployment.environment": settings.profile.value,
            }
        )
    )
    if settings.telemetry_exporter == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    elif settings.telemetry_exporter == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            log.warning("OTLP exporter requested but the 'otlp' extra is not installed; telemetry stays local")
        else:
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True


_tracer = trace.get_tracer("agent_factory")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    clean = {k: v for k, v in attributes.items() if isinstance(v, str | int | float | bool) and v is not None}
    with _tracer.start_as_current_span(name, attributes=clean) as current:
        yield current


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None
