from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.decision.models import ProposalSource
from app.decision.models import RuntimeOutcome as ProductRuntimeOutcome
from app.generation.decision_contract import DecisionReasonCode, DecisionType
from app.governance.context import GovernedContext
from app.observability.run_trace import RunTrace


class DemoPreset(BaseModel):
    id: str
    label: str
    description: str
    category: str
    question: str
    mode: ProposalSource


class PlaygroundQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    preset_id: str | None = Field(default=None, max_length=80)


DecisionValue = DecisionType


RuntimeOutcome = ProductRuntimeOutcome


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trace_id: str
    question: str
    preset_id: str | None = None
    proposal_source: ProposalSource
    replay_notice: str | None = None
    model_decision: DecisionType | None = None
    model_reason_code: DecisionReasonCode | None = None
    runtime_outcome: RuntimeOutcome
    runtime_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    model_context: GovernedContext | None = None
    model_context_hash: str | None = None
    proposed_sql: str | None = None
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
    proposal_source: ProposalSource
    model_decision: DecisionType | None = None
    runtime_outcome: RuntimeOutcome
    runtime_reason: str | None = None
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
    title: str = "Governed Catalog"
    description: str = (
        "Server-owned queryable schema and relationships. Individual runs receive "
        "a bounded model-visible subset."
    )
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
    proposal_source: ProposalSource
    model_decision: DecisionType | None
    model_reason_code: DecisionReasonCode | None
    proposed_sql: str | None
    runtime_outcome: RuntimeOutcome
    runtime_reason: str | None
    provider: str | None
    model: str | None
    model_context: GovernedContext | None
    model_context_hash: str | None
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    duration_ms: float | None
