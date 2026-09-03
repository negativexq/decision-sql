from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.semantics.models import MetricCatalog
from app.semantics.requests import MetricRequest


class GovernedMetricGroundingDTO(BaseModel):
    """Minimal provider-facing contract; it contains no physical SQL details."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applicable: bool = True
    metric_name: str | None = Field(default=None, min_length=1)
    dimensions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_applicability(self) -> "GovernedMetricGroundingDTO":
        if self.applicable and self.metric_name is None:
            raise ValueError("applicable governed grounding requires metric_name")
        if not self.applicable and (self.metric_name is not None or self.dimensions):
            raise ValueError("not-applicable grounding cannot select metric or dimensions")
        return self


def grounding_to_request(
    grounding: GovernedMetricGroundingDTO, catalog: MetricCatalog
) -> MetricRequest:
    """Apply only exact, case-insensitive identifier normalization."""
    if not grounding.applicable or grounding.metric_name is None:
        raise ValueError("grounding is not applicable")
    request = MetricRequest(
        metric_name=grounding.metric_name.strip().lower(),
        dimensions=tuple(dimension.strip().lower() for dimension in grounding.dimensions),
    ).normalized()
    catalog.metric(request.metric_name)
    for dimension in request.dimensions:
        catalog.dimension(dimension)
    return request
