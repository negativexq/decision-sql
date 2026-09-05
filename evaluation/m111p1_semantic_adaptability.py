"""Offline semantic-relation and demonstration-adaptability contracts.

The two contracts deliberately answer different questions.  They operate only
on SQL artifacts and bounded SQLGlot structure; they never inspect outcomes,
retrieval data, partitions, providers, or databases.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any

from sqlglot import exp, parse_one

SEMANTIC_RELATION_CONTRACT_ID = "decision-sql-query-semantic-relation-v1"
ADAPTABILITY_CONTRACT_ID = "decision-sql-demonstration-adaptability-v1"
CONTRACT_VERSION = 1


class SemanticRelationState(StrEnum):
    PROVEN_EQUIVALENT = "PROVEN_EQUIVALENT"
    PROVEN_MATERIAL_CONFLICT = "PROVEN_MATERIAL_CONFLICT"
    UNKNOWN = "UNKNOWN"


class AdaptabilityState(StrEnum):
    PROVEN_ADAPTABLE = "PROVEN_ADAPTABLE"
    PROVEN_NON_ADAPTABLE = "PROVEN_NON_ADAPTABLE"
    UNKNOWN = "UNKNOWN"


class ProfileUnknownReason(StrEnum):
    PARSE_FAILURE = "PARSE_FAILURE"
    UNSUPPORTED_EXPRESSION = "UNSUPPORTED_EXPRESSION"
    AMBIGUOUS_SCOPE = "AMBIGUOUS_SCOPE"


SEMANTIC_DIMENSIONS = (
    "sources",
    "joins",
    "projection",
    "aggregation",
    "formula",
    "where",
    "having",
    "grouping",
    "ordering",
    "limit",
    "distinct_set",
    "windows",
    "temporal",
)

ADAPTABLE_PARAMETER_SLOT_FAMILIES = (
    "WHERE_NUMERIC_LITERAL",
    "WHERE_STRING_LITERAL",
    "HAVING_NUMERIC_LITERAL",
    "HAVING_STRING_LITERAL",
    "LIMIT_INTEGER",
)

_COMPARISON_TYPES = (
    exp.EQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.NEQ,
    exp.Like,
    exp.ILike,
)
_SET_OPERATION_TYPES = (exp.Union, exp.Intersect, exp.Except)


@dataclass(frozen=True)
class QuerySemanticProfile:
    """Material, bounded SQL structure used by the semantic relation contract."""

    sources: tuple[str, ...]
    joins: tuple[str, ...]
    projection: tuple[str, ...]
    aggregation: tuple[str, ...]
    formula: tuple[str, ...]
    where: tuple[str, ...]
    having: tuple[str, ...]
    grouping: tuple[str, ...]
    ordering: tuple[str, ...]
    limit: tuple[str, ...]
    distinct_set: tuple[str, ...]
    windows: tuple[str, ...]
    temporal: tuple[str, ...]


@dataclass(frozen=True)
class ParameterSlot:
    clause: str
    path: str
    family: str
    value: str


@dataclass(frozen=True)
class ContractPairAssessment:
    semantic_relation: SemanticRelationState
    adaptability: AdaptabilityState
    semantic_conflict_dimensions: tuple[str, ...]
    semantic_profile_hashes: tuple[str, str] | None
    skeleton_hashes: tuple[str, str] | None
    parameter_slots: tuple[dict[str, str], ...]
    unknown_reason: str | None
    evidence_hash: str


def _stable_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return sha256(_stable_json(value).encode()).hexdigest()


def _clean_node(node: exp.Expression, aliases: Mapping[str, str]) -> exp.Expression:
    def strip_parentheses(current: exp.Expression) -> exp.Expression:
        if isinstance(current, exp.Paren):
            inner = current.args.get("this")
            if isinstance(inner, exp.Expression):
                return strip_parentheses(inner)
        value = current.copy()
        for key, child in tuple(value.args.items()):
            if isinstance(child, exp.Expression):
                cleaned = strip_parentheses(child)
                value.set(key, cleaned)
            elif isinstance(child, list):
                value.set(
                    key,
                    [
                        strip_parentheses(item) if isinstance(item, exp.Expression) else item
                        for item in child
                    ],
                )
        return value

    value = strip_parentheses(node)
    for table in value.find_all(exp.Table):
        table.set("this", exp.Identifier(this=table.name.lower()))
        table.set("alias", None)
    for column in value.find_all(exp.Column):
        if column.table:
            column.set(
                "table",
                exp.Identifier(this=aliases.get(column.table.lower(), column.table.lower())),
            )
        column.set("this", exp.Identifier(this=column.name.lower()))
    for alias in value.find_all(exp.Alias):
        alias.set("alias", None)
    return value


def _flatten_and(node: exp.Expression) -> list[exp.Expression]:
    if isinstance(node, exp.And):
        return _flatten_and(node.args["this"]) + _flatten_and(node.args["expression"])
    return [node]


def _normalize_and(node: exp.Expression) -> exp.Expression:
    value = node.copy()
    for key, child in tuple(value.args.items()):
        if isinstance(child, exp.Expression):
            value.set(key, _normalize_and(child))
        elif isinstance(child, list):
            value.set(
                key,
                [
                    _normalize_and(item) if isinstance(item, exp.Expression) else item
                    for item in child
                ],
            )
    if not isinstance(value, exp.And):
        return value
    terms = [_normalize_and(term) for term in _flatten_and(value)]
    terms.sort(key=lambda term: term.sql(dialect="postgres", pretty=False).lower())
    result = terms[0]
    for term in terms[1:]:
        result = exp.And(this=result, expression=term)
    return result


def _canonical_sql(node: exp.Expression, aliases: Mapping[str, str], *, normalize_and: bool) -> str:
    value = _clean_node(node, aliases)
    if normalize_and:
        value = _normalize_and(value)
    return value.sql(dialect="postgres", pretty=False).strip().lower()


def _literal_family(node: exp.Literal) -> str | None:
    return "STRING_LITERAL" if node.is_string else "NUMERIC_LITERAL"


def _replace_allowed_literals(
    node: exp.Expression,
    clause: str,
) -> tuple[exp.Expression, tuple[tuple[str, str], ...]]:
    slots: list[tuple[str, str]] = []

    def visit(current: exp.Expression, parent: exp.Expression | None) -> exp.Expression:
        if isinstance(current, exp.Literal) and isinstance(parent, _COMPARISON_TYPES):
            family = _literal_family(current)
            if family is not None:
                slots.append((family, str(current.this)))
                return exp.Literal.string("__M111P1_PARAMETER__")
        value = current.copy()
        for key, child in tuple(value.args.items()):
            if isinstance(child, exp.Expression):
                value.set(key, visit(child, current))
            elif isinstance(child, list):
                value.set(
                    key,
                    [
                        visit(item, current) if isinstance(item, exp.Expression) else item
                        for item in child
                    ],
                )
        return value

    return visit(node, None), tuple(slots)


def _clause_values(
    tree: exp.Expression,
    key: str,
    aliases: Mapping[str, str],
    *,
    parameterize: bool,
) -> tuple[tuple[str, ...], tuple[ParameterSlot, ...]]:
    values: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for wrapper in tree.find_all(exp.Where if key == "where" else exp.Having):
        expression = wrapper.args.get("this")
        if not isinstance(expression, exp.Expression):
            continue
        normalized = _normalize_and(_clean_node(expression, aliases))
        terms = _flatten_and(normalized)
        clause_values: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        for term in terms:
            if parameterize:
                replaced, slots = _replace_allowed_literals(term, key.upper())
                clause_values.append(
                    (_canonical_sql(replaced, aliases, normalize_and=False), slots)
                )
            else:
                clause_values.append((_canonical_sql(term, aliases, normalize_and=False), ()))
        clause_values.sort(key=lambda item: item[0])
        values.extend(clause_values)
    rendered: list[str] = []
    slots_out: list[ParameterSlot] = []
    for index, (text, slots) in enumerate(values):
        rendered.append(text)
        for slot_index, (family, raw_value) in enumerate(slots):
            slots_out.append(
                ParameterSlot(
                    clause=key.upper(),
                    path=f"{key.upper()}.term[{index}].literal[{slot_index}]",
                    family=f"{key.upper()}_{family}",
                    value=raw_value,
                )
            )
    return tuple(rendered), tuple(slots_out)


def _limit_values(
    tree: exp.Expression,
    aliases: Mapping[str, str],
    *,
    parameterize: bool,
) -> tuple[tuple[str, ...], tuple[ParameterSlot, ...]]:
    values: list[str] = []
    slots: list[ParameterSlot] = []
    for index, limit in enumerate(tree.find_all(exp.Limit)):
        expression = limit.args.get("expression")
        if parameterize and isinstance(expression, exp.Literal) and not expression.is_string:
            replacement = limit.copy()
            replacement.set("expression", exp.Literal.string("__M111P1_PARAMETER__"))
            values.append(_canonical_sql(replacement, aliases, normalize_and=False))
            slots.append(
                ParameterSlot(
                    clause="LIMIT",
                    path=f"LIMIT[{index}].literal[0]",
                    family="LIMIT_INTEGER",
                    value=str(expression.this),
                )
            )
        else:
            values.append(_canonical_sql(limit, aliases, normalize_and=False))
    return tuple(values), tuple(slots)


def _table_aliases(tree: exp.Expression) -> tuple[dict[str, str] | None, str | None]:
    aliases: dict[str, str] = {}
    for index, table in enumerate(tree.find_all(exp.Table)):
        name = table.alias_or_name.lower()
        if name in aliases:
            return None, ProfileUnknownReason.AMBIGUOUS_SCOPE.value
        aliases[name] = f"relation_{index}_{table.name.lower()}"
    return aliases, None


def _profile(
    sql: str | None, *, parameterize: bool
) -> tuple[QuerySemanticProfile | None, tuple[ParameterSlot, ...], str | None]:
    if not isinstance(sql, str) or not sql.strip():
        return None, (), ProfileUnknownReason.PARSE_FAILURE.value
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return None, (), ProfileUnknownReason.PARSE_FAILURE.value
    if not isinstance(tree, (exp.Select, *_SET_OPERATION_TYPES)):
        return None, (), ProfileUnknownReason.UNSUPPORTED_EXPRESSION.value
    if any(isinstance(node, (exp.CTE, exp.Subquery)) for node in tree.walk()):
        return None, (), ProfileUnknownReason.UNSUPPORTED_EXPRESSION.value
    if list(tree.find_all(exp.Anonymous)):
        return None, (), ProfileUnknownReason.UNSUPPORTED_EXPRESSION.value
    aliases, alias_reason = _table_aliases(tree)
    if aliases is None:
        return None, (), alias_reason

    sources = tuple(table.name.lower() for table in tree.find_all(exp.Table))
    joins: list[str] = []
    for join in tree.find_all(exp.Join):
        target = join.args.get("this")
        if not isinstance(target, exp.Expression):
            return None, (), ProfileUnknownReason.UNSUPPORTED_EXPRESSION.value
        side = str(join.args.get("side") or "INNER").upper()
        kind = str(join.args.get("kind") or "").upper()
        on = join.args.get("on")
        on_text = (
            _canonical_sql(on, aliases, normalize_and=True)
            if isinstance(on, exp.Expression)
            else "<NONE>"
        )
        joins.append(
            f"{side}|{kind}|target={_canonical_sql(target, aliases, normalize_and=False)}"
            f"|on={on_text}"
        )

    selects = list(tree.find_all(exp.Select)) if isinstance(tree, exp.SetOperation) else [tree]
    projection = tuple(
        _canonical_sql(expression, aliases, normalize_and=False)
        for select in selects
        for expression in select.expressions
    )
    aggregation = tuple(
        sorted(
            _canonical_sql(node, aliases, normalize_and=False)
            for node in tree.find_all(exp.AggFunc)
        )
    )
    formula_types = (exp.Add, exp.Sub, exp.Mul, exp.Div)
    formula = tuple(
        sorted(
            _canonical_sql(node, aliases, normalize_and=False)
            for node in tree.walk()
            if isinstance(node, formula_types)
        )
    )
    where, where_slots = _clause_values(tree, "where", aliases, parameterize=parameterize)
    having, having_slots = _clause_values(tree, "having", aliases, parameterize=parameterize)
    grouping = tuple(
        sorted(
            _canonical_sql(expression, aliases, normalize_and=False)
            for group in tree.find_all(exp.Group)
            for expression in group.expressions
        )
    )
    order = tree.find_all(exp.Order)
    ordering = tuple(
        _canonical_sql(expression, aliases, normalize_and=False)
        for order_group in order
        for expression in order_group.expressions
    )
    limit, limit_slots = _limit_values(tree, aliases, parameterize=parameterize)
    distinct = bool(list(tree.find_all(exp.Distinct)))
    set_operations = tuple(
        _canonical_sql(node, aliases, normalize_and=False)
        for node in tree.find_all(exp.SetOperation)
    )
    windows = tuple(
        _canonical_sql(node, aliases, normalize_and=False) for node in tree.find_all(exp.Window)
    )
    temporal_types = (exp.TimestampTrunc, exp.Extract)
    temporal = tuple(
        sorted(
            _canonical_sql(node, aliases, normalize_and=False)
            for node in tree.walk()
            if isinstance(node, temporal_types)
        )
    )
    profile = QuerySemanticProfile(
        sources=sources,
        joins=tuple(joins),
        projection=projection,
        aggregation=aggregation,
        formula=formula,
        where=where,
        having=having,
        grouping=grouping,
        ordering=ordering,
        limit=limit,
        distinct_set=(str(distinct), *set_operations),
        windows=windows,
        temporal=temporal,
    )
    return profile, (*where_slots, *having_slots, *limit_slots), None


def semantic_profile(sql: str | None) -> tuple[QuerySemanticProfile | None, str | None]:
    """Build the unparameterized evaluator-side semantic profile."""
    profile, _, reason = _profile(sql, parameterize=False)
    return profile, reason


def _slot_evidence(
    memory_slots: Iterable[ParameterSlot], target_slots: Iterable[ParameterSlot]
) -> tuple[dict[str, str], ...] | None:
    memory = tuple(sorted(memory_slots, key=lambda slot: slot.path))
    target = tuple(sorted(target_slots, key=lambda slot: slot.path))
    if len(memory) != len(target):
        return None
    evidence: list[dict[str, str]] = []
    for memory_slot, target_slot in zip(memory, target, strict=True):
        if (
            memory_slot.path != target_slot.path
            or memory_slot.family != target_slot.family
            or memory_slot.family not in ADAPTABLE_PARAMETER_SLOT_FAMILIES
        ):
            return None
        evidence.append(
            {
                "clause": memory_slot.clause,
                "path": memory_slot.path,
                "kind": memory_slot.family,
                "memory": memory_slot.value,
                "target": target_slot.value,
            }
        )
    return tuple(evidence)


def assess_pair(target_sql: str | None, memory_sql: str | None) -> ContractPairAssessment:
    """Apply both contracts to a target/memory SQL pair."""
    target_profile, _, target_reason = _profile(target_sql, parameterize=False)
    memory_profile, _, memory_reason = _profile(memory_sql, parameterize=False)
    unknown_reason = target_reason or memory_reason
    if target_profile is None or memory_profile is None:
        semantic = SemanticRelationState.UNKNOWN
        adaptability = AdaptabilityState.UNKNOWN
        conflict_dimensions: tuple[str, ...] = ()
        semantic_hashes = None
        skeleton_hashes = None
        slots: tuple[dict[str, str], ...] = ()
    else:
        conflict_dimensions = tuple(
            dimension
            for dimension in SEMANTIC_DIMENSIONS
            if getattr(target_profile, dimension) != getattr(memory_profile, dimension)
        )
        semantic = (
            SemanticRelationState.PROVEN_EQUIVALENT
            if not conflict_dimensions
            else SemanticRelationState.PROVEN_MATERIAL_CONFLICT
        )
        semantic_hashes = (_hash(asdict(target_profile)), _hash(asdict(memory_profile)))
        target_skeleton, target_slots, _ = _profile(target_sql, parameterize=True)
        memory_skeleton, memory_slots, _ = _profile(memory_sql, parameterize=True)
        if target_skeleton is None or memory_skeleton is None:
            adaptability = AdaptabilityState.UNKNOWN
            skeleton_hashes = None
            slots = ()
        else:
            skeleton_hashes = (_hash(asdict(target_skeleton)), _hash(asdict(memory_skeleton)))
            slot_evidence = _slot_evidence(memory_slots, target_slots)
            adaptability = (
                AdaptabilityState.PROVEN_ADAPTABLE
                if target_skeleton == memory_skeleton and slot_evidence is not None
                else AdaptabilityState.PROVEN_NON_ADAPTABLE
            )
            slots = slot_evidence or ()
    evidence = {
        "semantic_relation": semantic.value,
        "adaptability": adaptability.value,
        "semantic_conflict_dimensions": conflict_dimensions,
        "semantic_profile_hashes": semantic_hashes,
        "skeleton_hashes": skeleton_hashes,
        "parameter_slots": slots,
        "unknown_reason": unknown_reason,
    }
    return ContractPairAssessment(
        semantic_relation=semantic,
        adaptability=adaptability,
        semantic_conflict_dimensions=conflict_dimensions,
        semantic_profile_hashes=semantic_hashes,
        skeleton_hashes=skeleton_hashes,
        parameter_slots=slots,
        unknown_reason=unknown_reason,
        evidence_hash=_hash(evidence),
    )


def assessment_dict(assessment: ContractPairAssessment) -> dict[str, Any]:
    """Serialize bounded pair evidence deterministically."""
    return {
        "semantic_relation": assessment.semantic_relation.value,
        "adaptability": assessment.adaptability.value,
        "semantic_conflict_dimensions": list(assessment.semantic_conflict_dimensions),
        "semantic_profile_hashes": (
            list(assessment.semantic_profile_hashes)
            if assessment.semantic_profile_hashes
            else None
        ),
        "skeleton_hashes": (
            list(assessment.skeleton_hashes) if assessment.skeleton_hashes else None
        ),
        "parameter_slots": list(assessment.parameter_slots),
        "unknown_reason": assessment.unknown_reason,
        "evidence_hash": assessment.evidence_hash,
    }


def contract_interaction_key(assessment: ContractPairAssessment) -> str:
    return f"{assessment.semantic_relation.value}+{assessment.adaptability.value}"
