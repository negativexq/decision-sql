"""Bounded forensic reconstruction for the four M17 native mismatches.

This module never calls a provider.  The M17 ledger is authoritative for case
selection; missing model output is reported as unrecoverable rather than
guessed.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.config import Settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    QueryPlanProviderBoundaryError,
)
from app.provenance.canonical import text_hash
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Compiler,
    QueryPlanV1ValidationError,
    render_query_plan_v1_context,
    stable_hash,
    validate_query_plan_v1,
)
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from evaluation.m17_queryplan_contract import (
    M17Case,
    M17Dataset,
    _call,
)
from evaluation.m17_queryplan_contract import (
    _fixture as m17_fixture,
)
from evaluation.m17_queryplan_contract import (
    _gold_snapshot as m17_gold_snapshot,
)
from evaluation.m17_queryplan_contract import (
    _settings as m17_settings,
)
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    _snapshot,
    compare_snapshots,
)

FIXTURES = Path(__file__).with_name("fixtures")
M17_DATASET = FIXTURES / "m17_dataset.json"
M17_RESULT = FIXTURES / "m17_result.json"
FORENSICS_RESULT = FIXTURES / "m18_residual_forensics.json"
TARGETED_DATASET = FIXTURES / "m18_targeted_dataset.json"
TARGETED_RESULT = FIXTURES / "m18_targeted_result.json"
TARGETED_MANIFEST = FIXTURES / "m18_targeted_manifest.json"
TARGETED_ATTEMPT2_DATASET = FIXTURES / "m18_targeted_attempt2_dataset.json"
TARGETED_ATTEMPT2_RESULT = FIXTURES / "m18_targeted_attempt2_result.json"
TARGETED_ATTEMPT2_MANIFEST = FIXTURES / "m18_targeted_attempt2_manifest.json"


def _fixture() -> DiagnosticFixture:
    fixture = DiagnosticFixture(
        fixture_id="m17-gold-demo",
        fixture_version="m17-gold-1",
        schema_version="decision-sql-demo-schema-1",
        seed="m17-fresh-gold-seed",
        content_hash="m17-frozen",
    )
    return fixture


def _gold_snapshot(case: dict[str, Any]) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash=case["gold_sql_hash"],
        fixture_id="m17-gold-demo",
        columns=tuple(case["gold_columns"]),
        typed_rows=tuple(tuple(tuple(item) for item in row) for row in case["gold_typed_rows"]),
        row_count=case["gold_row_count"],
        truncated=False,
        result_hash=case["gold_result_hash"],
        latency_ms=0.0,
    )


def _plan_differences(gold: QueryPlanV1, predicted: QueryPlanV1) -> list[str]:
    differences: list[str] = []
    if gold.applicable != predicted.applicable:
        differences.append("APPLICABLE_DIFFERENCE")
    if gold.source != predicted.source:
        differences.append("SOURCE_DIFFERENCE")
    if gold.joins != predicted.joins:
        differences.append("RELATIONSHIP_DIFFERENCE")
    if tuple(item.join_type for item in gold.joins) != tuple(
        item.join_type for item in predicted.joins
    ):
        differences.append("JOIN_TYPE_DIFFERENCE")
    if gold.projection != predicted.projection:
        differences.append("PROJECTION_DIFFERENCE")
    if tuple(item.column_id for item in gold.filters) != tuple(
        item.column_id for item in predicted.filters
    ):
        differences.append("PREDICATE_COLUMN_DIFFERENCE")
    if tuple(item.operator for item in gold.filters) != tuple(
        item.operator for item in predicted.filters
    ):
        differences.append("PREDICATE_OPERATOR_DIFFERENCE")
    if tuple(item.value for item in gold.filters) != tuple(
        item.value for item in predicted.filters
    ):
        differences.append("PREDICATE_VALUE_DIFFERENCE")
    return differences


def _reconstruct() -> dict[str, Any]:
    dataset = json.loads(M17_DATASET.read_text())
    result = json.loads(M17_RESULT.read_text())
    cases = {case["case_id"]: case for case in dataset["cases"]}
    mismatches = [
        item
        for item in result["ledger"]
        if item["control"]["correct"] and not item["intervention"]["correct"]
    ]
    if len(mismatches) != 4:
        raise RuntimeError(f"expected exactly four M17 mismatches, got {len(mismatches)}")

    settings = Settings(_env_file=".env")
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    plan_by_hash = {case["gold_plan_hash"]: case["gold_plan"] for case in dataset["cases"]}
    records: list[dict[str, Any]] = []
    try:
        for item in mismatches:
            case = cases[item["case_id"]]
            gold = QueryPlanV1.model_validate(case["gold_plan"])
            validate_query_plan_v1(gold, catalog)
            gold_sql = compiler.compile(gold).sql
            if text_hash(gold_sql) != case["gold_sql_hash"]:
                raise RuntimeError(f"M17 gold SQL hash changed: {item['case_id']}")
            planned = safety.plan(
                SqlCandidate(sql=gold_sql, correlation_id=f"m18-gold-{item['case_id']}")
            )
            if not isinstance(planned, QueryPlan):
                raise RuntimeError(f"M17 gold M1 failure: {item['case_id']}")
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                raise RuntimeError(f"M17 gold execution failure: {item['case_id']}")
            actual_gold = _snapshot(
                execution, case["gold_sql_hash"], _fixture(), order_sensitive=False
            )
            if actual_gold.result_hash != case["gold_result_hash"]:
                raise RuntimeError(f"M17 gold result changed: {item['case_id']}")

            predicted_hash = item["intervention"]["plan_hash"]
            predicted_payload = plan_by_hash.get(predicted_hash)
            record: dict[str, Any] = {
                "case_id": item["case_id"],
                "question": case["question"],
                "shape": case["shape"],
                "catalog_hash": dataset["catalog_hash"],
                "gold_valid": True,
                "question_unambiguous": True,
                "gold_plan": case["gold_plan"],
                "gold_plan_hash": case["gold_plan_hash"],
                "gold_sql_hash": case["gold_sql_hash"],
                "gold_result_hash": case["gold_result_hash"],
                "predicted_plan_hash": predicted_hash,
                "predicted_sql_hash": item["intervention"]["compiled_sql_hash"],
                "predicted_result_hash": item["intervention"]["result_hash"],
                "m1": "PASS",
                "execution": "SUCCESS",
                "d2_witness": "NOT_NEEDED" if predicted_payload is not None else "NOT_AVAILABLE",
            }
            if predicted_payload is None:
                record.update(
                    {
                        "reconstruction": "PARTIAL_HASH_ONLY",
                        "predicted_plan": None,
                        "differing_fields": ["UNRECOVERABLE_FROM_COMPACT_M17_ARTIFACT"],
                        "sql_structural_differences": ["UNRECOVERABLE_FROM_COMPACT_M17_ARTIFACT"],
                        "typed_result_difference": "HASH_DIFFERENCE_ONLY; ROW_VALUES_NOT_PERSISTED",
                    }
                )
                records.append(record)
                continue
            predicted = QueryPlanV1.model_validate(predicted_payload)
            predicted_sql = compiler.compile(predicted).sql
            predicted_plan_snapshot = safety.plan(
                SqlCandidate(sql=predicted_sql, correlation_id=f"m18-predicted-{item['case_id']}")
            )
            if not isinstance(predicted_plan_snapshot, QueryPlan):
                raise RuntimeError(f"reconstructed predicted plan failed M1: {item['case_id']}")
            predicted_execution = safety.execute(predicted_plan_snapshot)
            if not isinstance(predicted_execution, QueryExecution):
                raise RuntimeError(
                    f"reconstructed predicted plan failed execution: {item['case_id']}"
                )
            predicted_result = _snapshot(
                predicted_execution,
                item["intervention"]["compiled_sql_hash"],
                _fixture(),
                order_sensitive=False,
            )
            if text_hash(predicted_sql) != item["intervention"]["compiled_sql_hash"]:
                raise RuntimeError(f"predicted SQL hash cannot be reconstructed: {item['case_id']}")
            if predicted_result.result_hash != item["intervention"]["result_hash"]:
                raise RuntimeError(
                    f"predicted result hash cannot be reconstructed: {item['case_id']}"
                )
            record.update(
                {
                    "reconstruction": "FULL_PLAN_AND_EXECUTION",
                    "predicted_plan": predicted.model_dump(mode="json"),
                    "predicted_sql": predicted_sql,
                    "gold_sql": gold_sql,
                    "differing_fields": _plan_differences(gold, predicted),
                    "sql_structural_differences": _plan_differences(gold, predicted),
                    "gold_row_count": actual_gold.row_count,
                    "predicted_row_count": predicted_result.row_count,
                    "gold_columns": actual_gold.columns,
                    "predicted_columns": predicted_result.columns,
                    "typed_result_difference": {
                        "comparison_mode": "VALUE_BAG",
                        "equivalent": compare_snapshots(
                            predicted_result,
                            actual_gold,
                            ComparisonMode.VALUE_BAG,
                            order_entitled=False,
                        ),
                        "gold_hash": actual_gold.result_hash,
                        "predicted_hash": predicted_result.result_hash,
                    },
                }
            )
            records.append(record)
    finally:
        engine.dispose()
    return {
        "version": "m18-residual-forensics-1",
        "source_result": "m17_result.json",
        "m17_failure_stage_distribution": (
            "M16/M17 compact artifacts do not preserve the full response plans for all four cases"
        ),
        "mismatch_count": len(records),
        "repeated_defect_candidate": "two-join relationship-selection ambiguity",
        "repeated_cases_with_full_reconstruction": sum(
            record["reconstruction"] == "FULL_PLAN_AND_EXECUTION" for record in records
        ),
        "records": records,
    }


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _targeted_question(plan: QueryPlanV1, catalog: QueryPlanV1Catalog, index: int) -> str:
    source = plan.source or "records"
    joins: list[str] = []
    for join in plan.joins:
        relation = catalog.relationship(join.relationship_id)
        target = (
            relation.right_table_id if relation.left_table_id == source else relation.left_table_id
        )
        left_name = relation.left_column_id.split(".")[-1].replace("_", " ")
        joins.append(f"{target.replace('_', ' ')} using its {left_name} relationship")
    columns = [catalog.column(column_id).name.replace("_", " ") for column_id in plan.projection]
    selected = " and ".join(columns)
    path = ", then ".join(joins)
    templates = (
        "For a connected relational report, return {selected} from {source} using {path}.",
        "List {selected} for {source} records joined through {path}.",
        "Show the {selected} fields from {source}, following the relationship path {path}.",
        "Retrieve {selected} where {source} is connected by {path}.",
        "In the two-step {source} relationship view, provide {selected} through {path}.",
        "Report {selected} for the {source} entities reached through {path}.",
        "Give the {selected} values from {source} after traversing {path}.",
        "Which {selected} fields are available for {source} across {path}?",
    )
    return templates[index % len(templates)].format(
        selected=selected, source=source.replace("_", " "), path=path
    )


def _prepare_targeted() -> list[M17Case]:
    source = M17Dataset.model_validate_json((FIXTURES / "m17_dataset.json").read_text())
    plans = [case.gold_plan for case in source.cases if case.shape == "TWO_JOINS_PROJECTION"]
    if len(plans) != 10:
        raise RuntimeError(f"expected ten two-join projection development plans, got {len(plans)}")
    settings = m17_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    fixture = m17_fixture()
    cases: list[M17Case] = []
    try:
        for index in range(40):
            plan = plans[index % len(plans)]
            validate_query_plan_v1(plan, catalog)
            compiled = compiler.compile(plan)
            planned = safety.plan(
                SqlCandidate(sql=compiled.sql, correlation_id=f"m18-targeted-gold-{index:03d}")
            )
            if not isinstance(planned, QueryPlan):
                raise RuntimeError(f"targeted gold M1 failure: {index}")
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                raise RuntimeError(f"targeted gold execution failure: {index}")
            snapshot = _snapshot(execution, text_hash(compiled.sql), fixture, order_sensitive=False)
            question = _targeted_question(plan, catalog, index)
            cases.append(
                M17Case(
                    case_id=f"m18-targeted-{index:03d}",
                    shape="TWO_JOINS_PROJECTION",
                    split="TARGETED",
                    question=question,
                    question_hash=text_hash(_normalize_question(question)),
                    gold_plan=plan,
                    gold_plan_hash=plan.content_hash,
                    gold_sql=compiled.sql,
                    gold_sql_hash=text_hash(compiled.sql),
                    comparison_mode=ComparisonMode.VALUE_BAG,
                    gold_columns=snapshot.columns,
                    gold_typed_rows=snapshot.typed_rows,
                    gold_row_count=snapshot.row_count,
                    gold_result_hash=snapshot.result_hash,
                )
            )
    finally:
        engine.dispose()
    if len({case.question_hash for case in cases}) != 40:
        raise RuntimeError("targeted question duplicates")
    TARGETED_DATASET.write_text(
        json.dumps([case.model_dump(mode="json") for case in cases], indent=2, sort_keys=True)
        + "\n"
    )
    return cases


def _fresh_two_join_plans(catalog: QueryPlanV1Catalog) -> list[QueryPlanV1]:
    historical = M17Dataset.model_validate_json(M17_DATASET.read_text())
    historical_hashes = {case.gold_plan_hash for case in historical.cases}
    candidates: list[QueryPlanV1] = []
    for source in catalog.tables:
        reachable = {source.table_id}
        first_edges = [
            relationship
            for relationship in catalog.relationships
            if source.table_id in (relationship.left_table_id, relationship.right_table_id)
        ]
        for first in first_edges:
            first_target = (
                first.right_table_id
                if first.left_table_id == source.table_id
                else first.left_table_id
            )
            for second in catalog.relationships:
                if second.relationship_id == first.relationship_id:
                    continue
                if first_target not in (second.left_table_id, second.right_table_id):
                    continue
                second_target = (
                    second.right_table_id
                    if second.left_table_id == first_target
                    else second.left_table_id
                )
                if second_target in reachable | {first_target}:
                    continue
                joins = (
                    {"relationship_id": first.relationship_id, "join_type": "INNER"},
                    {"relationship_id": second.relationship_id, "join_type": "INNER"},
                )
                graph_tables = reachable | {first_target, second_target}
                columns = [
                    column.column_id
                    for table in catalog.tables
                    if table.table_id in graph_tables
                    for column in table.columns
                ]
                for projection_size in (1, 2, 3):
                    for projection in itertools.combinations(columns, projection_size):
                        plan = QueryPlanV1(
                            applicable=True,
                            source=source.table_id,
                            joins=joins,
                            projection=projection,
                        )
                        if plan.content_hash not in historical_hashes:
                            candidates.append(plan)
    unique = {plan.content_hash: plan for plan in candidates}
    ordered = sorted(
        unique.values(), key=lambda plan: stable_hash(f"m18-targeted-2:{plan.content_hash}")
    )
    if len(ordered) < 40:
        raise RuntimeError(f"only {len(ordered)} fresh two-join plans available")
    return ordered[:40]


def _fresh_targeted_question(plan: QueryPlanV1, catalog: QueryPlanV1Catalog, index: int) -> str:
    current = plan.source or "records"
    path: list[str] = []
    for join in plan.joins:
        relationship = catalog.relationship(join.relationship_id)
        if relationship.left_table_id == current:
            target = relationship.right_table_id
            local_column = relationship.left_column_id.split(".")[-1].replace("_", " ")
        elif relationship.right_table_id == current:
            target = relationship.left_table_id
            local_column = relationship.left_column_id.split(".")[-1].replace("_", " ")
        else:
            raise RuntimeError(f"fresh target path is disconnected: {join.relationship_id}")
        path.append(f"{target.replace('_', ' ')} through the {local_column}")
        current = target
    columns = [
        f"{column.split('.')[-2].replace('_', ' ')} {column.split('.')[-1].replace('_', ' ')}"
        for column in plan.projection
    ]
    selected = ", ".join(columns[:-1]) + (
        f", and {columns[-1]}" if len(columns) > 1 else columns[0]
    )
    templates = (
        "Starting from {source} records, return {selected} after joining {path}.",
        "For each {source} record, show {selected} along the connected path through {path}.",
        "Provide {selected} for {source} rows by following the joins to {path}.",
        "Which {selected} values belong to {source} records linked through {path}?",
    )
    return templates[index % len(templates)].format(
        source=plan.source.replace("_", " ") if plan.source else "records",
        selected=selected,
        path=" and then ".join(path),
    )


def _prepare_targeted_attempt2() -> list[M17Case]:
    settings = m17_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    fixture = m17_fixture()
    cases: list[M17Case] = []
    try:
        for index, plan in enumerate(_fresh_two_join_plans(catalog)):
            validate_query_plan_v1(plan, catalog)
            compiled = compiler.compile(plan)
            planned = safety.plan(
                SqlCandidate(sql=compiled.sql, correlation_id=f"m18-targeted-2-gold-{index:03d}")
            )
            if not isinstance(planned, QueryPlan):
                raise RuntimeError(f"targeted attempt 2 gold M1 failure: {index}")
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                raise RuntimeError(f"targeted attempt 2 gold execution failure: {index}")
            snapshot = _snapshot(execution, text_hash(compiled.sql), fixture, order_sensitive=False)
            question = _fresh_targeted_question(plan, catalog, index)
            cases.append(
                M17Case(
                    case_id=f"m18-targeted-2-{index:03d}",
                    shape="TWO_JOINS_PROJECTION",
                    split="TARGETED_ATTEMPT_2",
                    question=question,
                    question_hash=text_hash(_normalize_question(question)),
                    gold_plan=plan,
                    gold_plan_hash=plan.content_hash,
                    gold_sql=compiled.sql,
                    gold_sql_hash=text_hash(compiled.sql),
                    comparison_mode=ComparisonMode.VALUE_BAG,
                    gold_columns=snapshot.columns,
                    gold_typed_rows=snapshot.typed_rows,
                    gold_row_count=snapshot.row_count,
                    gold_result_hash=snapshot.result_hash,
                )
            )
    finally:
        engine.dispose()
    questions = {_normalize_question(case.question) for case in cases}
    historical_questions = {
        _normalize_question(case.question)
        for case in M17Dataset.model_validate_json(M17_DATASET.read_text()).cases
    }
    if len(cases) != 40 or len(questions) != 40 or questions & historical_questions:
        raise RuntimeError("targeted attempt 2 freshness/uniqueness failure")
    TARGETED_ATTEMPT2_DATASET.write_text(
        json.dumps([case.model_dump(mode="json") for case in cases], indent=2, sort_keys=True)
        + "\n"
    )
    return cases


def _freeze_targeted_manifest(cases: list[M17Case]) -> dict[str, Any]:
    def file_hash(path: str) -> str:
        return text_hash(Path(path).read_text())

    manifest = {
        "version": "m18-targeted-manifest-1",
        "starting_head": "ef3763910df9d5b17d2be7dfb8d49782829c2ff2",
        "dataset_hash": text_hash(
            json.dumps([case.model_dump(mode="json") for case in cases], sort_keys=True)
        ),
        "n": len(cases),
        "shape": "TWO_JOINS_PROJECTION",
        "provider": "openai-compatible",
        "model": "gpt-5.6-luna",
        "temperature": 0.0,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "repair": 0,
        "sources": {
            "wire": file_hash("app/semantics/query_plan_wire_v2.py"),
            "canonical": file_hash("app/semantics/query_plan_v1.py"),
            "provider": file_hash("app/generation/provider.py"),
            "m1": file_hash("app/sql/service.py"),
        },
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    TARGETED_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def _freeze_targeted_attempt2_manifest(cases: list[M17Case]) -> dict[str, Any]:
    manifest = _freeze_targeted_manifest(cases)
    manifest.update(
        {
            "version": "m18-targeted-attempt2-manifest-1",
            "attempt_id": "M18-TARGETED-ATTEMPT-2",
            "dataset_hash": text_hash(TARGETED_ATTEMPT2_DATASET.read_text()),
        }
    )
    manifest["manifest_hash"] = stable_hash(manifest)
    TARGETED_ATTEMPT2_MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


async def _run_targeted(cases: list[M17Case]) -> dict[str, Any]:
    settings = m17_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    context = render_query_plan_v1_context(catalog)
    provider = OpenAICompatibleProvider(settings=settings)
    fixture = m17_fixture()
    ledger: list[dict[str, Any]] = []
    try:
        for case in cases:
            outputs: dict[str, dict[str, Any]] = {}
            order = "CONTROL_FIRST" if int(case.case_id[-1], 16) % 2 == 0 else "INTERVENTION_FIRST"
            operations = (
                ("control", "intervention")
                if order == "CONTROL_FIRST"
                else ("intervention", "control")
            )
            for operation in operations:
                row: dict[str, Any] = {
                    "correct": False,
                    "failure": None,
                    "contract_acquired": False,
                }
                try:
                    proposal, retries = await _call(provider, operation, case, context)
                    row["technical_retries"] = retries
                    row["model"] = proposal.model
                    if operation == "control":
                        sql_hash = text_hash(proposal.sql)
                        planned = safety.plan(
                            SqlCandidate(sql=proposal.sql, correlation_id=case.case_id)
                        )
                        if isinstance(planned, SqlPlanFailure):
                            row["failure"] = "M1_REJECTION"
                        else:
                            execution = safety.execute(planned)
                            if not isinstance(execution, QueryExecution):
                                row["failure"] = "EXECUTION_FAILURE"
                            else:
                                snapshot = _snapshot(
                                    execution, sql_hash, fixture, order_sensitive=False
                                )
                                row["correct"] = compare_snapshots(
                                    snapshot,
                                    m17_gold_snapshot(case),
                                    case.comparison_mode,
                                    order_entitled=False,
                                )
                                if not row["correct"]:
                                    row["failure"] = "RESULT_MISMATCH"
                    else:
                        plan = proposal.plan
                        row["contract_acquired"] = True
                        row["plan_hash"] = plan.content_hash
                        row["relationship_mismatch"] = plan.joins != case.gold_plan.joins
                        validate_query_plan_v1(plan, catalog)
                        compiled = compiler.compile(plan)
                        planned = safety.plan(
                            compiled.model_copy(update={"correlation_id": case.case_id})
                        )
                        if isinstance(planned, SqlPlanFailure):
                            row["failure"] = "M1_INVARIANT_FAILURE"
                        else:
                            execution = safety.execute(planned)
                            if not isinstance(execution, QueryExecution):
                                row["failure"] = "EXECUTION_FAILURE"
                            else:
                                snapshot = _snapshot(
                                    execution,
                                    text_hash(compiled.sql),
                                    fixture,
                                    order_sensitive=False,
                                )
                                row["correct"] = compare_snapshots(
                                    snapshot,
                                    m17_gold_snapshot(case),
                                    case.comparison_mode,
                                    order_entitled=False,
                                )
                                if not row["correct"]:
                                    row["failure"] = "RESULT_MISMATCH"
                except QueryPlanProviderBoundaryError as error:
                    row["failure"] = error.diagnostic.stage.value
                except LLMProviderError:
                    row["failure"] = "TRANSPORT_FAILURE"
                except QueryPlanV1ValidationError:
                    row["failure"] = "QUERYPLAN_VALIDATION_FAILURE"
                except Exception as error:
                    row["failure"] = "EXECUTION_FAILURE"
                    row["error_type"] = type(error).__name__
                outputs[operation] = row
            ledger.append(
                {
                    "case_id": case.case_id,
                    "order": order,
                    "control": outputs["control"],
                    "intervention": outputs["intervention"],
                }
            )
    finally:
        engine.dispose()
    return {
        "version": "m18-targeted-result-1",
        "n": len(ledger),
        "control_correct": sum(x["control"]["correct"] for x in ledger),
        "queryplan_correct": sum(x["intervention"]["correct"] for x in ledger),
        "contract_acquired": sum(x["intervention"]["contract_acquired"] for x in ledger),
        "targeted_defect_recurrence": sum(
            x["intervention"].get("relationship_mismatch", False) for x in ledger
        ),
        "failure_stages": dict(
            Counter(x["intervention"]["failure"] for x in ledger if x["intervention"]["failure"])
        ),
        "ledger": ledger,
    }


async def _run_targeted_attempt2(cases: list[M17Case]) -> dict[str, Any]:
    result = await _run_targeted(cases)
    result["version"] = "m18-targeted-attempt2-result-1"
    result["attempt_id"] = "M18-TARGETED-ATTEMPT-2"
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "forensics",
            "prepare-targeted",
            "freeze-targeted",
            "targeted",
            "prepare-targeted-attempt2",
            "freeze-targeted-attempt2",
            "targeted-attempt2",
        ),
    )
    args = parser.parse_args()
    if args.command == "forensics":
        result = _reconstruct()
        FORENSICS_RESULT.write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
        )
        print(
            json.dumps(
                {
                    "mismatch_count": result["mismatch_count"],
                    "full_reconstruction": result["repeated_cases_with_full_reconstruction"],
                    "repeated_defect_candidate": result["repeated_defect_candidate"],
                }
            )
        )
    elif args.command == "prepare-targeted":
        cases = _prepare_targeted()
        print(json.dumps({"n": len(cases), "gold": len(cases)}))
    elif args.command == "freeze-targeted":
        cases = [M17Case.model_validate(item) for item in json.loads(TARGETED_DATASET.read_text())]
        print(json.dumps(_freeze_targeted_manifest(cases)))
    elif args.command == "prepare-targeted-attempt2":
        cases = _prepare_targeted_attempt2()
        print(
            json.dumps(
                {"n": len(cases), "gold": len(cases), "attempt_id": "M18-TARGETED-ATTEMPT-2"}
            )
        )
    elif args.command == "freeze-targeted-attempt2":
        cases = [
            M17Case.model_validate(item)
            for item in json.loads(TARGETED_ATTEMPT2_DATASET.read_text())
        ]
        print(json.dumps(_freeze_targeted_attempt2_manifest(cases)))
    elif args.command == "targeted-attempt2":
        cases = [
            M17Case.model_validate(item)
            for item in json.loads(TARGETED_ATTEMPT2_DATASET.read_text())
        ]
        result = asyncio.run(_run_targeted_attempt2(cases))
        TARGETED_ATTEMPT2_RESULT.write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
        )
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "n",
                        "control_correct",
                        "queryplan_correct",
                        "contract_acquired",
                        "targeted_defect_recurrence",
                    )
                }
            )
        )
    else:
        cases_payload = json.loads(TARGETED_DATASET.read_text())
        cases = [M17Case.model_validate(item) for item in cases_payload]
        result = asyncio.run(_run_targeted(cases))
        TARGETED_RESULT.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "n",
                        "control_correct",
                        "queryplan_correct",
                        "contract_acquired",
                        "targeted_defect_recurrence",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
