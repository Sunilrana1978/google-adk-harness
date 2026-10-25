"""Test setup: an in-memory tracer so tests can inspect the spans ADK and the harness emit."""
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

_EXPORTER = InMemorySpanExporter()


def pytest_configure(config):
    # Must happen before ADK creates its tracer, and a global provider can only be set once.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(provider)


@pytest.fixture
def spans():
    _EXPORTER.clear()
    return _EXPORTER
