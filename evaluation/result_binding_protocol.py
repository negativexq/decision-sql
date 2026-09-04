"""Conservative SQL-projection binding for the M9.5 protocol.

This module is deliberately separate from the result-equivalence comparator.
It identifies the semantic identity of *generated* SELECT expressions.  It
does not receive a reference query, reference rows, expected labels, or an
evaluator, and it never changes result rows.

The protocol is intentionally bounded.  A projection that cannot be resolved
from the generated SQL and predeclared server-owned metadata is left unbound;
it is never guessed from aliases or values.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from hashlib import sha256
from collections.abc import Iterable
from typing import Any

from sqlglot import exp, parse_one

from app.sql.models import QueryExecution
from evaluation.result_equivalence_contract import (
    BoundResult,
    ResultEquivalenceContract,
    validate_contract,
)

BINDING_PROTOCOL_VERSION = "benchmark-result-binding-v1"
BINDER_SOURCE_FILES = ("evaluation/result_binding_protocol.py",)


class BindingMethod(StrEnum):
    GOVERNED_PROVENANCE = "GOVERNED_PROVENANCE"
    DIRECT_COLUMN_LINEAGE = "DIRECT_COLUMN_LINEAGE"
    SEMANTIC_EXPRESSION_FINGERPRINT = "SEMANTIC_EXPRESSION_FINGERPRINT"
    LINEAGE_PLUS_ALIAS_CORROBORATION = "LINEAGE_PLUS_ALIAS_CORROBORATION"
    UNBOUND = "UNBOUND"
    AMBIGUOUS = "AMBIGUOUS"


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    UNBOUND = "UNBOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED_EXPRESSION = "UNSUPPORTED_EXPRESSION"
    INVALID_SQL_SHAPE = "INVALID_SQL_SHAPE"


class BindingKind(StrEnum):
    DIRECT_COLUMN = "DIRECT_COLUMN"
    EXPRESSION = "EXPRESSION"
    GOVERNED_PROVENANCE = "GOVERNED_PROVENANCE"


class BindingProtocolError(ValueError):
    """Fail-closed protocol/configuration error."""


@dataclass(frozen=True)
class SchemaSemanticEntry:
    relation: str
    column: str
    semantic_identity: str


@dataclass(frozen=True)
class SchemaSemanticMap:
    version: str
    entries: tuple[SchemaSemanticEntry, ...]

    def resolve(self, relation: str, column: str) -> tuple[str, ...]:
        relation = relation.lower()
        column = column.lower()
        return tuple(
            entry.semantic_identity
            for entry in self.entries
            if entry.relation.lower() == relation and entry.column.lower() == column
        )

    def relations_for_column(self, column: str) -> tuple[str, ...]:
        column = column.lower()
        return tuple(
            entry.relation
            for entry in self.entries
            if entry.column.lower() == column
        )


@dataclass(frozen=True)
class SlotBindingSpec:
    slot_id: str
    semantic_identity: str
    binding_kind: BindingKind
    expression_fingerprint: str
    allowed_expression_class: str
    source_semantic_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResultBindingSpec:
    binding_protocol_version: str
    contract_instance_hash: str
    semantic_catalog_version: str
    schema_semantic_map_version: str
    slot_binding_specs: tuple[SlotBindingSpec, ...]


@dataclass(frozen=True)
class OutputBinding:
    projection_index: int
    result_column_index: int
    semantic_identity: str | None
    contract_slot_id: str | None
    binding_method: BindingMethod
    expression_fingerprint_hash: str | None
    lineage_semantic_ids: tuple[str, ...]
    status: BindingStatus
    ambiguity_count: int
    bounded_reason: str


@dataclass(frozen=True)
class BindingReport:
    binding_protocol_version: str
    binding_spec_hash: str
    schema_semantic_map_hash: str
    binder_source_hash: str
    bindings: tuple[OutputBinding, ...]

    @property
    def bound_count(self) -> int:
        return sum(binding.status is BindingStatus.BOUND for binding in self.bindings)

    @property
    def unbound_count(self) -> int:
        return sum(binding.status is BindingStatus.UNBOUND for binding in self.bindings)

    @property
    def ambiguous_count(self) -> int:
        return sum(binding.status is BindingStatus.AMBIGUOUS for binding in self.bindings)

    @property
    def unsupported_count(self) -> int:
        return sum(
            binding.status is BindingStatus.UNSUPPORTED_EXPRESSION for binding in self.bindings
        )


@dataclass(frozen=True)
class BoundQueryExecution:
    result: BoundResult
    report: BindingReport


def _canonical(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def binding_spec_hash(spec: ResultBindingSpec) -> str:
    return canonical_hash(spec)


def schema_semantic_map_hash(schema_map: SchemaSemanticMap) -> str:
    return canonical_hash(schema_map)


def binding_taxonomy_hash() -> str:
    return canonical_hash(
        {
            "methods": [method.value for method in BindingMethod],
            "statuses": [status.value for status in BindingStatus],
            "kinds": [kind.value for kind in BindingKind],
        }
    )


def binder_source_hash() -> str:
    """Hash the semantic binder source only, excluding fixtures and run data."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return sha256(
        b"".join((root / relative).read_bytes() for relative in BINDER_SOURCE_FILES)
    ).hexdigest()


def _identifier(value: exp.Expression | None) -> str:
    if isinstance(value, exp.Identifier):
        return value.name.lower()
    return str(value or "").lower()


def _table_aliases(select: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        relation = table.name.lower()
        aliases[relation] = relation
        aliases[table.alias_or_name.lower()] = relation
    return aliases


def _cte_selects(statement: exp.Expression) -> dict[str, exp.Select]:
    with_clause = statement.args.get("with_")
    if with_clause is None:
        return {}
    result: dict[str, exp.Select] = {}
    for cte in with_clause.find_all(exp.CTE):
        alias = cte.alias_or_name.lower()
        query = cte.this
        if isinstance(query, exp.Subquery):
            query = query.this
        if isinstance(query, exp.Select):
            result[alias] = query
    return result


def _projection_select(statement: exp.Expression) -> exp.Select:
    if isinstance(statement, exp.Select):
        return statement
    select = statement.find(exp.Select)
    if select is None:
        raise BindingProtocolError("generated SQL has no SELECT projection")
    return select


def _column_candidates(
    column: exp.Column,
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    cte_fingerprints: dict[str, dict[str, tuple[Any, ...]]] | None = None,
) -> tuple[Any, ...]:
    name = column.name.lower()
    table = column.table.lower()
    aliases = _table_aliases(select)
    if table in (cte_fingerprints or {}):
        return (cte_fingerprints[table].get(name),)
    if table:
        relation = aliases.get(table, table)
        values = schema_map.resolve(relation, name)
        return tuple(("COLUMN", value) for value in values)
    relations = set(aliases.values())
    values: list[Any] = []
    for relation in sorted(relations):
        values.extend(("COLUMN", value) for value in schema_map.resolve(relation, name))
    return tuple(values)


def _node_fingerprint(
    node: exp.Expression,
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    cte_fingerprints: dict[str, dict[str, tuple[Any, ...]]] | None = None,
) -> tuple[Any, ...] | None:
    if isinstance(node, exp.Alias):
        return _node_fingerprint(node.this, select, schema_map, cte_fingerprints)
    if isinstance(node, exp.Column):
        candidates = _column_candidates(node, select, schema_map, cte_fingerprints)
        if len(candidates) != 1 or candidates[0] is None:
            return None
        return candidates[0]
    if isinstance(node, exp.Literal):
        return ("LITERAL", node.is_string, node.this)
    if isinstance(node, exp.Null):
        return ("NULL",)
    if isinstance(node, exp.Star):
        return None
    if isinstance(node, exp.Paren):
        return _node_fingerprint(node.this, select, schema_map, cte_fingerprints)
    if isinstance(node, exp.Cast):
        child = _node_fingerprint(node.this, select, schema_map, cte_fingerprints)
        return None if child is None else ("CAST", child, node.to.sql(dialect="postgres"))
    if isinstance(node, exp.Window):
        child = _node_fingerprint(node.this, select, schema_map, cte_fingerprints)
        if child is None:
            return None
        partition = tuple(
            _node_fingerprint(item, select, schema_map, cte_fingerprints)
            for item in node.args.get("partition_by", [])
        )
        order = node.args.get("order")
        order_items = () if order is None else tuple(
            _node_fingerprint(item.this, select, schema_map, cte_fingerprints)
            for item in order.expressions
        )
        if any(item is None for item in (*partition, *order_items)):
            return None
        spec = node.args.get("spec")
        frame = None if spec is None else spec.sql(dialect="postgres")
        return ("WINDOW", child, partition, order_items, frame)
    if isinstance(node, exp.Select):
        return None
    if isinstance(node, exp.Subquery):
        inner = _projection_select(node.this)
        if len(inner.expressions) != 1:
            return None
        return _node_fingerprint(inner.expressions[0], inner, schema_map, cte_fingerprints)

    children: list[Any] = []
    for key, value in node.args.items():
        if key in {"alias", "comments", "location"}:
            continue
        if isinstance(value, exp.Expression):
            child = _node_fingerprint(value, select, schema_map, cte_fingerprints)
            if child is None:
                return None
            children.append((key, child))
        elif isinstance(value, list):
            items: list[Any] = []
            for item in value:
                if isinstance(item, exp.Expression):
                    child = _node_fingerprint(item, select, schema_map, cte_fingerprints)
                    if child is None:
                        return None
                    items.append(child)
            children.append((key, tuple(items)))
        elif value is not None and key in {"distinct", "big_int", "side", "kind"}:
            children.append((key, value))
    key = node.key.upper()
    if key in {"ADD", "MUL"}:
        children = sorted(children, key=repr)
    return (key, tuple(children))


def expression_fingerprint(
    expression: exp.Expression,
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    cte_fingerprints: dict[str, dict[str, tuple[Any, ...]]] | None = None,
) -> tuple[Any, ...] | None:
    """Return a semantic AST fingerprint, or ``None`` when unsupported/ambiguous."""
    return _node_fingerprint(expression, select, schema_map, cte_fingerprints)


def _build_cte_fingerprints(
    statement: exp.Expression, schema_map: SchemaSemanticMap
) -> dict[str, dict[str, tuple[Any, ...]]]:
    fingerprints: dict[str, dict[str, tuple[Any, ...]]] = {}
    for name, select in _cte_selects(statement).items():
        values: dict[str, tuple[Any, ...]] = {}
        for projection in select.expressions:
            alias = projection.alias_or_name.lower()
            fingerprint = expression_fingerprint(projection, select, schema_map, fingerprints)
            if fingerprint is not None:
                values[alias] = fingerprint
        fingerprints[name] = values
    for subquery in statement.find_all(exp.Subquery):
        alias = subquery.alias_or_name.lower()
        if not alias or alias in fingerprints or not isinstance(subquery.this, exp.Select):
            continue
        values = {}
        for projection in subquery.this.expressions:
            name = projection.alias_or_name.lower()
            fingerprint = expression_fingerprint(
                projection, subquery.this, schema_map, fingerprints
            )
            if fingerprint is not None:
                values[name] = fingerprint
        fingerprints[alias] = values
    return fingerprints


def projection_fingerprints(
    generated_sql: str, schema_map: SchemaSemanticMap
) -> tuple[tuple[Any, ...] | None, ...]:
    statement = parse_one(generated_sql, read="postgres")
    select = _projection_select(statement)
    ctes = _build_cte_fingerprints(statement, schema_map)
    return tuple(
        expression_fingerprint(expression, select, schema_map, ctes)
        for expression in select.expressions
    )


def _validate_spec(
    spec: ResultBindingSpec, contract: ResultEquivalenceContract, schema_map: SchemaSemanticMap
) -> None:
    if spec.binding_protocol_version != BINDING_PROTOCOL_VERSION:
        raise BindingProtocolError("unsupported binding protocol version")
    if spec.schema_semantic_map_version != schema_map.version:
        raise BindingProtocolError("schema semantic map version mismatch")
    if validate_contract(contract):
        raise BindingProtocolError("result contract is invalid")
    contract_ids = {slot.slot_id for slot in contract.slots}
    spec_ids = [item.slot_id for item in spec.slot_binding_specs]
    if len(spec_ids) != len(set(spec_ids)) or not set(spec_ids) <= contract_ids:
        raise BindingProtocolError("binding spec contains unknown or duplicate slot IDs")
    if any(not item.expression_fingerprint for item in spec.slot_binding_specs):
        raise BindingProtocolError("binding specs require explicit expression fingerprints")


def _rows_from_execution(execution: QueryExecution) -> tuple[tuple[Any, ...], ...]:
    columns = tuple(execution.columns)
    if len(columns) != len(set(columns)):
        raise BindingProtocolError("duplicate result column labels are not alignable")
    return tuple(tuple(row.get(column) for column in columns) for row in execution.rows)


def bind_generated_result(
    generated_sql: str,
    execution: QueryExecution,
    binding_spec: ResultBindingSpec,
    contract: ResultEquivalenceContract,
    schema_map: SchemaSemanticMap,
) -> BoundQueryExecution:
    """Bind generated result columns without any reference/evaluator input.

    The contract is used only to validate slot IDs and to select the result
    scalar/tabular marker.  No result values are inspected for binding.
    """
    _validate_spec(binding_spec, contract, schema_map)
    statement = parse_one(generated_sql, read="postgres")
    select = _projection_select(statement)
    if len(select.expressions) != len(execution.columns):
        raise BindingProtocolError("SELECT projection and result-column widths differ")
    ctes = _build_cte_fingerprints(statement, schema_map)
    specs_by_fingerprint: dict[str, list[SlotBindingSpec]] = {}
    for item in binding_spec.slot_binding_specs:
        specs_by_fingerprint.setdefault(item.expression_fingerprint, []).append(item)

    output_bindings: list[OutputBinding] = []
    semantic_bindings: list[str | None] = []
    for index, expression in enumerate(select.expressions):
        fingerprint = expression_fingerprint(expression, select, schema_map, ctes)
        fingerprint_hash = None if fingerprint is None else canonical_hash(fingerprint)
        matches = [] if fingerprint is None else specs_by_fingerprint.get(
            canonical_hash(fingerprint), []
        )
        lineage = tuple(
            value[1]
            for value in (fingerprint or ())
            if isinstance(value, tuple) and len(value) == 2 and value[0] == "COLUMN"
        )
        if fingerprint is None:
            output_bindings.append(
                OutputBinding(
                    index,
                    index,
                    None,
                    None,
                    BindingMethod.UNBOUND,
                    None,
                    lineage,
                    BindingStatus.UNSUPPORTED_EXPRESSION,
                    0,
                    "projection lineage or expression semantics are unresolved",
                )
            )
            semantic_bindings.append(None)
        elif len(matches) > 1:
            output_bindings.append(
                OutputBinding(
                    index,
                    index,
                    None,
                    None,
                    BindingMethod.AMBIGUOUS,
                    fingerprint_hash,
                    lineage,
                    BindingStatus.AMBIGUOUS,
                    len(matches),
                    "one generated expression matches multiple exclusive slots",
                )
            )
            semantic_bindings.append(None)
        elif len(matches) == 1:
            item = matches[0]
            method = (
                BindingMethod.DIRECT_COLUMN_LINEAGE
                if item.binding_kind is BindingKind.DIRECT_COLUMN
                else BindingMethod.SEMANTIC_EXPRESSION_FINGERPRINT
            )
            output_bindings.append(
                OutputBinding(
                    index,
                    index,
                    item.semantic_identity,
                    item.slot_id,
                    method,
                    fingerprint_hash,
                    lineage,
                    BindingStatus.BOUND,
                    1,
                    "explicit predeclared fingerprint match",
                )
            )
            semantic_bindings.append(item.slot_id)
        else:
            output_bindings.append(
                OutputBinding(
                    index,
                    index,
                    f"expression:{fingerprint_hash}",
                    None,
                    BindingMethod.SEMANTIC_EXPRESSION_FINGERPRINT,
                    fingerprint_hash,
                    lineage,
                    BindingStatus.BOUND,
                    0,
                    "resolved generated expression is not declared by the contract",
                )
            )
            semantic_bindings.append(f"expression:{fingerprint_hash}")

    bound_slots = [
        binding.contract_slot_id
        for binding in output_bindings
        if binding.status is BindingStatus.BOUND and binding.contract_slot_id is not None
    ]
    duplicate_slots = {slot for slot in bound_slots if bound_slots.count(slot) > 1}
    if duplicate_slots:
        output_bindings = [
            replace(
                binding,
                semantic_identity=None,
                contract_slot_id=None,
                binding_method=BindingMethod.AMBIGUOUS,
                status=BindingStatus.AMBIGUOUS,
                ambiguity_count=2,
                bounded_reason="multiple generated projections bind the same exclusive slot",
            )
            if binding.contract_slot_id in duplicate_slots
            else binding
            for binding in output_bindings
        ]
        semantic_bindings = [
            None if binding.contract_slot_id in duplicate_slots else value
            for binding, value in zip(output_bindings, semantic_bindings, strict=True)
        ]

    report = BindingReport(
        BINDING_PROTOCOL_VERSION,
        binding_spec_hash(binding_spec),
        schema_semantic_map_hash(schema_map),
        binder_source_hash(),
        tuple(output_bindings),
    )
    result = BoundResult(
        tuple(execution.columns),
        tuple(semantic_bindings),
        _rows_from_execution(execution),
        contract.scalar_or_tabular,
    )
    return BoundQueryExecution(result, report)


def make_schema_semantic_map(entries: Iterable[tuple[str, str, str]]) -> SchemaSemanticMap:
    return SchemaSemanticMap(
        "decision-sql-schema-semantic-map-v1",
        tuple(SchemaSemanticEntry(*entry) for entry in entries),
    )
