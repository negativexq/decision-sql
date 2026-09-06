"""Bounded server-owned relational QueryPlan V1.

The provider can select identifiers from this contract, but it cannot supply
SQL syntax, join predicates, expressions, or executable fragments.  The
catalog and compiler are intentionally independent of the demo schema.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.sql.models import CandidateSource, SqlCandidate


class QueryPlanV1ValidationError(ValueError):
    """Raised when an untrusted plan is outside the bounded V1 contract."""


class QueryPlanV1JoinType(StrEnum):
    INNER = "INNER"
    LEFT = "LEFT"


class QueryPlanV1Operator(StrEnum):
    EQ = "EQ"
    NE = "NE"
    LT = "LT"
    LTE = "LTE"
    GT = "GT"
    GTE = "GTE"
    IS_NULL = "IS_NULL"
    IS_NOT_NULL = "IS_NOT_NULL"


QueryPlanV1ValueKind = Literal["string", "integer", "decimal", "boolean", "date", "timestamp"]


class QueryPlanV1Value(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QueryPlanV1ValueKind
    value: Any

    @model_validator(mode="after")
    def validate_value(self) -> QueryPlanV1Value:
        if self.kind in {"string", "date", "timestamp"} and not isinstance(self.value, str):
            raise ValueError(f"{self.kind} values must be strings")
        if self.kind == "integer" and (type(self.value) is not int):
            raise ValueError("integer values must be JSON integers")
        if self.kind == "boolean" and type(self.value) is not bool:
            raise ValueError("boolean values must be JSON booleans")
        if self.kind == "decimal":
            if isinstance(self.value, str):
                try:
                    Decimal(self.value)
                except ValueError as error:
                    raise ValueError("decimal strings must contain a decimal number") from error
            elif not (
                isinstance(self.value, (Decimal, int, float)) and type(self.value) is not bool
            ):
                raise ValueError("decimal values must be numeric")
        if self.kind == "date":
            try:
                date.fromisoformat(self.value)
            except ValueError as error:
                raise ValueError("date values must use ISO date syntax") from error
        if self.kind == "timestamp":
            try:
                datetime.fromisoformat(self.value)
            except ValueError as error:
                raise ValueError("timestamp values must use ISO timestamp syntax") from error
        return self


class QueryPlanV1Join(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str = Field(min_length=1)
    join_type: QueryPlanV1JoinType = QueryPlanV1JoinType.INNER


class QueryPlanV1Predicate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column_id: str = Field(min_length=1)
    operator: QueryPlanV1Operator
    value: QueryPlanV1Value | None = None

    @model_validator(mode="after")
    def validate_operator_value(self) -> QueryPlanV1Predicate:
        null_operator = self.operator in {
            QueryPlanV1Operator.IS_NULL,
            QueryPlanV1Operator.IS_NOT_NULL,
        }
        if null_operator and self.value is not None:
            raise ValueError("NULL predicates do not accept a value")
        if not null_operator and self.value is None:
            raise ValueError("value-taking predicates require a value")
        return self


class QueryPlanV1(BaseModel):
    """Strict provider-facing V1 plan; it contains no SQL-bearing field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    applicable: bool
    source: str | None = None
    joins: tuple[QueryPlanV1Join, ...] = ()
    projection: tuple[str, ...] = ()
    filters: tuple[QueryPlanV1Predicate, ...] = ()

    @model_validator(mode="after")
    def validate_bounds(self) -> QueryPlanV1:
        if not self.applicable:
            if self.source is not None or self.joins or self.projection or self.filters:
                raise ValueError("non-applicable plans cannot contain semantic slots")
            return self
        if self.source is None:
            raise ValueError("applicable plans require a source")
        if not 1 <= len(self.projection) <= 3:
            raise ValueError("V1 plans require one to three projection columns")
        if len(self.joins) > 2:
            raise ValueError("V1 plans support at most two joins")
        if len(self.filters) > 2:
            raise ValueError("V1 plans support at most two predicates")
        return self

    def normalized(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def content_hash(self) -> str:
        return stable_hash(self.normalized())


class QueryPlanV1Column(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column_id: str
    table_id: str
    name: str
    data_type: str
    description: str


class QueryPlanV1Table(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    table_id: str
    description: str
    columns: tuple[QueryPlanV1Column, ...]


class QueryPlanV1Relationship(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relationship_id: str
    left_table_id: str
    left_column_id: str
    right_table_id: str
    right_column_id: str


class QueryPlanV1Catalog(BaseModel):
    """Small immutable catalog supplied by the server to the compiler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "queryplan-v1-catalog-1"
    tables: tuple[QueryPlanV1Table, ...]
    relationships: tuple[QueryPlanV1Relationship, ...]

    @classmethod
    def from_schema(
        cls, schema: SchemaCatalog, *, version: str = "queryplan-v1-catalog-1"
    ) -> QueryPlanV1Catalog:
        tables: list[QueryPlanV1Table] = []
        for table in sorted(
            (item for item in schema.tables if item.queryable), key=lambda item: item.name
        ):
            columns = tuple(
                QueryPlanV1Column(
                    column_id=f"{table.name}.{column.name}",
                    table_id=table.name,
                    name=column.name,
                    data_type=column.type,
                    description=column.description,
                )
                for column in table.columns
                if column.queryable
            )
            tables.append(
                QueryPlanV1Table(
                    table_id=table.name,
                    description=table.description,
                    columns=columns,
                )
            )
        relationships: list[QueryPlanV1Relationship] = []
        for table in sorted(schema.tables, key=lambda item: item.name):
            for relationship in sorted(table.relationships, key=lambda item: item.column):
                relationships.append(
                    QueryPlanV1Relationship(
                        relationship_id=f"{table.name}.{relationship.column}",
                        left_table_id=table.name,
                        left_column_id=f"{table.name}.{relationship.column}",
                        right_table_id=relationship.referenced_table,
                        right_column_id=(
                            f"{relationship.referenced_table}.{relationship.referenced_column}"
                        ),
                    )
                )
        return cls(version=version, tables=tuple(tables), relationships=tuple(relationships))

    def serialized(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @property
    def content_hash(self) -> str:
        return sha256(self.serialized().encode("utf-8")).hexdigest()

    def table(self, table_id: str) -> QueryPlanV1Table:
        for table in self.tables:
            if table.table_id == table_id:
                return table
        raise QueryPlanV1ValidationError(f"unknown source/table: {table_id}")

    def column(self, column_id: str) -> QueryPlanV1Column:
        table_id, separator, _ = column_id.partition(".")
        if not separator:
            raise QueryPlanV1ValidationError(f"invalid column ID: {column_id}")
        table = self.table(table_id)
        for column in table.columns:
            if column.column_id == column_id:
                return column
        raise QueryPlanV1ValidationError(f"unknown column: {column_id}")

    def relationship(self, relationship_id: str) -> QueryPlanV1Relationship:
        for relationship in self.relationships:
            if relationship.relationship_id == relationship_id:
                return relationship
        raise QueryPlanV1ValidationError(f"unknown relationship: {relationship_id}")


def render_query_plan_v1_context(catalog: QueryPlanV1Catalog) -> str:
    """Render identical schema/relationship facts for both provider arms."""
    lines = ["QUERYPLAN V1 SERVER-OWNED SCHEMA CONTEXT"]
    for table in catalog.tables:
        lines.append(f"TABLE {table.table_id}: {table.description}")
        for column in table.columns:
            lines.append(
                f"  COLUMN {column.column_id} TYPE {column.data_type}: {column.description}"
            )
    lines.append("SERVER-OWNED RELATIONSHIPS (select IDs; never write ON clauses):")
    for relationship in catalog.relationships:
        lines.append(
            f"  RELATIONSHIP {relationship.relationship_id}: "
            f"LEFT_TABLE {relationship.left_table_id} "
            f"LEFT_COLUMN {relationship.left_column_id} "
            f"REFERENCES RIGHT_TABLE {relationship.right_table_id} "
            f"RIGHT_COLUMN {relationship.right_column_id}"
        )
    return "\n".join(lines)


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


def validate_query_plan_v1(plan: QueryPlanV1, catalog: QueryPlanV1Catalog) -> QueryPlanV1:
    """Apply catalog, graph, identifier, and typed-predicate validation."""
    if not plan.applicable:
        return plan
    assert plan.source is not None
    catalog.table(plan.source)
    if len({join.relationship_id for join in plan.joins}) != len(plan.joins):
        raise QueryPlanV1ValidationError("duplicate relationships are not allowed")
    reachable = {plan.source}
    used_tables = {plan.source}
    relation_objects: list[QueryPlanV1Relationship] = []
    for join in plan.joins:
        relationship = catalog.relationship(join.relationship_id)
        endpoints = {relationship.left_table_id, relationship.right_table_id}
        connected = endpoints & reachable
        new_tables = endpoints - reachable
        if len(connected) != 1 or len(new_tables) != 1:
            raise QueryPlanV1ValidationError("joins must form a connected acyclic graph")
        new_table = next(iter(new_tables))
        if new_table in used_tables:
            raise QueryPlanV1ValidationError("duplicate table introduction is not allowed")
        reachable.add(new_table)
        used_tables.add(new_table)
        relation_objects.append(relationship)
    projection_columns = [catalog.column(column_id) for column_id in plan.projection]
    if len(set(plan.projection)) != len(plan.projection):
        raise QueryPlanV1ValidationError("duplicate projection columns are not allowed")
    if any(column.table_id not in reachable for column in projection_columns):
        raise QueryPlanV1ValidationError("projection references an unreachable table")
    for predicate in plan.filters:
        column = catalog.column(predicate.column_id)
        if column.table_id not in reachable:
            raise QueryPlanV1ValidationError("predicate references an unreachable table")
        if predicate.value is not None and predicate.value.kind != _type_family(column.data_type):
            raise QueryPlanV1ValidationError(
                f"predicate type {predicate.value.kind} is incompatible with {column.data_type}"
            )
    return plan


def _identifier(name: str) -> exp.Identifier:
    return exp.Identifier(this=name, quoted=False)


def _column(column_id: str, aliases: dict[str, str], catalog: QueryPlanV1Catalog) -> exp.Column:
    column = catalog.column(column_id)
    return exp.Column(this=_identifier(column.name), table=_identifier(aliases[column.table_id]))


def _literal(value: QueryPlanV1Value) -> exp.Expression:
    if value.kind == "string":
        return exp.Literal.string(value.value)
    if value.kind == "integer":
        return exp.Literal.number(str(value.value))
    if value.kind == "decimal":
        return exp.Literal.number(format(Decimal(str(value.value)), "f"))
    if value.kind == "boolean":
        return exp.Boolean(this=value.value)
    sql_type = "DATE" if value.kind == "date" else "TIMESTAMP"
    return exp.Cast(this=exp.Literal.string(value.value), to=exp.DataType.build(sql_type))


def _predicate_expression(
    predicate: QueryPlanV1Predicate, aliases: dict[str, str], catalog: QueryPlanV1Catalog
) -> exp.Expression:
    column = _column(predicate.column_id, aliases, catalog)
    if predicate.operator is QueryPlanV1Operator.IS_NULL:
        return exp.Is(this=column, expression=exp.Null())
    if predicate.operator is QueryPlanV1Operator.IS_NOT_NULL:
        return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
    assert predicate.value is not None
    value = _literal(predicate.value)
    expressions = {
        QueryPlanV1Operator.EQ: exp.EQ,
        QueryPlanV1Operator.NE: exp.NEQ,
        QueryPlanV1Operator.LT: exp.LT,
        QueryPlanV1Operator.LTE: exp.LTE,
        QueryPlanV1Operator.GT: exp.GT,
        QueryPlanV1Operator.GTE: exp.GTE,
    }
    return expressions[predicate.operator](this=column, expression=value)


class QueryPlanV1Compiler:
    """Deterministically compiles a validated plan into a PostgreSQL candidate."""

    version = "queryplan-v1-compiler-1"

    def __init__(self, catalog: QueryPlanV1Catalog) -> None:
        self.catalog = catalog

    def compile(self, plan: QueryPlanV1) -> SqlCandidate:
        validate_query_plan_v1(plan, self.catalog)
        if not plan.applicable:
            raise QueryPlanV1ValidationError("non-applicable plans cannot compile")
        assert plan.source is not None
        aliases = {plan.source: "t0"}
        reachable = {plan.source}
        join_specs: list[tuple[exp.Expression, exp.Expression, QueryPlanV1JoinType]] = []
        for index, join in enumerate(plan.joins, start=1):
            relationship = self.catalog.relationship(join.relationship_id)
            new_table = next(
                table
                for table in (relationship.left_table_id, relationship.right_table_id)
                if table not in reachable
            )
            aliases[new_table] = f"t{index}"
            left = _column(relationship.left_column_id, aliases, self.catalog)
            right = _column(relationship.right_column_id, aliases, self.catalog)
            join_specs.append(
                (
                    exp.Table(this=_identifier(new_table)).as_(aliases[new_table]),
                    exp.EQ(this=left, expression=right),
                    join.join_type,
                )
            )
            reachable.add(new_table)
        query = exp.select(
            *[_column(column, aliases, self.catalog) for column in plan.projection]
        ).from_(exp.Table(this=_identifier(plan.source)).as_("t0"))
        for table, condition, join_type in join_specs:
            query = query.join(table, on=condition, join_type=join_type.value)
        if plan.filters:
            predicate_expressions = [
                _predicate_expression(predicate, aliases, self.catalog)
                for predicate in plan.filters
            ]
            query = query.where(predicate_expressions[0])
            for predicate_expression in predicate_expressions[1:]:
                query = query.where(predicate_expression)
        sql = query.sql(dialect="postgres")
        return SqlCandidate(
            sql=sql,
            source=CandidateSource.QUERY_PLAN_V1_COMPILER,
        )


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
