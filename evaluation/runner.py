import json
from collections.abc import Callable, Iterable
from pathlib import Path
from statistics import mean

from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode
from app.sql.models import PolicyCode, QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.text_to_sql.service import TextToSqlService
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase, BaselineReport, CaseEvaluation


def load_baseline(path: Path) -> list[BaselineCase]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, list):
        raise ValueError("Baseline dataset must be a JSON array")
    return [BaselineCase.model_validate(item) for item in payload]


async def evaluate_baseline(
    cases: list[BaselineCase],
    service: TextToSqlService,
) -> BaselineReport:
    evaluations: list[CaseEvaluation] = []
    for case in cases:
        evaluations.append(await _evaluate_case(case, service))
    return _report(service.context_mode.value, evaluations)


async def evaluate_ablation(
    cases: list[BaselineCase],
    service_factory: Callable[[SchemaContextMode], TextToSqlService],
) -> dict[str, BaselineReport]:
    reports: dict[str, BaselineReport] = {}
    for mode in (SchemaContextMode.FULL, SchemaContextMode.RETRIEVED):
        reports[mode.value] = await evaluate_baseline(cases, service_factory(mode))
    return reports


async def _evaluate_case(case: BaselineCase, service: TextToSqlService) -> CaseEvaluation:
    result = await service.run(
        TextToSqlRequest(question=case.question, correlation_id=case.id, execute=True)
    )
    context = result.context
    plan_status = (
        result.plan_failure.status.value
        if result.plan_failure
        else ("ALLOWED" if result.plan else None)
    )
    estimate = result.plan.estimate if result.plan else (
        result.plan_failure.estimate if result.plan_failure else None
    )
    evaluation = CaseEvaluation(
        id=case.id,
        category=case.category,
        generated_sql=result.proposal.sql if result.proposal else None,
        normalized_sql=result.plan.normalized_sql if result.plan else None,
        provider=result.provider,
        model=result.model,
        context_table_count=(context.context_metadata.selected_table_count if context else None),
        context_column_count=(context.context_metadata.selected_column_count if context else None),
        retrieval_hit=(
            bool(context)
            and set(case.expected_tables).issubset(
                {table.name for table in context.tables}
            )
        )
        if context
        else None,
        parse_success=(result.failure_stage is not FailureStage.SQL_PARSE_ERROR)
        if result.candidate is not None
        else None,
        plan_accepted=isinstance(result.plan, QueryPlan),
        execution_success=result.execution is not None,
        failure_stage=result.failure_stage.value if result.failure_stage else None,
        plan_status=plan_status,
        estimated_rows=estimate.plan_rows if estimate else None,
        estimated_cost=estimate.total_cost if estimate else None,
        execution_row_count=result.execution.row_count if result.execution else None,
        execution_truncated=result.execution.truncated if result.execution else None,
        prompt_tokens=result.proposal.prompt_tokens if result.proposal else None,
        completion_tokens=result.proposal.completion_tokens if result.proposal else None,
        generation_latency_ms=result.generation_latency_ms,
    )
    if result.execution is not None:
        gold = service.safety_service.plan(SqlCandidate(sql=case.gold_sql))
        if isinstance(gold, QueryPlan):
            gold_execution = service.safety_service.execute(gold)
            if isinstance(gold_execution, QueryExecution):
                comparison = assess_query_results(
                    result.execution,
                    gold_execution,
                    order_sensitive=case.order_sensitive,
                    actual_sql=result.proposal.sql if result.proposal else None,
                    expected_sql=case.gold_sql,
                )
                evaluation.result_equivalent = comparison.equivalent
                evaluation.equivalence_diagnostic = (
                    comparison.diagnostic.value if comparison.diagnostic else None
                )
    if evaluation.result_equivalent is False:
        evaluation.failure_stage = FailureStage.RESULT_VALIDATION_ERROR.value
    evaluation.failure_attribution = _failure_attribution(evaluation, result.plan_failure)
    return evaluation


def _report(mode: str, evaluations: list[CaseEvaluation]) -> BaselineReport:
    total = len(evaluations)
    parse_values = [case.parse_success for case in evaluations if case.parse_success is not None]
    retrieval_values = [
        case.retrieval_hit for case in evaluations if case.retrieval_hit is not None
    ]
    equivalence_count = sum(case.result_equivalent is True for case in evaluations)
    parse_count = sum(case.parse_success is True for case in evaluations)
    plan_count = sum(case.plan_accepted for case in evaluations)
    execution_count = sum(case.execution_success for case in evaluations)
    return BaselineReport(
        mode=mode,
        total_cases=total,
        retrieval_hit_rate=mean(retrieval_values) if retrieval_values else None,
        parse_success_rate=mean(parse_values) if parse_values else 0.0,
        plan_acceptance_rate=(
            sum(case.plan_accepted for case in evaluations) / total if total else 0.0
        ),
        execution_success_rate=(
            sum(case.execution_success for case in evaluations) / total if total else 0.0
        ),
        result_equivalence_rate=equivalence_count / total if total else 0.0,
        result_equivalence_count=equivalence_count,
        parse_success_count=parse_count,
        plan_acceptance_count=plan_count,
        execution_success_count=execution_count,
        query_cost_rejection_count=sum(
            case.failure_attribution == "QUERY_COST_REJECTION" for case in evaluations
        ),
        policy_rejection_count=sum(
            case.failure_attribution == "POLICY_REJECTION" for case in evaluations
        ),
        generation_failure_count=sum(
            case.failure_attribution == "SQL_GENERATION_ERROR" for case in evaluations
        ),
        retrieval_miss_count=sum(case.retrieval_hit is False for case in evaluations),
        average_context_tables=_average(case.context_table_count for case in evaluations),
        average_context_columns=_average(case.context_column_count for case in evaluations),
        average_generation_latency_ms=_average(case.generation_latency_ms for case in evaluations),
        p50_generation_latency_ms=_percentile(
            [case.generation_latency_ms for case in evaluations], 0.50
        ),
        p95_generation_latency_ms=_percentile(
            [case.generation_latency_ms for case in evaluations], 0.95
        ),
        total_prompt_tokens=_sum_optional(case.prompt_tokens for case in evaluations),
        total_completion_tokens=_sum_optional(
            case.completion_tokens for case in evaluations
        ),
        average_prompt_tokens=_average(case.prompt_tokens for case in evaluations),
        average_completion_tokens=_average(case.completion_tokens for case in evaluations),
        cases=evaluations,
    )


def _average(values: Iterable[int | float | None]) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return mean(numbers) if numbers else None


def _percentile(values: Iterable[float | None], quantile: float) -> float | None:
    numbers = sorted(value for value in values if isinstance(value, (int, float)))
    if not numbers:
        return None
    if len(numbers) == 1:
        return float(numbers[0])
    position = (len(numbers) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(numbers) - 1)
    fraction = position - lower
    return float(numbers[lower] + (numbers[upper] - numbers[lower]) * fraction)


def _sum_optional(values: Iterable[int | None]) -> int | None:
    numbers = [value for value in values if isinstance(value, int)]
    return sum(numbers) if numbers else None


def _failure_attribution(
    evaluation: CaseEvaluation, plan_failure: SqlPlanFailure | None
) -> str | None:
    if evaluation.failure_stage == "SCHEMA_RETRIEVAL_ERROR":
        return "SCHEMA_RETRIEVAL_ERROR"
    if evaluation.failure_stage == "SQL_GENERATION_ERROR":
        return "SQL_GENERATION_ERROR"
    if evaluation.failure_stage == "SQL_PARSE_ERROR":
        return "SQL_PARSE_ERROR"
    if evaluation.failure_stage == "QUERY_COST_REJECTION":
        return "QUERY_COST_REJECTION"
    if evaluation.failure_stage == "POLICY_REJECTION":
        if (
            not evaluation.retrieval_hit
            and plan_failure is not None
            and plan_failure.rejection is not None
            and plan_failure.rejection.code is PolicyCode.UNKNOWN_TABLE
        ):
            return "SCHEMA_RETRIEVAL_MISS"
        return "POLICY_REJECTION"
    if evaluation.failure_stage == "EXECUTION_ERROR":
        return "EXECUTION_ERROR"
    if evaluation.failure_stage == "RESULT_VALIDATION_ERROR":
        return "RESULT_INCORRECT"
    if evaluation.execution_success and evaluation.result_equivalent is False:
        return "RESULT_INCORRECT"
    return None
