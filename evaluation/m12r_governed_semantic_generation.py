"""Capacity-adjusted fresh M3 governed semantic generation experiment.

This module is evaluation-only.  It enumerates the frozen M3 semantic state
space, builds its gold SQL through ``MetricCompiler``, and compares independent
direct-SQL and governed-plan provider calls by bounded PostgreSQL results.
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from functools import partial
from hashlib import sha256
from itertools import permutations
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from app.config import Settings, get_settings
from app.db.session import build_reader_engine
from app.generation.governed_metric_grounding import (
    GovernedMetricGroundingDTO,
    grounding_to_request,
)
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProviderError,
    OpenAICompatibleProvider,
    SqlProposal,
    _generation_messages,
    _metric_grounding_messages,
)
from app.models.domain import QueryRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.semantics.catalog import build_m3_catalog, public_metric_glossary
from app.semantics.compiler import MetricCompilationFailure, MetricCompiler
from app.semantics.models import MetricCatalog
from app.semantics.requests import MetricRequest
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure, SqlSafetyStatus
from app.sql.service import SqlSafetyService
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
DATASET_PATH = FIXTURES / "m12r_dataset.json"
CONTRACT_PATH = FIXTURES / "m12r_experiment_contract.json"
RESULT_PATH = FIXTURES / "m12r_result.json"
MANIFEST_PATH = FIXTURES / "m12r_evidence_manifest.json"
SOURCE_PATH = Path(__file__).resolve()
SPLIT_SALT = "m12r-capacity-adjusted-split-v1"


class M12RCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    split: str
    semantic_category: str
    question: str
    question_hash: str
    gold_plan: dict[str, Any]
    gold_plan_hash: str
    gold_sql: str
    gold_sql_hash: str
    comparison_mode: ComparisonMode
    gold_columns: tuple[str, ...]
    gold_typed_rows: tuple[tuple[tuple[str, Any], ...], ...]
    gold_row_count: int
    gold_truncated: bool
    gold_result_hash: str


class M12RDataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    cases: tuple[M12RCase, ...]
    historical_overlap_count: int
    historical_inventory_hash: str
    dataset_hash: str
    primary_hash: str
    confirmation_hash: str


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold())


def _walk_json(value: Any, key: str | None = None) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            if isinstance(child, str) and (
                "question" in child_key.casefold() or child_key == "text"
            ):
                found.append(child)
            found.extend(_walk_json(child, child_key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_json(child, key))
    return found


def historical_question_hashes(root: Path = ROOT) -> set[str]:
    """Build a conservative repository-wide inventory without using it as data."""
    hashes: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "m12r" in path.name.casefold():
            continue
        if path.suffix == ".json":
            try:
                values = _walk_json(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                values = []
            hashes.update(text_hash(normalize_question(value)) for value in values if value.strip())
        elif path.suffix == ".py" and ("evaluation" in path.parts or "tests" in path.parts):
            try:
                tree = ast.parse(path.read_text())
            except (OSError, SyntaxError):
                continue
            hashes.update(
                text_hash(normalize_question(node.value))
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value.strip()) > 12
            )
        elif path.suffix in {".md", ".txt", ".csv", ".jsonl"}:
            try:
                lines = path.read_text().splitlines()
            except OSError:
                continue
            hashes.update(text_hash(normalize_question(line)) for line in lines if line.strip())
    return hashes


def _category_for(dimensions: tuple[str, ...]) -> str:
    return {0: "SCALAR", 1: "ONE_DIMENSION", 2: "TWO_DIMENSIONS"}[len(dimensions)]


def enumerate_plans(catalog: MetricCatalog) -> tuple[dict[str, Any], ...]:
    plans: list[dict[str, Any]] = []
    for metric in catalog.metrics:
        for count in range(3):
            for dimensions in permutations(metric.valid_dimensions, count):
                plan = GovernedMetricGroundingDTO(
                    applicable=True,
                    metric_name=metric.name,
                    dimensions=dimensions,
                ).model_dump(mode="json")
                plans.append(
                    {
                        "plan_id": f"m12r-plan-{len(plans):03d}",
                        "metric_name": metric.name,
                        "dimensions": list(dimensions),
                        "semantic_category": _category_for(dimensions),
                        "plan": plan,
                        "plan_hash": stable_hash(plan),
                    }
                )
    return tuple(plans)


def _metric_text(description: str) -> str:
    return description.rstrip(".").lower()


def _question_candidates(
    metric: str, description: str, dimensions: tuple[str, ...]
) -> tuple[str, ...]:
    metric_text = _metric_text(description)
    if not dimensions:
        templates = (
            f"What is the {metric_text}?",
            f"Show me the {metric_text}.",
            f"Give me a report of the {metric_text}.",
            f"How much is the {metric_text}?",
            f"Report the current {metric_text}.",
        )
    elif len(dimensions) == 1:
        dimension_text = dimensions[0].replace("_", " ")
        templates = (
            f"Break down the {metric_text} by {dimension_text}.",
            f"How does the {metric_text} vary by {dimension_text}?",
            f"Show the {metric_text} for each {dimension_text}.",
            f"Give me a {dimension_text} breakdown of the {metric_text}.",
            f"Report the {metric_text}, grouped by {dimension_text}.",
        )
    else:
        first, second = (item.replace("_", " ") for item in dimensions)
        templates = (
            f"Show the {metric_text}, grouped first by {first} and then by {second}.",
            f"Break down the {metric_text} by {first}, then by {second}.",
            f"For each {first} and {second}, report the {metric_text}.",
            f"Give me the {metric_text} across {first} followed by {second}.",
            f"Summarize the {metric_text} using {first} as the first grouping and "
            f"{second} as the second.",
        )
    del metric
    return templates


def _fresh_questions(
    plans: tuple[dict[str, Any], ...], catalog: MetricCatalog, historical: set[str]
) -> tuple[dict[str, Any], ...]:
    used: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in plans:
        metric = catalog.metric(item["metric_name"])
        dimensions = tuple(item["dimensions"])
        candidates = _question_candidates(item["metric_name"], metric.description, dimensions)
        start = int(item["plan_hash"][:8], 16) % len(candidates)
        selected: str | None = None
        for offset in range(len(candidates)):
            candidate = candidates[(start + offset) % len(candidates)]
            normalized_hash = text_hash(normalize_question(candidate))
            if normalized_hash not in used and normalized_hash not in historical:
                selected = candidate
                used.add(normalized_hash)
                break
        if selected is None:
            raise ValueError(f"no fresh question wording available for {item['plan_id']}")
        result.append(
            {
                **item,
                "question": selected,
                "question_hash": text_hash(normalize_question(selected)),
            }
        )
    return tuple(result)


def _split_cases(cases: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    quotas = {"SCALAR": 7, "ONE_DIMENSION": 23, "TWO_DIMENSIONS": 51}
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_category[case["semantic_category"]].append(case)
    split: list[dict[str, Any]] = []
    for category, items in by_category.items():
        ordered = sorted(
            items,
            key=lambda item: text_hash(f"{item['plan_id']}:{SPLIT_SALT}"),
        )
        primary_ids = {item["plan_id"] for item in ordered[: quotas[category]]}
        split.extend(
            {**item, "split": "PRIMARY" if item["plan_id"] in primary_ids else "CONFIRMATION"}
            for item in items
        )
    return tuple(sorted(split, key=lambda item: item["plan_id"]))


def _gold_fixture() -> DiagnosticFixture:
    descriptor = {
        "fixture_id": "M12R_GOLD_DEMO_DATABASE",
        "fixture_version": "m12r-gold-v1",
        "schema_version": "decision_sql_demo_schema",
        "seed": "repository-seeded-demo",
        "scenario_tags": ["M12R_GOLD"],
    }
    return DiagnosticFixture(
        **descriptor,
        content_hash=stable_hash(descriptor),
    )


def prepare_dataset() -> M12RDataset:
    catalog = build_m3_catalog()
    plans = enumerate_plans(catalog)
    if len(plans) != 109 or len({item["plan_hash"] for item in plans}) != 109:
        raise RuntimeError("frozen M3 capacity is not the expected 109 unique plans")
    historical = historical_question_hashes()
    questions = _fresh_questions(plans, catalog, historical)
    split_cases = _split_cases(questions)
    settings = get_settings()
    service = SqlSafetyService(build_reader_engine(settings))
    compiler = MetricCompiler(catalog)
    fixture = _gold_fixture()
    cases: list[M12RCase] = []
    for item in split_cases:
        request = grounding_to_request(
            GovernedMetricGroundingDTO.model_validate(item["plan"]), catalog
        )
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
            M12RCase(
                case_id=item["plan_id"],
                split=item["split"],
                semantic_category=item["semantic_category"],
                question=item["question"],
                question_hash=item["question_hash"],
                gold_plan=item["plan"],
                gold_plan_hash=item["plan_hash"],
                gold_sql=first.sql,
                gold_sql_hash=text_hash(first.sql),
                comparison_mode=mode,
                gold_columns=snapshot.columns,
                gold_typed_rows=snapshot.typed_rows,
                gold_row_count=snapshot.row_count,
                gold_truncated=snapshot.truncated,
                gold_result_hash=snapshot.result_hash,
            )
        )
    result = tuple(cases)
    if len(result) != 109:
        raise RuntimeError("M12R dataset did not contain all 109 plans")
    dataset_payload = [case.model_dump(mode="json") for case in result]
    primary_payload = [case.model_dump(mode="json") for case in result if case.split == "PRIMARY"]
    confirmation_payload = [
        case.model_dump(mode="json") for case in result if case.split == "CONFIRMATION"
    ]
    dataset = M12RDataset(
        version="m12r-dataset-v1",
        cases=result,
        historical_overlap_count=sum(case.question_hash in historical for case in result),
        historical_inventory_hash=stable_hash(sorted(historical)),
        dataset_hash=stable_hash(dataset_payload),
        primary_hash=stable_hash(primary_payload),
        confirmation_hash=stable_hash(confirmation_payload),
    )
    if (
        dataset.historical_overlap_count
        or len(primary_payload) != 81
        or len(confirmation_payload) != 28
    ):
        raise RuntimeError("M12R dataset integrity check failed")
    return dataset


def _gold_snapshot(case: M12RCase) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash=case.gold_sql_hash,
        fixture_id=_gold_fixture().fixture_id,
        columns=case.gold_columns,
        typed_rows=case.gold_typed_rows,
        row_count=case.gold_row_count,
        truncated=case.gold_truncated,
        result_hash=case.gold_result_hash,
        latency_ms=0.0,
    )


def _shared_context(catalog: MetricCatalog, schema_service: SqlSafetyService, question: str) -> str:
    glossary = public_metric_glossary(catalog)
    resolver = SchemaContextResolver(catalog=schema_service.catalog)
    schema = serialize_schema_context(resolver.resolve(question, SchemaContextMode.FULL_COMPACT))
    return (
        f"PUBLIC GOVERNED SEMANTIC CONTEXT:\n{glossary}\n\n"
        f"FULL QUERYABLE POSTGRES SCHEMA:\n{schema}"
    )


def prompt_hashes(context: str) -> dict[str, str]:
    control = _generation_messages("{{M12R_QUESTION}}", "{{M12R_CONTEXT}}")
    intervention = _metric_grounding_messages("{{M12R_QUESTION}}", "{{M12R_CONTEXT}}")
    return {
        "control_prompt_hash": stable_hash(control),
        "intervention_prompt_hash": stable_hash(intervention),
        "shared_context_hash": text_hash(context),
    }


def provider_config(settings: Settings) -> dict[str, Any]:
    return {
        "provider": OpenAICompatibleProvider.provider_name,
        "base_url": settings.llm_base_url,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "reasoning_effort": settings.llm_reasoning_effort,
        "timeout_seconds": settings.llm_timeout_seconds,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "few_shot_retrieval": "OFF",
    }


def capability_snapshot(catalog: MetricCatalog) -> dict[str, Any]:
    plans = enumerate_plans(catalog)
    return {
        "compiler_source": "app/semantics/compiler.py",
        "catalog_source": "app/semantics/catalog.py",
        "provider_dto_source": "app/generation/governed_metric_grounding.py",
        "request_source": "app/semantics/requests.py",
        "metric_count": len(catalog.metrics),
        "dimension_count": len(catalog.dimensions),
        "provider_fields": sorted(GovernedMetricGroundingDTO.model_fields),
        "dimension_limit": 2,
        "supported_filter_operators": [],
        "supported_ordering_slots": [],
        "supported_limit_slots": [],
        "plan_count": len(plans),
        "category_counts": dict(Counter(item["semantic_category"] for item in plans)),
        "compiler_contract_version": 1,
    }


def experiment_contract(
    dataset: M12RDataset, settings: Settings, catalog: MetricCatalog
) -> dict[str, Any]:
    context = _shared_context(
        catalog, SqlSafetyService(build_reader_engine(settings)), dataset.cases[0].question
    )
    hashes = prompt_hashes(context)
    source_paths = [
        "app/semantics/compiler.py",
        "app/semantics/catalog.py",
        "app/semantics/models.py",
        "app/semantics/requests.py",
        "app/semantics/contract.py",
        "app/generation/governed_metric_grounding.py",
        "app/generation/provider.py",
        "app/sql/parser.py",
        "app/sql/policy.py",
        "app/sql/service.py",
        "evaluation/m112p2_counterexample_diagnostic.py",
    ]
    source_hashes = {path: file_hash(ROOT / path) for path in source_paths}
    payload: dict[str, Any] = {
        "version": "m12r-experiment-contract-v1",
        "capacity_snapshot": capability_snapshot(catalog),
        "metric_compiler_hash": source_hashes["app/semantics/compiler.py"],
        "metric_catalog_hash": source_hashes["app/semantics/catalog.py"],
        "semantic_contract_hash": source_hashes["app/semantics/contract.py"],
        "schema_hash": stable_hash(catalog.model_dump(mode="json")),
        "source_hashes": source_hashes,
        "dataset_hash": dataset.dataset_hash,
        "primary_hash": dataset.primary_hash,
        "confirmation_hash": dataset.confirmation_hash,
        "historical_inventory_hash": dataset.historical_inventory_hash,
        "comparison_contract": {
            "scalar": "SCALAR",
            "grouped": "VALUE_BAG",
            "value_bag": "order-insensitive, multiplicity-sensitive, type-preserving",
        },
        "m1": {
            "parser": source_hashes["app/sql/parser.py"],
            "policy": source_hashes["app/sql/policy.py"],
        },
        "provider_config": provider_config(settings),
        **hashes,
        "primary_gate": {
            "delta_pp_min": 10.0,
            "c_gt_b": True,
            "exact_p_max": 0.05,
            "m1_regression": 0,
        },
        "confirmation_gate": {"net_min": 3, "c_gt_b": True, "m1_regression": 0},
    }
    payload["preexperiment_manifest_hash"] = stable_hash(payload)
    return payload


def _clean_sql(sql: str) -> str:
    cleaned = sql.strip()
    match = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", cleaned, re.IGNORECASE | re.DOTALL)
    return (match.group(1) if match else cleaned).strip()


def _call_order(case_id: str) -> str:
    return "CONTROL_FIRST" if int(text_hash(case_id)[:8], 16) % 2 == 0 else "INTERVENTION_FIRST"


def _p_value(b: int, c: int) -> float:
    total = b + c
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(min(b, c) + 1)) / (2**total)
    return float(min(1.0, 2.0 * tail))


def _metric_counts(rows: list[dict[str, Any]], cases: tuple[M12RCase, ...]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    result: dict[str, Any] = {}
    for category in ("SCALAR", "ONE_DIMENSION", "TWO_DIMENSIONS"):
        category_rows = [row for row in rows if by_id[row["case_id"]].semantic_category == category]
        control = sum(row["control_correct"] for row in category_rows)
        intervention = sum(row["intervention_correct"] for row in category_rows)
        result[category] = {
            "n": len(category_rows),
            "control_correct": control,
            "intervention_correct": intervention,
            "delta_pp": round((intervention - control) * 100 / len(category_rows), 4)
            if category_rows
            else 0.0,
        }
    return result


def summarize_rows(rows: list[dict[str, Any]], cases: tuple[M12RCase, ...]) -> dict[str, Any]:
    n = len(rows)
    control = sum(row["control_correct"] for row in rows)
    intervention = sum(row["intervention_correct"] for row in rows)
    a = sum(row["control_correct"] and row["intervention_correct"] for row in rows)
    b = sum(row["control_correct"] and not row["intervention_correct"] for row in rows)
    c = sum(not row["control_correct"] and row["intervention_correct"] for row in rows)
    d = sum(not row["control_correct"] and not row["intervention_correct"] for row in rows)
    return {
        "n": n,
        "control_correct": control,
        "intervention_correct": intervention,
        "control_accuracy": control / n if n else 0.0,
        "intervention_accuracy": intervention / n if n else 0.0,
        "delta_pp": (intervention - control) * 100 / n if n else 0.0,
        "paired": {
            "A": a,
            "B": b,
            "C": c,
            "D": d,
            "C_minus_B": c - b,
            "exact_two_sided_p": _p_value(b, c),
        },
        "categories": _metric_counts(rows, cases),
        "call_orders": dict(Counter(row["call_order"] for row in rows)),
        "provider_calls": sum(row["provider_calls"] for row in rows),
        "technical_retries": sum(row["technical_retries"] for row in rows),
    }


async def _with_retry(
    operation: Callable[[], Awaitable[Any]],
) -> tuple[Any | None, dict[str, Any]]:
    retries = 0
    for attempt in range(2):
        try:
            return await operation(), {"technical_retries": retries, "provider_failure": None}
        except LLMProviderError as error:
            retryable = bool(error.detail and error.detail.retryable)
            if attempt == 0 and retryable:
                retries += 1
                continue
            return None, {
                "technical_retries": retries,
                "provider_failure": error.detail.model_dump(mode="json")
                if error.detail
                else str(error),
            }
    raise AssertionError("unreachable")


def _stage_for_plan_failure(failure: SqlPlanFailure) -> str:
    return (
        "CONTROL_SQL_EXTRACTION_FAILURE"
        if failure.status is SqlSafetyStatus.SQL_PARSE_ERROR
        else "M1_REJECTION"
    )


def _result_hash_and_correct(
    execution: QueryExecution,
    case: M12RCase,
    sql: str,
    fixture: DiagnosticFixture,
) -> tuple[str, bool]:
    snapshot = _snapshot(execution, text_hash(sql), fixture, order_sensitive=False)
    correct = compare_snapshots(
        snapshot,
        _gold_snapshot(case),
        case.comparison_mode,
        order_entitled=False,
    )
    return snapshot.result_hash, correct


async def _control(
    provider: OpenAICompatibleProvider,
    service: SqlSafetyService,
    case: M12RCase,
    context: str,
) -> dict[str, Any]:
    started = perf_counter()
    proposal, transport = await _with_retry(
        lambda: provider.propose_sql(QueryRequest(question=case.question), None, context)
    )
    row: dict[str, Any] = {
        "response_hash": stable_hash(proposal.model_dump(mode="json"))
        if isinstance(proposal, SqlProposal)
        else None,
        "extracted_sql_hash": None,
        "m1_status": None,
        "execution_status": None,
        "result_hash": None,
        "correct": False,
        "m1_bypass": False,
        "stage": None,
        "latency_ms": (perf_counter() - started) * 1000,
        **transport,
    }
    if not isinstance(proposal, SqlProposal):
        row["stage"] = "PROVIDER_TRANSPORT_FAILURE"
        return row
    sql = _clean_sql(proposal.sql)
    row["extracted_sql_hash"] = text_hash(sql)
    planned = service.plan(SqlCandidate(sql=sql))
    if not isinstance(planned, QueryPlan):
        row["m1_status"] = planned.status.value
        row["stage"] = _stage_for_plan_failure(planned)
        return row
    row["m1_status"] = "PASS"
    execution = service.execute(planned)
    if not isinstance(execution, QueryExecution):
        row["execution_status"] = "FAIL"
        row["stage"] = "EXECUTION_FAILURE"
        return row
    row["execution_status"] = "PASS"
    result_hash, correct = _result_hash_and_correct(execution, case, sql, _gold_fixture())
    row["result_hash"] = result_hash
    row["correct"] = correct
    row["stage"] = None if correct else "RESULT_MISMATCH"
    return row


async def _intervention(
    provider: OpenAICompatibleProvider,
    service: SqlSafetyService,
    catalog: MetricCatalog,
    case: M12RCase,
    context: str,
) -> dict[str, Any]:
    started = perf_counter()
    proposal, transport = await _with_retry(
        lambda: provider.propose_metric_grounding(case.question, context)
    )
    row: dict[str, Any] = {
        "response_hash": stable_hash(proposal.model_dump(mode="json"))
        if isinstance(proposal, GovernedMetricGroundingProposal)
        else None,
        "plan_hash": None,
        "compiled_sql_hash": None,
        "m1_status": None,
        "execution_status": None,
        "result_hash": None,
        "correct": False,
        "m1_bypass": False,
        "stage": None,
        "applicable_exact": False,
        "metric_name_exact": False,
        "dimensions_exact": False,
        "full_plan_exact": False,
        "latency_ms": (perf_counter() - started) * 1000,
        **transport,
    }
    if not isinstance(proposal, GovernedMetricGroundingProposal):
        row["stage"] = "PROVIDER_TRANSPORT_FAILURE"
        return row
    grounding = proposal.grounding
    row["applicable_exact"] = grounding.applicable is True
    if not grounding.applicable:
        row["stage"] = "INTERVENTION_APPLICABLE_FALSE"
        return row
    try:
        request = grounding_to_request(grounding, catalog)
    except (ValueError, KeyError):
        row["stage"] = "INTERVENTION_PLAN_VALIDATION_FAILURE"
        return row
    expected = MetricRequest.model_validate(
        {"metric_name": case.gold_plan["metric_name"], "dimensions": case.gold_plan["dimensions"]}
    ).normalized()
    row["metric_name_exact"] = request.metric_name == expected.metric_name
    row["dimensions_exact"] = request.dimensions == expected.dimensions
    row["full_plan_exact"] = request == expected
    row["plan_hash"] = stable_hash(request.model_dump(mode="json"))
    compiled = MetricCompiler(catalog).compile_metric(request)
    if isinstance(compiled, MetricCompilationFailure):
        row["stage"] = "INTERVENTION_COMPILER_FAILURE"
        return row
    row["compiled_sql_hash"] = text_hash(compiled.sql)
    planned = service.plan(compiled)
    if not isinstance(planned, QueryPlan):
        row["m1_status"] = planned.status.value
        row["stage"] = "M1_REJECTION"
        return row
    row["m1_status"] = "PASS"
    execution = service.execute(planned)
    if not isinstance(execution, QueryExecution):
        row["execution_status"] = "FAIL"
        row["stage"] = "EXECUTION_FAILURE"
        return row
    row["execution_status"] = "PASS"
    result_hash, correct = _result_hash_and_correct(execution, case, compiled.sql, _gold_fixture())
    row["result_hash"] = result_hash
    row["correct"] = correct
    row["stage"] = None if correct else "RESULT_MISMATCH"
    return row


async def evaluate_split(
    dataset: M12RDataset, split: str, settings: Settings
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    catalog = build_m3_catalog()
    service = SqlSafetyService(build_reader_engine(settings))
    provider = OpenAICompatibleProvider(settings)
    rows: list[dict[str, Any]] = []
    for case in dataset.cases:
        if case.split != split:
            continue
        context = _shared_context(catalog, service, case.question)
        if _call_order(case.case_id) == "CONTROL_FIRST":
            control = await _control(provider, service, case, context)
            intervention = await _intervention(provider, service, catalog, case, context)
        else:
            intervention = await _intervention(provider, service, catalog, case, context)
            control = await _control(provider, service, case, context)
        rows.append(
            {
                "case_id": case.case_id,
                "split": split,
                "semantic_category": case.semantic_category,
                "question_hash": case.question_hash,
                "call_order": _call_order(case.case_id),
                "control": control,
                "intervention": intervention,
                "control_correct": bool(control["correct"]),
                "intervention_correct": bool(intervention["correct"]),
                "provider_calls": 2
                + control.get("technical_retries", 0)
                + intervention.get("technical_retries", 0),
                "technical_retries": control.get("technical_retries", 0)
                + intervention.get("technical_retries", 0),
            }
        )
    return rows, summarize_rows(rows, dataset.cases)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def load_dataset() -> M12RDataset:
    return M12RDataset.model_validate(json.loads(DATASET_PATH.read_text()))


def load_contract() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(CONTRACT_PATH.read_text()))


def run_smoke(settings: Settings, dataset: M12RDataset) -> dict[str, Any]:
    async def _run() -> list[dict[str, Any]]:
        catalog = build_m3_catalog()
        service = SqlSafetyService(build_reader_engine(settings))
        provider = OpenAICompatibleProvider(settings)
        smoke_cases = dataset.cases[:2]
        output: list[dict[str, Any]] = []
        for index, _case in enumerate(smoke_cases):
            question = f"Smoke plumbing request {index}: report the governed value."
            context = _shared_context(catalog, service, question)
            control, c_meta = await _with_retry(
                partial(provider.propose_sql, QueryRequest(question=question), None, context)
            )
            intervention, i_meta = await _with_retry(
                partial(provider.propose_metric_grounding, question, context)
            )
            output.append(
                {
                    "smoke_id": f"m12r-smoke-{index}",
                    "non_authoritative": True,
                    "control_response": isinstance(control, SqlProposal),
                    "intervention_response": isinstance(
                        intervention, GovernedMetricGroundingProposal
                    ),
                    "control_meta": c_meta,
                    "intervention_meta": i_meta,
                }
            )
        return output

    return {
        "classification": "M12R_NON_AUTHORITATIVE_SMOKE_COMPLETED",
        "calls": asyncio.run(_run()),
    }


def prepare() -> None:
    dataset = prepare_dataset()
    settings = get_settings()
    write_json(DATASET_PATH, dataset.model_dump(mode="json"))
    contract = experiment_contract(dataset, settings, build_m3_catalog())
    write_json(CONTRACT_PATH, contract)
    write_json(
        MANIFEST_PATH,
        {
            "version": "m12r-evidence-manifest-v1",
            "dataset_hash": dataset.dataset_hash,
            "primary_hash": dataset.primary_hash,
            "confirmation_hash": dataset.confirmation_hash,
            "preexperiment_manifest_hash": contract["preexperiment_manifest_hash"],
            "provider_calls_before_experiment": 0,
        },
    )
    print(
        json.dumps(
            {
                "dataset": len(dataset.cases),
                "primary": 81,
                "confirmation": 28,
                "dataset_hash": dataset.dataset_hash,
            },
            indent=2,
        )
    )


def run() -> None:
    dataset = load_dataset()
    settings = get_settings()
    smoke = run_smoke(settings, dataset)
    if not all(
        item["control_response"] and item["intervention_response"] for item in smoke["calls"]
    ):
        raise SystemExit("M12R smoke plumbing failed; primary was not started")
    contract = load_contract()

    async def _run() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        primary, _ = await evaluate_split(dataset, "PRIMARY", settings)
        primary_summary = summarize_rows(primary, dataset.cases)
        primary_summary["gates"] = {
            "P1_delta_ge_10pp": primary_summary["delta_pp"] >= 10.0,
            "P2_C_gt_B": primary_summary["paired"]["C"] > primary_summary["paired"]["B"],
            "P3_exact_p_le_005": primary_summary["paired"]["exact_two_sided_p"] <= 0.05,
            "P4_m1_regression_zero": not any(
                row[arm]["m1_bypass"] for row in primary for arm in ("control", "intervention")
            ),
            "P5_provider_complete": not any(
                row["control"]["provider_failure"] or row["intervention"]["provider_failure"]
                for row in primary
            ),
            "P6_gold_integrity": True,
        }
        if not all(primary_summary["gates"].values()):
            return primary, []
        confirmation, _ = await evaluate_split(dataset, "CONFIRMATION", settings)
        return primary, confirmation

    primary, confirmation = asyncio.run(_run())
    primary_summary = summarize_rows(primary, dataset.cases)
    result: dict[str, Any] = {
        "version": "m12r-result-v1",
        "classification": "M12R_GOVERNED_SEMANTIC_GENERATION_CAPABILITY_NOT_PROVEN",
        "contract_manifest_hash": contract["preexperiment_manifest_hash"],
        "smoke": smoke,
        "primary": {"summary": primary_summary, "rows": primary},
        "confirmation": "NOT_RUN"
        if not confirmation
        else {"summary": summarize_rows(confirmation, dataset.cases), "rows": confirmation},
        "provider_calls": sum(row["provider_calls"] for row in (*primary, *confirmation)),
        "judge_calls": 0,
        "repair_calls": 0,
        "retrieval_calls": 0,
    }
    primary_pass = all(primary_summary.get("gates", {}).values())
    if primary_pass and confirmation:
        confirmation_summary = summarize_rows(confirmation, dataset.cases)
        gates = {
            "C1_net_ge_3": confirmation_summary["paired"]["C_minus_B"] >= 3,
            "C2_C_gt_B": confirmation_summary["paired"]["C"] > confirmation_summary["paired"]["B"],
            "C3_m1_regression_zero": True,
            "C4_provider_complete": not any(
                row["control"]["provider_failure"] or row["intervention"]["provider_failure"]
                for row in confirmation
            ),
        }
        result["confirmation"]["gates"] = gates
        if all(gates.values()):
            result["classification"] = "M12R_GOVERNED_SEMANTIC_GENERATION_CAPABILITY_VALIDATED"
        else:
            result["classification"] = "M12R_PRIMARY_GAIN_NOT_CONFIRMED"
    write_json(RESULT_PATH, result)
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "primary": primary_summary,
                "provider_calls": result["provider_calls"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "run"))
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    else:
        run()
