"""M16 fresh QueryPlan V1 capability experiment.

Preparation is deterministic and provider-free.  The experiment runner uses
one direct SQL call and one strict QueryPlan V1 call per case, with no repair,
judge, or intervention fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.config import Settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    LLMProviderError,
    OpenAICompatibleProvider,
    QueryPlanV1Proposal,
)
from app.models.domain import QueryRequest
from app.provenance.canonical import text_hash
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Compiler,
    QueryPlanV1Operator,
    QueryPlanV1ValidationError,
    QueryPlanV1Value,
    render_query_plan_v1_context,
    stable_hash,
    validate_query_plan_v1,
)
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from evaluation.m112p2_counterexample_diagnostic import (
    ComparisonMode,
    DiagnosticExecutionSnapshot,
    DiagnosticFixture,
    _snapshot,
    compare_snapshots,
)

FIXTURES = Path(__file__).with_name("fixtures")
DATASET_PATH = FIXTURES / "m16_dataset.json"
CONTRACT_PATH = FIXTURES / "m16_experiment_contract.json"
RESULT_PATH = FIXTURES / "m16_result.json"
MANIFEST_PATH = FIXTURES / "m16_evidence_manifest.json"
SPLIT_SALT = "m16-queryplan-v1-split-2026-09-06"

SHAPE_TARGETS = {
    "SOURCE_PROJECTION": 15,
    "SOURCE_FILTER": 25,
    "ONE_JOIN_PROJECTION": 20,
    "ONE_JOIN_FILTER": 30,
    "TWO_JOINS_PROJECTION": 10,
    "TWO_JOINS_FILTER": 20,
}
PRIMARY_TARGETS = {
    "SOURCE_PROJECTION": 11,
    "SOURCE_FILTER": 19,
    "ONE_JOIN_PROJECTION": 15,
    "ONE_JOIN_FILTER": 22,
    "TWO_JOINS_PROJECTION": 8,
    "TWO_JOINS_FILTER": 15,
}


class M16Case(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    shape: str
    split: str
    question: str
    question_hash: str
    gold_plan: QueryPlanV1
    gold_plan_hash: str
    gold_sql: str
    gold_sql_hash: str
    comparison_mode: ComparisonMode
    gold_columns: tuple[str, ...]
    gold_typed_rows: tuple[tuple[tuple[str, Any], ...], ...]
    gold_row_count: int
    gold_result_hash: str


class M16Dataset(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    catalog_hash: str
    split_salt: str
    cases: tuple[M16Case, ...]
    dataset_hash: str


def _hash_without(value: BaseModel, field: str) -> str:
    return stable_hash(value.model_dump(mode="json", exclude={field}))


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _historical_question_hashes() -> set[str]:
    hashes: set[str] = set()
    excluded = {DATASET_PATH.resolve(), RESULT_PATH.resolve(), MANIFEST_PATH.resolve()}
    for path in Path("evaluation").rglob("*.json"):
        if path.resolve() in excluded:
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                question = value.get("question")
                if isinstance(question, str):
                    hashes.add(text_hash(_normalize_question(question)))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(payload)
    return hashes


def _file_hash(path: Path) -> str:
    return stable_hash(path.read_bytes().hex())


def _fixture() -> DiagnosticFixture:
    fixture = DiagnosticFixture(
        fixture_id="m16-gold-demo",
        fixture_version="m16-gold-1",
        schema_version="decision-sql-demo-schema-1",
        seed="deterministic-demo-seed",
        content_hash="placeholder",
    )
    return fixture.model_copy(update={"content_hash": _hash_without(fixture, "content_hash")})


def _value_for(column: Any, connection: Any) -> QueryPlanV1Value:
    query = text(
        f'SELECT "{column.name}" FROM "{column.table_id}" WHERE "{column.name}" IS NOT NULL LIMIT 1'
    )
    raw = connection.execute(query).scalar_one_or_none()
    if raw is None:
        raise RuntimeError(f"no deterministic value for {column.column_id}")
    family = _type_family(column.data_type)
    if family == "integer":
        return QueryPlanV1Value(kind="integer", value=int(raw))
    if family == "decimal":
        return QueryPlanV1Value(kind="decimal", value=str(raw))
    if family == "boolean":
        return QueryPlanV1Value(kind="boolean", value=bool(raw))
    if family == "date":
        return QueryPlanV1Value(kind="date", value=raw.isoformat())
    if family == "timestamp":
        return QueryPlanV1Value(kind="timestamp", value=raw.isoformat())
    return QueryPlanV1Value(kind="string", value=str(raw))


def _type_family(data_type: str) -> str:
    normalized = data_type.upper()
    if any(token in normalized for token in ("CHAR", "TEXT", "CLOB")):
        return "string"
    if any(token in normalized for token in ("INT", "SMALLINT", "BIGINT")):
        return "integer"
    if any(token in normalized for token in ("NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE")):
        return "decimal"
    if "BOOL" in normalized:
        return "boolean"
    if "DATE" in normalized and "TIME" not in normalized:
        return "date"
    if "TIME" in normalized:
        return "timestamp"
    return "unsupported"


def _operators(column: Any) -> tuple[QueryPlanV1Operator, ...]:
    family = _type_family(column.data_type)
    if family in {"string", "boolean"}:
        return (
            QueryPlanV1Operator.EQ,
            QueryPlanV1Operator.NE,
            QueryPlanV1Operator.IS_NULL,
            QueryPlanV1Operator.IS_NOT_NULL,
        )
    return (
        QueryPlanV1Operator.EQ,
        QueryPlanV1Operator.NE,
        QueryPlanV1Operator.LT,
        QueryPlanV1Operator.LTE,
        QueryPlanV1Operator.GT,
        QueryPlanV1Operator.GTE,
        QueryPlanV1Operator.IS_NULL,
        QueryPlanV1Operator.IS_NOT_NULL,
    )


def _join_paths(
    catalog: QueryPlanV1Catalog, count: int
) -> list[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    paths: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []
    for source in sorted(table.table_id for table in catalog.tables):
        for first in catalog.relationships:
            if source not in {first.left_table_id, first.right_table_id}:
                continue
            reachable = {source, first.left_table_id, first.right_table_id}
            for second in catalog.relationships:
                if second.relationship_id == first.relationship_id:
                    continue
                endpoints = {second.left_table_id, second.right_table_id}
                if len(endpoints & reachable) != 1 or len(endpoints - reachable) != 1:
                    continue
                paths.append(
                    (
                        source,
                        (first.relationship_id, second.relationship_id),
                        tuple(sorted(endpoints | reachable)),
                    )
                )
    unique: dict[tuple[str, tuple[str, ...]], tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    for item in paths:
        unique.setdefault((item[0], item[1]), item)
    return list(unique.values())[:count]


def _make_plan(
    shape: str,
    source: str,
    joins: tuple[str, ...],
    projection: tuple[str, ...],
    filters: tuple[dict[str, Any], ...],
) -> QueryPlanV1:
    return QueryPlanV1(
        applicable=True,
        source=source,
        joins=tuple({"relationship_id": item, "join_type": "INNER"} for item in joins),
        projection=projection,
        filters=filters,
    )


def _replace_filters(plan: QueryPlanV1, filters: tuple[Any, ...]) -> QueryPlanV1:
    payload = plan.model_dump(mode="python")
    payload["filters"] = filters
    return QueryPlanV1.model_validate(payload)


def _question(plan: QueryPlanV1, shape: str, catalog: QueryPlanV1Catalog) -> str:
    def column_name(column_id: str) -> str:
        column = catalog.column(column_id)
        return column.name.replace("_", " ")

    def table_name(table_id: str) -> str:
        return table_id.replace("_", " ")

    projection = [column_name(item) for item in plan.projection]
    if len(projection) == 1:
        selected = projection[0]
    elif len(projection) == 2:
        selected = f"{projection[0]} and {projection[1]}"
    else:
        selected = f"{projection[0]}, {projection[1]}, and {projection[2]}"
    source = table_name(plan.source or "records")
    if not plan.joins:
        base = f"the {selected} from {source} records"
    else:
        joined = " and ".join(
            (
                table_name(
                    catalog.relationship(join.relationship_id).right_table_id
                    if catalog.relationship(join.relationship_id).left_table_id == plan.source
                    else catalog.relationship(join.relationship_id).left_table_id
                )
                + " through the "
                + catalog.relationship(join.relationship_id)
                .left_column_id.split(".")[-1]
                .replace("_", " ")
                + " link"
            )
            for join in plan.joins
        )
        base = f"the {selected} from {source} records related to {joined}"
    if not plan.filters:
        templates = (
            "Please retrieve {base} from the business data.",
            "I need {base} from the available records.",
            "For this relational lookup, return {base}.",
            "Provide {base} for this data request.",
        )
        return templates[int(plan.content_hash[:8], 16) % len(templates)].format(base=base)
    clauses = []
    for predicate in plan.filters:
        name = column_name(predicate.column_id)
        if predicate.operator is QueryPlanV1Operator.IS_NULL:
            clauses.append(f"where {name} is missing")
        elif predicate.operator is QueryPlanV1Operator.IS_NOT_NULL:
            clauses.append(f"where {name} is present")
        else:
            assert predicate.value is not None
            value = str(predicate.value.value)
            words = {
                QueryPlanV1Operator.EQ: "equals",
                QueryPlanV1Operator.NE: "does not equal",
                QueryPlanV1Operator.LT: "is below",
                QueryPlanV1Operator.LTE: "is at most",
                QueryPlanV1Operator.GT: "is above",
                QueryPlanV1Operator.GTE: "is at least",
            }
            qualified_name = predicate.column_id.replace("_", " ").replace(".", " ")
            clauses.append(f"where {qualified_name} {words[predicate.operator]} '{value}'")
    condition = " and ".join(item.removeprefix("where ") for item in clauses)
    templates = (
        "Please retrieve {base}, restricted to records where {condition}.",
        "I need {base} for records satisfying {condition}.",
        "For this relational lookup, return {base} subject to {condition}.",
        "Provide {base} when the records meet {condition}.",
    )
    return templates[int(plan.content_hash[8:16], 16) % len(templates)].format(
        base=base, condition=condition
    )


def _candidate_plans(catalog: QueryPlanV1Catalog, connection: Any) -> list[tuple[str, QueryPlanV1]]:
    tables = sorted(catalog.tables, key=lambda item: item.table_id)
    result: list[tuple[str, QueryPlanV1]] = []
    used: set[str] = set()

    def add(shape: str, plan: QueryPlanV1) -> None:
        validate_query_plan_v1(plan, catalog)
        if plan.content_hash not in used:
            used.add(plan.content_hash)
            result.append((shape, plan))

    projection_pools = [
        [
            projection
            for count in (1, 2, 3)
            for projection in itertools.combinations(
                [column.column_id for column in table.columns], count
            )
        ]
        for table in tables
    ]
    cursor = 0
    while (
        len([item for item in result if item[0] == "SOURCE_PROJECTION"])
        < SHAPE_TARGETS["SOURCE_PROJECTION"]
    ):
        table_index = cursor % len(tables)
        projection = projection_pools[table_index][cursor // len(tables)]
        add(
            "SOURCE_PROJECTION",
            _make_plan("SOURCE_PROJECTION", tables[table_index].table_id, (), projection, ()),
        )
        cursor += 1

    filter_pools: list[list[tuple[Any, QueryPlanV1Operator, QueryPlanV1Value | None]]] = []
    for table in tables:
        columns = tuple(table.columns)
        options = []
        for source_column in columns:
            for operator in _operators(source_column):
                value = (
                    None
                    if operator in {QueryPlanV1Operator.IS_NULL, QueryPlanV1Operator.IS_NOT_NULL}
                    else _value_for(source_column, connection)
                )
                options.append((source_column, operator, value))
        filter_pools.append(options)
    cursor = 0
    while (
        len([item for item in result if item[0] == "SOURCE_FILTER"])
        < SHAPE_TARGETS["SOURCE_FILTER"]
    ):
        table_index = cursor % len(tables)
        source_columns = tuple(tables[table_index].columns)
        source_column, operator, value = filter_pools[table_index][cursor // len(tables)]
        projection = (source_columns[(cursor + 1) % len(source_columns)].column_id,)
        filters = ({"column_id": source_column.column_id, "operator": operator, "value": value},)
        add(
            "SOURCE_FILTER",
            _make_plan("SOURCE_FILTER", tables[table_index].table_id, (), projection, filters),
        )
        cursor += 1

    join_pairs: list[tuple[str, Any, list[str]]] = []
    for relationship in sorted(catalog.relationships, key=lambda item: item.relationship_id):
        for source in (relationship.left_table_id, relationship.right_table_id):
            target = (
                relationship.right_table_id
                if source == relationship.left_table_id
                else relationship.left_table_id
            )
            reachable_columns = [
                column.column_id
                for table in tables
                if table.table_id in {source, target}
                for column in table.columns
            ]
            join_pairs.append((source, relationship, reachable_columns))
    one_join_seen = {"ONE_JOIN_PROJECTION": 0, "ONE_JOIN_FILTER": 0}
    cursor = 0
    while not all(one_join_seen[key] >= SHAPE_TARGETS[key] for key in one_join_seen):
        source, relationship, reachable_columns = join_pairs[cursor % len(join_pairs)]
        pair_round = cursor // len(join_pairs)
        if one_join_seen["ONE_JOIN_PROJECTION"] < SHAPE_TARGETS["ONE_JOIN_PROJECTION"]:
            projection_options = list(itertools.combinations(reachable_columns, 2))
            projection = projection_options[pair_round % len(projection_options)]
            add(
                "ONE_JOIN_PROJECTION",
                _make_plan(
                    "ONE_JOIN_PROJECTION",
                    source,
                    (relationship.relationship_id,),
                    projection,
                    (),
                ),
            )
            one_join_seen["ONE_JOIN_PROJECTION"] += 1
        if one_join_seen["ONE_JOIN_FILTER"] < SHAPE_TARGETS["ONE_JOIN_FILTER"]:
            column_id = reachable_columns[pair_round % len(reachable_columns)]
            joined_column = catalog.column(column_id)
            operators = _operators(joined_column)
            operator = operators[pair_round % len(operators)]
            value = (
                None
                if operator in {QueryPlanV1Operator.IS_NULL, QueryPlanV1Operator.IS_NOT_NULL}
                else _value_for(joined_column, connection)
            )
            add(
                "ONE_JOIN_FILTER",
                _make_plan(
                    "ONE_JOIN_FILTER",
                    source,
                    (relationship.relationship_id,),
                    (reachable_columns[0],),
                    ({"column_id": column_id, "operator": operator, "value": value},),
                ),
            )
            one_join_seen["ONE_JOIN_FILTER"] += 1
        cursor += 1

    two_paths = _join_paths(catalog, 100)
    two_seen = {"TWO_JOINS_PROJECTION": 0, "TWO_JOINS_FILTER": 0}
    for source, relationships, _ in two_paths:
        reachable_tables: set[str] = {source}
        for relationship_id in relationships:
            relationship = catalog.relationship(relationship_id)
            reachable_tables.update((relationship.left_table_id, relationship.right_table_id))
        join_columns = [
            column.column_id
            for table in tables
            if table.table_id in reachable_tables
            for column in table.columns
        ]
        for projection in itertools.islice(itertools.combinations(join_columns, 2), 3):
            if two_seen["TWO_JOINS_PROJECTION"] >= SHAPE_TARGETS["TWO_JOINS_PROJECTION"]:
                break
            add(
                "TWO_JOINS_PROJECTION",
                _make_plan("TWO_JOINS_PROJECTION", source, relationships, projection, ()),
            )
            two_seen["TWO_JOINS_PROJECTION"] += 1
        for column_id in join_columns:
            if two_seen["TWO_JOINS_FILTER"] >= SHAPE_TARGETS["TWO_JOINS_FILTER"]:
                break
            two_column = catalog.column(column_id)
            operators = _operators(two_column)
            operator = operators[int(stable_hash(column_id)[:4], 16) % len(operators)]
            value = (
                None
                if operator in {QueryPlanV1Operator.IS_NULL, QueryPlanV1Operator.IS_NOT_NULL}
                else _value_for(two_column, connection)
            )
            add(
                "TWO_JOINS_FILTER",
                _make_plan(
                    "TWO_JOINS_FILTER",
                    source,
                    relationships,
                    (join_columns[0],),
                    ({"column_id": column_id, "operator": operator, "value": value},),
                ),
            )
            two_seen["TWO_JOINS_FILTER"] += 1
        if all(two_seen[key] >= SHAPE_TARGETS[key] for key in two_seen):
            break

    # Preserve the fixed structural quotas while ensuring that the population
    # exercises the bounded two-predicate and typed-literal paths.
    used = {plan.content_hash for _, plan in result}
    double_predicate_count = 0
    for index, (shape, plan) in enumerate(result):
        if double_predicate_count >= 12 or not plan.filters:
            continue
        double_reachable: set[str] = {plan.source} if plan.source else set()
        for join in plan.joins:
            relationship = catalog.relationship(join.relationship_id)
            double_reachable.update((relationship.left_table_id, relationship.right_table_id))
        filtered = {predicate.column_id for predicate in plan.filters}
        available = [
            column
            for table in catalog.tables
            if table.table_id in double_reachable
            for column in table.columns
            if column.column_id not in filtered
        ]
        if not available:
            continue
        second_column = available[0]
        replacement = _replace_filters(
            plan,
            (
                *[predicate.model_dump(mode="python") for predicate in plan.filters],
                {
                    "column_id": second_column.column_id,
                    "operator": QueryPlanV1Operator.EQ,
                    "value": _value_for(second_column, connection),
                },
            ),
        )
        if replacement.content_hash in used:
            continue
        used.remove(plan.content_hash)
        used.add(replacement.content_hash)
        result[index] = (shape, replacement)
        double_predicate_count += 1

    for desired_family in ("decimal", "timestamp"):
        if any(
            predicate.value is not None and predicate.value.kind == desired_family
            for _, plan in result
            for predicate in plan.filters
        ):
            continue
        for index, (shape, plan) in enumerate(result):
            if not plan.filters:
                continue
            type_reachable: set[str] = {plan.source} if plan.source else set()
            for join in plan.joins:
                relationship = catalog.relationship(join.relationship_id)
                type_reachable.update((relationship.left_table_id, relationship.right_table_id))
            candidates = [
                column
                for table in catalog.tables
                if table.table_id in type_reachable
                for column in table.columns
                if _type_family(column.data_type) == desired_family
            ]
            if not candidates:
                continue
            replacement = _replace_filters(
                plan,
                (
                    {
                        "column_id": candidates[0].column_id,
                        "operator": QueryPlanV1Operator.EQ,
                        "value": _value_for(candidates[0], connection),
                    },
                ),
            )
            if replacement.content_hash in used:
                continue
            used.remove(plan.content_hash)
            used.add(replacement.content_hash)
            result[index] = (shape, replacement)
            break

    counts = Counter(shape for shape, _ in result)
    if counts != Counter(SHAPE_TARGETS):
        raise RuntimeError(f"could not construct required M16 shape counts: {counts}")
    return result


def _split_cases(items: list[tuple[str, QueryPlanV1]]) -> list[tuple[str, QueryPlanV1, str]]:
    grouped: dict[str, list[tuple[str, QueryPlanV1]]] = defaultdict(list)
    for item in items:
        grouped[item[0]].append(item)
    selected: list[tuple[str, QueryPlanV1, str]] = []
    for shape, group in grouped.items():
        ordered = sorted(
            group, key=lambda item: stable_hash(f"{item[1].content_hash}:{SPLIT_SALT}")
        )
        primary_count = PRIMARY_TARGETS[shape]
        selected.extend(
            (shape, plan, "PRIMARY" if index < primary_count else "CONFIRMATION")
            for index, (_, plan) in enumerate(ordered)
        )
    return sorted(selected, key=lambda item: item[1].content_hash)


def prepare_dataset() -> M16Dataset:
    settings = get_m16_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    fixture = _fixture()
    with engine.connect() as connection:
        items = _split_cases(_candidate_plans(catalog, connection))
    cases: list[M16Case] = []
    try:
        with engine.connect() as connection:
            for index, (shape, plan, split) in enumerate(items):
                candidate = compiler.compile(plan)
                second = compiler.compile(plan)
                if candidate.sql != second.sql:
                    raise RuntimeError(f"compiler nondeterminism: {plan.content_hash}")
                planned = safety.plan(
                    candidate.model_copy(update={"correlation_id": f"m16-gold-{index:03d}"})
                )
                if not isinstance(planned, QueryPlan):
                    raise RuntimeError(f"gold M1 failure: {plan.content_hash}")
                execution = safety.execute(planned)
                if not isinstance(execution, QueryExecution) or execution.truncated:
                    raise RuntimeError(f"gold execution failure: {plan.content_hash}")
                snapshot = _snapshot(
                    execution, text_hash(candidate.sql), fixture, order_sensitive=False
                )
                repeat = safety.execute(planned)
                if not isinstance(repeat, QueryExecution):
                    raise RuntimeError(f"gold reproducibility failure: {plan.content_hash}")
                repeat_snapshot = _snapshot(
                    repeat, text_hash(candidate.sql), fixture, order_sensitive=False
                )
                if repeat_snapshot.result_hash != snapshot.result_hash:
                    raise RuntimeError(f"gold result nondeterminism: {plan.content_hash}")
                question = _question(plan, shape, catalog)
                cases.append(
                    M16Case(
                        case_id=f"m16-queryplan-{index:03d}",
                        shape=shape,
                        split=split,
                        question=question,
                        question_hash=text_hash(_normalize_question(question)),
                        gold_plan=plan,
                        gold_plan_hash=plan.content_hash,
                        gold_sql=candidate.sql,
                        gold_sql_hash=text_hash(candidate.sql),
                        comparison_mode=ComparisonMode.VALUE_BAG,
                        gold_columns=snapshot.columns,
                        gold_typed_rows=snapshot.typed_rows,
                        gold_row_count=snapshot.row_count,
                        gold_result_hash=snapshot.result_hash,
                    )
                )
    finally:
        engine.dispose()
    if len(cases) != 120 or len({case.question_hash for case in cases}) != 120:
        raise RuntimeError("M16 question uniqueness failure")
    historical_overlap = {case.question_hash for case in cases} & _historical_question_hashes()
    if historical_overlap:
        raise RuntimeError(f"M16 historical question overlap: {sorted(historical_overlap)}")
    dataset = M16Dataset(
        version="m16-queryplan-v1-1",
        catalog_hash=catalog.content_hash,
        split_salt=SPLIT_SALT,
        cases=tuple(cases),
        dataset_hash="placeholder",
    )
    dataset = dataset.model_copy(update={"dataset_hash": _hash_without(dataset, "dataset_hash")})
    FIXTURES.mkdir(exist_ok=True)
    DATASET_PATH.write_text(
        json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    contract = {
        "version": "m16-queryplan-v1-contract-1",
        "catalog_hash": catalog.content_hash,
        "compiler_version": compiler.version,
        "context_hash": text_hash(render_query_plan_v1_context(catalog)),
        "m1": "SqlSafetyService unchanged",
        "comparison_mode": ComparisonMode.VALUE_BAG,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "few_shot": "OFF",
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "reasoning_effort": settings.llm_reasoning_effort,
        "control_calls": "direct SQL",
        "intervention_calls": "strict QueryPlanV1 JSON; no fallback",
        "dataset_hash": dataset.dataset_hash,
        "primary_count": sum(case.split == "PRIMARY" for case in cases),
        "confirmation_count": sum(case.split == "CONFIRMATION" for case in cases),
    }
    CONTRACT_PATH.write_text(json.dumps(contract, indent=2, sort_keys=True, default=str) + "\n")
    manifest = {
        "version": "m16-preexperiment-manifest-1",
        "source_hashes": {
            "m16_runner": _file_hash(Path(__file__)),
            "query_plan_v1": _file_hash(Path("app/semantics/query_plan_v1.py")),
            "provider": _file_hash(Path("app/generation/provider.py")),
            "m1": _file_hash(Path("app/sql/service.py")),
            "typed_comparator": _file_hash(Path("evaluation/m112p2_counterexample_diagnostic.py")),
        },
        "dataset_hash": dataset.dataset_hash,
        "catalog_hash": catalog.content_hash,
        "contract_hash": stable_hash(contract),
        "question_overlap": 0,
        "primary_hash": stable_hash(
            [case.model_dump(mode="json") for case in cases if case.split == "PRIMARY"]
        ),
        "confirmation_hash": stable_hash(
            [case.model_dump(mode="json") for case in cases if case.split == "CONFIRMATION"]
        ),
        "provider": settings.llm_model,
        "temperature": settings.llm_temperature,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "few_shot": "OFF",
        "gates": {
            "primary_gain_pp": 10.0,
            "primary_min_discordant_direction": "C>B",
            "primary_exact_p": 0.05,
            "confirmation_gain_cases": 3,
        },
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return dataset


def get_m16_settings() -> Settings:
    base = Settings(_env_file=".env")
    return base.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_temperature": 0.0,
            "llm_reasoning_effort": "none",
            "eval_capture_model_io": False,
        }
    )


def _gold_snapshot(case: M16Case) -> DiagnosticExecutionSnapshot:
    return DiagnosticExecutionSnapshot(
        query_hash=case.gold_sql_hash,
        fixture_id="m16-gold-demo",
        columns=case.gold_columns,
        typed_rows=case.gold_typed_rows,
        row_count=case.gold_row_count,
        truncated=False,
        result_hash=case.gold_result_hash,
        latency_ms=0.0,
    )


def _exact_plan_fields(actual: QueryPlanV1, gold: QueryPlanV1) -> dict[str, bool]:
    return {
        "applicable": actual.applicable == gold.applicable,
        "source": actual.source == gold.source,
        "relationships": actual.joins == gold.joins,
        "join_type": tuple(item.join_type for item in actual.joins)
        == tuple(item.join_type for item in gold.joins),
        "projection": actual.projection == gold.projection,
        "predicate_columns": tuple(item.column_id for item in actual.filters)
        == tuple(item.column_id for item in gold.filters),
        "predicate_operators": tuple(item.operator for item in actual.filters)
        == tuple(item.operator for item in gold.filters),
        "predicate_values": tuple(item.value for item in actual.filters)
        == tuple(item.value for item in gold.filters),
        "full": actual.normalized() == gold.normalized(),
    }


def _exact_binomial_pvalue(b: int, c: int) -> float:
    discordant = b + c
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(min(b, c) + 1)) / (2**discordant)
    return float(min(1.0, 2.0 * tail))


async def _provider_call(
    provider: OpenAICompatibleProvider, operation: str, case: M16Case, context: str
) -> Any:
    for attempt in range(2):
        try:
            if operation == "control":
                return await provider.propose_sql(
                    QueryRequest(question=case.question), None, context
                )
            return await provider.propose_query_plan_v1(case.question, context)
        except LLMProviderError as error:
            if attempt == 0 and error.detail is not None and error.detail.retryable:
                continue
            raise


async def run_experiment(dataset: M16Dataset, confirmation: bool = False) -> dict[str, Any]:
    settings = get_m16_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    context = render_query_plan_v1_context(catalog)
    provider = OpenAICompatibleProvider(settings=settings)
    fixture = _fixture()
    cases = [case for case in dataset.cases if confirmation or case.split == "PRIMARY"]
    ledger: list[dict[str, Any]] = []
    try:
        for case in cases:
            order = (
                "CONTROL_FIRST"
                if int(stable_hash(case.case_id)[0], 16) % 2 == 0
                else "INTERVENTION_FIRST"
            )
            outputs: dict[str, dict[str, Any]] = {}
            operations = (
                ("control", "intervention")
                if order == "CONTROL_FIRST"
                else ("intervention", "control")
            )
            for operation in operations:
                started = perf_counter()
                row: dict[str, Any] = {"operation": operation, "correct": False, "failure": None}
                try:
                    proposal = await _provider_call(provider, operation, case, context)
                    row["response_hash"] = stable_hash(proposal.model_dump(mode="json"))
                    row["provider"] = proposal.provider
                    row["model"] = proposal.model
                    row["latency_ms"] = proposal.latency_ms
                    if operation == "control":
                        assert hasattr(proposal, "sql")
                        row["sql_hash"] = text_hash(proposal.sql)
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
                                    execution, row["sql_hash"], fixture, order_sensitive=False
                                )
                                row["result_hash"] = snapshot.result_hash
                                row["correct"] = compare_snapshots(
                                    snapshot,
                                    _gold_snapshot(case),
                                    case.comparison_mode,
                                    order_entitled=False,
                                )
                                if not row["correct"]:
                                    row["failure"] = "RESULT_MISMATCH"
                    else:
                        assert isinstance(proposal, QueryPlanV1Proposal)
                        plan = proposal.plan
                        row["plan_hash"] = plan.content_hash
                        fields = _exact_plan_fields(plan, case.gold_plan)
                        row["plan_exactness"] = fields
                        try:
                            validate_query_plan_v1(plan, catalog)
                            compiled = compiler.compile(plan)
                        except QueryPlanV1ValidationError:
                            row["failure"] = "PLAN_VALIDATION_FAILURE"
                        else:
                            row["compiled_sql_hash"] = text_hash(compiled.sql)
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
                                        row["compiled_sql_hash"],
                                        fixture,
                                        order_sensitive=False,
                                    )
                                    row["result_hash"] = snapshot.result_hash
                                    row["correct"] = compare_snapshots(
                                        snapshot,
                                        _gold_snapshot(case),
                                        case.comparison_mode,
                                        order_entitled=False,
                                    )
                                    if not row["correct"]:
                                        row["failure"] = "RESULT_MISMATCH"
                except LLMProviderError:
                    row["failure"] = "PROVIDER_FAILURE"
                row["elapsed_ms"] = (perf_counter() - started) * 1000
                outputs[operation] = row
            ledger.append(
                {
                    "case_id": case.case_id,
                    "split": case.split,
                    "shape": case.shape,
                    "order": order,
                    **outputs,
                }
            )
    finally:
        engine.dispose()
    control = sum(bool(item["control"]["correct"]) for item in ledger)
    intervention = sum(bool(item["intervention"]["correct"]) for item in ledger)
    b = sum(item["control"]["correct"] and not item["intervention"]["correct"] for item in ledger)
    c = sum(not item["control"]["correct"] and item["intervention"]["correct"] for item in ledger)
    result = {
        "version": "m16-queryplan-v1-result-1",
        "split": "CONFIRMATION" if confirmation else "PRIMARY",
        "n": len(ledger),
        "control_correct": control,
        "intervention_correct": intervention,
        "delta_pp": (intervention - control) * 100 / len(ledger),
        "A": sum(item["control"]["correct"] and item["intervention"]["correct"] for item in ledger),
        "B": b,
        "C": c,
        "D": sum(
            not item["control"]["correct"] and not item["intervention"]["correct"]
            for item in ledger
        ),
        "C_minus_B": c - b,
        "exact_p_value": _exact_binomial_pvalue(b, c),
        "ledger": ledger,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "primary", "confirmation"))
    args = parser.parse_args()
    if args.command == "prepare":
        dataset = prepare_dataset()
        print(json.dumps({"dataset_hash": dataset.dataset_hash, "n": len(dataset.cases)}))
        return
    dataset = M16Dataset.model_validate_json(DATASET_PATH.read_text())
    result = asyncio.run(run_experiment(dataset, confirmation=args.command == "confirmation"))
    RESULT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "split",
                    "n",
                    "control_correct",
                    "intervention_correct",
                    "delta_pp",
                    "A",
                    "B",
                    "C",
                    "D",
                    "exact_p_value",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
