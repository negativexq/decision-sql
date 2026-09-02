from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine

from app.sql.service import SqlSafetyService


def test_m1_stage_spans_are_bounded_and_named() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("decision-sql-test")
    service = SqlSafetyService(create_engine("sqlite://"), tracer=tracer)

    result = service.execute("SELECT * FROM pg_roles")

    assert result.rejection is not None
    assert [span.name for span in exporter.get_finished_spans()] == [
        "decision_sql.validate",
        "decision_sql.policy",
    ]
