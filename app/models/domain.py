from enum import StrEnum

from pydantic import BaseModel, Field


class ExecutionMode(StrEnum):
    """Server-owned execution scope for a text-to-SQL request."""

    DIRECT = "DIRECT"
    SEMANTIC = "SEMANTIC"
    GOVERNED_METRIC = "GOVERNED_METRIC"


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class TextToSqlRequest(QueryRequest):
    """Minimal natural-language request; identity remains outside this model."""

    correlation_id: str | None = None
    execute: bool = True
    execution_mode: ExecutionMode = ExecutionMode.DIRECT


class FailureStage(StrEnum):
    SCHEMA_RETRIEVAL_ERROR = "SCHEMA_RETRIEVAL_ERROR"
    SCHEMA_LINKING_ERROR = "SCHEMA_LINKING_ERROR"
    QUERY_INTENT_GENERATION_ERROR = "QUERY_INTENT_GENERATION_ERROR"
    SEMANTIC_RESOLUTION_ERROR = "SEMANTIC_RESOLUTION_ERROR"
    SQL_GENERATION_ERROR = "SQL_GENERATION_ERROR"
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"
    AUTHORITY_REJECTION = "AUTHORITY_REJECTION"
    SEMANTIC_SAFETY_REJECTION = "SEMANTIC_SAFETY_REJECTION"
    QUERY_COST_REJECTION = "QUERY_COST_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    RESULT_VALIDATION_ERROR = "RESULT_VALIDATION_ERROR"
    ANSWER_SYNTHESIS_ERROR = "ANSWER_SYNTHESIS_ERROR"
    AMBIGUOUS_QUESTION = "AMBIGUOUS_QUESTION"
    UNANSWERABLE_QUESTION = "UNANSWERABLE_QUESTION"
