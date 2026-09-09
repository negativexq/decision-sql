"""Deterministic, fail-closed normalization for a narrow grain-safe SQL shape.

The normalizer is deliberately independent of benchmark truth, references,
fixtures, and execution.  It may rewrite only a validator-proven direct
LEFT JOIN fanout where additive child measures can be reduced to the declared
parent key before the existing parent query is evaluated.
"""

from __future__ import annotations

import re
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from app.semantics.grain import (
    AggregationBehavior,
    GrainDiagnostic,
    GrainDiagnosticCode,
    GrainKey,
    GrainRelationship,
    GrainSafetyValidator,
    MeasureCatalog,
    MeasureSemantics,
)


class NormalizationStatus(StrEnum):
    UNCHANGED = "UNCHANGED"
    NORMALIZED = "NORMALIZED"
    ABSTAIN = "ABSTAIN"


class NormalizationReason(StrEnum):
    UNCHANGED_NOT_APPLICABLE = "UNCHANGED_NOT_APPLICABLE"
    UNCHANGED_ALREADY_SAFE = "UNCHANGED_ALREADY_SAFE"
    NORMALIZED_CHILD_PREAGGREGATION = "NORMALIZED_CHILD_PREAGGREGATION"
    ABSTAIN_NO_SQL = "ABSTAIN_NO_SQL"
    ABSTAIN_PARSE_FAILURE = "ABSTAIN_PARSE_FAILURE"
    ABSTAIN_UNRESOLVED_LINEAGE = "ABSTAIN_UNRESOLVED_LINEAGE"
    ABSTAIN_UNRESOLVED_MEASURE = "ABSTAIN_UNRESOLVED_MEASURE"
    ABSTAIN_UNRESOLVED_RELATIONSHIP = "ABSTAIN_UNRESOLVED_RELATIONSHIP"
    ABSTAIN_MULTIPLE_FANOUT_EDGES = "ABSTAIN_MULTIPLE_FANOUT_EDGES"
    ABSTAIN_UNSUPPORTED_JOIN_SHAPE = "ABSTAIN_UNSUPPORTED_JOIN_SHAPE"
    ABSTAIN_CHILD_FILTER_SEMANTICS = "ABSTAIN_CHILD_FILTER_SEMANTICS"
    ABSTAIN_CHILD_NON_AGGREGATE_USAGE = "ABSTAIN_CHILD_NON_AGGREGATE_USAGE"
    ABSTAIN_UNSUPPORTED_AGGREGATE = "ABSTAIN_UNSUPPORTED_AGGREGATE"
    ABSTAIN_OUTPUT_NOT_GRAIN_SAFE = "ABSTAIN_OUTPUT_NOT_GRAIN_SAFE"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GrainNormalizationResult(_Frozen):
    status: NormalizationStatus
    reason_code: NormalizationReason
    input_sql_hash: str
    output_sql_hash: str
    input_sql: str
    output_sql: str
    input_diagnostic: GrainDiagnostic
    output_diagnostic: GrainDiagnostic
    parent_measure_ids: tuple[str, ...] = ()
    child_measure_ids: tuple[str, ...] = ()
    fanout_relationship_ids: tuple[str, ...] = ()
    parent_grain: GrainKey | None = None
    child_grain: GrainKey | None = None
    rewrite_evidence: dict[str, Any] = Field(default_factory=dict)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _hash_sql(sql: str) -> str:
    return sha256(sql.encode("utf-8")).hexdigest()


def _column_name(attribute_id: str) -> str:
    return attribute_id.rsplit(":", 1)[-1]


def _is_simple_identifier(value: str) -> bool:
    return bool(_IDENTIFIER.fullmatch(value))


def _and_terms(node: exp.Expression | None) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _and_terms(node.this) + _and_terms(node.expression)
    return [node] if node is not None else []


def _table_sources(select: exp.Select) -> dict[str, exp.Table]:
    sources: dict[str, exp.Table] = {}
    from_clause = select.args.get("from") or select.args.get("from_")
    if from_clause is not None and isinstance(from_clause.this, exp.Table):
        table = from_clause.this
        sources[table.alias_or_name.lower()] = table
    for join in select.args.get("joins", []):
        if isinstance(join.this, exp.Table):
            sources[join.this.alias_or_name.lower()] = join.this
    return sources


def _source_table_name(table: exp.Table) -> str:
    return table.name.lower()


def _column_matches(column: exp.Column, alias: str, physical_name: str) -> bool:
    return column.table.lower() == alias.lower() and column.name.lower() == physical_name.lower()


class GrainSafeNormalizer:
    """Normalize exactly one proven direct child-fanout shape."""

    def __init__(self, catalog: MeasureCatalog) -> None:
        catalog.validate_contract()
        self.catalog = catalog
        self.validator = GrainSafetyValidator(catalog)

    def normalize(self, sql: str | None) -> GrainNormalizationResult:
        original = sql or ""
        if not original.strip():
            diagnostic = self.validator.validate(original)
            return self._result(
                original,
                original,
                NormalizationStatus.ABSTAIN,
                NormalizationReason.ABSTAIN_NO_SQL,
                diagnostic,
                diagnostic,
            )
        try:
            tree = sqlglot.parse_one(original, read="postgres")
        except Exception as error:
            diagnostic = GrainDiagnostic(
                code=GrainDiagnosticCode.UNRESOLVED_LINEAGE,
                message="SQL could not be parsed for grain normalization.",
                evidence={"error": str(error)},
            )
            return self._result(
                original,
                original,
                NormalizationStatus.ABSTAIN,
                NormalizationReason.ABSTAIN_PARSE_FAILURE,
                diagnostic,
                diagnostic,
            )

        diagnostic = self.validator.validate(original)
        if diagnostic.code is GrainDiagnosticCode.DISTINCT_VALUE_FANOUT_MASK:
            return self._abstain(
                original,
                diagnostic,
                NormalizationReason.ABSTAIN_UNSUPPORTED_AGGREGATE,
            )
        if diagnostic.code is GrainDiagnosticCode.UNSAFE_ROLLUP:
            return self._abstain(
                original,
                diagnostic,
                NormalizationReason.ABSTAIN_UNSUPPORTED_AGGREGATE,
            )
        if diagnostic.code is not GrainDiagnosticCode.PARENT_MEASURE_FANOUT:
            reason = (
                NormalizationReason.UNCHANGED_ALREADY_SAFE
                if diagnostic.code in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}
                else NormalizationReason.UNCHANGED_NOT_APPLICABLE
            )
            return self._result(
                original,
                original,
                NormalizationStatus.UNCHANGED,
                reason,
                diagnostic,
                diagnostic,
            )
        if not isinstance(tree, exp.Select):
            return self._abstain(
                original, diagnostic, NormalizationReason.ABSTAIN_UNSUPPORTED_JOIN_SHAPE
            )

        try:
            normalized, evidence = self._rewrite(tree, diagnostic)
        except _Abstain as error:
            return self._abstain(original, diagnostic, error.reason, error.evidence)

        try:
            output_tree = sqlglot.parse_one(normalized, read="postgres")
        except Exception as error:
            return self._abstain(
                original,
                diagnostic,
                NormalizationReason.ABSTAIN_OUTPUT_NOT_GRAIN_SAFE,
                {"output_parse_error": str(error)},
            )
        if not isinstance(output_tree, exp.Select):
            return self._abstain(
                original, diagnostic, NormalizationReason.ABSTAIN_OUTPUT_NOT_GRAIN_SAFE
            )
        output_diagnostic = self.validator.validate(normalized)
        if output_diagnostic.code not in {
            GrainDiagnosticCode.PASS,
            GrainDiagnosticCode.NOT_APPLICABLE,
        }:
            return self._abstain(
                original,
                diagnostic,
                NormalizationReason.ABSTAIN_OUTPUT_NOT_GRAIN_SAFE,
                {"output_diagnostic": output_diagnostic.model_dump(mode="json")},
            )
        return self._result(
            original,
            normalized,
            NormalizationStatus.NORMALIZED,
            NormalizationReason.NORMALIZED_CHILD_PREAGGREGATION,
            diagnostic,
            output_diagnostic,
            evidence,
        )

    def _result(
        self,
        input_sql: str,
        output_sql: str,
        status: NormalizationStatus,
        reason: NormalizationReason,
        input_diagnostic: GrainDiagnostic,
        output_diagnostic: GrainDiagnostic,
        evidence: dict[str, Any] | None = None,
    ) -> GrainNormalizationResult:
        parent_ids = tuple(input_diagnostic.measure_ids)
        relationships = tuple(input_diagnostic.fanout_edges)
        parent_grain = input_diagnostic.native_grains[0] if input_diagnostic.native_grains else None
        return GrainNormalizationResult(
            status=status,
            reason_code=reason,
            input_sql_hash=_hash_sql(input_sql),
            output_sql_hash=_hash_sql(output_sql),
            input_sql=input_sql,
            output_sql=output_sql,
            input_diagnostic=input_diagnostic,
            output_diagnostic=output_diagnostic,
            parent_measure_ids=parent_ids,
            child_measure_ids=tuple((evidence or {}).get("child_measure_ids", ())),
            fanout_relationship_ids=tuple(item.relationship_id for item in relationships),
            parent_grain=parent_grain,
            child_grain=(evidence or {}).get("child_grain"),
            rewrite_evidence=evidence or {},
        )

    def _abstain(
        self,
        original: str,
        diagnostic: GrainDiagnostic,
        reason: NormalizationReason,
        evidence: dict[str, Any] | None = None,
    ) -> GrainNormalizationResult:
        return self._result(
            original,
            original,
            NormalizationStatus.ABSTAIN,
            reason,
            diagnostic,
            diagnostic,
            evidence,
        )

    def _rewrite(
        self, select: exp.Select, diagnostic: GrainDiagnostic
    ) -> tuple[str, dict[str, Any]]:
        if len(diagnostic.fanout_edges) != 1 or len(diagnostic.measure_ids) != 1:
            raise _Abstain(NormalizationReason.ABSTAIN_MULTIPLE_FANOUT_EDGES)
        edge = diagnostic.fanout_edges[0]
        parent_measure = self.catalog.measure(diagnostic.measure_ids[0])
        if parent_measure.aggregation_behavior is not AggregationBehavior.ADDITIVE:
            raise _Abstain(NormalizationReason.ABSTAIN_UNRESOLVED_MEASURE)
        parent_entity = self.catalog.entity(edge.to_entity_id)
        child_entity = self.catalog.entity(edge.from_entity_id)
        sources = _table_sources(select)
        parent_aliases = {
            alias
            for alias, table in sources.items()
            if _source_table_name(table) == parent_entity.physical_table.lower()
        }
        child_aliases = {
            alias
            for alias, table in sources.items()
            if _source_table_name(table) == child_entity.physical_table.lower()
        }
        if len(parent_aliases) != 1 or len(child_aliases) != 1:
            raise _Abstain(NormalizationReason.ABSTAIN_UNRESOLVED_LINEAGE)
        parent_alias = next(iter(parent_aliases))
        child_alias = next(iter(child_aliases))

        direct_fanout_edges = []
        for candidate in self.catalog.relationships:
            if candidate.to_entity_id != parent_entity.entity_id:
                continue
            if candidate.cardinality.upper().replace("-", "_") != "MANY_TO_ONE":
                continue
            candidate_child = self.catalog.entity(candidate.from_entity_id).physical_table.lower()
            if candidate_child in {_source_table_name(table) for table in sources.values()}:
                direct_fanout_edges.append(candidate)
        if (
            len(direct_fanout_edges) != 1
            or direct_fanout_edges[0].relationship_id != edge.relationship_id
        ):
            raise _Abstain(NormalizationReason.ABSTAIN_MULTIPLE_FANOUT_EDGES)

        join = self._find_supported_join(select, edge, parent_alias, child_alias)
        if join is None:
            raise _Abstain(NormalizationReason.ABSTAIN_UNSUPPORTED_JOIN_SHAPE)
        self._check_other_joins(select, edge, parent_entity.entity_id, child_alias)
        child_measures = self._child_aggregates(select, child_alias, child_entity.physical_table)
        if not child_measures:
            raise _Abstain(NormalizationReason.ABSTAIN_UNSUPPORTED_AGGREGATE)
        self._check_child_scope_usage(select, child_alias, edge, child_measures)

        existing_aliases = set(sources)
        derived_alias = self._fresh_alias(f"{child_alias}__grain", existing_aliases)
        source_alias = f"{child_alias}__source"
        child_key_columns = tuple(_column_name(item) for item in edge.from_attribute_ids)
        if not all(_is_simple_identifier(item) for item in child_key_columns):
            raise _Abstain(NormalizationReason.ABSTAIN_UNRESOLVED_RELATIONSHIP)
        projection: list[exp.Expression] = [
            exp.column(column, table=source_alias) for column in child_key_columns
        ]
        aggregate_aliases: dict[str, str] = {}
        for measure in child_measures:
            base_alias = f"{measure.physical_column_or_path}__grain_sum"
            output_alias = self._fresh_column_alias(base_alias, set(aggregate_aliases.values()))
            aggregate_aliases[measure.physical_column_or_path.lower()] = output_alias
            projection.append(
                exp.Alias(
                    this=exp.Sum(
                        this=exp.column(measure.physical_column_or_path, table=source_alias)
                    ),
                    alias=exp.to_identifier(output_alias),
                )
            )
        inner_table = next(
            table
            for table in sources.values()
            if _source_table_name(table) == child_entity.physical_table.lower()
        )
        inner_table = inner_table.copy()
        inner_table.set("alias", exp.TableAlias(this=exp.to_identifier(source_alias)))
        inner = exp.select(*projection).from_(inner_table)
        inner.set(
            "group",
            exp.Group(
                expressions=[exp.column(column, table=source_alias) for column in child_key_columns]
            ),
        )
        subquery = exp.Subquery(
            this=inner,
            alias=exp.TableAlias(this=exp.to_identifier(derived_alias)),
        )
        join.set("this", subquery)
        on_expression = join.args.get("on")
        if on_expression is None:
            raise _Abstain(NormalizationReason.ABSTAIN_UNSUPPORTED_JOIN_SHAPE)
        for column in on_expression.find_all(exp.Column):
            if column.table.lower() == child_alias:
                column.set("table", exp.to_identifier(derived_alias))
        for column in select.expressions:
            for nested in column.find_all(exp.Column):
                if nested.table.lower() != child_alias:
                    continue
                measure_alias = aggregate_aliases.get(nested.name.lower())
                if measure_alias is not None and isinstance(nested.parent, exp.Sum):
                    nested.set("table", exp.to_identifier(derived_alias))
                    nested.set("this", exp.to_identifier(measure_alias))
                elif nested.name.lower() in {item.lower() for item in child_key_columns}:
                    nested.set("table", exp.to_identifier(derived_alias))
                else:
                    raise _Abstain(NormalizationReason.ABSTAIN_CHILD_NON_AGGREGATE_USAGE)
        normalized = select.sql(dialect="postgres")
        evidence = {
            "parent_measure_ids": [parent_measure.measure_id],
            "child_measure_ids": [item.measure_id for item in child_measures],
            "parent_entity_id": parent_entity.entity_id,
            "child_entity_id": child_entity.entity_id,
            "child_grain": GrainKey(
                entity_id=child_entity.entity_id,
                key_attribute_ids=child_entity.key_attribute_ids,
            ).model_dump(mode="json"),
            "relationship_id": edge.relationship_id,
            "cardinality": edge.cardinality,
            "parent_key_attributes": list(edge.to_attribute_ids),
            "child_key_attributes": list(edge.from_attribute_ids),
            "original_child_alias": child_alias,
            "derived_child_alias": derived_alias,
            "rewritten_aggregate_aliases": aggregate_aliases,
        }
        return normalized, evidence

    def _find_supported_join(
        self,
        select: exp.Select,
        edge: GrainRelationship,
        parent_alias: str,
        child_alias: str,
    ) -> exp.Join | None:
        child_table = self.catalog.entity(edge.from_entity_id).physical_table.lower()
        for join in select.args.get("joins", []):
            if join.side != "LEFT" or not isinstance(join.this, exp.Table):
                continue
            if (
                join.this.alias_or_name.lower() != child_alias
                or join.this.name.lower() != child_table
            ):
                continue
            terms = _and_terms(join.args.get("on"))
            expected = {
                (_column_name(left), _column_name(right))
                for left, right in zip(edge.from_attribute_ids, edge.to_attribute_ids, strict=True)
            }
            actual: set[tuple[str, str]] = set()
            for term in terms:
                if not isinstance(term, exp.EQ):
                    return None
                left, right = term.this, term.expression
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    return None
                matched = None
                if left.table.lower() == child_alias and right.table.lower() == parent_alias:
                    matched = (left.name.lower(), right.name.lower())
                elif right.table.lower() == child_alias and left.table.lower() == parent_alias:
                    matched = (right.name.lower(), left.name.lower())
                if matched is None:
                    return None
                actual.add(matched)
            if actual == {(left.lower(), right.lower()) for left, right in expected}:
                return cast(exp.Join, join)
        return None

    def _check_other_joins(
        self, select: exp.Select, edge: GrainRelationship, parent_entity_id: str, child_alias: str
    ) -> None:
        parent_table = self.catalog.entity(parent_entity_id).physical_table.lower()
        for join in select.args.get("joins", []):
            if (
                not isinstance(join.this, exp.Table)
                or join.this.alias_or_name.lower() == child_alias
            ):
                continue
            joined_table = join.this.name.lower()
            safe = False
            for relationship in self.catalog.relationships:
                from_table = self.catalog.entity(relationship.from_entity_id).physical_table.lower()
                to_table = self.catalog.entity(relationship.to_entity_id).physical_table.lower()
                cardinality = relationship.cardinality.upper().replace("-", "_")
                if (
                    from_table == parent_table
                    and to_table == joined_table
                    and cardinality
                    in {
                        "MANY_TO_ONE",
                        "ONE_TO_ONE",
                    }
                ):
                    safe = True
                if (
                    from_table == joined_table
                    and to_table == parent_table
                    and cardinality == "ONE_TO_ONE"
                ):
                    safe = True
            if not safe:
                raise _Abstain(NormalizationReason.ABSTAIN_UNRESOLVED_RELATIONSHIP)

    def _child_aggregates(
        self, select: exp.Select, child_alias: str, child_table: str
    ) -> list[MeasureSemantics]:
        found: dict[str, MeasureSemantics] = {}
        for aggregate in select.find_all(exp.Sum):
            expression = aggregate.this
            if not isinstance(expression, exp.Column) or expression.table.lower() != child_alias:
                continue
            measures = self.catalog.measures_for_physical(child_table, expression.name)
            if (
                len(measures) != 1
                or measures[0].aggregation_behavior is not AggregationBehavior.ADDITIVE
            ):
                raise _Abstain(NormalizationReason.ABSTAIN_UNSUPPORTED_AGGREGATE)
            found[measures[0].measure_id] = measures[0]
        return list(found.values())

    def _check_child_scope_usage(
        self,
        select: exp.Select,
        child_alias: str,
        edge: GrainRelationship,
        child_measures: list[MeasureSemantics],
    ) -> None:
        key_columns = {_column_name(item).lower() for item in edge.from_attribute_ids}
        measure_columns = {item.physical_column_or_path.lower() for item in child_measures}
        for argument_name in ("where", "group", "having", "order"):
            argument = select.args.get(argument_name)
            if argument is not None and any(
                column.table.lower() == child_alias for column in argument.find_all(exp.Column)
            ):
                reason = (
                    NormalizationReason.ABSTAIN_CHILD_FILTER_SEMANTICS
                    if argument_name == "where"
                    else NormalizationReason.ABSTAIN_CHILD_NON_AGGREGATE_USAGE
                )
                raise _Abstain(reason)
        for window in select.find_all(exp.Window):
            if any(column.table.lower() == child_alias for column in window.find_all(exp.Column)):
                raise _Abstain(NormalizationReason.ABSTAIN_CHILD_NON_AGGREGATE_USAGE)
        for expression in select.expressions:
            for column in expression.find_all(exp.Column):
                if column.table.lower() != child_alias:
                    continue
                if column.name.lower() in key_columns:
                    continue
                if column.name.lower() in measure_columns and isinstance(column.parent, exp.Sum):
                    continue
                raise _Abstain(NormalizationReason.ABSTAIN_CHILD_NON_AGGREGATE_USAGE)

    @staticmethod
    def _fresh_alias(base: str, existing: set[str]) -> str:
        candidate = base
        index = 2
        while candidate.lower() in {item.lower() for item in existing}:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    @staticmethod
    def _fresh_column_alias(base: str, existing: set[str]) -> str:
        candidate = base
        index = 2
        while candidate.lower() in {item.lower() for item in existing}:
            candidate = f"{base}_{index}"
            index += 1
        return candidate


class _Abstain(Exception):
    def __init__(self, reason: NormalizationReason, evidence: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.evidence = evidence or {}
        super().__init__(reason.value)
