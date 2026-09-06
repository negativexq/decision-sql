"""M19 — public transfer for the frozen QueryPlan V1 contract.

Gold SQL is consumed here only by the offline representability/lowering pass.
The provider runners receive the question and catalog-derived context, never
gold SQL, gold results, or representability labels.
"""

# The public-benchmark adapter is intentionally runtime-dynamic because the
# pinned Defog and BIRD packages expose untyped pandas/driver boundaries.
# Domain semantics remain typed in app.semantics.query_plan_v1.
# mypy: ignore-errors

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import sqlglot
from pydantic import ValidationError
from sqlglot import exp

from app.generation.provider import LLMProviderError, ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import QueryRequest
from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Catalog,
    QueryPlanV1Column,
    QueryPlanV1Join,
    QueryPlanV1JoinType,
    QueryPlanV1Operator,
    QueryPlanV1Predicate,
    QueryPlanV1Relationship,
    QueryPlanV1Table,
    QueryPlanV1Value,
    validate_query_plan_v1,
)
from app.semantics.query_plan_wire_v2 import query_plan_wire_v2_prompt
from evaluation.external.bird.benchmark import (
    BirdQuestion,
)
from evaluation.external.bird.benchmark import (
    load_questions as load_bird_questions,
)
from evaluation.external.bird.benchmark import (
    postgres_connection_kwargs as bird_connection_kwargs,
)
from evaluation.external.bird.schema import BIRD_TABLES
from evaluation.external.defog.benchmark import (
    BenchmarkQuestion,
    canonical_gold_variants,
)
from evaluation.external.defog.benchmark import (
    load_questions as load_defog_questions,
)
from evaluation.external.defog.benchmark import (
    postgres_connection_kwargs as defog_connection_kwargs,
)

ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = ROOT / "evaluation" / "results" / "m19"
ARTIFACT = ROOT / "evaluation" / "fixtures" / "m19_queryplan_public_transfer_result.json"
REASON_CODES = (
    "AGGREGATION",
    "GROUP_BY",
    "HAVING",
    "DISTINCT",
    "ORDER_BY",
    "LIMIT",
    "OFFSET",
    "WINDOW",
    "CTE",
    "SUBQUERY",
    "SET_OPERATION",
    "OR_BOOLEAN",
    "FUNCTION_OR_EXPRESSION",
    "TOO_MANY_JOINS",
    "TOO_MANY_PROJECTIONS",
    "TOO_MANY_FILTERS",
    "UNSUPPORTED_JOIN_TYPE",
    "UNDECLARED_RELATIONSHIP",
    "UNSUPPORTED_PREDICATE",
    "CATALOG_RESOLUTION_FAILURE",
    "MULTIPLE_UNSUPPORTED_FEATURES",
)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_identifier(name: str) -> bool:
    return bool(re.fullmatch(r"[a-z_][a-z0-9_]*", name))


@dataclass(frozen=True)
class PublicCatalog:
    name: str
    catalog: QueryPlanV1Catalog
    context: str
    catalog_hash: str
    tables: int
    columns: int
    relationships: int


def _connection(kind: str) -> dict[str, Any]:
    return defog_connection_kwargs() if kind == "defog" else bird_connection_kwargs()


def build_public_catalog(kind: str, db_name: str) -> PublicCatalog:
    """Build a catalog solely from information_schema and pg_constraint."""
    kwargs = _connection(kind)
    db = db_name if kind == "defog" else "bird"
    with psycopg.connect(dbname=db, **kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.table_schema, c.table_name, c.column_name, c.data_type,
                       c.udt_name, c.ordinal_position
                FROM information_schema.columns c
                WHERE c.table_schema NOT IN ('information_schema', 'pg_catalog')
                ORDER BY c.table_schema, c.table_name, c.ordinal_position
                """
            )
            column_rows = cursor.fetchall()
            cursor.execute(
                """
                SELECT sn.nspname, st.relname, sa.attname, tn.nspname, tt.relname,
                       ta.attname, array_length(fk.conkey, 1)
                FROM pg_constraint fk
                JOIN pg_class st ON st.oid = fk.conrelid
                JOIN pg_namespace sn ON sn.oid = st.relnamespace
                JOIN pg_class tt ON tt.oid = fk.confrelid
                JOIN pg_namespace tn ON tn.oid = tt.relnamespace
                JOIN pg_attribute sa ON sa.attrelid = st.oid AND sa.attnum = fk.conkey[1]
                JOIN pg_attribute ta ON ta.attrelid = tt.oid AND ta.attnum = fk.confkey[1]
                WHERE fk.contype = 'f' AND array_length(fk.conkey, 1) = 1
                  AND sn.nspname NOT IN ('information_schema', 'pg_catalog')
                ORDER BY sn.nspname, st.relname, sa.attname, tn.nspname, tt.relname, ta.attname
                """
            )
            relationship_rows = cursor.fetchall()
    grouped: dict[str, list[QueryPlanV1Column]] = {}
    table_names: dict[str, str] = {}
    allowed_bird_tables = (
        {table.lower() for table in BIRD_TABLES[db_name]} if kind == "bird" else None
    )
    for schema, table, column, data_type, udt_name, _position in column_rows:
        # QueryPlan V1 identifiers have no schema slot. A duplicate table name is
        # conservatively rejected rather than hidden behind an alias.
        table_id = str(table)
        if allowed_bird_tables is not None and table_id.lower() not in allowed_bird_tables:
            continue
        table_names[table_id] = f"{schema}.{table}"
        grouped.setdefault(table_id, []).append(
            QueryPlanV1Column(
                column_id=f"{table_id}.{column}",
                table_id=table_id,
                name=str(column),
                data_type=str(data_type or udt_name),
                description="",
            )
        )
    if len(table_names) != len({value.rsplit(".", 1)[1] for value in table_names.values()}):
        raise ValueError(f"duplicate unqualified table IDs in {kind}:{db_name}")
    tables = tuple(
        QueryPlanV1Table(table_id=table, description="", columns=tuple(columns))
        for table, columns in sorted(grouped.items())
    )
    relationships: list[QueryPlanV1Relationship] = []
    for (
        _left_schema,
        left_table,
        left_column,
        _right_schema,
        right_table,
        right_column,
        _,
    ) in relationship_rows:
        left = str(left_table)
        right = str(right_table)
        if left not in grouped or right not in grouped:
            continue
        rid = f"{left}.{left_column}__to__{right}.{right_column}"
        relationships.append(
            QueryPlanV1Relationship(
                relationship_id=rid,
                left_table_id=left,
                left_column_id=f"{left}.{left_column}",
                right_table_id=right,
                right_column_id=f"{right}.{right_column}",
            )
        )
    catalog = QueryPlanV1Catalog(tables=tables, relationships=tuple(relationships))
    context_lines = ["M19 PUBLIC BENCHMARK SERVER-OWNED SCHEMA CONTEXT"]
    for table in catalog.tables:
        context_lines.append(f"TABLE {table.table_id}")
        for column in table.columns:
            context_lines.append(f"  COLUMN {column.column_id} TYPE {column.data_type}")
    context_lines.append("SERVER-OWNED RELATIONSHIPS (both endpoints are authoritative):")
    for relationship in catalog.relationships:
        context_lines.append(
            f"  RELATIONSHIP {relationship.relationship_id}: "
            f"LEFT_TABLE {relationship.left_table_id} LEFT_COLUMN {relationship.left_column_id} "
            f"RIGHT_TABLE {relationship.right_table_id} RIGHT_COLUMN {relationship.right_column_id}"
        )
    context = "\n".join(context_lines)
    return PublicCatalog(
        name=f"{kind}:{db_name}",
        catalog=catalog,
        context=context,
        catalog_hash=catalog.content_hash,
        tables=len(tables),
        columns=sum(len(table.columns) for table in tables),
        relationships=len(relationships),
    )


def _table_name(table: exp.Table) -> str:
    return str(table.name)


def _unwrap_column(node: exp.Expression) -> exp.Column | None:
    if isinstance(node, exp.Alias):
        node = node.this
    return node if isinstance(node, exp.Column) and not node.is_star else None


def _source_table(node: exp.Expression | None) -> exp.Table | None:
    if isinstance(node, exp.Table):
        return node
    return None


def _split_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _split_and(node.left) + _split_and(node.right)
    return [node]


def _column_id(column: exp.Column, aliases: dict[str, str], catalog: QueryPlanV1Catalog) -> str:
    table = column.table or ""
    table_id = aliases.get(table, table)
    if not table_id:
        candidates = [
            item.column_id
            for item in catalog.tables
            for item in item.columns
            if item.name == column.name
        ]
        if len(candidates) != 1:
            raise ValueError("ambiguous column")
        return candidates[0]
    return f"{table_id}.{column.name}"


def _value(node: exp.Expression) -> QueryPlanV1Value:
    if isinstance(node, exp.Cast) and isinstance(node.this, exp.Literal) and node.this.is_string:
        target = node.to.sql(dialect="postgres").upper()
        kind = "date" if "DATE" in target and "TIME" not in target else "timestamp"
        return QueryPlanV1Value(kind=kind, value=str(node.this.this))
    if isinstance(node, exp.Boolean):
        return QueryPlanV1Value(kind="boolean", value=bool(node.this))
    if isinstance(node, exp.Literal):
        if node.is_string:
            return QueryPlanV1Value(kind="string", value=str(node.this))
        text = str(node.this)
        if re.fullmatch(r"[+-]?\d+", text):
            return QueryPlanV1Value(kind="integer", value=int(text))
        if re.fullmatch(r"[+-]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?", text):
            return QueryPlanV1Value(kind="decimal", value=text)
    raise ValueError("unsupported typed literal")


_OPERATOR_MAP: dict[type[exp.Expression], QueryPlanV1Operator] = {
    exp.EQ: QueryPlanV1Operator.EQ,
    exp.NEQ: QueryPlanV1Operator.NE,
    exp.LT: QueryPlanV1Operator.LT,
    exp.LTE: QueryPlanV1Operator.LTE,
    exp.GT: QueryPlanV1Operator.GT,
    exp.GTE: QueryPlanV1Operator.GTE,
}


def _predicate(
    node: exp.Expression, aliases: dict[str, str], catalog: QueryPlanV1Catalog
) -> QueryPlanV1Predicate:
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        column = _unwrap_column(node.this)
        if column is None:
            raise ValueError("invalid null predicate")
        return QueryPlanV1Predicate(
            column_id=_column_id(column, aliases, catalog),
            operator=QueryPlanV1Operator.IS_NULL,
            value=None,
        )
    if (
        isinstance(node, exp.Not)
        and isinstance(node.this, exp.Is)
        and isinstance(node.this.expression, exp.Null)
    ):
        column = _unwrap_column(node.this.this)
        if column is None:
            raise ValueError("invalid not-null predicate")
        return QueryPlanV1Predicate(
            column_id=_column_id(column, aliases, catalog),
            operator=QueryPlanV1Operator.IS_NOT_NULL,
            value=None,
        )
    operation = next(
        (operator for kind, operator in _OPERATOR_MAP.items() if isinstance(node, kind)), None
    )
    if operation is None:
        raise ValueError("unsupported predicate")
    left = _unwrap_column(node.left)
    if left is None:
        left, right = _unwrap_column(node.right), node.left
    else:
        right = node.right
    if left is None or isinstance(right, exp.Column):
        raise ValueError("predicate is not column-to-literal")
    return QueryPlanV1Predicate(
        column_id=_column_id(left, aliases, catalog), operator=operation, value=_value(right)
    )


def _relationship_for(
    condition: exp.Expression, aliases: dict[str, str], catalog: QueryPlanV1Catalog
) -> str:
    if not isinstance(condition, exp.EQ):
        raise ValueError("join condition is not equality")
    left, right = _unwrap_column(condition.left), _unwrap_column(condition.right)
    if left is None or right is None:
        raise ValueError("join condition is not two columns")
    left_id, right_id = _column_id(left, aliases, catalog), _column_id(right, aliases, catalog)
    matches = []
    for relationship in catalog.relationships:
        endpoints = {relationship.left_column_id, relationship.right_column_id}
        if {left_id, right_id} == endpoints:
            matches.append(relationship.relationship_id)
    if len(matches) != 1:
        raise ValueError("join is not one declared relationship")
    return matches[0]


@dataclass(frozen=True)
class Representability:
    status: str
    reasons: tuple[str, ...]
    plan: QueryPlanV1 | None


def lower_gold_sql(sql: str, catalog: QueryPlanV1Catalog) -> Representability:
    reasons: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="postgres")
        if len(statements) != 1:
            return Representability("UNSUPPORTED", ("SET_OPERATION",), None)
        tree = statements[0]
    except Exception:
        return Representability("UNSUPPORTED", ("CATALOG_RESOLUTION_FAILURE",), None)
    if not isinstance(tree, exp.Select):
        reasons.append("SET_OPERATION")
    if tree.args.get("with") is not None or tree.args.get("with_") is not None:
        reasons.append("CTE")
    forbidden = (
        (exp.Group, "GROUP_BY"),
        (exp.Having, "HAVING"),
        (exp.Order, "ORDER_BY"),
        (exp.Limit, "LIMIT"),
        (exp.Offset, "OFFSET"),
        (exp.Distinct, "DISTINCT"),
        (exp.Window, "WINDOW"),
    )
    for kind, reason in forbidden:
        if tree.find(kind) is not None:
            reasons.append(reason)
    if any(isinstance(node, exp.AggFunc) for node in tree.walk()):
        reasons.append("AGGREGATION")
    if any(
        isinstance(node, (exp.Subquery, exp.Union, exp.Intersect, exp.Except))
        for node in tree.walk()
    ):
        reasons.append("SUBQUERY" if tree.find(exp.Subquery) is not None else "SET_OPERATION")
    if tree.find(exp.Or) is not None:
        reasons.append("OR_BOOLEAN")
    from_node = tree.args.get("from_")
    source = _source_table(from_node.this if from_node is not None else None)
    joins = list(tree.find_all(exp.Join))
    if len(joins) > 2:
        reasons.append("TOO_MANY_JOINS")
    if len(tree.expressions) > 3 or not tree.expressions:
        reasons.append("TOO_MANY_PROJECTIONS")
    if any(_unwrap_column(item) is None for item in tree.expressions):
        reasons.append("FUNCTION_OR_EXPRESSION")
    where = tree.args.get("where")
    filters = _split_and(where.this) if where is not None else []
    if len(filters) > 2:
        reasons.append("TOO_MANY_FILTERS")
    if reasons or source is None:
        if source is None and "CATALOG_RESOLUTION_FAILURE" not in reasons:
            reasons.append("CATALOG_RESOLUTION_FAILURE")
        return Representability("UNSUPPORTED", tuple(dict.fromkeys(reasons)), None)
    source_id = _table_name(source)
    aliases = {source_id: source_id, source.alias_or_name: source_id}
    if source_id not in {table.table_id for table in catalog.tables}:
        return Representability("UNSUPPORTED", ("CATALOG_RESOLUTION_FAILURE",), None)
    if not _safe_identifier(source_id):
        return Representability("UNSUPPORTED", ("CATALOG_RESOLUTION_FAILURE",), None)
    for table in joins:
        join_table = _source_table(table.this)
        if join_table is None or not _safe_identifier(_table_name(join_table)):
            return Representability("UNSUPPORTED", ("CATALOG_RESOLUTION_FAILURE",), None)
        alias = join_table.alias_or_name
        aliases[alias] = _table_name(join_table)
    try:
        plan_joins: list[QueryPlanV1Join] = []
        for join in joins:
            kind = str(join.args.get("kind") or "INNER").upper()
            if kind not in {"INNER", "LEFT"}:
                raise ValueError("unsupported join type")
            relation_id = _relationship_for(join.args["on"], aliases, catalog)
            plan_joins.append(
                QueryPlanV1Join(relationship_id=relation_id, join_type=QueryPlanV1JoinType(kind))
            )
        projections = tuple(
            _column_id(_unwrap_column(item), aliases, catalog) for item in tree.expressions
        )  # type: ignore[arg-type]
        predicates = tuple(_predicate(item, aliases, catalog) for item in filters)
        plan = QueryPlanV1(
            applicable=True,
            source=source_id,
            joins=tuple(plan_joins),
            projection=projections,
            filters=predicates,
        )
        validate_query_plan_v1(plan, catalog)
        return Representability("REPRESENTABLE", (), plan)
    except ValueError as error:
        text = str(error)
        reason = (
            "UNSUPPORTED_JOIN_TYPE"
            if "join type" in text
            else "UNDECLARED_RELATIONSHIP"
            if "relationship" in text
            else "UNSUPPORTED_PREDICATE"
            if "predicate" in text or "literal" in text
            else "CATALOG_RESOLUTION_FAILURE"
        )
        return Representability("UNSUPPORTED", (reason,), None)
    except ValidationError:
        return Representability("UNSUPPORTED", ("CATALOG_RESOLUTION_FAILURE",), None)


def _defog_paths(root: Path) -> dict[str, Path]:
    return {
        "classic": root / "data" / "questions_gen_postgres.csv",
        "advanced": root / "data" / "instruct_advanced_postgres.csv",
    }


def _parity_context(catalog: PublicCatalog) -> str:
    return catalog.context


def _direct_prompt(question: str, context: str, extra: str = "") -> str:
    return (
        "Generate exactly one read-only PostgreSQL SELECT query. Return only SQL, no markdown. "
        "Use the supplied schema and answer the user question.\n\n"
        + context
        + extra
        + "\n\nQUESTION:\n"
        + question
    )


def _queryplan_prompt(question: str, context: str) -> str:
    return query_plan_wire_v2_prompt(context) + "\n\nQUESTION:\n" + question


async def _call(
    provider: OpenAICompatibleProvider, arm: str, question: str, context: str
) -> tuple[dict[str, Any], ModelIOCapture | None]:
    started = time.perf_counter()
    try:
        if arm == "direct":
            proposal = await provider.propose_sql(QueryRequest(question=question), None, context)
            record = {
                "success": True,
                "sql": proposal.sql,
                "latency_ms": proposal.latency_ms,
                "input_tokens": proposal.prompt_tokens,
                "output_tokens": proposal.completion_tokens,
            }
        else:
            proposal = await provider.propose_query_plan_wire_v2(question, context)
            record = {
                "success": True,
                "plan": proposal.plan,
                "wire": proposal.wire,
                "latency_ms": proposal.latency_ms,
                "input_tokens": proposal.prompt_tokens,
                "output_tokens": proposal.completion_tokens,
            }
    except LLMProviderError as error:
        record = {"success": False, "failure": "PROVIDER_FAILURE", "error": str(error)[:300]}
    capture = provider.consume_model_io()
    record["total_latency_ms"] = (time.perf_counter() - started) * 1000
    return record, capture if isinstance(capture, ModelIOCapture) else None


def _load_public_data(
    sql_eval: Path, bird_path: Path
) -> tuple[list[BenchmarkQuestion], list[BenchmarkQuestion], list[BirdQuestion]]:
    classic = load_defog_questions(_defog_paths(sql_eval)["classic"], "classic")
    advanced = load_defog_questions(_defog_paths(sql_eval)["advanced"], "advanced")
    bird = load_bird_questions(bird_path)
    if (len(classic), len(advanced), len(bird)) != (210, 64, 500):
        raise RuntimeError("public dataset population does not match frozen M15 populations")
    return classic, advanced, bird


def analyze_questions(
    questions: Iterable[Any], catalogs: dict[str, PublicCatalog], kind: str
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for question in questions:
        db_name = question.db_name if kind == "defog" else question.db_id
        catalog = catalogs[db_name]
        sql_variants = (
            canonical_gold_variants(question.gold_sql) if kind == "defog" else [question.gold_sql]
        )
        lowered_variants = [lower_gold_sql(variant, catalog.catalog) for variant in sql_variants]
        lowered = next(
            (candidate for candidate in lowered_variants if candidate.status == "REPRESENTABLE"),
            lowered_variants[0],
        )
        records.append(
            {
                "case_id": (
                    f"defog:{question.dataset}:{question.index}"
                    if kind == "defog"
                    else f"bird:{question.index}"
                ),
                "db_name": db_name,
                "question": question.question,
                "gold_sql_sha256": sha_text(question.gold_sql),
                "status": lowered.status,
                "reasons": list(lowered.reasons),
                "gold_plan": lowered.plan.model_dump(mode="json") if lowered.plan else None,
                "gold_variant_count": len(sql_variants),
                "catalog_hash": catalog.catalog_hash,
            }
        )
    return records


def _env_path(name: str, fallback: str) -> Path:
    value = os.getenv(name, fallback)
    return Path(value)


def coverage_command(sql_eval: Path, bird_path: Path) -> dict[str, Any]:
    classic, advanced, bird = _load_public_data(sql_eval, bird_path)
    defog_dbs = sorted({q.db_name for q in classic + advanced})
    bird_dbs = sorted({q.db_id for q in bird})
    catalogs = {name: build_public_catalog("defog", name) for name in defog_dbs}
    catalogs.update({name: build_public_catalog("bird", name) for name in bird_dbs})
    records = (
        analyze_questions(classic, catalogs, "defog")
        + analyze_questions(advanced, catalogs, "defog")
        + analyze_questions(bird, catalogs, "bird")
    )
    by_benchmark: dict[str, Any] = {}
    for label, subset in (
        ("defog_classic", records[: len(classic)]),
        ("defog_advanced", records[len(classic) : len(classic) + len(advanced)]),
        ("bird", records[len(classic) + len(advanced) :]),
    ):
        by_benchmark[label] = {
            "total": len(subset),
            "representable": sum(r["status"] == "REPRESENTABLE" for r in subset),
            "reasons": dict(
                Counter(
                    reason
                    for r in subset
                    if r["status"] != "REPRESENTABLE"
                    for reason in r["reasons"]
                )
            ),
            "case_ids": [r["case_id"] for r in subset if r["status"] == "REPRESENTABLE"],
        }
    return {
        "catalogs": {
            name: {
                "hash": c.catalog_hash,
                "tables": c.tables,
                "columns": c.columns,
                "relationships": c.relationships,
            }
            for name, c in catalogs.items()
        },
        "benchmarks": by_benchmark,
        "records": records,
    }


def _clean_sql(value: str | None) -> str:
    text = (value or "").strip()
    if text.startswith("```sql"):
        text = text[6:]
    elif text.startswith("```"):
        text = text[3:]
    return text[:-3].strip() if text.endswith("```") else text


def _gold_execution(kind: str, question: Any) -> Any:
    if kind == "defog":
        from evaluation.external.defog.benchmark import DefogBenchmarkExecutor

        executor = DefogBenchmarkExecutor()
        variants = canonical_gold_variants(question.gold_sql)
        return [(variant, executor.execute(question.db_name, variant)) for variant in variants]
    from evaluation.external.bird.benchmark import BirdBenchmarkExecutor

    return BirdBenchmarkExecutor().execute(question.gold_sql)


def _evaluate(
    kind: str, question: Any, generated: Any, gold: Any, generated_sql: str
) -> dict[str, bool]:
    if kind == "defog":
        from evaluation.external.defog.benchmark import canonical_compare

        exact = False
        subset = False
        for gold_sql, gold_result in gold:
            one_exact, one_subset = canonical_compare(
                gold_result, generated, question, gold_sql, generated_sql
            )
            exact |= one_exact
            subset |= one_subset
        return {"exact": exact, "subset": subset}
    from evaluation.external.bird.benchmark import bird_execution_match

    return {"exact": bird_execution_match(generated, gold), "subset": False}


def _prepare_representable(
    records: list[dict[str, Any]], catalogs: dict[str, PublicCatalog], kind: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply the mandatory evaluator-integrity gate before any provider call."""
    from app.semantics.query_plan_v1 import QueryPlanV1Compiler

    sql_eval = _env_path("DEFOG_SQL_EVAL_ROOT", "/external/sql-eval")
    bird_path = _env_path("BIRD_DATASET", "/bird/mini_dev_postgresql.json")
    classic, advanced, bird = _load_public_data(sql_eval, bird_path)
    all_questions: dict[str, Any] = {
        **{f"defog:{q.dataset}:{q.index}": q for q in classic + advanced},
        **{f"bird:{q.index}": q for q in bird},
    }
    valid: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for record in records:
        if record["status"] != "REPRESENTABLE" or not record.get("gold_plan"):
            continue
        question = all_questions[record["case_id"]]
        catalog = catalogs[record["db_name"]].catalog
        plan = QueryPlanV1.model_validate(record["gold_plan"])
        try:
            candidate = QueryPlanV1Compiler(catalog).compile(plan)
            # The compiler emits a normal SELECT candidate. Public evaluators
            # remain authoritative; this gate only verifies gold lowering.
            original = _gold_execution(kind, question)
            if kind == "defog":
                from evaluation.external.defog.benchmark import DefogBenchmarkExecutor

                generated = DefogBenchmarkExecutor().execute(question.db_name, candidate.sql)
                matches = _evaluate(kind, question, generated, original, candidate.sql)
                if not matches["exact"] and not matches["subset"]:
                    raise ValueError("lowered plan did not reproduce Defog gold")
            else:
                from evaluation.external.bird.benchmark import BirdBenchmarkExecutor

                generated = BirdBenchmarkExecutor().execute(candidate.sql)
                if not _evaluate(kind, question, generated, original, candidate.sql)["exact"]:
                    raise ValueError("lowered plan did not reproduce BIRD gold")
        except Exception as error:
            failures.append({"case_id": record["case_id"], "error": str(error)[:240]})
            continue
        valid.append(record)
    return valid, {"candidate_count": len(valid), "failures": failures}


def _assert_complete_preflight(
    grouped: dict[str, list[dict[str, Any]]],
    all_valid: dict[str, list[dict[str, Any]]],
    integrity: dict[str, dict[str, Any]],
) -> None:
    """Fail closed before constructing a provider for a transfer run."""
    failures = {
        label: {
            "candidate_count": len(grouped[label]),
            "valid_count": len(all_valid.get(label, [])),
            "integrity": integrity.get(label, {}),
        }
        for label in grouped
        if len(all_valid.get(label, [])) != len(grouped[label])
        or integrity.get(label, {}).get("failures")
    }
    if failures:
        raise RuntimeError(
            "M19 provider preflight failed closed; no provider was constructed: "
            + json.dumps(failures, default=str, sort_keys=True)
        )


def run_command(sql_eval: Path, bird_path: Path) -> dict[str, Any]:
    """Run paired transfer calls for all gold-proven representable cases."""
    from app.config import get_settings

    classic, advanced, bird = _load_public_data(sql_eval, bird_path)
    dbs = sorted({q.db_name for q in classic + advanced})
    bird_dbs = sorted({q.db_id for q in bird})
    catalogs = {name: build_public_catalog("defog", name) for name in dbs}
    catalogs.update({name: build_public_catalog("bird", name) for name in bird_dbs})
    grouped = {
        "defog_classic": analyze_questions(classic, catalogs, "defog"),
        "defog_advanced": analyze_questions(advanced, catalogs, "defog"),
        "bird": analyze_questions(bird, catalogs, "bird"),
    }
    all_valid: dict[str, list[dict[str, Any]]] = {}
    integrity: dict[str, Any] = {}
    for label, rows in grouped.items():
        kind = "bird" if label == "bird" else "defog"
        all_valid[label], integrity[label] = _prepare_representable(rows, catalogs, kind)
    _assert_complete_preflight(grouped, all_valid, integrity)
    settings = get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )
    provider = OpenAICompatibleProvider(settings)
    by_id = {f"defog:{q.dataset}:{q.index}": q for q in classic + advanced}
    by_id.update({f"bird:{q.index}": q for q in bird})
    results: list[dict[str, Any]] = []
    for label, rows in all_valid.items():
        kind = "bird" if label == "bird" else "defog"
        for row in rows:
            question = by_id[row["case_id"]]
            question_text = question.question
            if kind == "bird":
                question_text += f"\n\nBIRD expert evidence:\n{question.evidence}"
            catalog = catalogs[row["db_name"]]
            context = _parity_context(catalog)
            arms = ["direct", "queryplan"]
            if int(sha_text(row["case_id"])[0:8], 16) % 2:
                arms.reverse()
            arm_records: dict[str, Any] = {}
            for arm in arms:
                record, capture = asyncio.run(_call(provider, arm, question_text, context))
                if capture is not None:
                    record["model_io"] = capture.model_dump(mode="json")
                if record.get("success"):
                    if arm == "direct":
                        sql = _clean_sql(record["sql"])
                        record["sql"] = sql
                        try:
                            if kind == "defog":
                                from evaluation.external.defog.benchmark import (
                                    DefogBenchmarkExecutor,
                                )

                                generated = DefogBenchmarkExecutor().execute(question.db_name, sql)
                            else:
                                from evaluation.external.bird.benchmark import BirdBenchmarkExecutor

                                generated = BirdBenchmarkExecutor().execute(sql)
                            result = _evaluate(
                                kind,
                                question,
                                generated,
                                _gold_execution(kind, question),
                                sql,
                            )
                            record.update(result)
                        except Exception as error:
                            record.update(
                                {"failure": "EXECUTION_FAILURE", "error": str(error)[:240]}
                            )
                    else:
                        try:
                            plan = record["plan"]
                            validate_query_plan_v1(plan, catalog.catalog)
                            from app.semantics.query_plan_v1 import QueryPlanV1Compiler

                            sql = QueryPlanV1Compiler(catalog.catalog).compile(plan).sql
                            if kind == "defog":
                                from evaluation.external.defog.benchmark import (
                                    DefogBenchmarkExecutor,
                                )

                                generated = DefogBenchmarkExecutor().execute(question.db_name, sql)
                            else:
                                from evaluation.external.bird.benchmark import BirdBenchmarkExecutor

                                generated = BirdBenchmarkExecutor().execute(sql)
                            record.update(
                                _evaluate(
                                    kind,
                                    question,
                                    generated,
                                    _gold_execution(kind, question),
                                    sql,
                                )
                            )
                            record["sql"] = sql
                        except Exception as error:
                            record.update(
                                {"failure": "QUERYPLAN_FAILURE", "error": str(error)[:240]}
                            )
                arm_records[arm] = record
            results.append(
                {
                    "case_id": row["case_id"],
                    "benchmark": label,
                    "direct": arm_records.get("direct"),
                    "queryplan": arm_records.get("queryplan"),
                }
            )
    output = {
        "integrity": integrity,
        "results": results,
        "model": "gpt-5.6-luna",
        "provider_calls": len(results) * 2,
    }
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    (RESULT_ROOT / "transfer.json").write_text(json.dumps(output, indent=2, default=str) + "\n")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("coverage", "run"))
    parser.add_argument(
        "--sql-eval", type=Path, default=_env_path("DEFOG_SQL_EVAL_ROOT", "/external/sql-eval")
    )
    parser.add_argument(
        "--bird-dataset",
        type=Path,
        default=_env_path("BIRD_DATASET", "/bird/mini_dev_postgresql.json"),
    )
    args = parser.parse_args()
    result = (
        coverage_command(args.sql_eval, args.bird_dataset)
        if args.command == "coverage"
        else run_command(args.sql_eval, args.bird_dataset)
    )
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = RESULT_ROOT / (
        "coverage.json" if args.command == "coverage" else "transfer.json"
    )
    artifact_path.write_text(json.dumps(result, indent=2, default=str) + "\n")
    if args.command == "coverage":
        printable = {
            "benchmarks": result["benchmarks"],
            "artifact": str(artifact_path),
        }
    else:
        printable = {
            "integrity": result["integrity"],
            "provider_calls": result["provider_calls"],
            "artifact": str(artifact_path),
        }
    print(json.dumps(printable, indent=2))


if __name__ == "__main__":
    main()
