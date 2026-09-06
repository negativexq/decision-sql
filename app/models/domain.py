from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class UserContext(BaseModel):
    """Identity supplied by the server boundary, never by generated SQL or the client."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    roles: tuple[str, ...] = ()


class ExecutionMode(StrEnum):
    """Server-owned execution scope for a text-to-SQL request."""

    DIRECT = "DIRECT"
    GOVERNED_METRIC = "GOVERNED_METRIC"


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class TextToSqlRequest(QueryRequest):
    """Minimal natural-language request; identity remains outside this model."""

    correlation_id: str | None = None
    execute: bool = True
    execution_mode: ExecutionMode = ExecutionMode.DIRECT


class QueryStatus(StrEnum):
    RECEIVED = "received"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NOT_IMPLEMENTED = "not_implemented"


class FailureStage(StrEnum):
    SCHEMA_RETRIEVAL_ERROR = "SCHEMA_RETRIEVAL_ERROR"
    SCHEMA_LINKING_ERROR = "SCHEMA_LINKING_ERROR"
    QUERY_INTENT_GENERATION_ERROR = "QUERY_INTENT_GENERATION_ERROR"
    SEMANTIC_RESOLUTION_ERROR = "SEMANTIC_RESOLUTION_ERROR"
    SQL_GENERATION_ERROR = "SQL_GENERATION_ERROR"
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"
    QUERY_COST_REJECTION = "QUERY_COST_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    RESULT_VALIDATION_ERROR = "RESULT_VALIDATION_ERROR"
    ANSWER_SYNTHESIS_ERROR = "ANSWER_SYNTHESIS_ERROR"
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION"
    UNANSWERABLE_QUESTION = "UNANSWERABLE_QUESTION"


class QueryResult(BaseModel):
    status: QueryStatus
    answer: str | None = None
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, object]] = Field(default_factory=list)
    row_count: int = 0
    failure_stage: FailureStage | None = None
    failure_detail: str | None = None
    generated_at: datetime | None = None
    total_amount: Decimal | None = None
