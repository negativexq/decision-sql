"""Server-owned measure grain semantics and diagnostic SQL grain validation.

This module is deliberately independent of benchmark cases and model prompts.
It extends the existing :mod:`app.semantics` boundary with facts about source
measure grain, additivity, and relationship cardinality.  The validator is
diagnostic in M46A: it never rewrites SQL, blocks execution, or changes scores.
"""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope


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

    def fanout_edges_from(self, parent_entity_id: str) -> tuple[GrainRelationship, ...]:
        return tuple(
            relationship
            for relationship in self.relationships
            if relationship.to_entity_id == parent_entity_id
            and relationship.cardinality.upper().replace("-", "_") == "MANY_TO_ONE"
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
        scopes = _scopes(tree)
        scope_of = {id(scope.expression): scope for scope in scopes}
        diagnostics = [
            self._validate_select(select, scope_of.get(id(select)))
            for select in tree.find_all(exp.Select)
        ]
        diagnostics.extend(self._traced_fanout(scope) for scope in scopes)
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

    def _validate_select(self, select: exp.Select, scope: Scope | None = None) -> GrainDiagnostic:
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
                table = self._column_table(column, aliases)
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
        if scope is not None:
            direct_tables |= self._derived_child_tables(scope)
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
                    if self._column_table(column, aliases) == measure.physical_table.lower()
                }
                # Grouped by the parent's key, a group is one parent repeated once per
                # child: its average is right, but its sum counts the parent per child.
                if isinstance(aggregate, exp.Avg) and native_columns.issubset(grouped_native):
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

    def _traced_fanout(self, scope: Scope) -> GrainDiagnostic:
        """Fan-out of a measure that reaches this scope through a CTE or derived table.

        ``_validate_select`` sees only columns read straight from a table, so a parent
        measure joined to its children inside a CTE and summed outside it would pass.
        Each column of a SUM or AVG is followed back through the sources' projections to
        the table it is read from, and every table joined along the way counts as joined.
        """
        select = scope.expression
        not_applicable = GrainDiagnostic(
            code=GrainDiagnosticCode.NOT_APPLICABLE,
            message="No measure reaches this scope through a CTE or derived table.",
        )
        if not isinstance(select, exp.Select):
            return not_applicable
        for aggregate in select.find_all(exp.Sum, exp.Avg):
            if aggregate.find_ancestor(exp.Select) is not select:
                continue
            for column in aggregate.this.find_all(exp.Column):
                for table, name, path in self._lineage(scope, column, (), through_derived=True):
                    for measure in self.catalog.measures_for_physical(table, name):
                        if measure.aggregation_behavior is not AggregationBehavior.ADDITIVE:
                            continue
                        diagnostic = self._exposed_measure(measure, path, aggregate, column)
                        if diagnostic is not None:
                            return diagnostic
        return not_applicable

    def _exposed_measure(
        self,
        measure: MeasureSemantics,
        path: tuple[Scope, ...],
        aggregate: exp.Expression,
        column: exp.Column,
    ) -> GrainDiagnostic | None:
        """The fan-out a traced measure meets on its way from its table to the aggregate.

        ``path`` runs from the aggregating scope inward. Tables are collected from the
        innermost scope outward; a scope that groups or de-duplicates on the measure's own
        key leaves one row per parent, so the children joined below it no longer repeat it.
        The aggregating scope's own GROUP BY makes only an average safe: a sum still adds
        the parent once per child row in its group.
        """
        joined: set[str] = set()
        last = len(path) - 1
        outward = tuple(reversed(path))
        for depth, scope in enumerate(outward):
            # The source the measure arrives through is traced, not a sibling child.
            arrived_through = path[last - depth + 1] if depth > 0 else None
            joined |= _physical_tables(scope) | self._derived_child_tables(
                scope, arrived_through, outward[depth:]
            )
            if depth == last and not isinstance(aggregate, exp.Avg):
                continue
            if self._keeps_native_grain(scope, measure, aggregating=depth == last):
                joined = set()
        child_grains = {
            edge.relationship_id: GrainKey(
                entity_id=edge.from_entity_id,
                key_attribute_ids=self.graph.entity(edge.from_entity_id).key_attribute_ids,
            )
            for edge in self.graph.fanout_edges_from(measure.entity_id)
        }
        for edge in self.graph.fanout_edges_from(measure.entity_id):
            child_table = self.graph.entity(edge.from_entity_id).physical_table.lower()
            if child_table not in joined:
                continue
            if child_grains[edge.relationship_id] in measure.allowed_rollup_grains:
                continue
            masked = isinstance(aggregate.this, exp.Distinct)
            return GrainDiagnostic(
                code=(
                    GrainDiagnosticCode.DISTINCT_VALUE_FANOUT_MASK
                    if masked
                    else GrainDiagnosticCode.PARENT_MEASURE_FANOUT
                ),
                message=(
                    "Value-level DISTINCT masks a parent measure across a fanout edge."
                    if masked
                    else "Parent additive measure is aggregated after a one-to-many fanout "
                    "inside a CTE or derived table."
                ),
                measure_ids=(measure.measure_id,),
                native_grains=(measure.native_grain,),
                fanout_edges=(edge,),
                aggregate_expressions=(aggregate.sql(dialect="postgres"),),
                evidence={
                    "column": column.sql(dialect="postgres"),
                    "child_table": child_table,
                    "traced": True,
                },
            )
        return None

    def _derived_child_tables(
        self, scope: Scope, skip: Scope | None = None, outward: tuple[Scope, ...] = ()
    ) -> set[str]:
        """Tables behind the CTEs and derived tables joined in this scope that can give
        more than one row per joined key, so they repeat whatever they are joined to.

        ``JOIN (SELECT order_id FROM order_items) i ON i.order_id = o.id`` repeats each
        order once per item; grouped or DISTINCT on ``order_id`` it gives one row per
        order. DISTINCT (order_id, product_id) still gives one row per product of the
        order, unless the query groups by product_id on the way out (``outward`` runs
        from this scope to the aggregating one): then each group holds an order once.
        """
        found: set[str] = set()
        for alias, (_, source) in scope.selected_sources.items():
            if not isinstance(source, Scope) or source is skip:
                continue
            if self._repeats_rows(scope, alias, source, outward or (scope,)):
                found |= _repeated_tables(source, outermost=True)
        return found

    def _repeats_rows(
        self, scope: Scope, alias: str, source: Scope, outward: tuple[Scope, ...]
    ) -> bool:
        """Whether a derived source can give more than one row per key it is joined on."""
        select = source.expression
        if not isinstance(select, exp.Select):
            return False
        keys = _row_keys(source)
        if keys is None:
            return True
        extras = keys - _join_keys(scope.expression, alias)
        if not extras:
            return False
        grouped = [self._grouped_lineage(later) for later in outward]
        for extra in extras:
            if extra is None:
                return True
            column = exp.column(extra, table=alias)
            lineage = {
                (table, name)
                for table, name, _ in self._lineage(scope, column, (), through_derived=False)
            }
            if not lineage or not any(lineage <= group for group in grouped):
                return True
        return False

    def _grouped_lineage(self, scope: Scope) -> set[tuple[str, str]]:
        """The table columns a scope's GROUP BY reads."""
        select = scope.expression
        group = select.args.get("group") if isinstance(select, exp.Select) else None
        if not isinstance(group, exp.Group):
            return set()
        return {
            (table, name)
            for item in group.expressions
            for column in item.find_all(exp.Column)
            for table, name, _ in self._lineage(scope, column, (), through_derived=False)
        }

    def _keeps_native_grain(
        self, scope: Scope, measure: MeasureSemantics, *, aggregating: bool
    ) -> bool:
        """Whether the scope leaves one row per parent: grouped by its key, or DISTINCT
        over a row that carries the key. The aggregating scope counts only its GROUP BY."""
        select = scope.expression
        if not isinstance(select, exp.Select):
            return False
        group = select.args.get("group")
        columns: list[exp.Column] = []
        if isinstance(group, exp.Group):
            columns = [c for item in group.expressions for c in item.find_all(exp.Column)]
        elif select.args.get("distinct") is not None and not aggregating:
            columns = [
                c for projection in select.expressions for c in projection.find_all(exp.Column)
            ]
        native = {
            self._physical_column(attribute_id).lower()
            for attribute_id in measure.native_grain.key_attribute_ids
        }
        grouped = {
            (table, name)
            for column in columns
            for table, name, _ in self._lineage(scope, column, (), through_derived=False)
        }
        # A column the joins equate with the key stands for it: DISTINCT a.district_id
        # keeps one row per district when the scope joins d.district_id = a.district_id.
        for equality in select.find_all(exp.EQ):
            if equality.find_ancestor(exp.Select) is not select:
                continue
            sides = [
                {(t, n) for t, n, _ in self._lineage(scope, side, (), through_derived=False)}
                for side in (equality.left, equality.right)
                if isinstance(side, exp.Column)
            ]
            if len(sides) == 2 and (sides[0] & grouped or sides[1] & grouped):
                grouped |= sides[0] | sides[1]
        table = measure.physical_table.lower()
        return bool(columns) and native.issubset({n for t, n in grouped if t == table})

    def _lineage(
        self,
        scope: Scope,
        column: exp.Column,
        path: tuple[Scope, ...],
        *,
        through_derived: bool,
    ) -> list[tuple[str, str, tuple[Scope, ...]]]:
        """The physical (table, column) pairs a column of ``scope`` is read from.

        With ``through_derived`` only columns that pass through a CTE or derived table
        are returned: a column read straight from a table is ``_validate_select``'s.
        A projection aggregated in the inner scope is traced too: a parent total grouped by
        the parent's key is still one value per parent, and repeats when joined to children.
        A window function ends the trace.
        """
        path = (*path, scope)
        found: list[tuple[str, str, tuple[Scope, ...]]] = []
        for source in _column_sources(scope, column, self.catalog):
            if isinstance(source, exp.Table):
                if not through_derived:
                    found.append((source.name.lower(), column.name.lower(), path))
                continue
            inner = source.expression
            if not isinstance(inner, exp.Select):
                continue
            projection = _projection(inner, column.name)
            if projection is None:
                # SELECT * or t.*: the name is whichever source of the inner scope has it.
                if _has_star(inner):
                    star = exp.column(column.name)
                    found.extend(self._lineage(source, star, path, through_derived=False))
                continue
            if projection.find(exp.Window) is not None:
                continue
            for inner_column in projection.find_all(exp.Column):
                found.extend(self._lineage(source, inner_column, path, through_derived=False))
        return found

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

    def _column_table(self, column: exp.Column, aliases: dict[str, str]) -> str | None:
        """The physical table a column reads, including an unqualified measure column.

        PostgreSQL rejects an unqualified name that two joined tables share, so a name
        that is a measure of exactly one joined table can only mean that table.
        """
        if column.table:
            return aliases.get(column.table.lower())
        owners = {
            table
            for table in set(aliases.values())
            if self.catalog.measures_for_physical(table, column.name)
        }
        return owners.pop() if len(owners) == 1 else None

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


def _scopes(tree: exp.Expression) -> list[Scope]:
    try:
        return traverse_scope(tree)
    except Exception:
        return []


def _physical_tables(scope: Scope) -> set[str]:
    return {
        source.name.lower()
        for _, source in scope.selected_sources.values()
        if isinstance(source, exp.Table)
    }


def _column_sources(
    scope: Scope, column: exp.Column, catalog: MeasureCatalog
) -> list[exp.Table | Scope]:
    """The sources of ``scope`` a column can be read from.

    An unqualified name belongs to the only source, or to the sources that have it:
    a derived source that projects it, or a table where it is a modeled measure.
    PostgreSQL rejects a name two sources share, so more than one is never a guess.
    """
    sources = {name.lower(): source for name, (_, source) in scope.selected_sources.items()}
    if column.table:
        source = sources.get(column.table.lower())
        return [] if source is None else [source]
    if len(sources) == 1:
        return list(sources.values())
    found: list[exp.Table | Scope] = []
    for source in sources.values():
        if isinstance(source, exp.Table):
            if catalog.measures_for_physical(source.name, column.name):
                found.append(source)
        elif isinstance(source.expression, exp.Select) and (
            _projection(source.expression, column.name) is not None or _has_star(source.expression)
        ):
            found.append(source)
    return found


def _projection(select: exp.Select, name: str) -> exp.Expression | None:
    for projection in select.expressions:
        if projection.alias_or_name.lower() == name.lower() and not isinstance(
            projection, exp.Star
        ):
            inner: exp.Expression = projection.unalias()
            return inner
    return None


def _has_star(select: exp.Select) -> bool:
    return any(
        isinstance(projection, exp.Star)
        or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))
        for projection in select.expressions
    )


def _repeated_tables(scope: Scope, *, outermost: bool = False) -> set[str]:
    """The physical tables a repeating derived source reads, through its inner sources
    of raw rows; an inner source that is grouped or DISTINCT is taken at its own grain.

    A select that only passes one source through repeats as that source does, so its
    source is read as the repeating one: ``SELECT order_id FROM items`` over items
    grouped by (order_id, product_id) repeats order_items.
    """
    select = scope.expression
    if not isinstance(select, exp.Select) or (not outermost and _collapses_rows(scope)):
        return set()
    inner = _passthrough_source(scope)
    if inner is not None:
        return _repeated_tables(inner, outermost=True)
    found = _physical_tables(scope)
    for _, source in scope.selected_sources.values():
        if isinstance(source, Scope):
            found |= _repeated_tables(source)
    return found


def _collapses_rows(scope: Scope) -> bool:
    """Grouped, DISTINCT, or a single aggregated row, here or in a source passed through."""
    return _row_keys(scope) is not None


def _passthrough_source(scope: Scope) -> Scope | None:
    """The one derived source a select reads without grouping, DISTINCT or aggregating,
    which it therefore gives the rows of (fewer, if it filters)."""
    select = scope.expression
    if (
        not isinstance(select, exp.Select)
        or select.args.get("group") is not None
        or select.args.get("distinct") is not None
        or _aggregates_to_one_row(select)
        or len(scope.selected_sources) != 1
    ):
        return None
    ((_, source),) = scope.selected_sources.values()
    return source if isinstance(source, Scope) else None


def _row_keys(scope: Scope) -> frozenset[str | None] | None:
    """The output columns a select gives one row per; None for raw rows.

    One aggregated row has no key (an empty set). A GROUP BY expression no output
    column names counts as an unknown key (None), which nothing outside can match.
    A select passing one source through keeps that source's keys under the names it
    gives them; a key it leaves out leaves its rows repeating, as raw rows do.
    """
    select = scope.expression
    if not isinstance(select, exp.Select):
        return None
    distinct = select.args.get("distinct")
    group = select.args.get("group")
    if isinstance(group, exp.Group):
        named: set[str | None] = set()
        for item in group.expressions:
            projection = next((p for p in select.expressions if p.unalias() == item), None)
            if projection is not None:
                named.add(projection.alias_or_name.lower())
            elif isinstance(item, exp.Column):
                named.add(item.name.lower())
            else:
                named.add(None)
        return frozenset(named)
    if isinstance(distinct, exp.Distinct):
        on = distinct.args.get("on")
        items = on.expressions if isinstance(on, exp.Tuple) else select.expressions
        return frozenset(
            item.alias_or_name.lower() if item.alias_or_name else None for item in items
        )
    if _aggregates_to_one_row(select):
        return frozenset()
    inner = _passthrough_source(scope)
    if inner is None:
        return None
    inner_keys = _row_keys(inner)
    if inner_keys is None or _has_star(select):
        return inner_keys
    renamed: dict[str, str] = {}
    for projection in select.expressions:
        column = projection.unalias()
        if isinstance(column, exp.Column):
            renamed.setdefault(column.name.lower(), projection.alias_or_name.lower())
    keys: set[str | None] = set()
    for key in inner_keys:
        if key is not None and key not in renamed:
            return None
        keys.add(None if key is None else renamed[key])
    return frozenset(keys)


def _aggregates_to_one_row(select: exp.Select) -> bool:
    """An aggregate of this select's own, outside any window, in its output; one in a
    scalar subquery or under OVER belongs to that query or window instead."""
    return any(
        aggregate.find_ancestor(exp.Select, exp.Window) is select
        for projection in select.expressions
        for aggregate in projection.find_all(exp.AggFunc)
    )


def _join_keys(select: exp.Expression, alias: str) -> set[str | None]:
    """Columns of source ``alias`` that this select equates with a column of another source."""
    keys: set[str | None] = set()
    if not isinstance(select, exp.Select):
        return keys
    for equality in select.find_all(exp.EQ):
        if equality.find_ancestor(exp.Select) is not select:
            continue
        left, right = equality.left, equality.right
        if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
            continue
        for mine, other in ((left, right), (right, left)):
            if mine.table.lower() == alias.lower() and other.table.lower() != alias.lower():
                keys.add(mine.name.lower())
    return keys
