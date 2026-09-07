"""Bounded, untrusted semantic blueprint for one-call SQL generation."""

from __future__ import annotations

import json
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field


class BlueprintJoin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str = Field(min_length=1, max_length=160)
    right: str = Field(min_length=1, max_length=160)
    relationship: str = Field(min_length=1, max_length=240)


class BlueprintFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str = Field(min_length=1, max_length=160)
    operator: str = Field(min_length=1, max_length=40)
    value_or_rule: str = Field(min_length=1, max_length=240)


class BlueprintAggregation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    measure: str = Field(min_length=1, max_length=160)
    function: str = Field(min_length=1, max_length=40)


class BlueprintOrdering(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: str = Field(min_length=1, max_length=160)
    direction: str = Field(pattern="^(ASC|DESC)$")


class BlueprintChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    identifiers_exist: bool = False
    join_paths_supported: bool = False
    aggregation_grain_consistent: bool = False
    function_signatures_postgresql: bool = False
    filter_values_grounded: bool = False
    order_limit_consistent: bool = False


class QueryBlueprint(BaseModel):
    """Compact model assertion with no compilation or execution authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    population: str = Field(default="", max_length=160)
    grain: list[str] = Field(default_factory=list, max_length=4)
    joins: list[BlueprintJoin] = Field(default_factory=list, max_length=8)
    filters: list[BlueprintFilter] = Field(default_factory=list, max_length=12)
    aggregations: list[BlueprintAggregation] = Field(default_factory=list, max_length=6)
    temporal: list[str] = Field(default_factory=list, max_length=6)
    projection: list[str] = Field(default_factory=list, max_length=12)
    ordering: list[BlueprintOrdering] = Field(default_factory=list, max_length=6)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    distinct_required: bool = False
    notes: list[str] = Field(default_factory=list, max_length=6)
    checks: BlueprintChecks | None = None


class BlueprintSqlProposal(BaseModel):
    """One untrusted blueprint and SQL from one provider response."""

    model_config = ConfigDict(frozen=True)

    blueprint: QueryBlueprint
    sql: str = Field(min_length=1)
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_prompt_tokens: int | None = None
    latency_ms: float | None = None


def blueprint_messages(question: str, schema_context: str) -> list[dict[str, str]]:
    """Build the dedicated M27 contract without requesting chain-of-thought."""
    system = (
        "You generate one read-only PostgreSQL analytical query. Use only the bounded schema "
        "context below. First determine a compact semantic blueprint, then generate SQL "
        "consistent with that blueprint. Return exactly one JSON object with exactly these "
        "top-level keys: blueprint and sql. The blueprint is untrusted descriptive guidance, "
        "not executable SQL. The sql value must contain exactly one read-only PostgreSQL query. "
        "Do not return Markdown, reasoning prose, chain-of-thought, or extra top-level fields. "
        "Do not invent tables, columns, values, joins, or functions. Use empty arrays or null "
        "when a concept is not implied by the question. Verify identifiers, PostgreSQL function "
        "signatures, aggregation grain, and order/limit consistency before finalizing.\n\n"
        "BLUEPRINT FIELD CONTRACT:\n"
        "population: compact logical entity set; grain: bounded result-grain strings; joins: "
        "conceptual endpoints and relationship; filters: target/operator/value_or_rule; "
        "aggregations: measure/function; temporal: compact time rules; projection: requested "
        "outputs; ordering: expression and ASC or DESC; limit: integer or null; "
        "distinct_required: boolean; notes: short bounded notes; checks: optional boolean "
        "self-check assertions.\n\n"
        f"BOUNDED SCHEMA CONTEXT:\n{schema_context}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def parse_blueprint_payload(
    payload: Any,
    *,
    model: str,
    provider: str,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cached_prompt_tokens: int | None = None,
    latency_ms: float | None = None,
) -> BlueprintSqlProposal:
    """Parse the strict provider object; never rewrite the SQL value."""
    content = payload
    if isinstance(payload, str):
        content = json.loads(payload)
    if not isinstance(content, dict):
        raise ValueError("blueprint response must be a JSON object")
    if set(content) != {"blueprint", "sql"}:
        raise ValueError("blueprint response has unexpected top-level fields")
    return BlueprintSqlProposal(
        blueprint=QueryBlueprint.model_validate(content["blueprint"]),
        sql=content["sql"],
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        latency_ms=latency_ms,
    )


def blueprint_sql_consistency(blueprint: QueryBlueprint, sql: str) -> dict[str, Any]:
    """Return deterministic diagnostics; never blocks or repairs the SQL."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return {"classification": "BLUEPRINT_SQL_STRONG_MISMATCH", "parseable": False}
    sql_tables = {table.name.casefold() for table in tree.find_all(sqlglot.exp.Table)}
    join_count = len(list(tree.find_all(sqlglot.exp.Join)))
    aggregate_names = {
        str(node.key).casefold() for node in tree.walk() if isinstance(node, sqlglot.exp.AggFunc)
    }
    limit_node = tree.find(sqlglot.exp.Limit)
    checks: dict[str, bool] = {
        "tables_joins": all(
            endpoint.split(".", 1)[0].casefold() in sql_tables
            for join in blueprint.joins
            for endpoint in (join.left, join.right)
            if endpoint
        )
        and (not blueprint.joins or join_count > 0),
        "filters": not blueprint.filters or tree.find(sqlglot.exp.Where) is not None,
        "aggregation": not blueprint.aggregations
        or any(
            aggregation.function.casefold() in aggregate_names
            for aggregation in blueprint.aggregations
        ),
        "distinct": not blueprint.distinct_required or tree.find(sqlglot.exp.Distinct) is not None,
        "order": not blueprint.ordering or tree.find(sqlglot.exp.Order) is not None,
        "limit": blueprint.limit is None
        or (limit_node is not None and int(limit_node.expression.name) == blueprint.limit),
    }
    failed = [name for name, passed in checks.items() if not passed]
    classification = (
        "BLUEPRINT_SQL_CONSISTENT"
        if not failed
        else "BLUEPRINT_SQL_STRONG_MISMATCH"
        if len(failed) >= 3
        else "BLUEPRINT_SQL_PARTIAL_MISMATCH"
    )
    return {
        "classification": classification,
        "parseable": True,
        "failed_checks": failed,
        "join_count": join_count,
        "sql_table_count": len(sql_tables),
    }
