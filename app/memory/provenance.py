"""Bounded runtime provenance for optional verified-query-memory augmentation."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.config import VerifiedMemoryMode


class VerifiedMemoryOutcome(StrEnum):
    DISABLED = "DISABLED"
    NOT_SAMPLED = "NOT_SAMPLED"
    RETRIEVED = "RETRIEVED"
    NO_HIT = "NO_HIT"
    RETRIEVAL_FAILURE = "RETRIEVAL_FAILURE"
    MEMORY_GENERATION_FAILURE = "MEMORY_GENERATION_FAILURE"
    M1_REJECTION = "M1_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    SUCCESS = "SUCCESS"


class ShadowResultComparison(StrEnum):
    RESULTS_EQUAL = "RESULTS_EQUAL"
    RESULTS_DIFFER = "RESULTS_DIFFER"


class VerifiedMemoryFallbackReason(StrEnum):
    FEATURE_OFF = "FEATURE_OFF"
    NOT_SAMPLED = "NOT_SAMPLED"
    EMPTY_CORPUS = "EMPTY_CORPUS"
    NO_RETRIEVAL_HIT = "NO_RETRIEVAL_HIT"
    RETRIEVAL_ERROR = "RETRIEVAL_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    M1_REJECTION = "M1_REJECTION"
    EXECUTION_ERROR = "EXECUTION_ERROR"


class VerifiedMemoryProvenance(BaseModel):
    """Immutable, bounded identity and outcome for one memory decision."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: VerifiedMemoryMode
    corpus_id: str
    corpus_version: int = Field(ge=1)
    corpus_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    retriever_version: str
    k: int = Field(ge=1, le=3)
    retrieved_example_ids: tuple[str, ...] = Field(max_length=3)
    retrieval_scores: tuple[float, ...] = Field(max_length=3)
    retrieval_latency_ms: float = Field(ge=0)
    sampled: bool
    outcome: VerifiedMemoryOutcome
    fallback_reason: VerifiedMemoryFallbackReason | None = None
    shadow_result_comparison: ShadowResultComparison | None = None
