"""Deterministic semantic-contract checks over compiled SQLGlot ASTs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel
from sqlglot import exp

from app.semantics.semantic_compiler import CompiledSemanticQuery, ExpressionCompiler
from app.semantics.semantic_errors import SemanticFailureCode
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    CommonTableExpression,
    CTERelationSource,
    DerivedRelation,
    DerivedRelationSource,
    EntityRelationSource,
    ExistsExpression,
    ScalarSubqueryExpression,
    SemanticQueryIR,
)


@dataclass(frozen=True)
class SemanticValidationResult:
    accepted: bool
    failures: tuple[tuple[SemanticFailureCode, str], ...] = ()


class SemanticConsistencyValidator:
    """Check the SQL proposal against a typed IR without authorizing execution."""

    def __init__(self, mapping: SemanticMappingSnapshot) -> None:
        self.mapping = mapping

    def validate(
        self, ir: SemanticQueryIR, compiled: CompiledSemanticQuery | exp.Select
    ) -> SemanticValidationResult:
        tree = compiled.ast if isinstance(compiled, CompiledSemanticQuery) else compiled
        failures: list[tuple[SemanticFailureCode, str]] = []
        expected_tables = _expected_tables(self.mapping, ir)
        for join in ir.joins:
            if join.relationship_id is None:
                continue
            relationship = self.mapping.relationship(join.relationship_id)
            expected_tables.update(
                self.mapping.entity(endpoint).physical_table.lower()
                for endpoint in (
                    relationship.from_entity_id,
                    relationship.to_entity_id,
                )
            )
        actual_tables = {table.name.lower() for table in tree.find_all(exp.Table)}
        if actual_tables != expected_tables:
            failures.append(
                (
                    SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION,
                    "tables differ: "
                    f"expected {sorted(expected_tables)}, got {sorted(actual_tables)}",
                )
            )
        aliases = {
            table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)
        }
        expected_relationships = {
            _relationship_columns(self.mapping, join.relationship_id)
            for join in ir.joins
            if join.relationship_id is not None
        }
        physical_tables = {item.physical_table.lower() for item in self.mapping.entities}
        actual_relationships = {
            _join_columns(join, aliases)
            for join in tree.args.get("joins", [])
            if _join_columns(join, aliases) is not None
            and all(table in physical_tables for table, _ in (_join_columns(join, aliases) or ()))
        }
        planned_relationship_count = sum(join.relationship_id is not None for join in ir.joins)
        actual_physical_join_count = sum(
            1
            for join in tree.args.get("joins", [])
            if (_join_columns(join, aliases) or ())
            and all(table in physical_tables for table, _ in (_join_columns(join, aliases) or ()))
        )
        if actual_physical_join_count != planned_relationship_count:
            failures.append(
                (
                    SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION,
                    "join count differs from the semantic contract",
                )
            )
        elif actual_relationships != expected_relationships:
            failures.append(
                (
                    SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION,
                    "join relationships differ from the semantic contract",
                )
            )
        actual_select = tuple(tree.expressions)
        if len(actual_select) != len(ir.select):
            failures.append(
                (
                    SemanticFailureCode.PROJECTION_MISMATCH,
                    f"expected {len(ir.select)} outputs, got {len(actual_select)}",
                )
            )
        expected_columns = _expression_columns(item.expression for item in ir.select)
        actual_columns = {
            (column.name.lower(), aliases.get(column.table.lower(), column.table.lower()))
            for item in actual_select
            for column in item.find_all(exp.Column)
        }
        expected_physical = set()
        for attribute_id in expected_columns:
            try:
                attribute = self.mapping.attribute(attribute_id)
            except Exception:
                # Nested exported attributes are validated against their
                # explicit relation schema below, not the physical catalog.
                continue
            expected_physical.add(
                (
                    attribute.physical_column.lower(),
                    self.mapping.entity(attribute.entity_id).physical_table.lower(),
                )
            )
        if not expected_physical.issubset({(name, table) for name, table in actual_columns}):
            failures.append(
                (SemanticFailureCode.PROJECTION_MISMATCH, "required output attribute is missing")
            )
        expected_aggregates = {
            item.function
            for item in _all_expressions(tuple(item.expression for item in ir.select))
            if isinstance(item, AggregateExpression)
        }
        actual_aggregates = {
            {
                "COUNT": "COUNT",
                "SUM": "SUM",
                "AVG": "AVG",
                "MIN": "MIN",
                "MAX": "MAX",
            }.get(type(item).__name__.upper(), type(item).__name__.upper())
            for item in tree.find_all(exp.AggFunc)
        }
        normalized_expected = set(expected_aggregates)
        if "COUNT_DISTINCT" in normalized_expected:
            normalized_expected.remove("COUNT_DISTINCT")
            normalized_expected.add("COUNT")
        if normalized_expected and not any(
            expected in " ".join(actual_aggregates) for expected in normalized_expected
        ):
            failures.append(
                (SemanticFailureCode.INVALID_AGGREGATION, "aggregate contract is not present")
            )
        expected_aggregate_filters = tuple(
            expression.filter
            for expression in _current_scope_expressions(
                tuple(item.expression for item in ir.select)
            )
            if isinstance(expression, AggregateExpression) and expression.filter is not None
        )
        actual_aggregate_filters = tuple(
            filter_node.args["expression"].this
            for filter_node in _current_scope_ast_nodes(tree)
            if isinstance(filter_node, exp.Filter)
            if isinstance(filter_node.args.get("expression"), exp.Where)
        )
        if len(expected_aggregate_filters) != len(actual_aggregate_filters):
            failures.append(
                (
                    SemanticFailureCode.INVALID_AGGREGATION,
                    "filtered aggregate contract does not match the compiled query",
                )
            )
        elif expected_aggregate_filters and not any(
            any(
                isinstance(node, AttributeRef) and node.attribute_id.startswith("output:")
                for node in _all_expressions(filter_expression)
            )
            for filter_expression in expected_aggregate_filters
        ):
            entity_aliases: dict[str, str] = {}
            for alias, table_name in aliases.items():
                try:
                    entity_aliases[self.mapping.entity_for_physical(table_name).entity_id] = alias
                except Exception:
                    continue
            expected_filter_sql = tuple(
                _normalize_sql(
                    ExpressionCompiler(self.mapping, entity_aliases)
                    .compile(filter_expression)
                    .sql(dialect="postgres")
                )
                for filter_expression in expected_aggregate_filters
                if filter_expression is not None
            )
            actual_filter_sql = tuple(
                _normalize_sql(expression.sql(dialect="postgres"))
                for expression in actual_aggregate_filters
            )
            if expected_filter_sql != actual_filter_sql:
                failures.append(
                    (
                        SemanticFailureCode.INVALID_AGGREGATION,
                        "filtered aggregate predicate differs from the semantic contract",
                    )
                )
        actual_group = tree.args.get("group")
        actual_group_expressions = (
            tuple(actual_group.expressions) if isinstance(actual_group, exp.Group) else ()
        )
        if len(actual_group_expressions) != len(ir.group_by):
            if ir.group_by or actual_group_expressions:
                failures.append(
                    (
                        SemanticFailureCode.INVALID_GRAIN,
                        "GROUP BY does not match the semantic contract",
                    )
                )
        if (tree.args.get("having") is None) != (ir.having is None):
            failures.append(
                (
                    SemanticFailureCode.INVALID_AGGREGATION,
                    "HAVING does not match the semantic contract",
                )
            )
        actual_order = tree.args.get("order")
        actual_order_expressions = (
            tuple(actual_order.expressions) if isinstance(actual_order, exp.Order) else ()
        )
        if len(actual_order_expressions) != len(ir.order_by):
            if ir.order_by or actual_order_expressions:
                failures.append(
                    (
                        SemanticFailureCode.ORDER_LIMIT_MISMATCH,
                        "ORDER BY does not match the semantic contract",
                    )
                )
        elif any(
            (item.args.get("desc") is True) != (spec.direction.value == "DESC")
            for item, spec in zip(actual_order_expressions, ir.order_by, strict=True)
            if isinstance(item, exp.Ordered)
        ):
            failures.append(
                (
                    SemanticFailureCode.ORDER_LIMIT_MISMATCH,
                    "ORDER BY direction does not match the semantic contract",
                )
            )
        expected_limit = ir.limit
        limit = tree.args.get("limit")
        actual_limit = None
        if (
            isinstance(limit, exp.Limit)
            and isinstance(limit.expression, exp.Literal)
            and limit.expression.is_int
        ):
            actual_limit = int(limit.expression.this)
        if expected_limit != actual_limit:
            if expected_limit is not None or actual_limit is not None:
                failures.append(
                    (
                        SemanticFailureCode.ORDER_LIMIT_MISMATCH,
                        "LIMIT does not match the semantic contract",
                    )
                )
        expected_distinct = ir.distinct
        actual_distinct = tree.args.get("distinct") is not None
        if expected_distinct != actual_distinct:
            failures.append(
                (
                    SemanticFailureCode.PROJECTION_MISMATCH,
                    "DISTINCT does not match the semantic contract",
                )
            )
        if ir.where is not None:
            if tree.args.get("where") is None:
                failures.append(
                    (
                        SemanticFailureCode.MISSING_REQUIRED_FILTER,
                        "required WHERE predicate is missing",
                    )
                )
            else:
                root_entity = ir.from_entity_id
                if isinstance(ir.from_source, EntityRelationSource):
                    root_entity = ir.from_source.entity_id
                if root_entity is None or any(join.relationship_id is None for join in ir.joins):
                    # Relation-local references (CTE/derived outputs) are
                    # checked by the recursive scope validator below.  They
                    # do not have physical mapping aliases at this level.
                    self._validate_nested_scopes(ir, tree, failures)
                    return SemanticValidationResult(accepted=not failures, failures=tuple(failures))
                aliases = {root_entity: _source_alias(tree)}
                for index, join in enumerate(ir.joins, start=1):
                    if join.relationship_id is None:
                        continue
                    relation = self.mapping.relationship(join.relationship_id)
                    joined_entity = next(
                        endpoint
                        for endpoint in (relation.from_entity_id, relation.to_entity_id)
                        if endpoint not in aliases
                    )
                    physical = self.mapping.entity(joined_entity).physical_table.lower()
                    aliases[joined_entity] = next(
                        (
                            table.alias_or_name
                            for table in tree.find_all(exp.Table)
                            if table.name.lower() == physical
                        ),
                        f"t{index}",
                    )
                expected_where = (
                    ExpressionCompiler(self.mapping, aliases)
                    .compile(ir.where)
                    .sql(dialect="postgres")
                )
                actual_where = tree.args["where"].this.sql(dialect="postgres")
                if _normalize_sql(expected_where) != _normalize_sql(actual_where):
                    failures.append(
                        (
                            SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION,
                            "WHERE differs from the semantic contract",
                        )
                    )
        elif tree.args.get("where") is not None:
            failures.append(
                (SemanticFailureCode.UNSUPPORTED_SEMANTIC_ADDITION, "unexpected WHERE predicate")
            )
        self._validate_nested_scopes(ir, tree, failures)
        return SemanticValidationResult(accepted=not failures, failures=tuple(failures))

    def _validate_nested_scopes(
        self, ir: SemanticQueryIR, tree: exp.Select, failures: list[tuple[SemanticFailureCode, str]]
    ) -> None:
        with_clause = tree.args.get("with_")
        actual_ctes = (
            {item.alias_or_name: item.this for item in with_clause.expressions}
            if with_clause is not None
            else {}
        )
        expected_ctes = {item.cte_id: item for item in ir.ctes}
        if set(actual_ctes) != set(expected_ctes):
            failures.append(
                (SemanticFailureCode.INVALID_CTE_REFERENCE, "CTE scope differs from contract")
            )
        for cte_id, cte in expected_ctes.items():
            nested_ast = actual_ctes.get(cte_id)
            if isinstance(nested_ast, exp.Select):
                nested_result = self.validate(cte.query, nested_ast)
                failures.extend(nested_result.failures)
        expected_derived = {item.relation_id: item for item in ir.derived_relations}
        actual_derived = {
            item.alias_or_name: item.this
            for item in tree.find_all(exp.Subquery)
            if item.alias_or_name
        }
        for relation_id, relation in expected_derived.items():
            nested_ast = actual_derived.get(relation_id)
            if isinstance(nested_ast, exp.Select):
                nested_result = self.validate(relation.query, nested_ast)
                failures.extend(nested_result.failures)


def _expression_columns(expressions: object) -> set[str]:
    return {
        expression.attribute_id
        for expression in _all_expressions(expressions)
        if isinstance(expression, AttributeRef)
    }


def _expected_tables(mapping: SemanticMappingSnapshot, ir: SemanticQueryIR) -> set[str]:
    expected: set[str] = set()
    if ir.from_entity_id is not None:
        expected.add(mapping.entity(ir.from_entity_id).physical_table.lower())
    elif isinstance(ir.from_source, EntityRelationSource):
        expected.add(mapping.entity(ir.from_source.entity_id).physical_table.lower())
    elif isinstance(ir.from_source, CTERelationSource):
        expected.add(ir.from_source.cte_id.lower())
    elif isinstance(ir.from_source, DerivedRelationSource):
        expected.add(ir.from_source.relation_id.lower())
    for cte in ir.ctes:
        expected.update(_expected_tables(mapping, cte.query))
        expected.add(cte.cte_id.lower())
    for relation in ir.derived_relations:
        expected.update(_expected_tables(mapping, relation.query))
        expected.add(relation.relation_id.lower())
    for join in ir.joins:
        if join.relationship_id is not None:
            relationship = mapping.relationship(join.relationship_id)
            expected.update(
                mapping.entity(endpoint).physical_table.lower()
                for endpoint in (
                    relationship.from_entity_id,
                    relationship.to_entity_id,
                )
            )
        source = join.target_source
        if isinstance(source, EntityRelationSource):
            expected.add(mapping.entity(source.entity_id).physical_table.lower())
        elif isinstance(source, CTERelationSource):
            expected.add(source.cte_id.lower())
        elif isinstance(source, DerivedRelationSource):
            expected.add(source.relation_id.lower())
    # Scalar subqueries are compiled in the enclosing CTE namespace.  Their
    # source relation therefore contributes to the enclosing compiled scope,
    # even though their own query-local CTE definitions are not rendered as a
    # second WITH clause by the compiler.
    for expression in _all_expressions(
        (
            *ir.select,
            ir.where,
            *ir.group_by,
            ir.having,
            *ir.order_by,
        )
    ):
        if isinstance(expression, ScalarSubqueryExpression):
            expected.update(_expected_scalar_scope_tables(mapping, expression.query))
    return expected


def _expected_scalar_scope_tables(
    mapping: SemanticMappingSnapshot, ir: SemanticQueryIR
) -> set[str]:
    result: set[str] = set()
    source = ir.from_source
    if isinstance(source, EntityRelationSource):
        result.add(mapping.entity(source.entity_id).physical_table.lower())
    elif isinstance(source, CTERelationSource):
        result.add(source.cte_id.lower())
    elif isinstance(source, DerivedRelationSource):
        result.add(source.relation_id.lower())
    for join in ir.joins:
        if join.relationship_id is not None:
            relationship = mapping.relationship(join.relationship_id)
            result.update(
                mapping.entity(endpoint).physical_table.lower()
                for endpoint in (relationship.from_entity_id, relationship.to_entity_id)
            )
        elif isinstance(join.target_source, EntityRelationSource):
            result.add(mapping.entity(join.target_source.entity_id).physical_table.lower())
        elif isinstance(join.target_source, CTERelationSource):
            result.add(join.target_source.cte_id.lower())
        elif isinstance(join.target_source, DerivedRelationSource):
            result.add(join.target_source.relation_id.lower())
    for expression in _all_expressions(
        (*ir.select, ir.where, *ir.group_by, ir.having, *ir.order_by)
    ):
        if isinstance(expression, ScalarSubqueryExpression):
            result.update(_expected_scalar_scope_tables(mapping, expression.query))
    return result


def _all_expressions(value: object) -> Iterator[BaseModel]:
    if isinstance(value, (tuple, list, set)):
        for item in value:
            yield from _all_expressions(item)
    elif isinstance(value, BaseModel) and value.__class__.__module__.startswith(
        "app.semantics.semantic_query"
    ):
        yield value
        for field in value.__class__.model_fields:
            child = getattr(value, field, None)
            if field in {
                "kind",
                "attribute_id",
                "value",
                "value_type",
                "function",
                "operator",
                "target_type",
                "alias",
                "position",
                "direction",
                "negated",
            }:
                continue
            yield from _all_expressions(child)


def _current_scope_expressions(value: object) -> Iterator[BaseModel]:
    """Walk semantic expressions without entering nested query scopes."""
    if isinstance(value, (tuple, list, set)):
        for item in value:
            yield from _current_scope_expressions(item)
    elif isinstance(value, BaseModel) and value.__class__.__module__.startswith(
        "app.semantics.semantic_query"
    ):
        yield value
        if isinstance(value, (SemanticQueryIR, CommonTableExpression, DerivedRelation)):
            return
        if isinstance(value, (ExistsExpression, ScalarSubqueryExpression)):
            return
        for field in value.__class__.model_fields:
            child = getattr(value, field, None)
            if field in {
                "kind",
                "attribute_id",
                "value",
                "value_type",
                "function",
                "operator",
                "target_type",
                "alias",
                "position",
                "direction",
                "negated",
            }:
                continue
            yield from _current_scope_expressions(child)


def _current_scope_ast_nodes(node: exp.Expression) -> Iterator[exp.Expression]:
    """Walk one SELECT scope without descending into nested SELECTs."""
    if isinstance(node, (exp.Subquery, exp.CTE)):
        return
    yield node
    for child in node.iter_expressions():
        yield from _current_scope_ast_nodes(child)


def _normalize_sql(value: str) -> str:
    return " ".join(value.lower().split())


def _source_alias(tree: exp.Select) -> str:
    from_clause = tree.args.get("from_")
    source = from_clause.this if isinstance(from_clause, exp.From) else None
    if isinstance(source, exp.Table):
        return source.alias_or_name
    return "t0"


def _relationship_columns(
    mapping: SemanticMappingSnapshot, relationship_id: str
) -> frozenset[tuple[str, str]]:
    relationship = mapping.relationship(relationship_id)
    left = mapping.attribute(relationship.from_attribute_id)
    right = mapping.attribute(relationship.to_attribute_id)
    return frozenset(
        {
            (mapping.entity(left.entity_id).physical_table.lower(), left.physical_column.lower()),
            (mapping.entity(right.entity_id).physical_table.lower(), right.physical_column.lower()),
        }
    )


def _join_columns(join: exp.Join, aliases: dict[str, str]) -> frozenset[tuple[str, str]] | None:
    condition = join.args.get("on")
    if condition is None:
        return None
    columns = list(condition.find_all(exp.Column))
    if len(columns) != 2:
        return None
    resolved: set[tuple[str, str]] = set()
    for column in columns:
        table = aliases.get(column.table.lower(), column.table.lower())
        resolved.add((table, column.name.lower()))
    return frozenset(resolved) if len(resolved) == 2 else None
