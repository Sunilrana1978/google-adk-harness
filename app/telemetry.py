"""Correlation for tracing (plan item 3).

ADK already emits OpenTelemetry spans (turn on export with `adk web --otel_to_cloud` or the OTEL
env vars). Two things are left for us:

1. Nesting. delegate_task runs in the same process and asyncio task chain as the orchestrator's
   tool call, so the sub-agent's spans nest under the orchestrator's `execute_tool delegate_task`
   span automatically. tests/test_delegate.py checks that, instead of assuming it.

2. One searchable ID. ADK creates the sub-agent's spans, so we cannot set attributes on them
   directly. Instead a SpanProcessor stamps `harness.correlation_id` (the PARENT session id) on
   every span that starts while a delegation is running. Query that one attribute in Cloud Trace
   to see the orchestrator turn, every delegation, and each sub-agent's model and tool calls.
"""
from __future__ import annotations

import weakref
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider

CORRELATION_ATTR = "harness.correlation_id"

# A ContextVar (not OTel baggage) on purpose: baggage can be copied into outgoing HTTP headers.
_correlation_id: ContextVar[str | None] = ContextVar("harness_correlation_id", default=None)


class CorrelationSpanProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None) -> None:
        correlation_id = _correlation_id.get()
        if correlation_id:
            span.set_attribute(CORRELATION_ATTR, correlation_id)

    def on_end(self, span) -> None:  # required by the interface
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_installed_on: "weakref.WeakSet[TracerProvider]" = weakref.WeakSet()


def ensure_correlation_processor() -> bool:
    """Attach the processor to the active tracer provider (once per provider).

    Returns False when tracing is not configured (no SDK provider), in which case there are no
    real spans to stamp. Called lazily at delegation time because ADK sets up its provider when
    the server starts, which may be after this module is imported.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return False
    if provider not in _installed_on:
        provider.add_span_processor(CorrelationSpanProcessor())
        _installed_on.add(provider)
    return True


@contextmanager
def correlation_scope(correlation_id: str):
    token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)
