from pydantic import BaseModel, ConfigDict, Field


class IntentJoin(BaseModel):
    """Untrusted structural join proposal returned by the model."""

    model_config = ConfigDict(frozen=True)

    source_table: str = Field(min_length=1)
    source_column: str = Field(min_length=1)
    target_table: str = Field(min_length=1)
    target_column: str = Field(min_length=1)


class QueryIntent(BaseModel):
    """Compact, non-authoritative interpretation of a natural-language question."""

    model_config = ConfigDict(frozen=True)

    selected_tables: tuple[str, ...] = ()
    selected_columns: tuple[str, ...] = ()
    joins: tuple[IntentJoin, ...] = ()
    filters: tuple[str, ...] = ()
    aggregations: tuple[str, ...] = ()
    group_by: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    limit: int | None = Field(default=None, ge=1, le=1000)
    window_operations: tuple[str, ...] = ()


class IntentProposal(BaseModel):
    """Structured provider result carrying intent plus bounded call metadata."""

    model_config = ConfigDict(frozen=True)

    intent: QueryIntent
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None
