"""Bounded, deterministic offline compatibility evidence for M11.0R.

This module is deliberately evaluator-side only.  It compares a target SQL
artifact with an immutable verified-memory SQL artifact using a small set of
decision-bearing SQL dimensions.  It does not import application code, scores,
correctness labels, partitions, or causal labels, and it never performs
retrieval or database work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlglot import exp, parse_one


class CompatibilityState(StrEnum):
    PROVEN_COMPATIBLE = "PROVEN_COMPATIBLE"
    PROVEN_INCOMPATIBLE = "PROVEN_INCOMPATIBLE"
    UNKNOWN = "UNKNOWN"


class DimensionRelation(StrEnum):
    MATCH = "MATCH"
    NON_CONFLICTING = "NON_CONFLICTING"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class UnknownReason(StrEnum):
    PARSE_FAILURE = "PARSE_FAILURE"
    INSUFFICIENT_TARGET_EVIDENCE = "INSUFFICIENT_TARGET_EVIDENCE"
    INSUFFICIENT_MEMORY_EVIDENCE = "INSUFFICIENT_MEMORY_EVIDENCE"
    UNSUPPORTED_EXPRESSION = "UNSUPPORTED_EXPRESSION"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"
    PARTIAL_ALIGNMENT_ONLY = "PARTIAL_ALIGNMENT_ONLY"
    MIXED_CONTEXT = "MIXED_CONTEXT"
    MISSING_REQUIRED_DIMENSION = "MISSING_REQUIRED_DIMENSION"


DIMENSIONS = (
    "SOURCE_RELATION",
    "JOIN_RELATIONSHIP",
    "RESULT_GRAIN",
    "AGGREGATION",
    "FORMULA",
    "FILTER",
    "TEMPORAL",
    "WINDOW_SEMANTICS",
    "PROJECTION_ROLE",
    "ORDERING",
    "TOP_N",
    "DISTINCT_SET",
)


@dataclass(frozen=True)
class SemanticSignature:
    """Normalized bounded semantic evidence, never a runtime feature."""

    source_relation: tuple[str, ...]
    join_relationship: tuple[str, ...]
    result_grain: tuple[str, ...]
    aggregation: tuple[str, ...]
    formula: tuple[str, ...]
    filter: tuple[str, ...]
    temporal: tuple[str, ...]
    window_semantics: tuple[str, ...]
    projection_role: tuple[str, ...]
    ordering: tuple[str, ...]
    top_n: tuple[str, ...]
    distinct_set: tuple[str, ...]


@dataclass(frozen=True)
class EntryCompatibilityEvidence:
    state: CompatibilityState
    relations: tuple[tuple[str, DimensionRelation], ...]
    positive_dimensions: tuple[str, ...] = ()
    conflict_dimensions: tuple[str, ...] = ()
    reason: str | None = None
    evidence_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextCompatibilityEvidence:
    state: CompatibilityState
    entries: tuple[EntryCompatibilityEvidence, ...]
    reason: str | None = None


def _canonical_expression(node: exp.Expression, aliases: dict[str, str]) -> str:
    """Render an expression after local alias normalization and alias removal."""

    value = node.copy()
    for column in value.find_all(exp.Column):
        if column.table:
            column.set(
                "table",
                exp.Identifier(this=aliases.get(column.table.lower(), column.table.lower())),
            )
        column.set("this", exp.Identifier(this=column.name.lower()))
    for table in value.find_all(exp.Table):
        table.set("this", exp.Identifier(this=table.name.lower()))
        table.set("alias", None)
    for alias in value.find_all(exp.Alias):
        alias.set("alias", None)
    return value.sql(dialect="postgres", pretty=False).strip().lower()


def _unsupported_reason(tree: exp.Expression) -> str | None:
    if list(tree.find_all(exp.CTE)):
        return UnknownReason.UNSUPPORTED_EXPRESSION.value
    if list(tree.find_all(exp.Subquery)):
        return UnknownReason.UNSUPPORTED_EXPRESSION.value
    if list(tree.find_all(exp.Anonymous)):
        return UnknownReason.UNSUPPORTED_EXPRESSION.value
    table_names = [table.name.lower() for table in tree.find_all(exp.Table)]
    if len(table_names) != len(set(table_names)):
        return UnknownReason.AMBIGUOUS_SCOPE.value
    if not isinstance(tree, exp.Select):
        return UnknownReason.UNSUPPORTED_EXPRESSION.value
    return None


def _parse_signature(sql: str | None) -> tuple[SemanticSignature | None, str | None]:
    if not isinstance(sql, str) or not sql.strip():
        return None, UnknownReason.PARSE_FAILURE.value
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", "", sql)
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return None, UnknownReason.PARSE_FAILURE.value
    reason = _unsupported_reason(tree)
    if reason is not None:
        return None, reason

    tables = list(tree.find_all(exp.Table))
    aliases = {table.alias_or_name.lower(): f"r{index}" for index, table in enumerate(tables)}
    for table in tables:
        table.set("alias", None)

    root = tree
    aggregate = tuple(
        sorted(_canonical_expression(function, aliases) for function in tree.find_all(exp.AggFunc))
    )
    formula_nodes = [
        node for node in tree.walk() if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div))
    ]
    formula = tuple(sorted(_canonical_expression(node, aliases) for node in formula_nodes))
    where = root.args.get("where")
    filter_values = (
        (_canonical_expression(where.this, aliases),) if isinstance(where, exp.Where) else ()
    )
    temporal_nodes = [
        node for node in tree.walk() if isinstance(node, (exp.TimestampTrunc, exp.Extract))
    ]
    temporal = tuple(sorted(_canonical_expression(node, aliases) for node in temporal_nodes))
    windows = tuple(
        sorted(_canonical_expression(node, aliases) for node in tree.find_all(exp.Window))
    )
    projection = tuple(
        _canonical_expression(expression, aliases) for expression in tree.expressions
    )
    ordering = tuple(
        sorted(_canonical_expression(node, aliases) for node in tree.find_all(exp.Ordered))
    )
    group = root.args.get("group")
    grain = (
        tuple(sorted(_canonical_expression(node, aliases) for node in group.expressions))
        if isinstance(group, exp.Group)
        else ()
    )
    limit = root.args.get("limit")
    top_n = (_canonical_expression(limit, aliases),) if isinstance(limit, exp.Limit) else ()
    join_expressions = [
        join.args["on"]
        for join in tree.find_all(exp.Join)
        if isinstance(join.args.get("on"), exp.Expression)
    ]
    joins = tuple(sorted(_canonical_expression(node, aliases) for node in join_expressions))
    sources = tuple(sorted(table.name.lower() for table in tables))
    distinct = bool(list(tree.find_all(exp.Distinct)))
    set_operations = tuple(
        sorted(_canonical_expression(node, aliases) for node in tree.find_all(exp.SetOperation))
    )
    return (
        SemanticSignature(
            source_relation=sources,
            join_relationship=joins,
            result_grain=grain,
            aggregation=aggregate,
            formula=formula,
            filter=filter_values,
            temporal=temporal,
            window_semantics=windows,
            projection_role=projection,
            ordering=ordering,
            top_n=top_n,
            distinct_set=(str(distinct), *set_operations),
        ),
        None,
    )


_FIELD_BY_DIMENSION = {
    "SOURCE_RELATION": "source_relation",
    "JOIN_RELATIONSHIP": "join_relationship",
    "RESULT_GRAIN": "result_grain",
    "AGGREGATION": "aggregation",
    "FORMULA": "formula",
    "FILTER": "filter",
    "TEMPORAL": "temporal",
    "WINDOW_SEMANTICS": "window_semantics",
    "PROJECTION_ROLE": "projection_role",
    "ORDERING": "ordering",
    "TOP_N": "top_n",
    "DISTINCT_SET": "distinct_set",
}


def compare_signatures(
    target: SemanticSignature, memory: SemanticSignature
) -> tuple[tuple[str, DimensionRelation], ...]:
    """Compare all bounded dimensions in a stable manifest order."""

    relations: list[tuple[str, DimensionRelation]] = []
    for dimension in DIMENSIONS:
        target_value = getattr(target, _FIELD_BY_DIMENSION[dimension])
        memory_value = getattr(memory, _FIELD_BY_DIMENSION[dimension])
        if not target_value and not memory_value:
            relation = DimensionRelation.NOT_APPLICABLE
        elif target_value == memory_value:
            relation = DimensionRelation.MATCH
        elif not target_value or not memory_value:
            relation = DimensionRelation.CONFLICT
        else:
            relation = DimensionRelation.CONFLICT
        relations.append((dimension, relation))
    return tuple(relations)


def _evidence_hashes(
    target: SemanticSignature,
    memory: SemanticSignature,
    relations: tuple[tuple[str, DimensionRelation], ...],
) -> tuple[str, ...]:
    import hashlib
    import json

    payload = {"target": target.__dict__, "memory": memory.__dict__, "relations": relations}
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return (hashlib.sha256(encoded).hexdigest(),)


def classify_entry(target_sql: str | None, memory_sql: str | None) -> EntryCompatibilityEvidence:
    """Classify one target/example pair without outcome or retrieval inputs."""

    target, target_reason = _parse_signature(target_sql)
    memory, memory_reason = _parse_signature(memory_sql)
    if target is None or memory is None:
        reason = target_reason if target is None else memory_reason
        return EntryCompatibilityEvidence(
            state=CompatibilityState.UNKNOWN,
            relations=(),
            reason=reason or UnknownReason.MISSING_REQUIRED_DIMENSION.value,
        )
    relations = compare_signatures(target, memory)
    conflicts = tuple(
        name for name, relation in relations if relation is DimensionRelation.CONFLICT
    )
    if conflicts:
        return EntryCompatibilityEvidence(
            state=CompatibilityState.PROVEN_INCOMPATIBLE,
            relations=relations,
            conflict_dimensions=conflicts,
            evidence_hashes=_evidence_hashes(target, memory, relations),
        )
    unknowns = tuple(name for name, relation in relations if relation is DimensionRelation.UNKNOWN)
    if unknowns:
        return EntryCompatibilityEvidence(
            state=CompatibilityState.UNKNOWN,
            relations=relations,
            reason=UnknownReason.PARTIAL_ALIGNMENT_ONLY.value,
            evidence_hashes=_evidence_hashes(target, memory, relations),
        )
    matches = tuple(name for name, relation in relations if relation is DimensionRelation.MATCH)
    if not matches:
        return EntryCompatibilityEvidence(
            state=CompatibilityState.UNKNOWN,
            relations=relations,
            reason=UnknownReason.INSUFFICIENT_TARGET_EVIDENCE.value,
            evidence_hashes=_evidence_hashes(target, memory, relations),
        )
    return EntryCompatibilityEvidence(
        state=CompatibilityState.PROVEN_COMPATIBLE,
        relations=relations,
        positive_dimensions=matches,
        evidence_hashes=_evidence_hashes(target, memory, relations),
    )


def classify_context(
    target_sql: str | None, memory_sqls: tuple[str | None, ...]
) -> ContextCompatibilityEvidence:
    """Apply the frozen conservative context aggregation rule."""

    entries = tuple(classify_entry(target_sql, memory_sql) for memory_sql in memory_sqls)
    if any(entry.state is CompatibilityState.PROVEN_INCOMPATIBLE for entry in entries):
        return ContextCompatibilityEvidence(
            state=CompatibilityState.PROVEN_INCOMPATIBLE,
            entries=entries,
            reason="incompatible_entry_dominates_context",
        )
    if entries and all(entry.state is CompatibilityState.PROVEN_COMPATIBLE for entry in entries):
        return ContextCompatibilityEvidence(
            state=CompatibilityState.PROVEN_COMPATIBLE,
            entries=entries,
        )
    return ContextCompatibilityEvidence(
        state=CompatibilityState.UNKNOWN,
        entries=entries,
        reason=UnknownReason.MIXED_CONTEXT.value
        if entries
        else UnknownReason.MISSING_REQUIRED_DIMENSION.value,
    )


def result_dict(
    evidence: EntryCompatibilityEvidence | ContextCompatibilityEvidence,
) -> dict[str, Any]:
    """Serialize evidence with deterministic ordering for bounded artifacts."""

    if isinstance(evidence, EntryCompatibilityEvidence):
        return {
            "state": evidence.state.value,
            "relations": [[name, relation.value] for name, relation in evidence.relations],
            "positive_dimensions": list(evidence.positive_dimensions),
            "conflict_dimensions": list(evidence.conflict_dimensions),
            "reason": evidence.reason,
            "evidence_hashes": list(evidence.evidence_hashes),
        }
    return {
        "state": evidence.state.value,
        "entries": [result_dict(entry) for entry in evidence.entries],
        "reason": evidence.reason,
    }


__all__ = [
    "CompatibilityState",
    "ContextCompatibilityEvidence",
    "DIMENSIONS",
    "DimensionRelation",
    "EntryCompatibilityEvidence",
    "SemanticSignature",
    "UnknownReason",
    "classify_context",
    "classify_entry",
    "compare_signatures",
    "result_dict",
]
