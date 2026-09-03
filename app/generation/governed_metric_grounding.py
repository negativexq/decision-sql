from pydantic import BaseModel, ConfigDict, Field

from app.semantics.models import MetricCatalog
from app.semantics.requests import MetricRequest


class GovernedMetricGroundingDTO(BaseModel):
    """Minimal provider-facing contract; it contains no physical SQL details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1)
    dimensions: tuple[str, ...] = ()


def grounding_to_request(
    grounding: GovernedMetricGroundingDTO, catalog: MetricCatalog
) -> MetricRequest:
    """Apply only exact, case-insensitive identifier normalization."""
    request = MetricRequest(
        metric_name=grounding.metric_name.strip().lower(),
        dimensions=tuple(dimension.strip().lower() for dimension in grounding.dimensions),
    ).normalized()
    catalog.metric(request.metric_name)
    for dimension in request.dimensions:
        catalog.dimension(dimension)
    return request
