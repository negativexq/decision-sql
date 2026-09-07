"""Provider-free oracle ceiling harness for the canonical semantic engine.

This module is evaluation-only.  A local harness may use an already available
reference SQL string to construct a semantic IR, then measures whether the
canonical compiler and M1 can represent and execute that contract.  No result
from this module is used by production runtime code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from sqlglot import exp, parse_one

from app.catalog.models import ColumnMetadata, RelationshipMetadata, SchemaCatalog, TableMetadata
from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_errors import SemanticEngineError, SemanticFailureCode
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AggregateExpression,
    AttributeRef,
    BetweenExpression,
    BinaryExpression,
    BinaryOperator,
    CaseBranch,
    CaseExpression,
    CastExpression,
    CommonTableExpression,
    CTERelationSource,
    DerivedRelation,
    DerivedRelationSource,
    EntityRelationSource,
    ExportedAttribute,
    Expression,
    FunctionExpression,
    InExpression,
    IntervalExpression,
    IRSelectItem,
    IsNullExpression,
    JoinKey,
    JoinType,
    LiteralExpression,
    LogicalExpression,
    NotExpression,
    OrderedAggregateExpression,
    OrderSpec,
    PlannedJoin,
    PlannedOutput,
    PopulationContract,
    ScalarSubqueryExpression,
    SemanticFunction,
    SemanticQueryIR,
    SemanticQueryPlan,
    SortDirection,
    StarExpression,
    WindowExpression,
    WindowOrder,
    plan_to_ir,
)
from app.semantics.semantic_validation import SemanticConsistencyValidator


@dataclass(frozen=True)
class OracleCeilingCounts:
    cases: int
    plan_validated: int
    ir_validated: int
    compiled: int
    semantic_validated: int
    unsupported: int
    unsupported_constructs: tuple[str, ...]


@dataclass(frozen=True)
class _OracleRelation:
    source: object
    relation_ref: str
    attributes: dict[str, str]
    entity_id: str | None = None
    base_entities: tuple[str, ...] = ()
    scope_depth: int = 0


@dataclass(frozen=True)
class _OracleQuery:
    ir: SemanticQueryIR
    relation: _OracleRelation
    exports: tuple[ExportedAttribute, ...]


def catalog_from_static_schema(path: Path) -> SchemaCatalog:
    """Build an evaluation-only catalog from a public Base-Lite DDL resource.

    This parser is intentionally outside application runtime.  It consumes
    only the public schema resource and preserves declared FK metadata; it
    does not inspect reference SQL.
    """
    text = path.read_text(encoding="utf-8")
    tables: list[TableMetadata] = []
    for statement in re.findall(r"(?is)(CREATE TABLE.*?\);)", text):
        create = parse_one(statement, read="postgres")
        schema = create.this
        table = schema.this
        columns: list[ColumnMetadata] = []
        primary: set[str] = set()
        relationships: list[RelationshipMetadata] = []
        for item in schema.expressions:
            if isinstance(item, exp.ColumnDef):
                constraints = item.args.get("constraints") or []
                is_primary = any(type(c.this).__name__ == "PrimaryKey" for c in constraints)
                columns.append(
                    ColumnMetadata(
                        name=item.name,
                        type=item.kind.sql(dialect="postgres"),  # type: ignore[union-attr]
                        description="",
                        primary_key=is_primary,
                    )
                )
            elif isinstance(item, exp.PrimaryKey):
                primary.update(identifier.name for identifier in item.expressions)
            elif isinstance(item, exp.ForeignKey):
                reference = item.args.get("reference")
                target = reference.this if isinstance(reference, exp.Reference) else None
                if isinstance(target, exp.Schema) and target.this is not None:
                    relationships.append(
                        RelationshipMetadata(
                            column=item.expressions[0].name,
                            referenced_table=target.this.name,
                            referenced_column=target.expressions[0].name,
                        )
                    )
        if primary:
            columns = [
                column.model_copy(update={"primary_key": column.name in primary})
                for column in columns
            ]
        if columns:
            tables.append(
                TableMetadata(
                    name=table.name,
                    description="",
                    columns=tuple(columns),
                    relationships=tuple(relationships),
                )
            )
    return SchemaCatalog(tables=tuple(tables))


def oracle_plan_from_reference_sql(
    sql: str, mapping: SemanticMappingSnapshot, *, database_id: str = "schema"
) -> SemanticQueryPlan:
    """Translate a reference query into a typed plan for ceiling diagnostics.

    The translator is intentionally isolated here.  It is never imported by
    application services and cannot create runtime mappings or provider input.
    """
    query = _oracle_query(parse_one(sql, dialect="postgres"), mapping, database_id=database_id)
    ir = query.ir
    return SemanticQueryPlan(
        database_id=ir.database_id,
        from_entity_id=ir.from_entity_id,
        from_source=ir.from_source,
        population_contract=ir.population_contract,
        outputs=tuple(
            PlannedOutput(
                position=item.position,
                semantic_role=item.alias or f"output_{item.position}",
                expression=item.expression,
                alias=item.alias,
            )
            for item in ir.select
        ),
        joins=ir.joins,
        where=ir.where,
        group_by=ir.group_by,
        having=ir.having,
        order_by=ir.order_by,
        distinct=ir.distinct,
        limit=ir.limit,
        offset=ir.offset,
        ctes=ir.ctes,
        derived_relations=ir.derived_relations,
    )


def _oracle_query(
    tree: exp.Expression,
    mapping: SemanticMappingSnapshot,
    *,
    database_id: str,
    inherited_ctes: dict[str, CommonTableExpression] | None = None,
    outer_aliases: dict[str, _OracleRelation] | None = None,
) -> _OracleQuery:
    if isinstance(tree, exp.Subquery):
        tree = tree.this
    if not isinstance(tree, exp.Select):
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT, type(tree).__name__
        )
    ctes: list[CommonTableExpression] = []
    known_ctes = dict(inherited_ctes or {})
    with_clause = tree.args.get("with_")
    if with_clause is not None:
        for cte_node in with_clause.expressions:
            cte_id = cte_node.alias_or_name
            if not cte_id:
                raise SemanticEngineError(
                    SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                    "CTE must have an alias",
                )
            nested = _oracle_query(
                cte_node.this,
                mapping,
                database_id=database_id,
                inherited_ctes=known_ctes,
            )
            cte = CommonTableExpression(
                cte_id=cte_id,
                query=nested.ir,
                exported_attributes=nested.exports,
            )
            ctes.append(cte)
            known_ctes[cte_id.lower()] = cte
    from_clause = tree.args.get("from_")
    if from_clause is None or from_clause.this is None:
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT, "missing FROM"
        )
    source, relation, derived = _oracle_source(from_clause.this, mapping, known_ctes, database_id)
    derived_relations = dict(derived)
    aliases = {relation.relation_ref.lower(): relation}
    if isinstance(from_clause.this, (exp.Table, exp.Subquery)):
        aliases[from_clause.this.alias_or_name.lower()] = relation
    joins: list[PlannedJoin] = []
    for join in tree.args.get("joins", []):
        target_source, target_relation, target_derived = _oracle_source(
            join.this, mapping, known_ctes, database_id
        )
        derived_relations.update(target_derived)
        aliases[target_relation.relation_ref.lower()] = target_relation
        if isinstance(join.this, (exp.Table, exp.Subquery)):
            aliases[join.this.alias_or_name.lower()] = target_relation
        condition = join.args.get("on")
        join_type = JoinType.LEFT if join.args.get("side") == "LEFT" else JoinType.INNER
        if isinstance(target_source, EntityRelationSource) and _is_physical_join(
            condition, aliases
        ):
            relationship = _relationship_for_condition(condition, aliases, mapping)
            joins.append(PlannedJoin(relationship_id=relationship, join_type=join_type))
        else:
            joins.append(
                PlannedJoin(
                    target_source=target_source,
                    join_keys=_join_keys(condition, aliases, mapping),
                    join_type=join_type,
                )
            )
    expression_aliases = dict(outer_aliases or {})
    local_depth = max((item.scope_depth for item in expression_aliases.values()), default=-1) + 1
    for alias, item in aliases.items():
        aliases[alias] = replace(item, scope_depth=local_depth)
    expression_aliases.update(aliases)
    select_items: list[exp.Expression] = []
    for item in tree.expressions:
        unwrapped = _unwrap_alias(item)
        if isinstance(unwrapped, exp.Star):
            star_relation = relation
            if isinstance(unwrapped, exp.Column) and unwrapped.table:
                star_relation = aliases.get(unwrapped.table.lower(), relation)
            select_items.extend(
                exp.column(column, table=star_relation.relation_ref).as_(column)
                for column in sorted(star_relation.attributes)
            )
        else:
            select_items.append(item)
    expressions = tuple(
        _expression(item, expression_aliases, mapping, inherited_ctes=known_ctes)
        for item in select_items
    )
    output_aliases: dict[str, Expression] = {}
    for select_item, expression in zip(select_items, expressions, strict=True):
        output_name = _output_alias(select_item)
        if output_name:
            output_aliases[output_name.lower()] = expression
    select = tuple(
        IRSelectItem(
            position=index,
            expression=expression,
            alias=_output_alias(item),
        )
        for index, (item, expression) in enumerate(zip(select_items, expressions, strict=True))
    )
    where = tree.args.get("where")
    having = tree.args.get("having")
    base_entities = list(relation.base_entities)
    if relation.entity_id and relation.entity_id not in base_entities:
        base_entities.insert(0, relation.entity_id)
    if not base_entities:
        base_entities = list(
            dict.fromkeys(
                entity
                for cte in (*ctes, *known_ctes.values())
                for entity in cte.query.population_contract.base_entity_ids
            )
        )
    if not base_entities:
        raise SemanticEngineError(
            SemanticFailureCode.INVALID_POPULATION_CONTRACT, "oracle source has no base population"
        )
    ir = SemanticQueryIR(
        database_id=database_id,
        from_entity_id=None,
        from_source=source,
        joins=tuple(joins),
        select=select,
        where=(
            _expression(where.this, expression_aliases, mapping, inherited_ctes=known_ctes)
            if where is not None
            else None
        ),
        group_by=tuple(
            _group_expression(item, expression_aliases, mapping, output_aliases, known_ctes)
            for item in (tree.args.get("group") or exp.Group()).expressions
        ),
        having=(
            _expression(having.this, expression_aliases, mapping, inherited_ctes=known_ctes)
            if having is not None
            else None
        ),
        order_by=tuple(
            _order(item, expression_aliases, mapping, output_aliases, known_ctes)
            for item in (tree.args.get("order") or exp.Order()).expressions
        ),
        distinct=tree.args.get("distinct") is not None,
        limit=_integer(tree.args["limit"].expression)
        if isinstance(tree.args.get("limit"), exp.Limit)
        else None,
        offset=_integer(tree.args["offset"].expression)
        if isinstance(tree.args.get("offset"), exp.Offset)
        else None,
        population_contract=PopulationContract(
            base_entity_ids=tuple(base_entities), fanout_allowed=bool(joins)
        ),
        ctes=tuple(ctes),
        derived_relations=tuple(derived_relations.values()),
    )
    return _OracleQuery(ir=ir, relation=relation, exports=_exports_for_query(ir))


def run_oracle_ceiling(
    sql_cases: Iterable[str], mapping: SemanticMappingSnapshot
) -> OracleCeilingCounts:
    """Measure representability/compilation only, without provider or execution."""
    total = plan_validated = ir_validated = compiled = semantic_validated = 0
    unsupported: list[str] = []
    for sql in sql_cases:
        total += 1
        try:
            plan = oracle_plan_from_reference_sql(sql, mapping)
            ir = plan_to_ir(plan)
            SemanticPlanValidator(mapping).validate(ir)
            plan_validated += 1
            ir_validated += 1
            compiled_query = SemanticQueryCompiler(mapping).compile(ir)
            compiled += 1
            if SemanticConsistencyValidator(mapping).validate(ir, compiled_query).accepted:
                semantic_validated += 1
        except (SemanticEngineError, ValueError, KeyError, TypeError) as error:
            code = getattr(error, "code", None)
            unsupported.append(code.value if code is not None else type(error).__name__)
    return OracleCeilingCounts(
        cases=total,
        plan_validated=plan_validated,
        ir_validated=ir_validated,
        compiled=compiled,
        semantic_validated=semantic_validated,
        unsupported=total - compiled,
        unsupported_constructs=tuple(sorted(set(unsupported))),
    )


def _unwrap_alias(node: exp.Expression) -> exp.Expression:
    return node.this if isinstance(node, exp.Alias) else node


def _oracle_source(
    node: exp.Expression,
    mapping: SemanticMappingSnapshot,
    ctes: dict[str, CommonTableExpression],
    database_id: str,
) -> tuple[object, _OracleRelation, dict[str, DerivedRelation]]:
    if isinstance(node, exp.Table):
        name = node.name
        cte = ctes.get(name.lower())
        if cte is not None:
            source: object = CTERelationSource(
                cte_id=cte.cte_id,
                source_id=node.alias_or_name or cte.cte_id,
            )
            return (
                source,
                _OracleRelation(
                    source=source,
                    # Query-local aliases are distinct relation scopes.  The
                    # CTE name identifies the source definition, while the
                    # alias identifies this particular relation instance;
                    # retaining the latter is required to preserve explicit
                    # outer references in nested oracle queries.
                    relation_ref=node.alias_or_name or cte.cte_id,
                    attributes={
                        item.output_name.lower(): item.attribute_id
                        for item in cte.exported_attributes
                    },
                    base_entities=cte.query.population_contract.base_entity_ids,
                ),
                {},
            )
        entity = mapping.entity_for_physical(name)
        source = EntityRelationSource(
            entity_id=entity.entity_id,
            source_id=node.alias_or_name or name,
        )
        attributes = {
            mapping.attribute(item.attribute_id).physical_column.lower(): item.attribute_id
            for item in mapping.attributes
            if item.entity_id == entity.entity_id
        }
        return (
            source,
            _OracleRelation(
                source,
                entity.entity_id,
                attributes,
                entity.entity_id,
                (entity.entity_id,),
            ),
            {},
        )
    if isinstance(node, exp.Subquery):
        relation_id = node.alias_or_name or f"derived_{len(ctes)}"
        nested = _oracle_query(node.this, mapping, database_id=database_id, inherited_ctes=ctes)
        relation = DerivedRelation(
            relation_id=relation_id,
            query=nested.ir,
            exported_attributes=nested.exports,
        )
        source = DerivedRelationSource(relation_id=relation_id)
        return (
            source,
            _OracleRelation(
                source=source,
                relation_ref=relation_id,
                attributes={item.output_name.lower(): item.attribute_id for item in nested.exports},
                base_entities=nested.ir.population_contract.base_entity_ids,
            ),
            {relation_id: relation},
        )
    raise SemanticEngineError(
        SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
        f"unsupported oracle relation {type(node).__name__}",
    )


def _relationship_for_condition(
    condition: exp.Expression | None,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
) -> str:
    keys = _column_pairs(condition)
    if len(keys) != 1:
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
            "FK join requires one equality key",
        )
    left, right = keys[0]
    left_rel = aliases.get(left.table.lower())
    right_rel = aliases.get(right.table.lower())
    if (
        left_rel is None
        or right_rel is None
        or left_rel.entity_id is None
        or right_rel.entity_id is None
    ):
        raise SemanticEngineError(
            SemanticFailureCode.UNKNOWN_RELATIONSHIP, "join scope is not physical"
        )
    left_attr = left_rel.attributes.get(left.name.lower())
    right_attr = right_rel.attributes.get(right.name.lower())
    if left_attr is None or right_attr is None:
        raise SemanticEngineError(
            SemanticFailureCode.UNKNOWN_RELATIONSHIP, "join attribute is unknown"
        )
    for item in mapping.relationships:
        if {item.from_attribute_id, item.to_attribute_id} == {left_attr, right_attr}:
            return item.relationship_id
    raise SemanticEngineError(SemanticFailureCode.UNKNOWN_RELATIONSHIP, "join is not server-mapped")


def _is_physical_join(
    condition: exp.Expression | None, aliases: dict[str, _OracleRelation]
) -> bool:
    pairs = _column_pairs(condition)
    if not pairs:
        return False
    return all(
        (aliases.get(left.table.lower()) is not None)
        and (aliases.get(right.table.lower()) is not None)
        and (aliases[left.table.lower()].entity_id is not None)
        and (aliases[right.table.lower()].entity_id is not None)
        for left, right in pairs
    )


def _join_keys(
    condition: exp.Expression | None,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
) -> tuple[JoinKey, ...]:
    del mapping
    result = []
    for left, right in _column_pairs(condition):
        left_rel = aliases.get(left.table.lower())
        right_rel = aliases.get(right.table.lower())
        if left_rel is None or right_rel is None:
            raise SemanticEngineError(SemanticFailureCode.INVALID_SCOPE_REFERENCE, "join scope")
        left_id = left_rel.attributes.get(left.name.lower())
        right_id = right_rel.attributes.get(right.name.lower())
        if left_id is None or right_id is None:
            raise SemanticEngineError(SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, "join key")
        result.append(
            JoinKey(
                left=AttributeRef(attribute_id=left_id, relation_ref=left_rel.relation_ref),
                right=AttributeRef(attribute_id=right_id, relation_ref=right_rel.relation_ref),
            )
        )
    if not result:
        raise SemanticEngineError(
            SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT, "empty join"
        )
    return tuple(result)


def _column_pairs(condition: exp.Expression | None) -> list[tuple[exp.Column, exp.Column]]:
    if isinstance(condition, exp.And):
        return _column_pairs(condition.this) + _column_pairs(condition.expression)
    if (
        isinstance(condition, exp.EQ)
        and isinstance(condition.this, exp.Column)
        and isinstance(condition.expression, exp.Column)
    ):
        return [(condition.this, condition.expression)]
    return []


def _exports_for_query(ir: SemanticQueryIR) -> tuple[ExportedAttribute, ...]:
    exports: list[ExportedAttribute] = []
    for item in ir.select:
        name = item.alias or f"output_{item.position}"
        exports.append(
            ExportedAttribute(
                attribute_id=f"output:query:{item.position}:{name}",
                output_name=name,
                position=item.position,
            )
        )
    return tuple(exports)


def _output_alias(item: exp.Expression) -> str | None:
    if item.alias:
        return item.alias
    node = _unwrap_alias(item)
    return node.name if isinstance(node, exp.Column) else None


def _column(
    node: exp.Column,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
) -> AttributeRef:
    if node.table:
        relation = aliases.get(node.table.lower())
        if relation is None:
            raise SemanticEngineError(SemanticFailureCode.INVALID_SCOPE_REFERENCE, node.table)
        attribute_id = relation.attributes.get(node.name.lower())
        if attribute_id is None:
            raise SemanticEngineError(SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE, node.name)
        return AttributeRef(attribute_id=attribute_id, relation_ref=relation.relation_ref)
    matches_by_ref: dict[str, _OracleRelation] = {}
    for relation in aliases.values():
        if node.name.lower() in relation.attributes:
            matches_by_ref[relation.relation_ref] = relation
    matches = list(matches_by_ref.values())
    if not matches:
        raise SemanticEngineError(
            SemanticFailureCode.UNKNOWN_SEMANTIC_ATTRIBUTE,
            "unqualified oracle column is not in the current scope",
        )
    deepest = max(item.scope_depth for item in matches)
    matches = [item for item in matches if item.scope_depth == deepest]
    if len(matches) != 1:
        raise SemanticEngineError(
            SemanticFailureCode.AMBIGUOUS_SCOPE_REFERENCE,
            f"oracle column is not uniquely resolvable: {node.name}",
        )
    return AttributeRef(
        attribute_id=matches[0].attributes[node.name.lower()],
        relation_ref=matches[0].relation_ref,
    )


def _expression(
    node: exp.Expression,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
    inherited_ctes: dict[str, CommonTableExpression] | None = None,
) -> Expression:
    if isinstance(node, exp.Alias):
        return _expression(node.this, aliases, mapping, inherited_ctes)
    if isinstance(node, exp.Paren):
        return _expression(node.this, aliases, mapping, inherited_ctes)
    if isinstance(node, exp.Column):
        return _column(node, aliases, mapping)
    if isinstance(node, exp.Not):
        return NotExpression(expression=_expression(node.this, aliases, mapping, inherited_ctes))
    if isinstance(node, exp.Star):
        return StarExpression()
    if isinstance(node, exp.Literal):
        if node.is_string:
            return LiteralExpression(value=str(node.this), value_type="string")
        return LiteralExpression(
            value=node.this, value_type="decimal" if "." in str(node.this) else "integer"
        )
    if isinstance(node, exp.Interval):
        amount = node.this
        unit = node.args.get("unit")
        if not isinstance(amount, exp.Literal) or not isinstance(unit, exp.Var):
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "interval requires a literal amount and unit",
            )
        return IntervalExpression(amount=str(amount.this), unit=str(unit.this))
    if isinstance(node, exp.Null):
        return LiteralExpression(value=None, value_type="null")
    if isinstance(node, exp.Boolean):
        return LiteralExpression(value=node.this == "TRUE", value_type="boolean")
    if isinstance(node, exp.Cast):
        return CastExpression(
            expression=_expression(node.this, aliases, mapping, inherited_ctes),
            target_type=node.args["to"].sql(dialect="postgres"),
        )
    if isinstance(node, exp.Case):
        case_operand = (
            _expression(node.this, aliases, mapping, inherited_ctes)
            if node.this is not None
            else None
        )
        return CaseExpression(
            branches=tuple(
                CaseBranch(
                    when=(
                        BinaryExpression(
                            operator=BinaryOperator.EQ,
                            left=case_operand,
                            right=_expression(item.this, aliases, mapping, inherited_ctes),
                        )
                        if case_operand is not None
                        else _expression(item.this, aliases, mapping, inherited_ctes)
                    ),
                    then=_expression(item.args["true"], aliases, mapping, inherited_ctes),
                )
                for item in node.args.get("ifs", [])
            ),
            default=_expression(node.args["default"], aliases, mapping, inherited_ctes)
            if node.args.get("default")
            else None,
        )
    if isinstance(node, exp.If):
        return CaseExpression(
            branches=(
                CaseBranch(
                    when=_expression(node.this, aliases, mapping, inherited_ctes),
                    then=_expression(node.args["true"], aliases, mapping, inherited_ctes),
                ),
            ),
        )
    if isinstance(node, exp.WithinGroup):
        if not isinstance(node.this, exp.PercentileCont):
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "unsupported ordered aggregate",
            )
        order = node.args.get("expression")
        if not isinstance(order, exp.Order) or len(order.expressions) != 1:
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "ordered aggregate requires one order expression",
            )
        return OrderedAggregateExpression(
            function="PERCENTILE_CONT",
            percentile=_expression(node.this.this, aliases, mapping, inherited_ctes),
            expression=_expression(order.expressions[0].this, aliases, mapping, inherited_ctes),
        )
    if isinstance(node, exp.Filter):
        aggregate = _expression(node.this, aliases, mapping, inherited_ctes)
        if not isinstance(aggregate, AggregateExpression):
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "FILTER requires an aggregate expression",
            )
        predicate = node.args.get("expression")
        if not isinstance(predicate, exp.Where):
            raise SemanticEngineError(
                SemanticFailureCode.UNSUPPORTED_RELATIONAL_CONSTRUCT,
                "FILTER requires a WHERE predicate",
            )
        return aggregate.model_copy(
            update={"filter": _expression(predicate.this, aliases, mapping, inherited_ctes)}
        )
    if isinstance(node, exp.Nullif):
        return FunctionExpression(
            function=SemanticFunction.NULLIF,
            arguments=(
                _expression(node.this, aliases, mapping, inherited_ctes),
                _expression(node.expression, aliases, mapping, inherited_ctes),
            ),
        )
    if isinstance(node, exp.Subquery):
        nested = _oracle_query(
            node.this,
            mapping,
            database_id="schema",
            inherited_ctes=inherited_ctes,
            outer_aliases=aliases,
        )
        return ScalarSubqueryExpression(query=nested.ir)
    if isinstance(node, exp.Between):
        return BetweenExpression(
            expression=_expression(node.this, aliases, mapping, inherited_ctes),
            low=_expression(node.args["low"], aliases, mapping, inherited_ctes),
            high=_expression(node.args["high"], aliases, mapping, inherited_ctes),
        )
    if isinstance(node, exp.In):
        return InExpression(
            expression=_expression(node.this, aliases, mapping, inherited_ctes),
            values=tuple(
                _expression(item, aliases, mapping, inherited_ctes) for item in node.expressions
            ),
            negated=False,
        )
    if isinstance(node, exp.Is):
        return IsNullExpression(expression=_expression(node.this, aliases, mapping, inherited_ctes))
    if isinstance(node, (exp.And, exp.Or)):
        return LogicalExpression(
            operator="AND" if isinstance(node, exp.And) else "OR",
            terms=(
                _expression(node.this, aliases, mapping, inherited_ctes),
                _expression(node.expression, aliases, mapping, inherited_ctes),
            ),
        )
    binary_operators: dict[type[exp.Expression], BinaryOperator] = {
        exp.Add: BinaryOperator.ADD,
        exp.Sub: BinaryOperator.SUBTRACT,
        exp.Mul: BinaryOperator.MULTIPLY,
        exp.Div: BinaryOperator.DIVIDE,
        exp.EQ: BinaryOperator.EQ,
        exp.NEQ: BinaryOperator.NE,
        exp.LT: BinaryOperator.LT,
        exp.LTE: BinaryOperator.LTE,
        exp.GT: BinaryOperator.GT,
        exp.GTE: BinaryOperator.GTE,
        exp.Mod: BinaryOperator.MODULO,
    }
    for expression_type, operator in binary_operators.items():
        if isinstance(node, expression_type):
            return BinaryExpression(
                operator=operator,
                left=_expression(node.this, aliases, mapping, inherited_ctes),
                right=_expression(node.expression, aliases, mapping, inherited_ctes),
            )
    aggregate_map = {
        exp.Count: "COUNT",
        exp.Sum: "SUM",
        exp.Avg: "AVG",
        exp.Min: "MIN",
        exp.Max: "MAX",
    }
    for expression_type, function in aggregate_map.items():
        if isinstance(node, expression_type):
            value = node.this
            distinct = isinstance(value, exp.Distinct)
            value = value.expressions[0] if distinct else value
            return AggregateExpression(
                function=function,
                expression=_expression(value, aliases, mapping, inherited_ctes),
                distinct=distinct,
            )
    if isinstance(node, exp.Window):
        function_node = node.this
        function_name = _function_name(function_node)
        return WindowExpression(
            function=function_name,
            arguments=tuple(
                _expression(item, aliases, mapping, inherited_ctes)
                for item in _function_argument_nodes(function_node)
            ),
            partition_by=tuple(
                _expression(item, aliases, mapping, inherited_ctes)
                for item in node.args.get("partition_by", [])
            ),
            order_by=tuple(
                _window_order(item, aliases, mapping, inherited_ctes)
                for item in (node.args.get("order") or exp.Order()).expressions
            ),
        )
    if isinstance(node, exp.Extract):
        field = node.this.sql(dialect="postgres")
        return FunctionExpression(
            function=SemanticFunction.EXTRACT,
            arguments=(
                LiteralExpression(value=field, value_type="string"),
                _expression(node.expression, aliases, mapping, inherited_ctes),
            ),
        )
    if isinstance(node, exp.Func):
        name = _function_name(node)
        argument_nodes = _function_argument_nodes(node)
        return FunctionExpression(
            function=SemanticFunction(name),
            arguments=tuple(
                _expression(item, aliases, mapping, inherited_ctes) for item in argument_nodes
            ),
        )
    raise SemanticEngineError(
        SemanticFailureCode.COMPILATION_FAILED,
        f"unsupported oracle expression {type(node).__name__}",
    )


def _order(
    node: exp.Expression,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
    output_aliases: dict[str, Expression] | None = None,
    inherited_ctes: dict[str, CommonTableExpression] | None = None,
) -> OrderSpec:
    if isinstance(node.this, exp.Column) and not node.this.table and output_aliases:
        expression = output_aliases.get(node.this.name.lower())
        if expression is not None:
            return OrderSpec(
                expression=expression,
                direction=SortDirection.DESC if node.args.get("desc") else SortDirection.ASC,
            )
    return OrderSpec(
        expression=_expression(node.this, aliases, mapping, inherited_ctes),
        direction=SortDirection.DESC if node.args.get("desc") else SortDirection.ASC,
    )


def _group_expression(
    node: exp.Expression,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
    output_aliases: dict[str, Expression],
    inherited_ctes: dict[str, CommonTableExpression] | None = None,
) -> Expression:
    if isinstance(node, exp.Column) and not node.table:
        expression = output_aliases.get(node.name.lower())
        if expression is not None:
            return expression
    return _expression(node, aliases, mapping, inherited_ctes)


def _window_order(
    node: exp.Expression,
    aliases: dict[str, _OracleRelation],
    mapping: SemanticMappingSnapshot,
    inherited_ctes: dict[str, CommonTableExpression] | None = None,
) -> WindowOrder:
    return WindowOrder(
        expression=_expression(node.this, aliases, mapping, inherited_ctes),
        direction=SortDirection.DESC if node.args.get("desc") else SortDirection.ASC,
    )


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.CurrentDate):
        return "CURRENT_DATE"
    if isinstance(node, exp.CurrentTimestamp):
        return "CURRENT_TIMESTAMP"
    if isinstance(node, exp.Anonymous):
        name = str(node.name).upper()
    else:
        name = type(node).__name__.upper()
    return {
        "POW": "POWER",
        "JSONEXTRACT": "JSON_EXTRACT",
        "JSONEXTRACTSCALAR": "JSON_EXTRACT_SCALAR",
        "ROWNUMBER": "ROW_NUMBER",
        "DENSERANK": "DENSE_RANK",
        "PERCENTRANK": "PERCENT_RANK",
        "FIRSTVALUE": "FIRST_VALUE",
        "LASTVALUE": "LAST_VALUE",
        "NTHVALUE": "NTH_VALUE",
        "CURRENTDATE": "CURRENT_DATE",
        "CURRENTTIMESTAMP": "CURRENT_TIMESTAMP",
    }.get(name, name)


def _function_argument_nodes(node: exp.Func) -> tuple[exp.Expression, ...]:
    """Return SQLGlot function arguments in call order.

    SQLGlot stores the first argument of many named PostgreSQL functions in
    ``this`` and subsequent arguments in named fields such as ``expression``
    or ``decimals``.  Anonymous functions are the exception: their arguments
    already live in ``expressions``.  The previous oracle adapter consulted
    only ``expressions``, which silently dropped arguments from ABS, ROUND,
    COALESCE, casts nested in functions, and similar expressions.

    This is evaluation-only AST adaptation; it does not create production
    mappings or runtime SQL rules.
    """
    if isinstance(node, exp.Anonymous):
        return tuple(node.expressions)
    if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar)):
        path = node.args.get("expression")
        if isinstance(path, exp.JSONPath):
            path_parts = tuple(
                exp.Literal.string(str(item.this))
                for item in path.args.get("expressions", [])
                if isinstance(item, exp.JSONPathKey)
            )
            return (node.this, *path_parts) if node.this is not None else path_parts
    arguments: list[exp.Expression] = []
    first = node.args.get("this")
    if isinstance(first, exp.Expression):
        arguments.append(first)
    for argument_name in ("expression", "decimals"):
        argument = node.args.get(argument_name)
        if isinstance(argument, exp.Expression):
            arguments.append(argument)
    arguments.extend(node.expressions)
    return tuple(arguments)


def _integer(node: exp.Expression | None) -> int | None:
    return int(node.this) if isinstance(node, exp.Literal) and node.is_int else None
