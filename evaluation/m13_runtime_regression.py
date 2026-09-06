"""Fresh provider-backed regression for the bounded M13 runtime route."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.catalog.models import SchemaContext
from app.config import Settings, get_settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.generation.provider import (
    OpenAICompatibleProvider,
    _generation_messages,
    _metric_grounding_messages,
)
from app.models.domain import ExecutionMode, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.requests import MetricRequest
from app.sql.models import QueryExecution, QueryPlan
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.m12r_governed_semantic_generation import (
    M12RCase,
    enumerate_plans,
    historical_question_hashes,
    normalize_question,
)
from evaluation.m12r_governed_semantic_generation import (
    load_dataset as load_m12r_dataset,
)
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    _snapshot,
    compare_snapshots,
    stable_hash,
    text_hash,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
DATASET_PATH = FIXTURES / "m13_runtime_dataset.json"
CONTRACT_PATH = FIXTURES / "m13_runtime_contract.json"
RESULT_PATH = FIXTURES / "m13_runtime_result.json"


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


class M13Case(BaseModel):
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


class M13Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    cases: tuple[M13Case, ...]
    historical_overlap_count: int
    dataset_hash: str


def _fixture() -> DiagnosticFixture:
    descriptor = {
        "fixture_id": "M13_RUNTIME_GOLD_DEMO_DATABASE",
        "fixture_version": "m13-runtime-v1",
        "schema_version": "decision_sql_demo_schema",
        "seed": "repository-seeded-demo",
        "scenario_tags": ["M13_RUNTIME_GOLD"],
    }
    return DiagnosticFixture(**descriptor, content_hash=stable_hash(descriptor))


def _metric_text(description: str) -> str:
    return description.rstrip(".").lower()


def _question_candidates(
    item: dict[str, Any], metric_description: str, dimensions: tuple[str, ...]
) -> tuple[str, ...]:
    metric = _metric_text(metric_description)
    names = tuple(d.replace("_", " ") for d in dimensions)
    wording = int(item["plan_hash"][:8], 16) % 5
    if not names:
        templates = (
            f"Please report the overall {metric}.",
            f"Give me a summary of {metric}.",
            f"I need the current total for {metric}.",
            f"Can you state the aggregate {metric}?",
            f"Summarize {metric} for this dataset.",
        )
    elif len(names) == 1:
        templates = (
            f"Show a breakdown of {metric} for every {names[0]}.",
            f"List {metric} across each {names[0]}.",
            f"How does {metric} distribute among {names[0]} values?",
            f"Provide {metric} organized by {names[0]}.",
            f"For each {names[0]}, give the corresponding {metric}.",
        )
    else:
        templates = (
            f"Compare {metric} across {names[0]} and {names[1]}.",
            f"Show {metric} with {names[0]} followed by {names[1]} as the groups.",
            f"Give the {metric} breakdown using {names[0]} and {names[1]}.",
            f"Summarize {metric} for every combination of {names[0]} and {names[1]}.",
            f"Report {metric} grouped by {names[0]} together with {names[1]}.",
        )
    return tuple(templates[(wording + offset) % len(templates)] for offset in range(len(templates)))


def _select(plans: tuple[dict[str, Any], ...], category: str, count: int) -> list[dict[str, Any]]:
    candidates = [item for item in plans if item["semantic_category"] == category]
    by_metric: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_metric.setdefault(item["metric_name"], []).append(item)
    selected: list[dict[str, Any]] = []
    # Round-robin metrics keeps the fresh integration set broad without choosing
    # cases based on any provider result.
    metrics = sorted(by_metric)
    cursor = 0
    while len(selected) < count:
        metric = metrics[cursor % len(metrics)]
        # Runtime regression permits semantic-plan repetition because it is
        # testing wiring on fresh language, not expanding M3 capacity.
        options = by_metric[metric]
        if options:
            selected.append(options[(cursor // len(metrics)) % len(options)])
        cursor += 1
    return selected


def _shared_serializer(glossary: str) -> Callable[[SchemaContext], str]:
    def render(context: SchemaContext) -> str:
        return (
            f"PUBLIC GOVERNED SEMANTIC CONTEXT:\n{glossary}\n\n"
            f"FULL QUERYABLE POSTGRES SCHEMA:\n{serialize_schema_context(context)}"
        )

    return render


def prepare_dataset() -> M13Dataset:
    catalog = build_m3_catalog()
    plans = enumerate_plans(catalog)
    selected = (
        _select(plans, "SCALAR", 12)
        + _select(plans, "ONE_DIMENSION", 18)
        + _select(plans, "TWO_DIMENSIONS", 18)
    )
    historical = historical_question_hashes()
    if DATASET_PATH.exists():
        # Re-preparation must be idempotent; the current M13 artifact is not a
        # prior evaluation corpus and must not make its own wording drift.
        historical.difference_update(
            case["question_hash"] for case in json.loads(DATASET_PATH.read_text())["cases"]
        )
    historical.update(
        case["question_hash"]
        for case in json.loads((FIXTURES / "m12r_dataset.json").read_text())["cases"]
    )
    used: set[str] = set()
    question_items: list[dict[str, Any]] = []
    for item in selected:
        metric = catalog.metric(item["metric_name"])
        question = None
        question_hash = None
        for candidate in _question_candidates(item, metric.description, tuple(item["dimensions"])):
            candidate_hash = text_hash(normalize_question(candidate))
            if candidate_hash not in historical and candidate_hash not in used:
                question = candidate
                question_hash = candidate_hash
                break
        if question is None or question_hash is None:
            raise RuntimeError(f"fresh question overlap: {item['plan_id']}")
        used.add(question_hash)
        question_items.append({**item, "question": question, "question_hash": question_hash})

    settings = get_settings()
    service = SqlSafetyService(build_reader_engine(settings))
    compiler = MetricCompiler(catalog)
    fixture = _fixture()
    cases: list[M13Case] = []
    for index, item in enumerate(question_items):
        request = MetricRequest(
            metric_name=item["metric_name"], dimensions=tuple(item["dimensions"])
        ).normalized()
        first = compiler.compile_metric(request)
        second = compiler.compile_metric(request)
        if isinstance(first, MetricCompilationFailure) or isinstance(
            second, MetricCompilationFailure
        ):
            raise RuntimeError(f"gold compiler failure: {item['plan_id']}")
        if first.sql != second.sql:
            raise RuntimeError(f"gold compiler nondeterminism: {item['plan_id']}")
        planned = service.plan(first)
        if not isinstance(planned, QueryPlan):
            raise RuntimeError(f"gold M1 failure: {item['plan_id']}")
        execution = service.execute(planned)
        if not isinstance(execution, QueryExecution) or execution.truncated:
            raise RuntimeError(f"gold execution failure: {item['plan_id']}")
        mode = ComparisonMode.SCALAR if not item["dimensions"] else ComparisonMode.VALUE_BAG
        snapshot = _snapshot(execution, text_hash(first.sql), fixture, order_sensitive=False)
        cases.append(
            M13Case(
                case_id=f"m13-runtime-{index:03d}",
                semantic_category=item["semantic_category"],
                question=item["question"],
                question_hash=item["question_hash"],
                gold_plan=item["plan"],
                gold_plan_hash=item["plan_hash"],
                gold_sql_hash=text_hash(first.sql),
                comparison_mode=mode,
                gold_columns=snapshot.columns,
                gold_typed_rows=snapshot.typed_rows,
                gold_row_count=snapshot.row_count,
                gold_truncated=snapshot.truncated,
                gold_result_hash=snapshot.result_hash,
            )
        )
    dataset_payload = [case.model_dump(mode="json") for case in cases]
    dataset = M13Dataset(
        version="m13-runtime-dataset-v1",
        cases=tuple(cases),
        historical_overlap_count=sum(case.question_hash in historical for case in cases),
        dataset_hash=stable_hash(dataset_payload),
    )
    if len(cases) != 48 or dataset.historical_overlap_count != 0:
        raise RuntimeError("M13 fresh dataset integrity failure")
    return dataset


def _gold_snapshot(case: M13Case | M12RCase) -> DiagnosticExecutionSnapshot:
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


def _correct(result: Any, case: M13Case) -> bool:
    if result.execution is None or result.execution_error is not None:
        return False
    snapshot = _snapshot(
        result.execution,
        text_hash(result.plan.normalized_sql) if result.plan else "missing",
        _fixture(),
        order_sensitive=False,
    )
    return compare_snapshots(
        snapshot, _gold_snapshot(case), case.comparison_mode, order_entitled=False
    )


def _stage(result: Any, *, governed: bool) -> str | None:
    if result.status.value == "SUCCEEDED":
        return None if result.execution is not None else "EXECUTION_FAILURE"
    if governed:
        route = result.diagnostics.get("route_state")
        if route == "GOVERNED_FALLBACK_PROVIDER_FAILURE":
            return "PLAN_PROVIDER_FAILURE"
        if route == "GOVERNED_FALLBACK_INVALID_PLAN":
            return "PLAN_VALIDATION_FAILURE"
        if route == "GOVERNED_POLICY_INVARIANT_FAILURE":
            return "GOVERNED_M1_REJECTION"
        if route == "GOVERNED_EXECUTION_FAILURE":
            return "GOVERNED_EXECUTION_FAILURE"
        return "GOVERNED_RESULT_MISMATCH"
    if result.failure_stage is not None and result.failure_stage.value == "POLICY_REJECTION":
        return "DIRECT_M1_REJECTION"
    return "DIRECT_SQL_EXTRACTION_FAILURE"


def _call_order(case_id: str) -> str:
    return (
        "CONTROL_RUNTIME_FIRST"
        if int(text_hash(case_id)[:8], 16) % 2 == 0
        else "GOVERNED_RUNTIME_FIRST"
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile)))
    return ordered[index]


def run_m3_regression(settings: Settings) -> dict[str, Any]:
    """Exercise the production-owned compiler boundary without provider calls."""
    catalog = build_m3_catalog()
    service = SqlSafetyService(build_reader_engine(settings), settings=settings)
    runtime = TextToSqlService(
        SchemaContextResolver(service.catalog),
        OpenAICompatibleProvider(settings),
        service,
        context_mode=SchemaContextMode.FULL_COMPACT,
        settings=settings,
    )
    expected = {case.gold_plan_hash: case for case in load_m12r_dataset().cases}
    compiler = runtime.governed_metric_route.compiler
    counts = {"plan_validation": 0, "compiler": 0, "m1": 0, "execution": 0, "equivalence": 0}
    for item in enumerate_plans(catalog):
        request = grounding_to_request(
            GovernedMetricGroundingDTO.model_validate(item["plan"]), catalog
        )
        compiled = compiler.compile_metric(request)
        if isinstance(compiled, MetricCompilationFailure):
            continue
        counts["plan_validation"] += 1
        counts["compiler"] += 1
        planned = service.plan(compiled)
        if not isinstance(planned, QueryPlan):
            continue
        counts["m1"] += 1
        execution = service.execute(planned)
        if not isinstance(execution, QueryExecution):
            continue
        counts["execution"] += 1
        case = expected[item["plan_hash"]]
        actual = _snapshot(execution, text_hash(compiled.sql), _fixture(), order_sensitive=False)
        if compare_snapshots(
            actual, _gold_snapshot(case), case.comparison_mode, order_entitled=False
        ):
            counts["equivalence"] += 1
    return {
        "total": len(expected),
        **{key: f"{value}/{len(expected)}" for key, value in counts.items()},
    }


async def _run(dataset: M13Dataset, settings: Settings) -> dict[str, Any]:
    catalog = build_m3_catalog()
    schema = SqlSafetyService(build_reader_engine(settings), settings=settings)
    glossary = public_metric_glossary(catalog)
    serializer = _shared_serializer(glossary)
    direct_provider = OpenAICompatibleProvider(settings)
    governed_provider = OpenAICompatibleProvider(settings)
    direct = TextToSqlService(
        SchemaContextResolver(schema.catalog),
        direct_provider,
        schema,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=serializer,
        settings=settings,
    )
    governed = TextToSqlService(
        SchemaContextResolver(schema.catalog),
        governed_provider,
        schema,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=serializer,
        settings=settings,
    )
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        direct_request = TextToSqlRequest(
            question=case.question, correlation_id=case.case_id, execution_mode=ExecutionMode.DIRECT
        )
        governed_request = TextToSqlRequest(
            question=case.question,
            correlation_id=case.case_id,
            execution_mode=ExecutionMode.GOVERNED_METRIC,
        )
        if _call_order(case.case_id) == "CONTROL_RUNTIME_FIRST":
            direct_result = await direct.run(direct_request)
            governed_result = await governed.run(governed_request)
        else:
            governed_result = await governed.run(governed_request)
            direct_result = await direct.run(direct_request)
        rows.append(
            {
                "case_id": case.case_id,
                "semantic_category": case.semantic_category,
                "call_order": _call_order(case.case_id),
                "direct_correct": _correct(direct_result, case),
                "governed_correct": _correct(governed_result, case),
                "governed_native_success": (
                    governed_result.generation_path.value == "GOVERNED_METRIC"
                )
                and governed_result.status.value == "SUCCEEDED",
                "direct_stage": _stage(direct_result, governed=False),
                "governed_stage": _stage(governed_result, governed=True),
                "governed_route_state": governed_result.diagnostics.get("route_state"),
                "fallback_reason": governed_result.diagnostics.get("fallback_reason"),
                "direct_latency_ms": direct_result.generation_latency_ms,
                "governed_latency_ms": governed_result.diagnostics.get("route_total_latency_ms"),
            }
        )
    n = len(rows)
    a = sum(row["direct_correct"] and row["governed_correct"] for row in rows)
    b = sum(row["direct_correct"] and not row["governed_correct"] for row in rows)
    c = sum(not row["direct_correct"] and row["governed_correct"] for row in rows)
    d = sum(not row["direct_correct"] and not row["governed_correct"] for row in rows)
    categories = {}
    for category in ("SCALAR", "ONE_DIMENSION", "TWO_DIMENSIONS"):
        subset = [row for row in rows if row["semantic_category"] == category]
        categories[category] = {
            "n": len(subset),
            "direct_correct": sum(row["direct_correct"] for row in subset),
            "governed_correct": sum(row["governed_correct"] for row in subset),
        }
    direct_latencies = [
        row["direct_latency_ms"] for row in rows if row["direct_latency_ms"] is not None
    ]
    governed_latencies = [
        row["governed_latency_ms"]
        for row in rows
        if row["governed_latency_ms"] is not None
    ]
    direct_failures = Counter(row["direct_stage"] for row in rows if row["direct_stage"])
    governed_failures = Counter(row["governed_stage"] for row in rows if row["governed_stage"])
    fallback_count = sum(
        row["governed_route_state"] != "GOVERNED_SUCCESS" for row in rows
    )
    return {
        "version": "m13-runtime-result-v1",
        "rows": rows,
        "summary": {
            "n": n,
            "direct_correct": sum(row["direct_correct"] for row in rows),
            "governed_correct": sum(row["governed_correct"] for row in rows),
            "governed_native_success": sum(row["governed_native_success"] for row in rows),
            "paired": {"A": a, "B": b, "C": c, "D": d, "C_minus_B": c - b},
            "categories": categories,
            "fallback_reasons": dict(
                Counter(row["fallback_reason"] for row in rows if row["fallback_reason"])
            ),
            "call_orders": dict(Counter(row["call_order"] for row in rows)),
            "fallback_count": fallback_count,
            "direct_failure_stages": dict(direct_failures),
            "governed_failure_stages": dict(governed_failures),
            "latency_ms": {
                "direct_median": _percentile(direct_latencies, 0.5),
                "direct_p95": _percentile(direct_latencies, 0.95),
                "governed_median": _percentile(governed_latencies, 0.5),
                "governed_p95": _percentile(governed_latencies, 0.95),
            },
        },
        "provider": {
            "provider": OpenAICompatibleProvider.provider_name,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "direct_calls": 48 + fallback_count,
            "semantic_plan_calls": 48,
            "fallback_generated_direct_calls": fallback_count,
            "technical_retries": 0,
            "judge_calls": 0,
            "repair_calls": 0,
            "retrieval_calls": 0,
        },
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def prepare() -> None:
    dataset = prepare_dataset()
    settings = get_settings()
    write_json(DATASET_PATH, dataset.model_dump(mode="json"))
    contract = {
            "version": "m13-runtime-contract-v1",
            "dataset_hash": dataset.dataset_hash,
            "source_paths": [
                "app/models/domain.py",
                "app/config.py",
                "app/text_to_sql/service.py",
                "app/semantics/routing.py",
                "app/semantics/compiler.py",
                "app/generation/governed_metric_grounding.py",
                "app/generation/provider.py",
                "app/sql/service.py",
            ],
            "provider": OpenAICompatibleProvider.provider_name,
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "feature_default": Settings(_env_file=None).governed_metric_runtime_enabled,
            "execution_modes": ["DIRECT", "GOVERNED_METRIC"],
            "fallback_before_m1": True,
            "fallback_after_m1": False,
            "source_hashes": {
                path: _file_hash(ROOT / path)
                for path in (
                    "app/models/domain.py",
                    "app/config.py",
                    "app/text_to_sql/service.py",
                    "app/semantics/routing.py",
                    "app/semantics/compiler.py",
                    "app/semantics/catalog.py",
                    "app/generation/governed_metric_grounding.py",
                    "app/generation/provider.py",
                    "app/sql/parser.py",
                    "app/sql/policy.py",
                    "app/sql/service.py",
                    "evaluation/m13_runtime_regression.py",
                )
            },
            "prompt_contract": {
                "control_hash": stable_hash(
                    _generation_messages("{{M13_QUESTION}}", "{{M13_CONTEXT}}")
                ),
                "intervention_hash": stable_hash(
                    _metric_grounding_messages("{{M13_QUESTION}}", "{{M13_CONTEXT}}")
                ),
            },
    }
    contract["preexperiment_manifest_hash"] = stable_hash(contract)
    write_json(CONTRACT_PATH, contract)
    print(json.dumps({"cases": len(dataset.cases), "dataset_hash": dataset.dataset_hash}, indent=2))


def run() -> None:
    dataset = M13Dataset.model_validate(json.loads(DATASET_PATH.read_text()))
    settings = get_settings().model_copy(update={"governed_metric_runtime_enabled": True})
    m3_regression = run_m3_regression(settings)
    if any(value != "109/109" for key, value in m3_regression.items() if key != "total"):
        raise RuntimeError(f"M13 production M3 regression failed: {m3_regression}")
    result = asyncio.run(_run(dataset, settings))
    result["m3_regression"] = m3_regression
    write_json(RESULT_PATH, result)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    args = parser.parse_args()
    (prepare if args.command == "prepare" else run)()
