"""Server-owned measure grain semantics and diagnostic SQL grain validation.

This module is deliberately independent of benchmark cases and model prompts.
It extends the existing :mod:`app.semantics` boundary with facts about source
measure grain, additivity, and relationship cardinality.  The validator is
diagnostic in M46A: it never rewrites SQL, blocks execution, or changes scores.
"""

from __future__ import annotations

import json
from collections import deque
from enum import StrEnum
from hashlib import sha256
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp


class GrainContractError(ValueError):
    """A server-owned grain contract is inconsistent."""


class AggregationBehavior(StrEnum):
    ADDITIVE = "ADDITIVE"
    SEMI_ADDITIVE = "SEMI_ADDITIVE"
    NON_ADDITIVE = "NON_ADDITIVE"
    DERIVED = "DERIVED"


class GrainDiagnosticCode(StrEnum):
    PASS = "PASS"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NO_SQL = "NO_SQL"
    UNRESOLVED_LINEAGE = "UNRESOLVED_LINEAGE"
    UNRESOLVED_MEASURE = "UNRESOLVED_MEASURE"
    UNRESOLVED_GRAIN = "UNRESOLVED_GRAIN"
    UNRESOLVED_RELATIONSHIP = "UNRESOLVED_RELATIONSHIP"
    PARENT_MEASURE_FANOUT = "PARENT_MEASURE_FANOUT"
    DISTINCT_VALUE_FANOUT_MASK = "DISTINCT_VALUE_FANOUT_MASK"
    INCOMPATIBLE_MEASURE_GRAINS = "INCOMPATIBLE_MEASURE_GRAINS"
    UNSAFE_ROLLUP = "UNSAFE_ROLLUP"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GrainKey(_Frozen):
    """The identity of one semantic row for an entity or measure."""

    entity_id: str = Field(min_length=1)
    key_attribute_ids: tuple[str, ...] = Field(min_length=1)


class GrainEntity(_Frozen):
    entity_id: str = Field(min_length=1)
    physical_table: str = Field(min_length=1)
    key_attribute_ids: tuple[str, ...] = Field(min_length=1)
    provenance: tuple[str, ...] = ()


class GrainRelationship(_Frozen):
    """A relationship edge with cardinality used for rollup reasoning."""

    relationship_id: str = Field(min_length=1)
    from_entity_id: str = Field(min_length=1)
    from_attribute_ids: tuple[str, ...] = Field(min_length=1)
    to_entity_id: str = Field(min_length=1)
    to_attribute_ids: tuple[str, ...] = Field(min_length=1)
    cardinality: str = Field(min_length=1)
    provenance: tuple[str, ...] = ()


class MeasureSemantics(_Frozen):
    """Typed meaning of a quantitative source attribute."""

    measure_id: str = Field(min_length=1)
    source_attribute_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)
    physical_table: str = Field(min_length=1)
    physical_column_or_path: str = Field(min_length=1)
    native_grain: GrainKey
    aggregation_behavior: AggregationBehavior
    allowed_rollup_grains: tuple[GrainKey, ...] = ()
    restricted_rollup_grains: tuple[GrainKey, ...] = ()
    derivation: str | None = None
    provenance: tuple[str, ...] = Field(min_length=1)
    used_by_cases: tuple[str, ...] = ()


class DerivedMeasureSemantics(_Frozen):
    """Server-owned formula and lineage for a governed derived measure."""

    measure_id: str = Field(min_length=1)
    metric_id: str = Field(min_length=1)
    expression_signature: str = Field(min_length=1)
    source_attribute_ids: tuple[str, ...] = Field(min_length=1)
    source_entity_ids: tuple[str, ...] = Field(min_length=1)
    authorized_relationship_ids: tuple[str, ...] = ()
    provenance: tuple[str, ...] = Field(min_length=1)


class MeasureCatalog(_Frozen):
    """Immutable, deterministic collection of server-owned measure facts."""

    version: str = "measure-semantics-1"
    entities: tuple[GrainEntity, ...] = ()
    relationships: tuple[GrainRelationship, ...] = ()
    measures: tuple[MeasureSemantics, ...] = ()
    derived_measures: tuple[DerivedMeasureSemantics, ...] = ()

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def entity(self, entity_id: str) -> GrainEntity:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise GrainContractError(f"unknown grain entity: {entity_id}")

    def measure(self, measure_id: str) -> MeasureSemantics:
        for measure in self.measures:
            if measure.measure_id == measure_id:
                return measure
        raise GrainContractError(f"unknown measure: {measure_id}")

    def measures_for_physical(self, table: str, column: str) -> tuple[MeasureSemantics, ...]:
        return tuple(
            measure
            for measure in self.measures
            if measure.physical_table.lower() == table.lower()
            and measure.physical_column_or_path.lower() == column.lower()
        )

    def relationship(self, relationship_id: str) -> GrainRelationship:
        for relationship in self.relationships:
            if relationship.relationship_id == relationship_id:
                return relationship
        raise GrainContractError(f"unknown grain relationship: {relationship_id}")

    def validate_contract(self) -> None:
        entity_ids = {entity.entity_id for entity in self.entities}
        relationship_ids: set[str] = set()
        measure_ids: set[str] = set()
        for entity in self.entities:
            if not entity.key_attribute_ids:
                raise GrainContractError(f"entity has no key: {entity.entity_id}")
        for relationship in self.relationships:
            if relationship.relationship_id in relationship_ids:
                raise GrainContractError(f"duplicate relationship: {relationship.relationship_id}")
            relationship_ids.add(relationship.relationship_id)
            if (
                relationship.from_entity_id not in entity_ids
                or relationship.to_entity_id not in entity_ids
            ):
                raise GrainContractError(
                    f"relationship has unknown entity: {relationship.relationship_id}"
                )
        for measure in self.measures:
            if measure.measure_id in measure_ids:
                raise GrainContractError(f"duplicate measure: {measure.measure_id}")
            measure_ids.add(measure.measure_id)
            if measure.entity_id not in entity_ids:
                raise GrainContractError(f"measure has unknown entity: {measure.measure_id}")
            if measure.native_grain.entity_id != measure.entity_id:
                raise GrainContractError(f"native grain entity mismatch: {measure.measure_id}")
            for rollup in (*measure.allowed_rollup_grains, *measure.restricted_rollup_grains):
                if rollup.entity_id not in entity_ids:
                    raise GrainContractError(
                        f"measure has unknown rollup entity: {measure.measure_id}"
                    )
            if not measure.provenance:
                raise GrainContractError(f"measure has no provenance: {measure.measure_id}")
        derived_ids: set[str] = set()
        for derived in self.derived_measures:
            if derived.measure_id in measure_ids or derived.measure_id in derived_ids:
                raise GrainContractError(f"duplicate measure: {derived.measure_id}")
            derived_ids.add(derived.measure_id)
            if not derived.source_attribute_ids:
                raise GrainContractError(f"derived measure has no sources: {derived.measure_id}")
            if not derived.source_entity_ids:
                raise GrainContractError(
                    f"derived measure has no source entities: {derived.measure_id}"
                )
            if any(entity_id not in entity_ids for entity_id in derived.source_entity_ids):
                raise GrainContractError(
                    f"derived measure has unknown entity: {derived.measure_id}"
                )
            if any(
                relationship_id not in relationship_ids
                for relationship_id in derived.authorized_relationship_ids
            ):
                raise GrainContractError(
                    f"derived measure has unknown relationship: {derived.measure_id}"
                )
            if not derived.provenance:
                raise GrainContractError(f"derived measure has no provenance: {derived.measure_id}")


class RollupStep(_Frozen):
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    direction: str
    cardinality: str


class GrainGraph(_Frozen):
    """Deterministic relationship/cardinality graph for grain reasoning."""

    entities: tuple[GrainEntity, ...] = ()
    relationships: tuple[GrainRelationship, ...] = ()

    @property
    def content_hash(self) -> str:
        payload = self.model_dump(mode="json")
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def from_catalog(cls, catalog: MeasureCatalog) -> GrainGraph:
        catalog.validate_contract()
        return cls(entities=catalog.entities, relationships=catalog.relationships)

    def entity(self, entity_id: str) -> GrainEntity:
        for entity in self.entities:
            if entity.entity_id == entity_id:
                return entity
        raise GrainContractError(f"unknown graph entity: {entity_id}")

    def rollup_path(
        self, source_entity_id: str, target_entity_id: str
    ) -> tuple[RollupStep, ...] | None:
        """Find a safe many-side-to-one-side rollup path, if one exists."""
        if source_entity_id == target_entity_id:
            return ()
        queue: deque[tuple[str, tuple[RollupStep, ...]]] = deque([(source_entity_id, ())])
        visited = {source_entity_id}
        while queue:
            current, path = queue.popleft()
            for relationship in self.relationships:
                cardinality = relationship.cardinality.upper().replace("-", "_")
                next_entity: str | None = None
                direction = "FORWARD"
                if relationship.from_entity_id == current and cardinality in {
                    "MANY_TO_ONE",
                    "ONE_TO_ONE",
                }:
                    next_entity = relationship.to_entity_id
                elif relationship.to_entity_id == current and cardinality == "ONE_TO_ONE":
                    next_entity = relationship.from_entity_id
                    direction = "REVERSE"
                if next_entity is None or next_entity in visited:
                    continue
                step = RollupStep(
                    relationship_id=relationship.relationship_id,
                    source_entity_id=current,
                    target_entity_id=next_entity,
                    direction=direction,
                    cardinality=relationship.cardinality,
                )
                next_path = path + (step,)
                if next_entity == target_entity_id:
                    return next_path
                visited.add(next_entity)
                queue.append((next_entity, next_path))
        return None

    def fanout_edges_from(self, parent_entity_id: str) -> tuple[GrainRelationship, ...]:
        return tuple(
            relationship
            for relationship in self.relationships
            if relationship.to_entity_id == parent_entity_id
            and relationship.cardinality.upper().replace("-", "_") == "MANY_TO_ONE"
        )


class AlignmentStatus(StrEnum):
    ALIGNED = "ALIGNED"
    REQUIRES_ROLLUP = "REQUIRES_ROLLUP"
    UNSAFE_FANOUT = "UNSAFE_FANOUT"
    UNRESOLVED = "UNRESOLVED"


class GrainAlignmentResult(_Frozen):
    alignment_status: AlignmentStatus
    left_measure_id: str
    right_measure_id: str
    left_native_grain: GrainKey
    right_native_grain: GrainKey
    alignment_grain: GrainKey | None = None
    required_rollups: tuple[RollupStep, ...] = ()
    fanout_edges: tuple[GrainRelationship, ...] = ()
    reason_code: str


class GrainAlignmentAnalyzer:
    """Compute measure alignment constraints without generating SQL."""

    def __init__(self, catalog: MeasureCatalog) -> None:
        catalog.validate_contract()
        self.catalog = catalog
        self.graph = GrainGraph.from_catalog(catalog)

    def align(self, left_measure_id: str, right_measure_id: str) -> GrainAlignmentResult:
        left = self.catalog.measure(left_measure_id)
        right = self.catalog.measure(right_measure_id)
        if left.native_grain == right.native_grain:
            return GrainAlignmentResult(
                alignment_status=AlignmentStatus.ALIGNED,
                left_measure_id=left.measure_id,
                right_measure_id=right.measure_id,
                left_native_grain=left.native_grain,
                right_native_grain=right.native_grain,
                alignment_grain=left.native_grain,
                reason_code="SAME_NATIVE_GRAIN",
            )
        right_to_left = self.graph.rollup_path(right.entity_id, left.entity_id)
        if right_to_left is not None:
            return GrainAlignmentResult(
                alignment_status=AlignmentStatus.REQUIRES_ROLLUP,
                left_measure_id=left.measure_id,
                right_measure_id=right.measure_id,
                left_native_grain=left.native_grain,
                right_native_grain=right.native_grain,
                alignment_grain=left.native_grain,
                required_rollups=right_to_left,
                fanout_edges=self.graph.fanout_edges_from(left.entity_id),
                reason_code="RIGHT_MEASURE_ROLLS_UP_TO_LEFT_GRAIN",
            )
        left_to_right = self.graph.rollup_path(left.entity_id, right.entity_id)
        if left_to_right is not None:
            return GrainAlignmentResult(
                alignment_status=AlignmentStatus.REQUIRES_ROLLUP,
                left_measure_id=left.measure_id,
                right_measure_id=right.measure_id,
                left_native_grain=left.native_grain,
                right_native_grain=right.native_grain,
                alignment_grain=right.native_grain,
                required_rollups=left_to_right,
                fanout_edges=self.graph.fanout_edges_from(right.entity_id),
                reason_code="LEFT_MEASURE_ROLLS_UP_TO_RIGHT_GRAIN",
            )
        return GrainAlignmentResult(
            alignment_status=AlignmentStatus.UNRESOLVED,
            left_measure_id=left.measure_id,
            right_measure_id=right.measure_id,
            left_native_grain=left.native_grain,
            right_native_grain=right.native_grain,
            reason_code="NO_SAFE_COMMON_ROLLUP_PATH",
        )


class GrainDiagnostic(_Frozen):
    code: GrainDiagnosticCode
    message: str
    measure_ids: tuple[str, ...] = ()
    native_grains: tuple[GrainKey, ...] = ()
    fanout_edges: tuple[GrainRelationship, ...] = ()
    aggregate_expressions: tuple[str, ...] = ()
    grouping_columns: tuple[str, ...] = ()
    evidence: dict[str, Any] = {}


class GrainSafetyValidator:
    """Diagnostic SQL validator for additive-measure fanout."""

    def __init__(self, catalog: MeasureCatalog) -> None:
        catalog.validate_contract()
        self.catalog = catalog
        self.graph = GrainGraph.from_catalog(catalog)

    def validate(self, sql: str | None) -> GrainDiagnostic:
        if not sql or not sql.strip():
            return GrainDiagnostic(code=GrainDiagnosticCode.NO_SQL, message="No SQL was supplied.")
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
        except Exception as exc:
            return GrainDiagnostic(
                code=GrainDiagnosticCode.UNRESOLVED_LINEAGE,
                message="SQL could not be parsed for grain analysis.",
                evidence={"error": str(exc)},
            )
        diagnostics = [self._validate_select(select) for select in tree.find_all(exp.Select)]
        actionable = [
            item
            for item in diagnostics
            if item.code not in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}
        ]
        if actionable:
            return actionable[0]
        if any(item.code is GrainDiagnosticCode.PASS for item in diagnostics):
            return next(item for item in diagnostics if item.code is GrainDiagnosticCode.PASS)
        return GrainDiagnostic(
            code=GrainDiagnosticCode.NOT_APPLICABLE,
            message="No additive parent measure was aggregated across a modeled fanout edge.",
        )

    def _validate_select(self, select: exp.Select) -> GrainDiagnostic:
        aliases = self._direct_table_aliases(select)
        if not aliases:
            return GrainDiagnostic(
                code=GrainDiagnosticCode.NOT_APPLICABLE,
                message="Select scope has no direct physical table sources.",
            )
        group_columns = tuple(
            column.sql(dialect="postgres") for column in self._group_columns(select)
        )
        parent_measures: list[tuple[MeasureSemantics, exp.AggFunc, str]] = []
        authorized_derived = False
        for aggregate in select.find_all(exp.AggFunc):
            if not isinstance(aggregate, (exp.Sum, exp.Avg)):
                continue
            expression = aggregate.this
            if self._authorized_derived_expression(select, expression, aliases):
                authorized_derived = True
                continue
            for column in expression.find_all(exp.Column):
                table = aliases.get(column.table.lower())
                if table is None:
                    continue
                measures = self.catalog.measures_for_physical(table, column.name)
                for measure in measures:
                    if measure.aggregation_behavior is AggregationBehavior.ADDITIVE:
                        parent_measures.append((measure, aggregate, column.sql(dialect="postgres")))
                    elif (
                        isinstance(aggregate, exp.Sum)
                        and isinstance(expression, exp.Column)
                        and measure.aggregation_behavior
                        in {
                            AggregationBehavior.NON_ADDITIVE,
                            AggregationBehavior.DERIVED,
                        }
                    ):
                        return GrainDiagnostic(
                            code=GrainDiagnosticCode.UNSAFE_ROLLUP,
                            message="A non-additive or derived measure is being rolled up.",
                            measure_ids=(measure.measure_id,),
                            native_grains=(measure.native_grain,),
                            aggregate_expressions=(aggregate.sql(dialect="postgres"),),
                            grouping_columns=group_columns,
                        )
        if not parent_measures:
            if authorized_derived:
                return GrainDiagnostic(
                    code=GrainDiagnosticCode.PASS,
                    message="Governed derived measure is evaluated at its authorized source grain.",
                    aggregate_expressions=tuple(
                        aggregate.sql(dialect="postgres")
                        for aggregate in select.find_all(exp.AggFunc)
                    ),
                    grouping_columns=group_columns,
                )
            return GrainDiagnostic(
                code=GrainDiagnosticCode.NOT_APPLICABLE,
                message="No modeled additive measure is aggregated in this select scope.",
                grouping_columns=group_columns,
            )
        direct_tables = set(aliases.values())
        for measure, aggregate, column_sql in parent_measures:
            parent_edges = self.graph.fanout_edges_from(measure.entity_id)
            for edge in parent_edges:
                child_table = self.graph.entity(edge.from_entity_id).physical_table.lower()
                if child_table not in direct_tables:
                    continue
                native_columns = {
                    self._physical_column(attribute_id).lower()
                    for attribute_id in measure.native_grain.key_attribute_ids
                }
                child_entity = self.graph.entity(edge.from_entity_id)
                child_grain = GrainKey(
                    entity_id=child_entity.entity_id,
                    key_attribute_ids=child_entity.key_attribute_ids,
                )
                if child_grain in measure.allowed_rollup_grains:
                    continue
                grouped_native = {
                    column.name.lower()
                    for column in self._group_columns(select)
                    if aliases.get(column.table.lower()) == measure.physical_table.lower()
                }
                if native_columns.issubset(grouped_native):
                    continue
                if isinstance(aggregate, exp.Sum) and isinstance(aggregate.this, exp.Distinct):
                    return GrainDiagnostic(
                        code=GrainDiagnosticCode.DISTINCT_VALUE_FANOUT_MASK,
                        message="Value-level DISTINCT masks a parent measure across a fanout edge.",
                        measure_ids=(measure.measure_id,),
                        native_grains=(measure.native_grain,),
                        fanout_edges=(edge,),
                        aggregate_expressions=(aggregate.sql(dialect="postgres"),),
                        grouping_columns=group_columns,
                        evidence={"column": column_sql, "child_table": child_table},
                    )
                return GrainDiagnostic(
                    code=GrainDiagnosticCode.PARENT_MEASURE_FANOUT,
                    message="Parent additive measure is aggregated after a one-to-many fanout.",
                    measure_ids=(measure.measure_id,),
                    native_grains=(measure.native_grain,),
                    fanout_edges=(edge,),
                    aggregate_expressions=(aggregate.sql(dialect="postgres"),),
                    grouping_columns=group_columns,
                    evidence={"column": column_sql, "child_table": child_table},
                )
        return GrainDiagnostic(
            code=GrainDiagnosticCode.PASS,
            message="Modeled additive measures are not exposed to an unsafe fanout.",
            measure_ids=tuple(item[0].measure_id for item in parent_measures),
            native_grains=tuple(item[0].native_grain for item in parent_measures),
            grouping_columns=group_columns,
        )

    def _authorized_derived_expression(
        self, select: exp.Select, expression: exp.Expression, aliases: dict[str, str]
    ) -> bool:
        signature = self._expression_signature(expression, aliases)
        if signature is None:
            return False
        direct_tables = set(aliases.values())
        for derived in self.catalog.derived_measures:
            if derived.expression_signature != signature:
                continue
            source_tables = {
                self.catalog.entity(entity_id).physical_table.lower()
                for entity_id in derived.source_entity_ids
            }
            if not source_tables.issubset(direct_tables):
                continue
            if not self._authorized_scope_is_connected(select, direct_tables, derived):
                continue
            return True
        return False

    def _authorized_scope_is_connected(
        self, select: exp.Select, direct_tables: set[str], derived: DerivedMeasureSemantics
    ) -> bool:
        entity_by_table = {
            entity.physical_table.lower(): entity.entity_id for entity in self.catalog.entities
        }
        direct_entities = {
            entity_by_table[table] for table in direct_tables if table in entity_by_table
        }
        if len(direct_entities) != len(direct_tables):
            return False
        if not set(derived.source_entity_ids).issubset(direct_entities):
            return False
        observed_relationships = self._observed_relationship_ids(select)
        if not set(derived.authorized_relationship_ids).issubset(observed_relationships):
            return False
        edges = []
        for relationship in self.catalog.relationships:
            if relationship.relationship_id not in observed_relationships:
                continue
            if (
                relationship.from_entity_id in direct_entities
                and relationship.to_entity_id in direct_entities
            ):
                edges.append((relationship.from_entity_id, relationship.to_entity_id))
        reachable = {next(iter(derived.source_entity_ids))}
        changed = True
        while changed:
            changed = False
            for left, right in edges:
                if left in reachable and right not in reachable:
                    reachable.add(right)
                    changed = True
                elif right in reachable and left not in reachable:
                    reachable.add(left)
                    changed = True
        return direct_entities.issubset(reachable)

    def _observed_relationship_ids(self, select: exp.Select) -> set[str]:
        table_aliases = self._direct_table_aliases(select)
        observed: set[str] = set()
        for join in select.args.get("joins", []):
            on = join.args.get("on")
            if on is None:
                continue
            columns = list(on.find_all(exp.Column))
            endpoints = {
                (table_aliases[column.table.lower()], column.name.lower())
                for column in columns
                if column.table and column.table.lower() in table_aliases
            }
            if len(endpoints) < 2:
                continue
            for relationship in self.catalog.relationships:
                from_table = self.catalog.entity(relationship.from_entity_id).physical_table.lower()
                to_table = self.catalog.entity(relationship.to_entity_id).physical_table.lower()
                from_column = relationship.from_attribute_ids[0].split(":")[-1].lower()
                to_column = relationship.to_attribute_ids[0].split(":")[-1].lower()
                if {
                    (from_table, from_column),
                    (to_table, to_column),
                }.issubset(endpoints):
                    observed.add(relationship.relationship_id)
        return observed

    def _expression_signature(
        self, expression: exp.Expression, aliases: dict[str, str]
    ) -> str | None:
        normalized = expression.copy()
        columns = list(normalized.find_all(exp.Column))
        for column in columns:
            table = aliases.get(column.table.lower()) if column.table else None
            if table is None and not column.table:
                candidates = [
                    entity.physical_table.lower()
                    for entity in self.catalog.entities
                    if any(
                        measure.physical_column_or_path.lower() == column.name.lower()
                        and measure.physical_table.lower() == entity.physical_table.lower()
                        for measure in self.catalog.measures
                    )
                ]
                if len(set(candidates)) != 1:
                    return None
                table = candidates[0]
            if table is None:
                return None
            column.set("table", exp.to_identifier(table))
        return normalized.sql(dialect="postgres", pretty=False).lower()

    @staticmethod
    def _direct_table_aliases(select: exp.Select) -> dict[str, str]:
        sources: dict[str, str] = {}
        from_clause = select.args.get("from") or select.args.get("from_")
        tables: list[exp.Table] = []
        if from_clause is not None:
            tables.extend(from_clause.find_all(exp.Table))
        for join in select.args.get("joins", []):
            if isinstance(join.this, exp.Table):
                tables.append(join.this)
        for table in tables:
            sources[table.alias_or_name.lower()] = table.name.lower()
        return sources

    @staticmethod
    def _group_columns(select: exp.Select) -> tuple[exp.Column, ...]:
        group = select.args.get("group")
        if not isinstance(group, exp.Group):
            return ()
        return tuple(column for item in group.expressions for column in item.find_all(exp.Column))

    def _physical_column(self, attribute_id: str) -> str:
        for measure in self.catalog.measures:
            if attribute_id == measure.source_attribute_id:
                return measure.physical_column_or_path
        # Grain keys are identity attributes rather than measures.  Canonical
        # attribute IDs end in the physical column name; the catalog already
        # validates the owning entity, so this fallback does not infer a join.
        if ":" in attribute_id:
            return attribute_id.rsplit(":", 1)[-1]
        raise GrainContractError(f"unknown native-grain attribute: {attribute_id}")
