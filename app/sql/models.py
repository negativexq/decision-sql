from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import FailureStage


class SqlSafetyStatus(StrEnum):
    ALLOWED = "ALLOWED"
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"
    QUERY_COST_REJECTION = "QUERY_COST_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class CandidateSource(StrEnum):
    INTERNAL = "internal"
    LLM = "llm"
    WINDOW_COMPILER = "window_compiler"
    SEMANTIC_METRIC_COMPILER = "semantic_metric_compiler"
    FUTURE_LLM = "future_llm"


class SqlCandidate(BaseModel):
    """Untrusted SQL proposed by an internal caller or a future generator."""

    model_config = ConfigDict(frozen=True)

    sql: str = Field(min_length=1)
    source: CandidateSource = CandidateSource.INTERNAL
    correlation_id: str | None = None


class PolicyCode(StrEnum):
    EMPTY_SQL = "EMPTY_SQL"
    MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
    NON_READ_ONLY_STATEMENT = "NON_READ_ONLY_STATEMENT"
    FORBIDDEN_TABLE = "FORBIDDEN_TABLE"
    UNKNOWN_TABLE = "UNKNOWN_TABLE"
    FORBIDDEN_COLUMN = "FORBIDDEN_COLUMN"
    UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
    FORBIDDEN_FUNCTION = "FORBIDDEN_FUNCTION"
    FORBIDDEN_CATALOG = "FORBIDDEN_CATALOG"
    QUERY_TOO_EXPENSIVE = "QUERY_TOO_EXPENSIVE"


class PolicyRejection(BaseModel):
    code: PolicyCode
    message: str
    object: str | None = None


class ExplainEstimate(BaseModel):
    total_cost: float
    plan_rows: int
    top_level_node_type: str


class QueryPlan(BaseModel):
    """Evidence-backed, executable plan produced only by SqlSafetyService.plan."""

    model_config = ConfigDict(frozen=True)

    plan_id: UUID
    correlation_id: str | None = None
    candidate_source: CandidateSource = CandidateSource.INTERNAL
    normalized_sql: str
    statement_type: str
    referenced_tables: tuple[str, ...] = ()
    referenced_columns: tuple[str, ...] = ()
    referenced_functions: tuple[str, ...] = ()
    policy_decision: Literal[SqlSafetyStatus.ALLOWED] = SqlSafetyStatus.ALLOWED
    estimate: ExplainEstimate
    executable: Literal[True] = True


class QueryExecution(BaseModel):
    """Bounded database outcome for one accepted QueryPlan."""

    plan_id: UUID
    correlation_id: str | None = None
    status: Literal[SqlSafetyStatus.ALLOWED] = SqlSafetyStatus.ALLOWED
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, object]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    latency_ms: float


class SqlPlanFailure(BaseModel):
    """Typed failure returned when deterministic planning cannot produce a plan."""

    status: SqlSafetyStatus
    failure_stage: FailureStage | None = None
    error: str | None = None
    rejection: PolicyRejection | None = None
    estimate: ExplainEstimate | None = None


class SqlExecutionError(BaseModel):
    """Typed, privacy-safe failure returned by the restricted executor."""

    plan_id: UUID | None = None
    correlation_id: str | None = None
    status: Literal[SqlSafetyStatus.EXECUTION_ERROR] = SqlSafetyStatus.EXECUTION_ERROR
    failure_stage: Literal[FailureStage.EXECUTION_ERROR] = FailureStage.EXECUTION_ERROR
    error: str
