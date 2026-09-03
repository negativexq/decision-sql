from pydantic import BaseModel, ConfigDict, Field


class MetricRequest(BaseModel):
    """The only semantic request accepted by the M3 compiler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_name: str = Field(min_length=1)
    dimensions: tuple[str, ...] = ()

    def normalized(self) -> "MetricRequest":
        dimensions = tuple(dimension.strip().lower() for dimension in self.dimensions)
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("duplicate metric dimensions are not allowed")
        return MetricRequest(metric_name=self.metric_name.strip().lower(), dimensions=dimensions)
