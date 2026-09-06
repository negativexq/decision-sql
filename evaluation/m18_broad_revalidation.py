"""Fresh M18 broad QueryPlan V1 revalidation after the residual context fix."""

from __future__ import annotations

import itertools
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.db.session import build_reader_engine
from app.provenance.canonical import text_hash
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Compiler,
    QueryPlanV1Operator,
    QueryPlanV1Value,
    stable_hash,
    validate_query_plan_v1,
)
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.m16_general_queryplan import _fixture, _type_family, _value_for
from evaluation.m17_queryplan_contract import (
    M17Case,
    M17Dataset,
    _settings,
    run,
)
from evaluation.m112p2_counterexample_diagnostic import _snapshot

FIXTURES = Path(__file__).with_name("fixtures")
DATASET_PATH = FIXTURES / "m18_broad_dataset.json"
MANIFEST_PATH = FIXTURES / "m18_broad_manifest.json"
PRIMARY_RESULT_PATH = FIXTURES / "m18_broad_primary_result.json"
CONFIRMATION_RESULT_PATH = FIXTURES / "m18_broad_confirmation_result.json"

SHAPE_TARGETS = {
    "SOURCE_PROJECTION": 20,
    "SOURCE_FILTER": 30,
    "ONE_JOIN_PROJECTION": 25,
    "ONE_JOIN_FILTER": 35,
    "TWO_JOINS_PROJECTION": 15,
    "TWO_JOINS_FILTER": 25,
}
PRIMARY_TARGETS = {
    "SOURCE_PROJECTION": 16,
    "SOURCE_FILTER": 24,
    "ONE_JOIN_PROJECTION": 20,
    "ONE_JOIN_FILTER": 28,
    "TWO_JOINS_PROJECTION": 12,
    "TWO_JOINS_FILTER": 20,
}


def _historical_hashes() -> set[str]:
    paths = (
        FIXTURES / "m16_dataset.json",
        FIXTURES / "m17_dataset.json",
        FIXTURES / "m18_targeted_dataset.json",
        FIXTURES / "m18_targeted_attempt2_dataset.json",
    )
    result: set[str] = set()
    for path in paths:
        if path.exists():
            payload = json.loads(path.read_text())
            items = payload.get("cases", payload) if isinstance(payload, dict) else payload
            result.update(item["gold_plan_hash"] for item in items)
    return result


def _paths(
    catalog: QueryPlanV1Catalog, join_count: int
) -> list[tuple[str, tuple[dict[str, str], ...], set[str]]]:
    paths: list[tuple[str, tuple[dict[str, str], ...], set[str]]] = []
    for source in catalog.tables:
        first_edges = [
            relationship
            for relationship in catalog.relationships
            if source.table_id in (relationship.left_table_id, relationship.right_table_id)
        ]
        if join_count == 0:
            paths.append((source.table_id, (), {source.table_id}))
            continue
        for first in first_edges:
            first_target = (
                first.right_table_id
                if first.left_table_id == source.table_id
                else first.left_table_id
            )
            first_joins = (("relationship_id", first.relationship_id), ("join_type", "INNER"))
            if join_count == 1:
                paths.append(
                    (
                        source.table_id,
                        (dict(first_joins),),
                        {source.table_id, first_target},
                    )
                )
                continue
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
                if second_target in {source.table_id, first_target}:
                    continue
                paths.append(
                    (
                        source.table_id,
                        (
                            dict(first_joins),
                            {
                                "relationship_id": second.relationship_id,
                                "join_type": "INNER",
                            },
                        ),
                        {source.table_id, first_target, second_target},
                    )
                )
    return paths


def _projection_columns(catalog: QueryPlanV1Catalog, tables: set[str]) -> list[str]:
    return [
        column.column_id
        for table in catalog.tables
        if table.table_id in tables
        for column in table.columns
    ]


def _predicates(
    catalog: QueryPlanV1Catalog, tables: set[str], connection: Any
) -> list[tuple[Any, ...]]:
    columns = [
        column
        for table in catalog.tables
        if table.table_id in tables
        for column in table.columns
        if _type_family(column.data_type) != "unsupported"
    ]
    result: list[tuple[Any, ...]] = []
    operators = (
        QueryPlanV1Operator.EQ,
        QueryPlanV1Operator.NE,
        QueryPlanV1Operator.GTE,
        QueryPlanV1Operator.LT,
    )
    for index, column in enumerate(columns):
        value = _value_for(column, connection)
        result.append(
            (
                {
                    "column_id": column.column_id,
                    "operator": operators[index % len(operators)],
                    "value": value,
                },
            )
        )
        if index + 1 < len(columns):
            other = columns[index + 1]
            if _type_family(other.data_type) == _type_family(column.data_type):
                result.append(
                    (
                        {
                            "column_id": column.column_id,
                            "operator": operators[index % len(operators)],
                            "value": value,
                        },
                        {
                            "column_id": other.column_id,
                            "operator": operators[(index + 1) % len(operators)],
                            "value": _value_for(other, connection),
                        },
                    )
                )
    return result


def _candidate_plans(
    catalog: QueryPlanV1Catalog, connection: Any, shape: str, excluded: set[str]
) -> list[QueryPlanV1]:
    join_count = {
        "SOURCE_PROJECTION": 0,
        "SOURCE_FILTER": 0,
        "ONE_JOIN_PROJECTION": 1,
        "ONE_JOIN_FILTER": 1,
        "TWO_JOINS_PROJECTION": 2,
        "TWO_JOINS_FILTER": 2,
    }[shape]
    has_filter = shape.endswith("FILTER")
    candidates: dict[str, QueryPlanV1] = {}
    for source, joins, tables in _paths(catalog, join_count):
        projections = _projection_columns(catalog, tables)
        predicate_sets = _predicates(catalog, tables, connection) if has_filter else [()]
        for size in (1, 2, 3):
            for projection in itertools.combinations(projections, size):
                for filters in predicate_sets:
                    plan = QueryPlanV1(
                        applicable=True,
                        source=source,
                        joins=joins,
                        projection=projection,
                        filters=filters,
                    )
                    if plan.content_hash not in excluded:
                        candidates[plan.content_hash] = plan
    return sorted(
        candidates.values(), key=lambda plan: stable_hash(f"m18-broad:{shape}:{plan.content_hash}")
    )


def _value_text(value: QueryPlanV1Value) -> str:
    if value.kind == "string":
        return f'"{value.value}"'
    return str(value.value).lower() if value.kind == "boolean" else str(value.value)


def _operator_text(operator: QueryPlanV1Operator) -> str:
    return {
        QueryPlanV1Operator.EQ: "equals",
        QueryPlanV1Operator.NE: "does not equal",
        QueryPlanV1Operator.LT: "is less than",
        QueryPlanV1Operator.LTE: "is at most",
        QueryPlanV1Operator.GT: "is greater than",
        QueryPlanV1Operator.GTE: "is at least",
    }[operator]


def _question(plan: QueryPlanV1, catalog: QueryPlanV1Catalog, index: int) -> str:
    assert plan.source is not None
    current = plan.source
    path: list[str] = []
    for join in plan.joins:
        relationship = catalog.relationship(join.relationship_id)
        if relationship.left_table_id == current:
            target = relationship.right_table_id
            local_column = relationship.left_column_id.split(".")[-1].replace("_", " ")
        else:
            target = relationship.left_table_id
            local_column = relationship.left_column_id.split(".")[-1].replace("_", " ")
        path.append(f"{target.replace('_', ' ')} through its {local_column}")
        current = target
    selected = [
        f"{column.split('.')[0].replace('_', ' ')} {column.split('.')[1].replace('_', ' ')}"
        for column in plan.projection
    ]
    selected_text = ", ".join(selected[:-1]) + (
        f", and {selected[-1]}" if len(selected) > 1 else selected[0]
    )
    source = plan.source.replace("_", " ")
    if path:
        body = f"starting from {source} records and joining " + " and then ".join(path)
    else:
        body = f"from {source} records"
    if plan.filters:
        filters = " and ".join(
            f"{item.column_id.replace('_', ' ')} {_operator_text(item.operator)} "
            f"{_value_text(item.value) if item.value is not None else 'NULL'}"
            for item in plan.filters
        )
        body += f" where {filters}"
    templates = (
        "Return {selected} {body}.",
        "For the requested relational report, show {selected} {body}.",
        "List {selected} {body}.",
        "Provide {selected} {body}.",
    )
    return templates[index % len(templates)].format(selected=selected_text, body=body)


def _prepare() -> M17Dataset:
    settings = _settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    catalog = QueryPlanV1Catalog.from_schema(safety.catalog)
    compiler = QueryPlanV1Compiler(catalog)
    excluded = _historical_hashes()
    fixture = _fixture()
    cases: list[M17Case] = []
    try:
        with engine.connect() as connection:
            for shape, target in SHAPE_TARGETS.items():
                selected = _candidate_plans(catalog, connection, shape, excluded)[:target]
                if len(selected) != target:
                    raise RuntimeError(f"insufficient fresh plans for {shape}: {len(selected)}")
                for plan in selected:
                    excluded.add(plan.content_hash)
                    validate_query_plan_v1(plan, catalog)
                    compiled = compiler.compile(plan)
                    if compiled.sql != compiler.compile(plan).sql:
                        raise RuntimeError(f"compiler nondeterminism: {plan.content_hash}")
                    planned = safety.plan(
                        SqlCandidate(
                            sql=compiled.sql, correlation_id=f"m18-broad-gold-{len(cases):03d}"
                        )
                    )
                    if not isinstance(planned, QueryPlan):
                        raise RuntimeError(f"M1 failure: {plan.content_hash}")
                    execution = safety.execute(planned)
                    if not isinstance(execution, QueryExecution) or execution.truncated:
                        raise RuntimeError(f"execution failure: {plan.content_hash}")
                    snapshot = _snapshot(
                        execution, text_hash(compiled.sql), fixture, order_sensitive=False
                    )
                    repeat = safety.execute(planned)
                    if not isinstance(repeat, QueryExecution):
                        raise RuntimeError(f"repeat execution failure: {plan.content_hash}")
                    repeat_snapshot = _snapshot(
                        repeat, text_hash(compiled.sql), fixture, order_sensitive=False
                    )
                    if repeat_snapshot.result_hash != snapshot.result_hash:
                        raise RuntimeError(f"result nondeterminism: {plan.content_hash}")
                    split = (
                        "PRIMARY"
                        if sum(1 for c in cases if c.shape == shape) < PRIMARY_TARGETS[shape]
                        else "CONFIRMATION"
                    )
                    question = _question(plan, catalog, len(cases))
                    cases.append(
                        M17Case(
                            case_id=f"m18-broad-{len(cases):03d}",
                            shape=shape,
                            split=split,
                            question=question,
                            question_hash=text_hash(" ".join(question.lower().split())),
                            gold_plan=plan,
                            gold_plan_hash=plan.content_hash,
                            gold_sql=compiled.sql,
                            gold_sql_hash=text_hash(compiled.sql),
                            comparison_mode="VALUE_BAG",
                            gold_columns=snapshot.columns,
                            gold_typed_rows=snapshot.typed_rows,
                            gold_row_count=snapshot.row_count,
                            gold_result_hash=snapshot.result_hash,
                        )
                    )
    finally:
        engine.dispose()
    if len(cases) != 150 or len({c.question_hash for c in cases}) != 150:
        raise RuntimeError("broad question uniqueness failure")
    dataset = M17Dataset(
        version="m18-broad-queryplan-v1-1",
        wire_version="QueryPlanWireV2",
        catalog_hash=catalog.content_hash,
        cases=tuple(cases),
        dataset_hash="placeholder",
    )
    dataset = dataset.model_copy(
        update={"dataset_hash": stable_hash(dataset.model_dump(mode="json"))}
    )
    DATASET_PATH.write_text(
        json.dumps(dataset.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    )
    return dataset


def _freeze(dataset: M17Dataset) -> dict[str, Any]:
    settings = _settings()
    manifest = {
        "version": "m18-broad-manifest-1",
        "attempt_id": "M18-BROAD-ATTEMPT-2",
        "starting_head": "ef3763910df9d5b17d2be7dfb8d49782829c2ff2",
        "dataset_hash": dataset.dataset_hash,
        "catalog_hash": dataset.catalog_hash,
        "provider": "openai-compatible",
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "reasoning_effort": settings.llm_reasoning_effort,
        "memory": "OFF",
        "retrieval": "OFF",
        "reranker": "OFF",
        "repair_calls": 0,
        "shape_targets": SHAPE_TARGETS,
        "primary_targets": PRIMARY_TARGETS,
        "source_hashes": {
            "query_plan_v1": text_hash(Path("app/semantics/query_plan_v1.py").read_text()),
            "provider": text_hash(Path("app/generation/provider.py").read_text()),
            "wire": text_hash(Path("app/semantics/query_plan_wire_v2.py").read_text()),
            "m1": text_hash(Path("app/sql/service.py").read_text()),
            "runner": text_hash(Path(__file__).read_text()),
        },
    }
    manifest["manifest_hash"] = stable_hash(manifest)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "freeze", "primary", "confirmation"))
    args = parser.parse_args()
    if args.command == "prepare":
        dataset = _prepare()
        print(
            json.dumps(
                {
                    "n": len(dataset.cases),
                    "shape_counts": dict(Counter(c.shape for c in dataset.cases)),
                }
            )
        )
    elif args.command == "freeze":
        dataset = M17Dataset.model_validate_json(DATASET_PATH.read_text())
        print(json.dumps(_freeze(dataset)))
    else:
        dataset = M17Dataset.model_validate_json(DATASET_PATH.read_text())
        if args.command == "confirmation":
            confirmation_cases = tuple(
                case.model_copy(update={"split": "PRIMARY"})
                for case in dataset.cases
                if case.split == "CONFIRMATION"
            )
            confirmation_dataset = dataset.model_copy(update={"cases": confirmation_cases})
            result = __import__("asyncio").run(run(confirmation_dataset, confirmation=False))
            result["split"] = "CONFIRMATION"
        else:
            result = __import__("asyncio").run(run(dataset, confirmation=False))
        result["attempt_id"] = "M18-BROAD-ATTEMPT-2"
        path = PRIMARY_RESULT_PATH if args.command == "primary" else CONFIRMATION_RESULT_PATH
        path.write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n")
        print(
            json.dumps(
                {
                    key: result[key]
                    for key in (
                        "n",
                        "control_correct",
                        "intervention_correct",
                        "contract_acquired",
                        "A",
                        "B",
                        "C",
                        "D",
                        "C_minus_B",
                        "exact_p_value",
                        "technical_retries",
                    )
                }
            )
        )


if __name__ == "__main__":
    main()
