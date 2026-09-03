from pydantic import BaseModel, ConfigDict, Field


class M34RoutingCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_path: str
    metric_name: str | None = None
    dimensions: tuple[str, ...] = ()
