from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import FailureStage


class SqlSafetyStatus(StrEnum):
    ALLOWED = "ALLOWED"
    SQL_PARSE_ERROR = "SQL_PARSE_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"
    QUERY_COST_REJECTION = "QUERY_COST_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"


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


class SqlExecutionResult(BaseModel):
    status: SqlSafetyStatus
    failure_stage: FailureStage | None = None
    error: str | None = None
    rejection: PolicyRejection | None = None
    estimate: ExplainEstimate | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, object]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
