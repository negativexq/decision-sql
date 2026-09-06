"""Versioned provider wire contract for the unchanged QueryPlan V1 domain plan."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from app.semantics.query_plan_v1 import (
    QueryPlanV1,
    QueryPlanV1Join,
    QueryPlanV1Operator,
    QueryPlanV1Predicate,
    QueryPlanV1Value,
)


class QueryPlanWireV2StringValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["string"]
    value: StrictStr


class QueryPlanWireV2IntegerValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["integer"]
    value: StrictInt


class QueryPlanWireV2DecimalValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["decimal"]
    value: StrictStr


class QueryPlanWireV2BooleanValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["boolean"]
    value: StrictBool


class QueryPlanWireV2DateValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["date"]
    value: StrictStr


class QueryPlanWireV2TimestampValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["timestamp"]
    value: StrictStr


QueryPlanWireV2Value = Annotated[
    QueryPlanWireV2StringValue
    | QueryPlanWireV2IntegerValue
    | QueryPlanWireV2DecimalValue
    | QueryPlanWireV2BooleanValue
    | QueryPlanWireV2DateValue
    | QueryPlanWireV2TimestampValue,
    Field(discriminator="kind"),
]


class QueryPlanWireV2Join(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: StrictStr
    join_type: Literal["INNER", "LEFT"]


class QueryPlanWireV2Predicate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column_id: StrictStr
    operator: Literal["EQ", "NE", "LT", "LTE", "GT", "GTE", "IS_NULL", "IS_NOT_NULL"]
    value: QueryPlanWireV2Value | None

    @model_validator(mode="after")
    def validate_value_slot(self) -> QueryPlanWireV2Predicate:
        null_operator = self.operator in {"IS_NULL", "IS_NOT_NULL"}
        if null_operator and self.value is not None:
            raise ValueError("IS_NULL and IS_NOT_NULL require value=null")
        if not null_operator and self.value is None:
            raise ValueError("value-taking operators require a typed value")
        return self


class QueryPlanWireV2(BaseModel):
    """All fields are required on the wire to remove omission ambiguity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applicable: StrictBool
    source: StrictStr | None
    joins: tuple[QueryPlanWireV2Join, ...]
    projection: tuple[StrictStr, ...]
    filters: tuple[QueryPlanWireV2Predicate, ...]

    @model_validator(mode="after")
    def validate_wire_bounds(self) -> QueryPlanWireV2:
        if self.applicable and self.source is None:
            raise ValueError("applicable wire plans require source")
        if not self.applicable and (
            self.source is not None or self.joins or self.projection or self.filters
        ):
            raise ValueError("non-applicable wire plans cannot contain semantic slots")
        return self


def wire_to_query_plan_v1(wire: QueryPlanWireV2) -> QueryPlanV1:
    """Perform only explicit field/enum/wrapper conversion; never repair semantics."""
    payload = {
        "applicable": wire.applicable,
        "source": wire.source,
        "joins": [
            QueryPlanV1Join(
                relationship_id=join.relationship_id,
                join_type=join.join_type,
            )
            for join in wire.joins
        ],
        "projection": tuple(wire.projection),
        "filters": [
            QueryPlanV1Predicate(
                column_id=predicate.column_id,
                operator=QueryPlanV1Operator(predicate.operator),
                value=(
                    QueryPlanV1Value(
                        kind=predicate.value.kind,
                        value=predicate.value.value,
                    )
                    if predicate.value is not None
                    else None
                ),
            )
            for predicate in wire.filters
        ],
    }
    return QueryPlanV1.model_validate(payload)


def query_plan_wire_v2_schema() -> dict[str, object]:
    return QueryPlanWireV2.model_json_schema()


def query_plan_wire_v2_schema_text() -> str:
    return json.dumps(query_plan_wire_v2_schema(), sort_keys=True, separators=(",", ":"))


def query_plan_wire_v2_schema_hash() -> str:
    from hashlib import sha256

    return sha256(query_plan_wire_v2_schema_text().encode("utf-8")).hexdigest()


def query_plan_wire_v2_prompt(schema_context: str) -> str:
    schema = query_plan_wire_v2_schema_text()
    example = {
        "applicable": True,
        "source": "example_table",
        "joins": [],
        "projection": ["example_table.id"],
        "filters": [],
    }
    return (
        "Return exactly one JSON object conforming to the versioned QueryPlanWireV2 "
        "schema below. This is a provider wire format, not SQL. Use only exact IDs "
        "from the supplied context. All five top-level fields are required. "
        "applicable is a JSON boolean; source is a string or null; joins, projection, "
        "and filters are arrays. join_type is INNER or LEFT. operator is one of EQ, "
        "NE, LT, LTE, GT, GTE, IS_NULL, IS_NOT_NULL. Typed values use kind string, "
        "integer, decimal, boolean, date, or timestamp; decimal values are strings, "
        "integer values are JSON integers, boolean values are JSON booleans, and date "
        "or timestamp values are ISO strings. IS_NULL and IS_NOT_NULL require value "
        "null; every other operator requires a typed value. Use at most two connected "
        "joins, three projection columns, and two filters. Never emit SQL, sql, "
        "raw_sql, expression, on_clause, where_sql, projection_sql, functions, "
        "ordering, grouping, limits, subqueries, or extra fields. The example is "
        "format-only and its IDs are not in the request schema.\n\n"
        f"WIRE SCHEMA VERSION: QueryPlanWireV2\nJSON SCHEMA:\n{schema}\n\n"
        f"FORMAT-ONLY EXAMPLE:\n{json.dumps(example, sort_keys=True)}\n\n"
        f"SERVER-OWNED SCHEMA CONTEXT:\n{schema_context}"
    )
