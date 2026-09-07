"""Bounded, untrusted semantic blueprint for one-call SQL generation."""

from __future__ import annotations

import json
import re
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field

from app.generation.quality_pack import render_query_quality_pack


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
    parse_warnings: tuple[str, ...] = ()


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
        "BLUEPRINT FIELD CONTRACT (the blueprint is best-effort diagnostics; SQL is the only "
        "value that will be executed):\n"
        "population: compact logical entity set; grain: bounded result-grain strings; joins: "
        "conceptual endpoints and relationship; filters: target/operator/value_or_rule; "
        "aggregations: measure/function; temporal: compact time rules; projection: requested "
        "outputs; ordering: expression and ASC or DESC; limit: integer or null; "
        "distinct_required: boolean; notes: short bounded notes; checks: optional boolean "
        "self-check assertions. If a descriptive field is easier to express as a string, use "
        "a string; the receiver will normalize it.\n\n"
        f"{render_query_quality_pack()}\n"
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
    """Parse a provider object without allowing blueprint formatting to discard valid SQL.

    The blueprint is descriptive and has no authority. Provider responses seen in practice
    vary between a typed object and compact strings/lists, so the parser performs bounded,
    loss-minimizing normalization and records warnings. The SQL value is never rewritten.
    """
    content = payload
    if isinstance(payload, str):
        raw = payload.strip()
        try:
            content = json.loads(raw)
        except json.JSONDecodeError:
            fenced = re.fullmatch(
                r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.IGNORECASE | re.DOTALL
            )
            if fenced is None:
                raise ValueError("blueprint response must be JSON") from None
            content = json.loads(fenced.group(1))
    if not isinstance(content, dict):
        raise ValueError("blueprint response must be a JSON object")
    sql = content.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("blueprint response must contain a non-empty SQL string")

    warnings: list[str] = []
    unexpected = sorted(set(content) - {"blueprint", "sql"})
    if unexpected:
        warnings.append("ignored unexpected top-level fields: " + ", ".join(unexpected[:8]))
    blueprint = content.get("blueprint")
    if blueprint is None:
        blueprint = {}
        warnings.append("blueprint missing; SQL accepted with an empty diagnostic blueprint")
    elif not isinstance(blueprint, dict):
        blueprint = {}
        warnings.append(
            "blueprint was not an object; SQL accepted with an empty diagnostic blueprint"
        )
    normalized = _normalize_blueprint(blueprint, warnings)
    return BlueprintSqlProposal(
        blueprint=QueryBlueprint.model_validate(normalized),
        sql=sql,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_prompt_tokens=cached_prompt_tokens,
        latency_ms=latency_ms,
        parse_warnings=tuple(warnings),
    )


_BLUEPRINT_FIELDS = {
    "population",
    "grain",
    "joins",
    "filters",
    "aggregations",
    "temporal",
    "projection",
    "ordering",
    "limit",
    "distinct_required",
    "notes",
    "checks",
}


def _text(value: Any, *, field: str, max_length: int, warnings: list[str]) -> str:
    if isinstance(value, str):
        result = value.strip()
    elif value is None:
        result = ""
    else:
        try:
            result = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            result = str(value)
        warnings.append(f"normalized {field} to text")
    if len(result) > max_length:
        warnings.append(f"truncated oversized {field}")
        return result[:max_length]
    return result


def _items(value: Any, *, field: str, warnings: list[str]) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value[:]
    warnings.append(f"normalized scalar {field} to a one-item list")
    return [value]


def _normalize_join(value: Any, warnings: list[str]) -> dict[str, str]:
    if isinstance(value, dict):
        left = value.get("left", value.get("from", "descriptive"))
        right = value.get("right", value.get("to", "descriptive"))
        relationship = value.get("relationship", value.get("on", "descriptive join"))
        return {
            "left": _text(left, field="join.left", max_length=160, warnings=warnings)
            or "descriptive",
            "right": _text(right, field="join.right", max_length=160, warnings=warnings)
            or "descriptive",
            "relationship": _text(
                relationship, field="join.relationship", max_length=240, warnings=warnings
            )
            or "descriptive join",
        }
    raw = _text(value, field="join", max_length=240, warnings=warnings)
    match = re.split(r"\s*(?:->|<->|=|\bJOIN\b)\s*", raw, maxsplit=1, flags=re.IGNORECASE)
    if len(match) == 2 and all(match):
        return {"left": match[0][:160], "right": match[1][:160], "relationship": raw[:240]}
    return {
        "left": "descriptive",
        "right": "descriptive",
        "relationship": raw or "descriptive join",
    }


def _normalize_filter(value: Any, warnings: list[str]) -> dict[str, str]:
    if isinstance(value, dict):
        target = value.get(
            "target", value.get("column", value.get("field", "descriptive predicate"))
        )
        operator = value.get("operator", "DESCRIPTIVE")
        rule = value.get("value_or_rule", value.get("value", value.get("rule", "descriptive")))
        return {
            "target": _text(target, field="filter.target", max_length=160, warnings=warnings)
            or "descriptive predicate",
            "operator": _text(operator, field="filter.operator", max_length=40, warnings=warnings)
            or "DESCRIPTIVE",
            "value_or_rule": _text(
                rule, field="filter.value_or_rule", max_length=240, warnings=warnings
            )
            or "descriptive",
        }
    return {
        "target": "descriptive predicate",
        "operator": "DESCRIPTIVE",
        "value_or_rule": _text(value, field="filter", max_length=240, warnings=warnings)
        or "descriptive",
    }


def _normalize_aggregation(value: Any, warnings: list[str]) -> dict[str, str]:
    if isinstance(value, dict):
        measure = value.get("measure", value.get("metric", value.get("expression", "descriptive")))
        function = value.get("function", value.get("aggregation", "DESCRIPTIVE"))
        return {
            "measure": _text(
                measure, field="aggregation.measure", max_length=160, warnings=warnings
            )
            or "descriptive",
            "function": _text(
                function, field="aggregation.function", max_length=40, warnings=warnings
            )
            or "DESCRIPTIVE",
        }
    return {
        "measure": _text(value, field="aggregation", max_length=160, warnings=warnings)
        or "descriptive",
        "function": "DESCRIPTIVE",
    }


def _normalize_ordering(value: Any, warnings: list[str]) -> dict[str, str]:
    if isinstance(value, dict):
        expression = value.get("expression", value.get("by", value.get("column", "descriptive")))
        direction = _text(
            value.get("direction", "ASC"),
            field="ordering.direction",
            max_length=8,
            warnings=warnings,
        ).upper()
    else:
        expression = value
        raw = _text(value, field="ordering", max_length=160, warnings=warnings)
        direction = "DESC" if re.search(r"\bDESC(?:ENDING)?\b", raw, re.IGNORECASE) else "ASC"
        if direction == "DESC":
            expression = re.sub(r"\s+DESC(?:ENDING)?\s*$", "", raw, flags=re.IGNORECASE)
        else:
            expression = re.sub(r"\s+ASC(?:ENDING)?\s*$", "", raw, flags=re.IGNORECASE)
    if direction not in {"ASC", "DESC"}:
        warnings.append("normalized invalid ordering direction to ASC")
        direction = "ASC"
    return {
        "expression": _text(
            expression, field="ordering.expression", max_length=160, warnings=warnings
        )
        or "descriptive",
        "direction": direction,
    }


def _normalize_blueprint(source: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    unknown = sorted(set(source) - _BLUEPRINT_FIELDS)
    if unknown:
        warnings.append("ignored unexpected blueprint fields: " + ", ".join(unknown[:8]))
    result: dict[str, Any] = {
        "population": _text(
            source.get("population", ""), field="population", max_length=160, warnings=warnings
        ),
        "grain": [
            _text(item, field="grain", max_length=160, warnings=warnings)
            for item in _items(source.get("grain"), field="grain", warnings=warnings)[:4]
        ],
        "joins": [
            _normalize_join(item, warnings)
            for item in _items(source.get("joins"), field="joins", warnings=warnings)[:8]
        ],
        "filters": [
            _normalize_filter(item, warnings)
            for item in _items(source.get("filters"), field="filters", warnings=warnings)[:12]
        ],
        "aggregations": [
            _normalize_aggregation(item, warnings)
            for item in _items(source.get("aggregations"), field="aggregations", warnings=warnings)[
                :6
            ]
        ],
        "temporal": [
            _text(item, field="temporal", max_length=160, warnings=warnings)
            for item in _items(source.get("temporal"), field="temporal", warnings=warnings)[:6]
        ],
        "projection": [
            _text(item, field="projection", max_length=160, warnings=warnings)
            for item in _items(source.get("projection"), field="projection", warnings=warnings)[:12]
        ],
        "ordering": [
            _normalize_ordering(item, warnings)
            for item in _items(source.get("ordering"), field="ordering", warnings=warnings)[:6]
        ],
        "limit": None,
        "distinct_required": False,
        "notes": [
            _text(item, field="notes", max_length=240, warnings=warnings)
            for item in _items(source.get("notes"), field="notes", warnings=warnings)[:6]
        ],
        "checks": None,
    }
    limit = source.get("limit")
    if isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 100_000:
        result["limit"] = limit
    elif isinstance(limit, str) and limit.strip().isdigit() and 1 <= int(limit.strip()) <= 100_000:
        result["limit"] = int(limit.strip())
        warnings.append("normalized string limit to integer")
    elif limit is not None:
        warnings.append("ignored invalid blueprint limit")
    distinct = source.get("distinct_required", False)
    if isinstance(distinct, bool):
        result["distinct_required"] = distinct
    elif distinct is not None:
        warnings.append("ignored non-boolean distinct_required")
    checks = source.get("checks")
    if isinstance(checks, dict):
        result["checks"] = {
            key: value
            for key, value in checks.items()
            if key in BlueprintChecks.model_fields and isinstance(value, bool)
        }
        if set(checks) - set(result["checks"]):
            warnings.append("ignored non-boolean or unknown blueprint checks")
    elif checks is not None:
        warnings.append("kept non-object blueprint checks out of typed diagnostics")
    return result


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
