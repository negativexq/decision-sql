from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.decision.models import ProposalSource
from app.decision.models import RuntimeOutcome as ProductRuntimeOutcome
from app.generation.decision_contract import DecisionDetail, DecisionReasonCode, DecisionType
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
    connection_id: str = Field(default="governed", min_length=1, max_length=64)


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
    model_detail: DecisionDetail | None = None
    runtime_outcome: RuntimeOutcome
    runtime_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    model_context: GovernedContext | None = None
    model_context_hash: str | None = None
    proposed_sql: str | None = None
    repaired_from_sql: str | None = None
    # The exact text the database received, and why its logic differs from proposed_sql.
    executed_sql: str | None = None
    sql_changes: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float | None = None
    created_at: str
    trace: RunTrace
    actor: str = "anonymous"
    connection_id: str = "governed"
    # False when the record was read back from the audit log, which keeps no result rows.
    results_retained: bool = True


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
    actor: str = "anonymous"


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
    row_estimate: int | None = None


class SourceDatabase(BaseModel):
    """Connection summary for the governed source; never exposes credentials."""

    name: str
    engine: str = "PostgreSQL"
    server_version: str | None = None
    schema_name: str = "public"
    connected: bool


class GovernedSchemaResponse(BaseModel):
    title: str = "Governed Catalog"
    description: str = (
        "Server-owned queryable schema and relationships. Individual runs receive "
        "a bounded model-visible subset."
    )
    database: SourceDatabase | None = None
    entities: list[SchemaEntity]


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    durable: bool = False


class ApiHealthResponse(BaseModel):
    status: str
    api: str
    database: str
