from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.generation.decision_contract import DecisionReasonCode, DecisionType
from app.observability.run_trace import RunTrace


class ProposalSource(StrEnum):
    LIVE_MODEL = "LIVE_MODEL"
    SAFETY_REPLAY = "SAFETY_REPLAY"
    RUNTIME_POLICY_REPLAY = "RUNTIME_POLICY_REPLAY"


class RuntimeOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    AUTHORITY_REJECTED = "AUTHORITY_REJECTED"
    POLICY_REJECTED = "POLICY_REJECTED"
    RUNTIME_REJECTED = "RUNTIME_REJECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    NOT_ENTERED = "NOT_ENTERED"
    GENERATION_ERROR = "GENERATION_ERROR"


class DecisionApplicationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_decision: DecisionType | None = None
    model_reason_code: DecisionReasonCode | None = None
    proposed_sql: str | None = None
    proposal_source: ProposalSource
    provider: str | None = None
    model: str | None = None
    runtime_outcome: RuntimeOutcome
    runtime_reason: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    trace: RunTrace
