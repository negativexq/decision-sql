"""Typed provenance event models and the runtime-safe recording context."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.domain import TextToSqlRequest
from app.provenance.canonical import semantic_hash


class ProvenanceStage(StrEnum):
    ROUTER = "ROUTER"
    MEMORY_RETRIEVAL_RESULT = "MEMORY_RETRIEVAL_RESULT"
    MEMORY_SELECTION = "MEMORY_SELECTION"
    GENERATION_CONTEXT = "GENERATION_CONTEXT"
    GOVERNED_GROUNDING_REQUEST = "GOVERNED_GROUNDING_REQUEST"
    GOVERNED_GROUNDING_RESULT = "GOVERNED_GROUNDING_RESULT"
    GOVERNED_COMPILER_INPUT = "GOVERNED_COMPILER_INPUT"
    GOVERNED_COMPILER_OUTPUT = "GOVERNED_COMPILER_OUTPUT"
    PROVIDER_REQUEST = "PROVIDER_REQUEST"
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"
    CANDIDATE_EXTRACTION = "CANDIDATE_EXTRACTION"
    M1_PLAN = "M1_PLAN"
    EXECUTION = "EXECUTION"


class ProvenanceEventType(StrEnum):
    ROUTE_DECIDED = "ROUTE_DECIDED"
    MEMORY_RETRIEVAL_COMPLETED = "MEMORY_RETRIEVAL_COMPLETED"
    MEMORY_SELECTION_COMPLETED = "MEMORY_SELECTION_COMPLETED"
    MEMORY_CONTEXT_RENDERED = "MEMORY_CONTEXT_RENDERED"
    GOVERNED_GROUNDING_REQUESTED = "GOVERNED_GROUNDING_REQUESTED"
    GOVERNED_GROUNDING_COMPLETED = "GOVERNED_GROUNDING_COMPLETED"
    GOVERNED_COMPILER_INPUT_READY = "GOVERNED_COMPILER_INPUT_READY"
    GOVERNED_COMPILER_COMPLETED = "GOVERNED_COMPILER_COMPLETED"
    PROVIDER_REQUEST_READY = "PROVIDER_REQUEST_READY"
    PROVIDER_RESPONSE_RECEIVED = "PROVIDER_RESPONSE_RECEIVED"
    CANDIDATE_EXTRACTION_COMPLETED = "CANDIDATE_EXTRACTION_COMPLETED"
    M1_PLAN_COMPLETED = "M1_PLAN_COMPLETED"
    EXECUTION_COMPLETED = "EXECUTION_COMPLETED"


class ProvenanceEvent(BaseModel):
    """One immutable, reference-blind runtime event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = "1"
    run_id: str = Field(min_length=1, max_length=256)
    case_id: str = Field(min_length=1, max_length=256)
    stage: ProvenanceStage
    event_type: ProvenanceEventType
    sequence: int = Field(default=0, ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("payload_hash")
    @classmethod
    def validate_payload_hash(cls, value: str) -> str:
        return value

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        case_id: str,
        stage: ProvenanceStage,
        event_type: ProvenanceEventType,
        payload: dict[str, Any],
    ) -> ProvenanceEvent:
        return cls(
            run_id=run_id,
            case_id=case_id,
            stage=stage,
            event_type=event_type,
            payload=payload,
            payload_hash=semantic_hash(payload),
        )


class ProvenanceSink(Protocol):
    """Minimal collector interface; sinks must not become decision authorities."""

    enabled: bool

    def record(self, event: ProvenanceEvent) -> None:
        """Record an event without changing the caller's semantic result."""


class ProvenanceRecorder:
    """Request-local helper that emits only when an injected sink is enabled."""

    def __init__(self, sink: ProvenanceSink, request: TextToSqlRequest) -> None:
        self.sink = sink
        identity = request.correlation_id or f"question:{semantic_hash(request.question)}"
        self.run_id = identity
        self.case_id = identity

    @classmethod
    def for_identity(cls, sink: ProvenanceSink, identity: str) -> ProvenanceRecorder:
        recorder = object.__new__(cls)
        recorder.sink = sink
        recorder.run_id = identity
        recorder.case_id = identity
        return recorder

    def emit(
        self,
        stage: ProvenanceStage,
        event_type: ProvenanceEventType,
        payload: dict[str, Any],
    ) -> None:
        if not self.sink.enabled:
            return
        try:
            self.sink.record(
                ProvenanceEvent.create(
                    run_id=self.run_id,
                    case_id=self.case_id,
                    stage=stage,
                    event_type=event_type,
                    payload=payload,
                )
            )
        except Exception:
            # Observability is fail-open for product decisions. Diagnostic
            # sinks retain their own bounded error state when possible.
            return


def recorder_for(sink: ProvenanceSink, request: TextToSqlRequest) -> ProvenanceRecorder:
    return ProvenanceRecorder(sink, request)


def recorder_for_identity(sink: ProvenanceSink, identity: str) -> ProvenanceRecorder:
    return ProvenanceRecorder.for_identity(sink, identity)
