"""Fail-closed generated-result binding for M9.5R.1.

This is a new protocol.  The consumed M9.5 binder is deliberately untouched.
The API accepts generated-side SQL/provenance and predeclared semantic slots
only; reference SQL, result values, and evaluator outcomes are not inputs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlglot import exp, parse_one

from app.sql.models import QueryExecution
from evaluation.m95_binding_protocol import build_schema_semantic_map
from evaluation.result_binding_protocol import SchemaSemanticMap
from evaluation.result_equivalence_contract import (
    BoundResult,
    ResultEquivalenceContract,
    validate_contract,
)

BINDING_PROTOCOL_ID = "benchmark-result-binding-v2"
BINDING_PROTOCOL_VERSION = "benchmark-result-binding-v2"
BINDER_SOURCE_FILES = ("evaluation/result_binding_protocol_v2.py",)


class ProofMethod(StrEnum):
    GOVERNED_PROVENANCE = "GOVERNED_PROVENANCE"
    UNIQUE_DIRECT_LINEAGE = "UNIQUE_DIRECT_LINEAGE"
    UNIQUE_CTE_PASSTHROUGH_LINEAGE = "UNIQUE_CTE_PASSTHROUGH_LINEAGE"
    UNIQUE_DERIVED_PASSTHROUGH_LINEAGE = "UNIQUE_DERIVED_PASSTHROUGH_LINEAGE"
    UNIQUE_EXPRESSION_FINGERPRINT = "UNIQUE_EXPRESSION_FINGERPRINT"
    EXPRESSION_WITH_UNIQUE_LINEAGE = "EXPRESSION_WITH_UNIQUE_LINEAGE"
    UNBOUND = "UNBOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"


class BindingStatus(StrEnum):
    BOUND = "BOUND"
    UNBOUND = "UNBOUND"
    AMBIGUOUS = "AMBIGUOUS"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_SQL_SHAPE = "INVALID_SQL_SHAPE"


class BindingProtocolError(ValueError):
    """Invalid generated-side binding input."""


@dataclass(frozen=True)
class SlotBindingSpecV2:
    slot_id: str
    semantic_identity: str
    expression_fingerprint: tuple[Any, ...] | None = None
    allowed_expression_class: str = "DIRECT_COLUMN"
    required: bool = True


@dataclass(frozen=True)
class ResultBindingSpecV2:
    binding_protocol_version: str
    contract_instance_hash: str
    semantic_catalog_version: str
    schema_semantic_map_version: str
    slot_binding_specs: tuple[SlotBindingSpecV2, ...]


@dataclass(frozen=True)
class GovernedProjectionProvenance:
    projection_index: int
    semantic_identity: str
    contract_slot_id: str | None = None


@dataclass(frozen=True)
class OutputBindingV2:
    projection_index: int
    result_column_index: int
    binding_status: BindingStatus
    proof_method: ProofMethod
    candidate_count: int
    semantic_identity: str | None
    contract_slot_id: str | None
    lineage_candidate_hashes: tuple[str, ...]
    expression_fingerprint_hash: str | None
    bounded_reason: str


@dataclass(frozen=True)
class BindingReportV2:
    binding_protocol_id: str
    binding_protocol_version: str
    binding_spec_hash: str
    schema_map_hash: str
    binder_source_hash: str
    bindings: tuple[OutputBindingV2, ...]


@dataclass(frozen=True)
class BoundQueryExecutionV2:
    result: BoundResult
    report: BindingReportV2


@dataclass(frozen=True)
class _Candidate:
    semantic_identity: str
    relation_instance: str
    proof_method: ProofMethod

    @property
    def identity_key(self) -> tuple[str, str]:
        return self.relation_instance, self.semantic_identity


@dataclass(frozen=True)
class _ScopeRelation:
    instance: str
    relation: str
    outputs: Mapping[str, tuple[_Candidate, ...]] | None = None


def _canonical(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


def binder_v2_source_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    return sha256(b"".join((root / name).read_bytes() for name in BINDER_SOURCE_FILES)).hexdigest()


def binding_spec_v2_hash(spec: ResultBindingSpecV2) -> str:
    return canonical_hash(spec)


def _unwrap(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Alias):
        node = node.this
    return node


def _from_items(select: exp.Select) -> list[exp.Expression]:
    result: list[exp.Expression] = []
    from_clause = select.args.get("from_")
    if from_clause is not None:
        result.append(from_clause.this)
    result.extend(join.this for join in select.args.get("joins", []))
    return result


def _cte_selects(statement: exp.Expression) -> dict[str, exp.Select]:
    with_clause = statement.args.get("with_")
    if with_clause is None:
        return {}
    result: dict[str, exp.Select] = {}
    for cte in with_clause.expressions:
        query = cte.this
        if isinstance(query, exp.Subquery):
            query = query.this
        if isinstance(query, exp.Select):
            result[cte.alias_or_name.lower()] = query
    return result


def _relation_instances(
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    ctes: Mapping[str, exp.Select],
    scope_name: str,
) -> tuple[_ScopeRelation, ...]:
    relations: list[_ScopeRelation] = []
    for item in _from_items(select):
        if isinstance(item, exp.Table):
            name = item.name.lower()
            instance = item.alias_or_name.lower()
            if name in ctes:
                outputs = _passthrough_outputs(ctes[name], schema_map, ctes, f"cte:{name}")
                relations.append(_ScopeRelation(instance, name, outputs))
            else:
                relations.append(_ScopeRelation(instance, name))
        elif isinstance(item, exp.Subquery) and isinstance(item.this, exp.Select):
            name = item.alias_or_name.lower()
            outputs = _passthrough_outputs(item.this, schema_map, ctes, f"derived:{name}")
            relations.append(_ScopeRelation(name, name, outputs))
    return tuple(relations)


def _passthrough_outputs(
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    ctes: Mapping[str, exp.Select],
    scope_name: str,
) -> dict[str, tuple[_Candidate, ...]]:
    outputs: dict[str, tuple[_Candidate, ...]] = {}
    for index, projection in enumerate(select.expressions):
        candidates, _ = _resolve_columns(_unwrap(projection), select, schema_map, ctes, scope_name)
        if len(candidates) == 1:
            outputs[projection.alias_or_name.lower()] = tuple(
                _Candidate(
                    item.semantic_identity, f"{scope_name}:{index}", _passthrough_method(scope_name)
                )
                for item in candidates
            )
    return outputs


def _passthrough_method(scope_name: str) -> ProofMethod:
    return (
        ProofMethod.UNIQUE_CTE_PASSTHROUGH_LINEAGE
        if scope_name.startswith("cte:")
        else ProofMethod.UNIQUE_DERIVED_PASSTHROUGH_LINEAGE
    )


def _resolve_columns(
    node: exp.Expression,
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    ctes: Mapping[str, exp.Select],
    scope_name: str,
) -> tuple[tuple[_Candidate, ...], bool]:
    candidates: list[_Candidate] = []
    relations = _relation_instances(select, schema_map, ctes, scope_name)
    for column in node.find_all(exp.Column):
        name = column.name.lower()
        visible = relations
        if column.table:
            visible = tuple(item for item in relations if item.instance == column.table.lower())
        for relation in visible:
            if relation.outputs is not None:
                candidates.extend(relation.outputs.get(name, ()))
            else:
                for identity in schema_map.resolve(relation.relation, name):
                    candidates.append(
                        _Candidate(identity, relation.instance, ProofMethod.UNIQUE_DIRECT_LINEAGE)
                    )
    ambiguous = len({candidate.identity_key for candidate in candidates}) > 1
    return tuple(candidates), ambiguous


def _fingerprint(
    node: exp.Expression,
    select: exp.Select,
    schema_map: SchemaSemanticMap,
    ctes: Mapping[str, exp.Select],
    scope_name: str,
) -> tuple[tuple[Any, ...] | None, tuple[_Candidate, ...], str | None]:
    node = _unwrap(node)
    if isinstance(node, exp.Column):
        candidates, _ = _resolve_columns(node, select, schema_map, ctes, scope_name)
        if len(candidates) != 1:
            return None, candidates, "column provenance is not unique"
        return ("COLUMN", candidates[0].semantic_identity), candidates, None
    if isinstance(node, exp.Star):
        return ("STAR",), (), None
    if isinstance(node, exp.Literal):
        return ("LITERAL", bool(node.is_string), node.this), (), None
    if isinstance(node, exp.Null):
        return ("NULL",), (), None
    if isinstance(node, exp.Paren):
        return _fingerprint(node.this, select, schema_map, ctes, scope_name)
    if isinstance(node, exp.Cast):
        child, child_leaves, error = _fingerprint(node.this, select, schema_map, ctes, scope_name)
        if child is None:
            return None, child_leaves, error
        return ("CAST", child, node.to.sql(dialect="postgres").lower()), child_leaves, None
    if isinstance(node, exp.Subquery):
        if not isinstance(node.this, exp.Select) or len(node.this.expressions) != 1:
            return None, (), "scalar subquery is not a single projection"
        return _fingerprint(
            node.this.expressions[0], node.this, schema_map, ctes, f"subquery:{scope_name}"
        )
    if isinstance(node, exp.Window):
        child, child_leaves, error = _fingerprint(node.this, select, schema_map, ctes, scope_name)
        if child is None:
            return None, child_leaves, error
        leaves = list(child_leaves)
        partition: list[Any] = []
        for item in node.args.get("partition_by", []):
            value, more, error = _fingerprint(item, select, schema_map, ctes, scope_name)
            leaves.extend(more)
            if value is None:
                return None, tuple(leaves), error
            partition.append(value)
        order = node.args.get("order")
        order_values: list[Any] = []
        if order is not None:
            for item in order.expressions:
                value, more, error = _fingerprint(item.this, select, schema_map, ctes, scope_name)
                leaves.extend(more)
                if value is None:
                    return None, tuple(leaves), error
                order_values.append((item.args.get("desc"), value))
        frame = (
            None
            if node.args.get("spec") is None
            else node.args["spec"].sql(dialect="postgres").lower()
        )
        return ("WINDOW", child, tuple(partition), tuple(order_values), frame), tuple(leaves), None
    if isinstance(node, (exp.Anonymous, exp.Func)):
        sql_name = cast(Any, node).sql_name().upper()
        if sql_name not in {
            "COUNT",
            "SUM",
            "AVG",
            "MIN",
            "MAX",
            "NULLIF",
            "ROW_NUMBER",
            "RANK",
            "DENSE_RANK",
            "COALESCE",
            "CASE",
            "IF",
        }:
            return None, (), f"unsupported function {sql_name}"
    leaf_list: list[_Candidate] = []
    children: list[Any] = []
    for key, value in node.args.items():
        if key in {"alias", "comments", "location"} or value is None:
            continue
        if isinstance(value, exp.Expression):
            child, more, error = _fingerprint(value, select, schema_map, ctes, scope_name)
            leaf_list.extend(more)
            if child is None:
                return None, tuple(leaf_list), error
            children.append((key, child))
        elif isinstance(value, list):
            items: list[Any] = []
            for item in value:
                if not isinstance(item, exp.Expression):
                    continue
                child, more, error = _fingerprint(item, select, schema_map, ctes, scope_name)
                leaf_list.extend(more)
                if child is None:
                    return None, tuple(leaf_list), error
                items.append(child)
            children.append((key, tuple(items)))
        elif key in {"distinct", "side", "kind", "quantifier"}:
            children.append((key, value))
    if node.key.upper() in {"ADD", "MUL"}:
        children = sorted(children, key=repr)
    return (node.key.upper(), tuple(children)), tuple(leaf_list), None


def projection_proofs(
    generated_sql: str,
    schema_map: SchemaSemanticMap,
) -> tuple[tuple[tuple[Any, ...] | None, tuple[_Candidate, ...], ProofMethod, str | None], ...]:
    statement = parse_one(generated_sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise BindingProtocolError("generated SQL has no SELECT projection")
    ctes = _cte_selects(statement)
    result: list[
        tuple[tuple[Any, ...] | None, tuple[_Candidate, ...], ProofMethod, str | None]
    ] = []
    for projection in select.expressions:
        node = _unwrap(projection)
        fingerprint, leaves, error = _fingerprint(node, select, schema_map, ctes, "root")
        if isinstance(node, exp.Column) and len(leaves) == 1:
            method = leaves[0].proof_method
        elif fingerprint is not None and leaves:
            method = ProofMethod.EXPRESSION_WITH_UNIQUE_LINEAGE
        elif fingerprint is not None:
            method = ProofMethod.UNIQUE_EXPRESSION_FINGERPRINT
        else:
            method = (
                ProofMethod.AMBIGUOUS
                if len({x.identity_key for x in leaves}) > 1
                else ProofMethod.UNSUPPORTED
            )
        result.append((fingerprint, leaves, method, error))
    return tuple(result)


def _validate_inputs(
    spec: ResultBindingSpecV2,
    contract: ResultEquivalenceContract,
    schema_map: SchemaSemanticMap,
) -> None:
    if spec.binding_protocol_version != BINDING_PROTOCOL_VERSION:
        raise BindingProtocolError("unsupported V2 binding protocol")
    if spec.schema_semantic_map_version != schema_map.version:
        raise BindingProtocolError("schema map version mismatch")
    if validate_contract(contract):
        raise BindingProtocolError("invalid result contract")
    contract_ids = {slot.slot_id for slot in contract.slots}
    ids = [slot.slot_id for slot in spec.slot_binding_specs]
    if len(ids) != len(set(ids)) or not set(ids) <= contract_ids:
        raise BindingProtocolError("binding spec slot set is invalid")


def bind_generated_result_v2(
    generated_sql: str,
    execution: QueryExecution,
    binding_spec: ResultBindingSpecV2,
    contract: ResultEquivalenceContract,
    schema_map: SchemaSemanticMap | None = None,
    governed_provenance: Sequence[GovernedProjectionProvenance] = (),
) -> BoundQueryExecutionV2:
    """Attach generated-side semantic identity without changing result data."""
    schema_map = build_schema_semantic_map() if schema_map is None else schema_map
    _validate_inputs(binding_spec, contract, schema_map)
    statement = parse_one(generated_sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None or len(select.expressions) != len(execution.columns):
        raise BindingProtocolError("projection and result-column widths differ")
    governed = {item.projection_index: item for item in governed_provenance}
    by_identity: dict[str, list[SlotBindingSpecV2]] = {}
    by_fingerprint: dict[str, list[SlotBindingSpecV2]] = {}
    for item in binding_spec.slot_binding_specs:
        by_identity.setdefault(item.semantic_identity, []).append(item)
        if item.expression_fingerprint is not None:
            by_fingerprint.setdefault(canonical_hash(item.expression_fingerprint), []).append(item)
    reports: list[OutputBindingV2] = []
    bindings: list[str | None] = []
    for index, (fingerprint, leaves, method, error) in enumerate(
        projection_proofs(generated_sql, schema_map)
    ):
        provenance = governed.get(index)
        if provenance is not None:
            matches = by_identity.get(provenance.semantic_identity, [])
            if provenance.contract_slot_id is not None:
                matches = [item for item in matches if item.slot_id == provenance.contract_slot_id]
            if len(matches) == 1:
                reports.append(
                    OutputBindingV2(
                        index,
                        index,
                        BindingStatus.BOUND,
                        ProofMethod.GOVERNED_PROVENANCE,
                        1,
                        provenance.semantic_identity,
                        matches[0].slot_id,
                        (),
                        None,
                        "unique compiler-owned provenance",
                    )
                )
                bindings.append(matches[0].slot_id)
                continue
        leaf_candidates = tuple(dict.fromkeys(candidate.identity_key for candidate in leaves))
        if fingerprint is None and len(leaf_candidates) > 1:
            reports.append(
                OutputBindingV2(
                    index,
                    index,
                    BindingStatus.AMBIGUOUS,
                    ProofMethod.AMBIGUOUS,
                    len(leaf_candidates),
                    None,
                    None,
                    tuple(canonical_hash(item) for item in leaf_candidates),
                    None,
                    "multiple relation-instance provenance candidates",
                )
            )
            bindings.append(None)
            continue
        if fingerprint is None:
            status = BindingStatus.AMBIGUOUS if len(leaves) > 1 else BindingStatus.UNSUPPORTED
            proof = (
                ProofMethod.AMBIGUOUS
                if status is BindingStatus.AMBIGUOUS
                else ProofMethod.UNSUPPORTED
            )
            reports.append(
                OutputBindingV2(
                    index,
                    index,
                    status,
                    proof,
                    len(leaves),
                    None,
                    None,
                    tuple(canonical_hash(item.identity_key) for item in leaves),
                    None,
                    error or "projection semantics are unsupported",
                )
            )
            bindings.append(None)
            continue
        matches = by_fingerprint.get(canonical_hash(fingerprint), [])
        if len(matches) > 1:
            reports.append(
                OutputBindingV2(
                    index,
                    index,
                    BindingStatus.AMBIGUOUS,
                    ProofMethod.AMBIGUOUS,
                    len(matches),
                    None,
                    None,
                    (),
                    canonical_hash(fingerprint),
                    "one expression matches multiple slots",
                )
            )
            bindings.append(None)
            continue
        if len(matches) == 0:
            identity = fingerprint[1] if fingerprint and fingerprint[0] == "COLUMN" else None
            reports.append(
                OutputBindingV2(
                    index,
                    index,
                    BindingStatus.UNBOUND,
                    ProofMethod.UNBOUND,
                    0,
                    identity,
                    None,
                    tuple(canonical_hash(item.identity_key) for item in leaves),
                    canonical_hash(fingerprint),
                    "no uniquely declared contract expression",
                )
            )
            bindings.append(None)
            continue
        item = matches[0]
        reports.append(
            OutputBindingV2(
                index,
                index,
                BindingStatus.BOUND,
                method,
                1,
                item.semantic_identity,
                item.slot_id,
                tuple(canonical_hash(candidate.identity_key) for candidate in leaves),
                canonical_hash(fingerprint),
                "unique lineage and exact slot compatibility",
            )
        )
        bindings.append(item.slot_id)
    slot_indices: dict[str, list[int]] = {}
    for index, report_item in enumerate(reports):
        if (
            report_item.binding_status is BindingStatus.BOUND
            and report_item.contract_slot_id is not None
        ):
            slot_indices.setdefault(report_item.contract_slot_id, []).append(index)
    for indices in slot_indices.values():
        if len(indices) > 1:
            for index in indices:
                reports[index] = replace(
                    reports[index],
                    binding_status=BindingStatus.AMBIGUOUS,
                    proof_method=ProofMethod.AMBIGUOUS,
                    candidate_count=len(indices),
                    semantic_identity=None,
                    contract_slot_id=None,
                    bounded_reason="multiple generated projections compete for one slot",
                )
                bindings[index] = None
    rows = tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows)
    result = BoundResult(
        tuple(execution.columns), tuple(bindings), rows, contract.scalar_or_tabular
    )
    report = BindingReportV2(
        BINDING_PROTOCOL_ID,
        BINDING_PROTOCOL_VERSION,
        binding_spec_v2_hash(binding_spec),
        canonical_hash(schema_map),
        binder_v2_source_hash(),
        tuple(reports),
    )
    return BoundQueryExecutionV2(result, report)


def empty_execution(sql: str) -> QueryExecution:
    """Build a deterministic empty execution for binder-only fixtures."""
    statement = parse_one(sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise BindingProtocolError("SQL has no SELECT projection")
    return QueryExecution(
        plan_id=uuid4(),
        columns=[item.alias_or_name for item in select.expressions],
        rows=[],
        latency_ms=0.0,
    )
