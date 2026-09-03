from enum import StrEnum

from pydantic import BaseModel, Field

from app.catalog.models import SchemaContext
from app.generation.intent import IntentProposal, QueryIntent
from app.generation.provider import ProviderErrorDetail, SqlProposal
from app.generation.result_shape import ResultShapeProposal, ResultShapeValidation
from app.memory.provenance import VerifiedMemoryProvenance
from app.models.domain import FailureStage
from app.sql.models import (
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)


class TextToSqlStatus(StrEnum):
    PLANNED = "PLANNED"
    SUCCEEDED = "SUCCEEDED"
    CONTEXT_RESOLUTION_ERROR = "CONTEXT_RESOLUTION_ERROR"
    QUERY_INTENT_GENERATION_ERROR = "QUERY_INTENT_GENERATION_ERROR"
    SQL_GENERATION_ERROR = "SQL_GENERATION_ERROR"
    PLAN_REJECTED = "PLAN_REJECTED"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    RESULT_SHAPE_GENERATION_ERROR = "RESULT_SHAPE_GENERATION_ERROR"
    RESULT_SHAPE_REJECTED = "RESULT_SHAPE_REJECTED"


class GenerationStrategy(StrEnum):
    """M2 compatibility names for the orthogonal generation mode."""

    M2_ONE_SHOT = "M2_ONE_SHOT"
    M25_GROUNDED = "M25_GROUNDED"

    @property
    def mode(self) -> "GenerationMode":
        return (
            GenerationMode.ONE_SHOT
            if self is GenerationStrategy.M2_ONE_SHOT
            else GenerationMode.GROUNDED
        )


class GenerationMode(StrEnum):
    ONE_SHOT = "ONE_SHOT"
    GROUNDED = "GROUNDED"


class GenerationPath(StrEnum):
    DIRECT_SQL = "DIRECT_SQL"
    DIRECT_SQL_WITH_VERIFIED_MEMORY = "DIRECT_SQL_WITH_VERIFIED_MEMORY"
    GOVERNED_METRIC = "GOVERNED_METRIC"


class TextToSqlResult(BaseModel):
    status: TextToSqlStatus
    correlation_id: str | None = None
    failure_stage: FailureStage | None = None
    error: str | None = None
    context: SchemaContext | None = None
    strategy: GenerationStrategy = GenerationStrategy.M2_ONE_SHOT
    generation_mode: GenerationMode = GenerationMode.ONE_SHOT
    intent_proposal: IntentProposal | None = None
    intent: QueryIntent | None = None
    result_shape_proposal: ResultShapeProposal | None = None
    result_shape_validation: ResultShapeValidation | None = None
    grounding_diagnostics: dict[str, object] | None = None
    proposal: SqlProposal | None = None
    candidate: SqlCandidate | None = None
    plan: QueryPlan | None = None
    execution: QueryExecution | None = None
    plan_failure: SqlPlanFailure | None = None
    execution_error: SqlExecutionError | None = None
    provider: str | None = None
    model: str | None = None
    intent_prompt_tokens: int | None = None
    intent_completion_tokens: int | None = None
    intent_latency_ms: float | None = None
    generation_latency_ms: float | None = None
    diagnostics: dict[str, int | float | str] = Field(default_factory=dict)
    provider_error: ProviderErrorDetail | None = None
    provider_calls_attempted: int = 0
    provider_calls_succeeded: int = 0
    provider_calls_failed: int = 0
    generation_path: GenerationPath = GenerationPath.DIRECT_SQL
    verified_memory_used: bool = False
    verified_memory_provenance: VerifiedMemoryProvenance | None = None
