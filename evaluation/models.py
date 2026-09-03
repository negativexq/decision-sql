from pydantic import BaseModel, Field


class BaselineCase(BaseModel):
    id: str
    question: str
    gold_sql: str
    category: str
    expected_tables: tuple[str, ...] = ()
    order_sensitive: bool = False


class CaseEvaluation(BaseModel):
    id: str
    category: str
    generated_sql: str | None = None
    normalized_sql: str | None = None
    provider: str | None = None
    model: str | None = None
    context_table_count: int | None = None
    context_column_count: int | None = None
    retrieval_hit: bool | None = None
    parse_success: bool | None = None
    plan_accepted: bool = False
    execution_success: bool = False
    result_equivalent: bool | None = None
    equivalence_diagnostic: str | None = None
    failure_stage: str | None = None
    failure_attribution: str | None = None
    plan_status: str | None = None
    estimated_rows: int | None = None
    estimated_cost: float | None = None
    execution_row_count: int | None = None
    execution_truncated: bool | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    generation_latency_ms: float | None = None


class BaselineReport(BaseModel):
    mode: str
    total_cases: int
    retrieval_hit_rate: float | None = None
    parse_success_rate: float
    plan_acceptance_rate: float
    execution_success_rate: float
    result_equivalence_rate: float | None = None
    result_equivalence_count: int = 0
    parse_success_count: int = 0
    plan_acceptance_count: int = 0
    execution_success_count: int = 0
    query_cost_rejection_count: int = 0
    policy_rejection_count: int = 0
    generation_failure_count: int = 0
    retrieval_miss_count: int = 0
    average_context_tables: float | None = None
    average_context_columns: float | None = None
    average_generation_latency_ms: float | None = None
    p50_generation_latency_ms: float | None = None
    p95_generation_latency_ms: float | None = None
    total_prompt_tokens: int | None = None
    total_completion_tokens: int | None = None
    average_prompt_tokens: float | None = None
    average_completion_tokens: float | None = None
    cases: list[CaseEvaluation] = Field(default_factory=list)
