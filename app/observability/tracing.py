from opentelemetry import trace


def get_tracer(name: str = "decision_sql") -> trace.Tracer:
    """Return a tracer without recording sensitive database rows by default."""
    return trace.get_tracer(name)
