from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings


def configure_tracing(settings: Settings) -> None:
    """Install an SDK provider and optional OTLP exporter without sensitive attributes."""
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        )
    trace.set_tracer_provider(provider)


def get_tracer(name: str = "decision_sql") -> trace.Tracer:
    """Return a tracer without recording sensitive database rows by default."""
    return trace.get_tracer(name)
