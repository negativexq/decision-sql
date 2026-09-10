from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.observability.run_trace import RunTrace


class DemoPreset(BaseModel):
    id: str
    label: str
    description: str
    category: str
    question: str


class PlaygroundQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    preset_id: str | None = Field(default=None, max_length=80)


class DecisionValue(StrEnum):
    ANSWER = "ANSWER"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    GENERATION_FAILED = "GENERATION_FAILED"


class RuntimeOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    AUTHORITY_REJECTED = "AUTHORITY_REJECTED"
    POLICY_REJECTED = "POLICY_REJECTED"
    RUNTIME_REJECTED = "RUNTIME_REJECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    NOT_ENTERED = "NOT_ENTERED"
    GENERATION_ERROR = "GENERATION_ERROR"


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trace_id: str
    question: str
    preset_id: str | None = None
    decision: DecisionValue
    runtime_outcome: RuntimeOutcome
    reason_code: str | None = None
    sql: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float | None = None
    created_at: str
    trace: RunTrace


class RunSummary(BaseModel):
    run_id: str
    trace_id: str
    question: str
    preset_id: str | None = None
    decision: DecisionValue
    runtime_outcome: RuntimeOutcome
    reason_code: str | None = None
    row_count: int = 0
    duration_ms: float | None = None
    created_at: str


class SchemaColumn(BaseModel):
    name: str
    type: str
    description: str
    queryable: bool
    primary_key: bool


class SchemaRelationship(BaseModel):
    source_column: str
    target_table: str
    target_column: str


class SchemaEntity(BaseModel):
    name: str
    description: str
    columns: list[SchemaColumn]
    relationships: list[SchemaRelationship]


class GovernedSchemaResponse(BaseModel):
    title: str = "Model-visible governed context"
    entities: list[SchemaEntity]


class RunListResponse(BaseModel):
    runs: list[RunSummary]


class ApiHealthResponse(BaseModel):
    status: str
    api: str
    database: str


class PlaygroundResponse(BaseModel):
    run_id: str
    trace_id: str
    decision: DecisionValue
    sql: str | None
    runtime_outcome: RuntimeOutcome
    reason_code: str | None
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    duration_ms: float | None
