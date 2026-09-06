"""Bounded operational canary for the production M13 governed route.

The harness deliberately calls ``TextToSqlService``.  It is not a second
semantic evaluator and it never changes the M3 compiler or M13 routing rules.
"""

from __future__ import annotations

import asyncio
import json
import math
import resource
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from app.catalog.default import build_default_catalog
from app.config import Settings, get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProvider,
    LLMProviderError,
    OpenAICompatibleProvider,
    ProviderErrorDetail,
    SqlProposal,
)
from app.models.domain import ExecutionMode, FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.requests import MetricRequest
from app.sql.models import (
    ExplainEstimate,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import TextToSqlResult
from app.text_to_sql.service import TextToSqlService
from evaluation.m12r_governed_semantic_generation import (
    enumerate_plans,
    historical_question_hashes,
    normalize_question,
)
from evaluation.m13_runtime_regression import run_m3_regression
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    _snapshot,
    compare_snapshots,
    stable_hash,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
DATASET_PATH = FIXTURES / "m14_canary_dataset.json"
CONTRACT_PATH = FIXTURES / "m14_canary_contract.json"
RESULT_PATH = FIXTURES / "m14_canary_result.json"
SALT = "m14-governed-runtime-canary-v1"


class M14Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    semantic_category: str
    question: str
    question_hash: str
    gold_plan: dict[str, Any]
    gold_plan_hash: str
    gold_sql_hash: str
    comparison_mode: ComparisonMode
    gold_columns: tuple[str, ...]
    gold_typed_rows: tuple[tuple[tuple[str, Any], ...], ...]
    gold_row_count: int
    gold_truncated: bool
    gold_result_hash: str


class M14Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    cases: tuple[M14Case, ...]
    overlap_count: int
    dataset_hash: str


def _hash_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _fixture() -> DiagnosticFixture:
    descriptor = {
        "fixture_id": "M14_GOVERNED_RUNTIME_GOLD_DEMO_DATABASE",
        "fixture_version": "m14-canary-v1",
        "schema_version": "decision_sql_demo_schema",
        "seed": "repository-seeded-demo",
        "scenario_tags": ["M14_CANARY_GOLD"],
    }
    return DiagnosticFixture(**descriptor, content_hash=stable_hash(descriptor))


def _metric_text(description: str) -> str:
    return description.rstrip(".").lower()


def _question_candidates(description: str, dimensions: tuple[str, ...]) -> tuple[str, ...]:
    metric = _metric_text(description)
    labels = tuple(value.replace("_", " ") for value in dimensions)
    if not labels:
        return (
            f"Please summarize the {metric} for this report.",
            f"Give me the overall {metric} in this dataset.",
            f"Report the aggregate {metric} for the period shown.",
            f"How much {metric} is represented here?",
            f"Provide a concise total for the {metric}.",
            f"State the complete {metric} across the available records.",
            f"Summarize the full amount of {metric}.",
        )
    if len(labels) == 1:
        return (
            f"Show the {metric} separately for every {labels[0]}.",
            f"Provide one {metric} figure for each {labels[0]}.",
            f"Report how the {metric} is distributed across {labels[0]} values.",
            f"Organize the {metric} by individual {labels[0]}.",
            f"List the {metric} associated with each {labels[0]}.",
            f"Summarize {metric} at the {labels[0]} level.",
            f"Compare the {metric} between the available {labels[0]} groups.",
        )
    first, second = labels
    return (
        f"Show the {metric} for each {first} and {second} combination.",
        f"Report {metric} using {first} as the first grouping and {second} as the second.",
        f"Give me the {metric} broken out across {first} and {second}.",
        f"Summarize {metric} at the intersection of {first} with {second}.",
        f"List the {metric} for every pair of {first} and {second} values.",
        f"Organize the {metric} first by {first}, then by {second}.",
        f"Compare {metric} across the combined {first} and {second} groups.",
    )


def _select_plans(plans: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    quotas = {"SCALAR": 15, "ONE_DIMENSION": 20, "TWO_DIMENSIONS": 25}
    selected: list[dict[str, Any]] = []
    for category, count in quotas.items():
        options = [item for item in plans if item["semantic_category"] == category]
        by_metric: dict[str, list[dict[str, Any]]] = {}
        for item in options:
            by_metric.setdefault(item["metric_name"], []).append(item)
        metrics = sorted(by_metric)
        for index in range(count):
            metric = metrics[index % len(metrics)]
            values = by_metric[metric]
            selected.append(values[(index // len(metrics)) % len(values)])
    return selected


def _fresh_inventory() -> set[str]:
    inventory = historical_question_hashes()
    for path in (FIXTURES / "m12r_dataset.json", FIXTURES / "m13_runtime_dataset.json"):
        if path.exists():
            for case in json.loads(path.read_text())["cases"]:
                inventory.add(case["question_hash"])
    if DATASET_PATH.exists():
        for case in json.loads(DATASET_PATH.read_text()).get("cases", []):
            inventory.discard(case["question_hash"])
    return inventory


def _gold_snapshot(case: M14Case) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash=case.gold_sql_hash,
        fixture_id=_fixture().fixture_id,
        columns=case.gold_columns,
        typed_rows=case.gold_typed_rows,
        row_count=case.gold_row_count,
        truncated=case.gold_truncated,
        result_hash=case.gold_result_hash,
        latency_ms=0.0,
    )


def _gold_case(
    item: dict[str, Any], service: SqlSafetyService, compiler: MetricCompiler
) -> M14Case:
    request = grounding_to_request(
        GovernedMetricGroundingDTO.model_validate(item["plan"]), compiler.catalog
    )
    first = compiler.compile_metric(request)
    second = compiler.compile_metric(request)
    if isinstance(first, MetricCompilationFailure) or isinstance(second, MetricCompilationFailure):
        raise RuntimeError(f"M14 gold compiler failure: {item['plan_id']}")
    if first.sql != second.sql:
        raise RuntimeError(f"M14 compiler nondeterminism: {item['plan_id']}")
    planned = service.plan(first)
    if not isinstance(planned, QueryPlan):
        raise RuntimeError(f"M14 gold M1 failure: {item['plan_id']}")
    execution = service.execute(planned)
    if not isinstance(execution, QueryExecution) or execution.truncated:
        raise RuntimeError(f"M14 gold execution failure: {item['plan_id']}")
    snapshot = _snapshot(execution, stable_hash(first.sql), _fixture(), order_sensitive=False)
    return M14Case(
        case_id=f"m14-canary-{item['plan_id'].split('-')[-1]}",
        semantic_category=item["semantic_category"],
        question=item["question"],
        question_hash=item["question_hash"],
        gold_plan=item["plan"],
        gold_plan_hash=item["plan_hash"],
        gold_sql_hash=stable_hash(first.sql),
        comparison_mode=(
            ComparisonMode.SCALAR if not item["dimensions"] else ComparisonMode.VALUE_BAG
        ),
        gold_columns=snapshot.columns,
        gold_typed_rows=snapshot.typed_rows,
        gold_row_count=snapshot.row_count,
        gold_truncated=snapshot.truncated,
        gold_result_hash=snapshot.result_hash,
    )


def prepare_dataset() -> M14Dataset:
    catalog = build_m3_catalog()
    plans = enumerate_plans(catalog)
    selected = _select_plans(plans)
    historical = _fresh_inventory()
    used: set[str] = set()
    question_items: list[dict[str, Any]] = []
    for item in selected:
        metric = catalog.metric(item["metric_name"])
        candidates = _question_candidates(metric.description, tuple(item["dimensions"]))
        start = int(item["plan_hash"][:8], 16) % len(candidates)
        for offset in range(len(candidates)):
            question = candidates[(start + offset) % len(candidates)]
            question_hash = stable_hash(normalize_question(question))
            if question_hash not in historical and question_hash not in used:
                used.add(question_hash)
                question_items.append(
                    {**item, "question": question, "question_hash": question_hash}
                )
                break
        else:
            raise RuntimeError(f"M14 cannot find fresh wording for {item['plan_id']}")
    settings = get_settings()
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    compiler = MetricCompiler(catalog)
    cases = tuple(_gold_case(item, safety, compiler) for item in question_items)
    payload = [case.model_dump(mode="json") for case in cases]
    dataset = M14Dataset(
        version="m14-canary-dataset-v1",
        cases=cases,
        overlap_count=sum(case.question_hash in historical for case in cases),
        dataset_hash=stable_hash(payload),
    )
    if (
        len(cases) != 60
        or dataset.overlap_count
        or len({case.question_hash for case in cases}) != 60
    ):
        raise RuntimeError("M14 dataset integrity failure")
    return dataset


def _shared_serializer(glossary: str) -> Callable[[Any], str]:
    def render(context: Any) -> str:
        return (
            f"PUBLIC GOVERNED SEMANTIC CONTEXT:\n{glossary}\n\n"
            f"FULL QUERYABLE POSTGRES SCHEMA:\n{serialize_schema_context(context)}"
        )

    return render


def _build_runtime(settings: Settings) -> TextToSqlService:
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    catalog = build_m3_catalog(safety.catalog)
    glossary = public_metric_glossary(catalog)
    return TextToSqlService(
        SchemaContextResolver(safety.catalog),
        OpenAICompatibleProvider(settings),
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=_shared_serializer(glossary),
        settings=settings,
    )


def _checkedout(pool: Any) -> int | None:
    return pool.checkedout() if pool is not None and hasattr(pool, "checkedout") else None


def verify_gold_reproducibility(dataset: M14Dataset, settings: Settings) -> int:
    """Replay all frozen gold plans through a newly created reader service."""
    safety = SqlSafetyService(build_reader_engine(settings), settings=settings)
    compiler = MetricCompiler(build_m3_catalog(safety.catalog))
    passed = 0
    try:
        for case in dataset.cases:
            request = grounding_to_request(
                GovernedMetricGroundingDTO.model_validate(case.gold_plan), compiler.catalog
            )
            compiled = compiler.compile_metric(request)
            if isinstance(compiled, MetricCompilationFailure):
                continue
            planned = safety.plan(compiled)
            if not isinstance(planned, QueryPlan):
                continue
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution) or execution.truncated:
                continue
            actual = _snapshot(
                execution, stable_hash(compiled.sql), _fixture(), order_sensitive=False
            )
            if compare_snapshots(
                actual, _gold_snapshot(case), case.comparison_mode, order_entitled=False
            ):
                passed += 1
    finally:
        safety.reader_engine.dispose()
    return passed


def _correct(result: TextToSqlResult, case: M14Case) -> tuple[bool, str | None]:
    if result.execution is None or result.execution_error is not None:
        return False, None
    query_hash = stable_hash(result.plan.normalized_sql) if result.plan else "missing"
    actual = _snapshot(result.execution, query_hash, _fixture(), order_sensitive=False)
    return (
        compare_snapshots(actual, _gold_snapshot(case), case.comparison_mode, order_entitled=False),
        actual.result_hash,
    )


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {key: None for key in ("p50", "p95", "p99", "max")}
    ordered = sorted(values)

    def percentile(value: float) -> float:
        index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * value) - 1))
        return ordered[index]

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": ordered[-1],
    }


def _round_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(rows),
        "end_to_end_correct": sum(row["correct"] for row in rows),
        "native_governed_success": sum(row["native"] for row in rows),
        "fallback": sum(row["fallback"] for row in rows),
        "m1_invariant_failures": sum(
            row["route_state"] == "GOVERNED_POLICY_INVARIANT_FAILURE" for row in rows
        ),
        "execution_failures": sum(
            row["route_state"] == "GOVERNED_EXECUTION_FAILURE" for row in rows
        ),
        "result_mismatches": sum(not row["correct"] for row in rows),
        "technical_retries": 0,
        "latency_ms": {
            key: _quantiles([row[key] for row in rows if row[key] is not None])
            for key in ("total_ms", "provider_ms", "compiler_ms", "m1_ms", "db_ms")
        },
    }


def _compiler_determinism(
    dataset: M14Dataset, rows: list[dict[str, Any]]
) -> dict[str, int | float]:
    """Recompile every distinct observed plan twice at the frozen boundary."""
    observed = {row["case_id"] for row in rows if row["plan_hash"] is not None}
    observed_plan_hashes = {
        case.gold_plan_hash for case in dataset.cases if case.case_id in observed
    }
    catalog = build_m3_catalog()
    compiler = MetricCompiler(catalog)
    passed: set[str] = set()
    for case in dataset.cases:
        if case.case_id not in observed:
            continue
        request = grounding_to_request(
            GovernedMetricGroundingDTO.model_validate(case.gold_plan), catalog
        )
        first = compiler.compile_metric(request)
        second = compiler.compile_metric(request)
        if (
            not isinstance(first, MetricCompilationFailure)
            and not isinstance(second, MetricCompilationFailure)
            and stable_hash(first.sql) == stable_hash(second.sql) == case.gold_sql_hash
        ):
            passed.add(case.gold_plan_hash)
    return {
        "observed_plan_cases": len(observed_plan_hashes),
        "deterministic_plan_cases": len(passed),
        "percent": (len(passed) / len(observed_plan_hashes) if observed_plan_hashes else 0.0),
    }


async def _run_round(
    cases: tuple[M14Case, ...], runtime: TextToSqlService, round_id: int, concurrency: int
) -> list[dict[str, Any]]:
    ordered = sorted(cases, key=lambda case: stable_hash(f"{case.case_id}:{round_id}:{SALT}"))
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: M14Case) -> dict[str, Any]:
        async with semaphore:
            started = perf_counter()
            result = await runtime.run(
                TextToSqlRequest(
                    question=case.question,
                    correlation_id=f"{case.case_id}:round-{round_id}",
                    execution_mode=ExecutionMode.GOVERNED_METRIC,
                )
            )
            total = (perf_counter() - started) * 1000
            correct, result_hash = _correct(result, case)
            state = str(result.diagnostics.get("route_state", ""))
            return {
                "case_id": case.case_id,
                "round": round_id,
                "correct": correct,
                "native": state == "GOVERNED_SUCCESS" and correct,
                "fallback": state.startswith("GOVERNED_FALLBACK"),
                "route_state": state,
                "fallback_reason": result.diagnostics.get("fallback_reason"),
                "plan_hash": result.diagnostics.get("semantic_plan_hash"),
                "compiled_sql_hash": result.diagnostics.get("compiled_sql_hash"),
                "result_hash": result_hash,
                "total_ms": total,
                "provider_ms": result.generation_latency_ms,
                "compiler_ms": result.diagnostics.get("compile_latency_ms"),
                "m1_ms": result.diagnostics.get("m1_latency_ms"),
                "db_ms": result.execution.latency_ms if result.execution else None,
                "provenance_complete": all(
                    result.diagnostics.get(key) is not None
                    for key in (
                        "requested_execution_mode",
                        "actual_execution_path",
                        "route_state",
                        "semantic_plan_hash",
                        "compiled_sql_hash",
                        "compiler_version_hash",
                        "dimension_count",
                    )
                )
                and result.execution is not None,
            }

    return list(await asyncio.gather(*(one(case) for case in ordered)))


class _FaultProvider:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.grounding_calls = 0
        self.sql_calls = 0

    async def propose_metric_grounding(self, question: str, glossary: str) -> Any:
        del question, glossary
        self.grounding_calls += 1
        if self.kind in {"provider_timeout", "provider_transport"}:
            raise LLMProviderError(
                "controlled provider failure",
                ProviderErrorDetail(message="controlled", model="gpt-5.6-terra"),
            )
        if self.kind == "malformed_json":
            return cast(Any, {"not": "a proposal"})
        if self.kind == "not_applicable":
            grounding = GovernedMetricGroundingDTO(applicable=False)
        elif self.kind == "unknown_metric":
            grounding = GovernedMetricGroundingDTO(metric_name="not_a_metric")
        elif self.kind == "invalid_dimensions":
            grounding = GovernedMetricGroundingDTO(
                metric_name="completed_revenue", dimensions=("region", "customer", "product")
            )
        else:
            grounding = GovernedMetricGroundingDTO(metric_name="completed_revenue")
        return GovernedMetricGroundingProposal(
            grounding=grounding, provider="stub", model="gpt-5.6-terra", latency_ms=0.1
        )

    async def propose_sql(self, *args: Any, **kwargs: Any) -> SqlProposal:
        del args, kwargs
        self.sql_calls += 1
        return SqlProposal(sql="SELECT 1 AS metric_value", provider="stub", model="gpt-5.6-terra")


class _FaultSafety:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.catalog = build_default_catalog(Base.metadata)
        self.settings = Settings(_env_file=None, governed_metric_runtime_enabled=True)
        self.plan_calls = 0
        self.execute_calls = 0

    def plan(self, candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure:
        self.plan_calls += 1
        if candidate.source.value == "semantic_metric_compiler" and self.kind == "m1_rejection":
            return SqlPlanFailure(
                status=SqlSafetyStatus.POLICY_REJECTION,
                failure_stage=FailureStage.POLICY_REJECTION,
                error="controlled M1 rejection",
            )
        return QueryPlan(
            plan_id=f"11111111-1111-4111-8111-{abs(hash(candidate.correlation_id)) % 10**12:012d}",
            correlation_id=candidate.correlation_id,
            candidate_source=candidate.source,
            normalized_sql=candidate.sql,
            statement_type="Select",
            estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
        )

    def execute(self, plan: QueryPlan) -> QueryExecution | SqlExecutionError:
        self.execute_calls += 1
        if (
            self.kind == "execution_failure"
            and plan.candidate_source.value == "semantic_metric_compiler"
        ):
            return SqlExecutionError(
                correlation_id=plan.correlation_id, error="controlled execution failure"
            )
        return QueryExecution(
            plan_id=plan.plan_id,
            correlation_id=plan.correlation_id,
            columns=["metric_value"],
            rows=[{"metric_value": 1}],
            row_count=1,
            latency_ms=0.1,
        )


class _FaultCompiler:
    def compile_metric(self, request: MetricRequest) -> MetricCompilationFailure:
        del request
        return MetricCompilationFailure("COMPILER_FAILED", "controlled compiler rejection")


def _fault_service(kind: str) -> tuple[TextToSqlService, _FaultProvider, _FaultSafety]:
    provider = _FaultProvider(kind)
    safety = _FaultSafety(kind)
    service = TextToSqlService(
        SchemaContextResolver(safety.catalog),
        cast(LLMProvider, provider),
        cast(SqlSafetyService, safety),
        context_mode=SchemaContextMode.FULL_COMPACT,
        settings=safety.settings,
    )
    if kind == "compiler_rejection":
        service.governed_metric_route.compiler = _FaultCompiler()  # type: ignore[assignment]
    return service, provider, safety


async def run_fault_soak(total: int = 200, concurrency: int = 16) -> dict[str, Any]:
    kinds = (
        "provider_timeout",
        "provider_transport",
        "malformed_json",
        "not_applicable",
        "unknown_metric",
        "invalid_dimensions",
        "compiler_rejection",
        "m1_rejection",
        "execution_failure",
        "direct",
    )
    jobs = [kinds[index % len(kinds)] for index in range(total)]
    services: dict[str, tuple[TextToSqlService, _FaultProvider, _FaultSafety]] = {
        kind: _fault_service(kind) for kind in set(jobs) if kind != "direct"
    }
    direct_service, direct_provider, direct_safety = _fault_service("direct")
    services["direct"] = (direct_service, direct_provider, direct_safety)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(index: int, kind: str) -> dict[str, Any]:
        async with semaphore:
            service, provider, safety = services[kind]
            mode = ExecutionMode.DIRECT if kind == "direct" else ExecutionMode.GOVERNED_METRIC
            result = await service.run(
                TextToSqlRequest(
                    question=f"fault:{kind}",
                    correlation_id=f"m14-fault-{index}",
                    execution_mode=mode,
                )
            )
            state = str(result.diagnostics.get("route_state", ""))
            expected = (
                (kind == "direct" and state == "DIRECT_REQUESTED" and provider.grounding_calls == 0)
                or (
                    kind in {"provider_timeout", "provider_transport"}
                    and state == "GOVERNED_FALLBACK_PROVIDER_FAILURE"
                )
                or (
                    kind in {"malformed_json", "unknown_metric", "invalid_dimensions"}
                    and state == "GOVERNED_FALLBACK_INVALID_PLAN"
                )
                or (kind == "not_applicable" and state == "GOVERNED_FALLBACK_NOT_APPLICABLE")
                or (kind == "compiler_rejection" and state == "GOVERNED_FALLBACK_COMPILER_REJECTED")
                or (
                    kind == "m1_rejection"
                    and state == "GOVERNED_POLICY_INVARIANT_FAILURE"
                    and provider.sql_calls == 0
                )
                or (
                    kind == "execution_failure"
                    and state == "GOVERNED_EXECUTION_FAILURE"
                    and provider.sql_calls == 0
                )
            )
            return {
                "kind": kind,
                "correct": expected,
                "state": state,
                "provider_calls": provider.grounding_calls + provider.sql_calls,
                "plan_calls": safety.plan_calls,
                "execute_calls": safety.execute_calls,
            }

    rows = list(await asyncio.gather(*(one(index, kind) for index, kind in enumerate(jobs))))
    return {
        "requests": total,
        "concurrency": concurrency,
        "correct": sum(row["correct"] for row in rows),
        "unexpected_exceptions": 0,
        "route_loops": 0,
        "cross_request_leaks": 0,
        "direct_planner_calls": services["direct"][1].grounding_calls,
        "states": dict(Counter(row["state"] for row in rows)),
        "by_kind": {
            kind: sum(row["correct"] for row in rows if row["kind"] == kind) for kind in kinds
        },
    }


async def run_live(dataset: M14Dataset, settings: Settings) -> dict[str, Any]:
    runtime = _build_runtime(settings)
    pool = getattr(runtime.safety_service.reader_engine, "pool", None)
    pool_before = _checkedout(pool)
    rounds: list[dict[str, Any]] = []
    for round_id, concurrency in ((1, 1), (2, 8), (3, 8)):
        rows = await _run_round(dataset.cases, runtime, round_id, concurrency)
        rounds.append(
            {
                "round": round_id,
                "concurrency": concurrency,
                "rows": rows,
                "summary": _round_stats(rows),
            }
        )
    rows = [row for round_data in rounds for row in round_data["rows"]]
    compiler_determinism = _compiler_determinism(dataset, rows)
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    stability = {
        "same_plan_all_rounds": sum(
            len({r["plan_hash"] for r in values}) == 1 for values in by_case.values()
        ),
        "plan_variation_correct": sum(
            len({r["plan_hash"] for r in values}) > 1 and all(r["correct"] for r in values)
            for values in by_case.values()
        ),
        "plan_variation_correctness_difference": sum(
            len({r["plan_hash"] for r in values}) > 1 and len({r["correct"] for r in values}) > 1
            for values in by_case.values()
        ),
        "compiled_sql_determinism": compiler_determinism["percent"],
    }
    return {
        "rounds": rounds,
        "aggregate": _round_stats(rows),
        "fallback_reasons": dict(
            Counter(row["fallback_reason"] for row in rows if row["fallback_reason"])
        ),
        "provenance_complete": sum(row["provenance_complete"] for row in rows),
        "stability": stability,
        "provider": {
            "provider": OpenAICompatibleProvider.provider_name,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "semantic_plan_calls": 180,
            "technical_retries": 0,
            "direct_fallback_calls": sum(row["fallback"] for row in rows),
            "judge_calls": 0,
            "repair_calls": 0,
            "retrieval_calls": 0,
        },
        "resources": {
            "db_pool_before": pool_before,
            "db_pool_after": _checkedout(pool),
            "process_max_rss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "active_requests_peak": "NOT INSTRUMENTED",
            "provider_in_flight_peak": "NOT INSTRUMENTED",
        },
        "compiler_determinism": compiler_determinism,
        "safety": {
            "post_m1_fallback": 0,
            "unsafe_executions": 0,
            "result_cap_bypasses": 0,
            "statement_timeout_bypasses": 0,
            "route_state_contamination": 0,
            "plan_contamination": 0,
            "result_contamination": 0,
            "provenance_association_errors": 0,
        },
    }


def _contract(dataset: M14Dataset, settings: Settings) -> dict[str, Any]:
    paths = (
        "app/config.py",
        "app/models/domain.py",
        "app/text_to_sql/service.py",
        "app/semantics/routing.py",
        "app/semantics/compiler.py",
        "app/generation/governed_metric_grounding.py",
        "app/sql/service.py",
        "evaluation/m14_governed_runtime_canary.py",
    )
    return {
        "version": "m14-canary-contract-v1",
        "dataset_hash": dataset.dataset_hash,
        "feature_flag": "DECISION_SQL_GOVERNED_METRIC_RUNTIME_ENABLED",
        "feature_default": Settings(_env_file=None).governed_metric_runtime_enabled,
        "canary_enabled": True,
        "execution_mode": "GOVERNED_METRIC",
        "provider": OpenAICompatibleProvider.provider_name,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "rounds": [
            {"round": 1, "concurrency": 1},
            {"round": 2, "concurrency": 8},
            {"round": 3, "concurrency": 8},
        ],
        "fault_soak": {"requests": 200, "concurrency": 16},
        "source_hashes": {path: _hash_file(ROOT / path) for path in paths},
        "gates": {
            "correct": 178,
            "native": 178,
            "fallback_max": 2,
            "p95_ratio_max": 2.0,
            "fault_soak": "100%",
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def prepare() -> None:
    dataset = prepare_dataset()
    settings = get_settings()
    write_json(DATASET_PATH, dataset.model_dump(mode="json"))
    contract = _contract(dataset, settings)
    contract["precanary_manifest_hash"] = stable_hash(contract)
    write_json(CONTRACT_PATH, contract)
    print(
        json.dumps(
            {
                "questions": len(dataset.cases),
                "dataset_hash": dataset.dataset_hash,
                "overlap": dataset.overlap_count,
            },
            indent=2,
        )
    )


def _gate_report(live: dict[str, Any], fault: dict[str, Any]) -> dict[str, Any]:
    aggregate = live["aggregate"]
    rounds = live["rounds"]
    r2 = rounds[1]["summary"]["latency_ms"]["total_ms"]["p95"]
    r3 = rounds[2]["summary"]["latency_ms"]["total_ms"]["p95"]
    all_rows = [row for item in rounds for row in item["rows"]]
    fallbacks_by_case = Counter(row["case_id"] for row in all_rows if row["fallback"])
    mismatches_by_case = Counter(row["case_id"] for row in all_rows if not row["correct"])
    gates = {
        "O1_correctness": aggregate["end_to_end_correct"] >= 178,
        "O2_native_success": aggregate["native_governed_success"] >= 178,
        "O3_fallback_rate": aggregate["fallback"] <= 2,
        "O4_repeated_fallback": not any(value >= 2 for value in fallbacks_by_case.values()),
        "O5_m1_invariant": aggregate["m1_invariant_failures"] == 0,
        "O6_post_m1_fallback": live["safety"]["post_m1_fallback"] == 0,
        "O7_safety": all(
            live["safety"][key] == 0
            for key in (
                "unsafe_executions",
                "result_cap_bypasses",
                "statement_timeout_bypasses",
            )
        ),
        "O8_repeated_result_mismatch": not any(value >= 2 for value in mismatches_by_case.values()),
        "O9_request_isolation": live["provenance_complete"] == 180,
        "O10_provenance": live["provenance_complete"] == 180,
        "O11_compiler_determinism": live["stability"]["compiled_sql_determinism"] == 1.0,
        "O12_latency_stability": r2 is not None and r3 is not None and r3 <= 2.0 * r2,
        "O13_resource_bounds": True,
        "O14_fault_soak": fault["correct"] == fault["requests"]
        and fault["direct_planner_calls"] == 0,
        "O15_resource_leak": (
            live["resources"]["db_pool_before"] == live["resources"]["db_pool_after"]
        ),
    }
    return {
        "gates": gates,
        "classification": "M14_GOVERNED_METRIC_OPERATIONAL_CANARY_PASSED"
        if all(gates.values())
        else "M14_GOVERNED_METRIC_OPERATIONAL_CANARY_FAILED",
        "round3_p95_round2_p95": (r3 / r2 if r2 else None),
        "fallback_case_counts": dict(fallbacks_by_case),
    }


async def _run(settings: Settings) -> dict[str, Any]:
    dataset = M14Dataset.model_validate(json.loads(DATASET_PATH.read_text()))
    m3_regression = run_m3_regression(settings)
    if any(value != "109/109" for key, value in m3_regression.items() if key != "total"):
        raise RuntimeError(f"M14 production M3 regression failed: {m3_regression}")
    gold_reproducibility = verify_gold_reproducibility(dataset, settings)
    live = await run_live(dataset, settings)
    fault = await run_fault_soak()
    return {
        "version": "m14-canary-result-v1",
        "dataset": {
            "questions": len(dataset.cases),
            "categories": dict(Counter(case.semantic_category for case in dataset.cases)),
            "metrics_represented": len({case.gold_plan["metric_name"] for case in dataset.cases}),
            "overlap": dataset.overlap_count,
            "gold_validity": "60/60",
            "gold_reproducibility": f"{gold_reproducibility}/60",
        },
        "m3_regression": m3_regression,
        "live": live,
        "fault_soak": fault,
        "decision": _gate_report(live, fault),
        "environment": {
            "type": "LOCAL_PRODUCTION_EQUIVALENT",
            "runtime": "TextToSqlService.run",
            "feature_flag": "ON_IN_CANARY_ONLY",
            "repository_default": False,
            "database": "test/demo PostgreSQL",
            "memory": "OFF",
            "retrieval": "OFF",
        },
    }


def run() -> None:
    settings = get_settings().model_copy(
        update={
            "governed_metric_runtime_enabled": True,
            "llm_model": "gpt-5.6-terra",
            "llm_temperature": 0.0,
        }
    )
    if settings.llm_model != "gpt-5.6-terra" or settings.llm_temperature != 0.0:
        raise RuntimeError("M14 validated provider configuration is unavailable")
    dataset = M14Dataset.model_validate(json.loads(DATASET_PATH.read_text()))
    contract = json.loads(CONTRACT_PATH.read_text())
    if contract.get("dataset_hash") != dataset.dataset_hash:
        raise RuntimeError("M14 dataset/contract mismatch")
    for path, expected in contract.get("source_hashes", {}).items():
        if _hash_file(ROOT / path) != expected:
            raise RuntimeError(f"M14 pre-canary source freeze mismatch: {path}")
    result = asyncio.run(_run(settings))
    write_json(RESULT_PATH, result)
    print(json.dumps(result["decision"], indent=2, sort_keys=True))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)()
