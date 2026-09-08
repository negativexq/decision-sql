"""Evaluation-only M32 alignment fixtures and semantic comparison helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.semantics.m32_alignment import (
    AlignmentAggregate,
    AlignmentAggregateFunction,
    AlignmentCalculation,
    AlignmentCalculationKind,
    AlignmentDirection,
    AlignmentFilter,
    AlignmentFilterOperator,
    AlignmentOrder,
    AlignmentPopulationMode,
    AlignmentQueryShape,
    AlignmentTemporal,
    AlignmentTemporalKind,
    QueryAlignmentV1,
    validate_alignment,
)
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import SemanticQueryPlan

_OPERATOR_REVERSE = {
    "LT": "GT",
    "LTE": "GTE",
    "GT": "LT",
    "GTE": "LTE",
}


def oracle_alignment_from_plan(
    plan: SemanticQueryPlan, mapping: SemanticMappingSnapshot
) -> QueryAlignmentV1:
    """Build an offline alignment fixture from a canonical oracle plan.

    This function is evaluation-only.  Its output is never included in model
    context or runtime server code.
    """

    data = plan.model_dump(mode="json", exclude_none=True)
    known_attributes = {item.attribute_id for item in mapping.attributes}
    entities = _entities(data, mapping)
    attributes = _attributes(data, mapping)
    relationship_ids = _relationship_ids(data, mapping)
    filters = _filters(data, known_attributes)
    aggregates = _aggregates(data, known_attributes)
    grouping = _direct_attribute_ids(data.get("group_by", [])) & known_attributes
    grouping_labels = sorted(
        item
        for item in _direct_attribute_ids(data.get("group_by", []))
        if item not in known_attributes
    )
    calculation = _calculation(data.get("calculation_contract"), data)
    ordering = _ordering(data.get("order_by", []), known_attributes)
    temporal = _temporal(data, known_attributes)
    shape = _shape(data)
    anchor = _anchor(data)
    population_mode = {
        "BASE_ENTITY": AlignmentPopulationMode.BASE_ENTITY,
        "MATCHING_RELATIONS": AlignmentPopulationMode.MATCHED_ONLY,
        "PRESERVE_BASE": AlignmentPopulationMode.PRESERVE_ANCHOR,
    }.get(
        str(data.get("population_contract", {}).get("inclusion_mode", "BASE_ENTITY")),
        AlignmentPopulationMode.BASE_ENTITY,
    )
    relevant_attributes = tuple(sorted(attributes))
    return QueryAlignmentV1(
        anchor_entity_id=anchor,
        relevant_entity_ids=tuple(sorted(entities | {anchor})),
        relevant_attribute_ids=relevant_attributes,
        relationship_ids=tuple(sorted(relationship_ids)),
        population_mode=population_mode,
        filters=tuple(filters),
        aggregations=tuple(aggregates),
        grouping_attribute_ids=tuple(sorted(grouping)),
        grouping_result_labels=tuple(grouping_labels),
        calculation=calculation,
        ordering=tuple(ordering),
        limit=data.get("limit"),
        temporal=tuple(temporal),
        query_shape=shape,
        logic_summary=_summary(
            anchor, population_mode, aggregates, grouping, calculation, ordering, data.get("limit")
        ),
    )


def alignment_component_match(
    candidate: QueryAlignmentV1, expected: QueryAlignmentV1
) -> dict[str, bool]:
    """Compare model-owned semantics, ignoring server bookkeeping.

    Oracle plans contain internal relationship keys, output slots, aliases, and
    nested implementation labels.  Those are intentionally not required from
    an alignment proposal.  This comparator therefore uses coverage for
    selected semantic IDs and normalized signatures for derived references.
    """

    return {
        "entities": set(candidate.relevant_entity_ids) == set(expected.relevant_entity_ids)
        and candidate.anchor_entity_id == expected.anchor_entity_id,
        "attributes": set(expected.relevant_attribute_ids).issubset(
            set(candidate.relevant_attribute_ids)
        ),
        "relationships": set(expected.relationship_ids).issubset(set(candidate.relationship_ids)),
        "population": candidate.population_mode == expected.population_mode,
        "filters": _filter_signatures(candidate.filters) == _filter_signatures(expected.filters),
        "aggregation": _json(candidate.aggregations) == _json(expected.aggregations),
        "grouping": set(expected.grouping_attribute_ids).issubset(
            set(candidate.grouping_attribute_ids)
        ),
        "calculation": _json(candidate.calculation) == _json(expected.calculation),
        "ordering": _order_signatures(candidate.ordering) == _order_signatures(expected.ordering),
        "limit": candidate.limit == expected.limit,
        "temporal": _json(candidate.temporal) == _json(expected.temporal),
        "query_shape": candidate.query_shape == expected.query_shape,
    }


def _filter_signatures(values: Iterable[AlignmentFilter]) -> set[tuple[str, str]]:
    return {(item.operator.value, str(item.value).strip().lower()) for item in values}


def _order_signatures(values: Iterable[AlignmentOrder]) -> tuple[tuple[str, str], ...]:
    return tuple((item.target_kind, item.direction.value) for item in values)


def alignment_is_sufficient(
    alignment: QueryAlignmentV1, mapping: SemanticMappingSnapshot, database_id: str
) -> bool:
    """Validate and ground an offline alignment fixture."""

    from app.semantics.m32_alignment import AlignmentGrounder

    validate_alignment(alignment, mapping)
    AlignmentGrounder(mapping, database_id).ground("offline fixture", alignment)
    return True


def _json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _anchor(data: dict[str, Any]) -> str:
    if isinstance(data.get("from_entity_id"), str):
        return str(data["from_entity_id"])
    source = data.get("from_source")
    if isinstance(source, dict) and source.get("entity_id"):
        return str(source["entity_id"])
    bases = data.get("population_contract", {}).get("base_entity_ids", [])
    if bases:
        return str(bases[0])
    raise ValueError("oracle plan has no semantic anchor")


def _entities(data: dict[str, Any], mapping: SemanticMappingSnapshot) -> set[str]:
    result = set(data.get("population_contract", {}).get("base_entity_ids", []))
    anchor = _anchor(data)
    result.add(anchor)
    for relationship_id in _relationship_ids(data, mapping):
        relationship = mapping.relationship(relationship_id)
        result.update((relationship.from_entity_id, relationship.to_entity_id))
    for item in _walk(data):
        if item.get("kind") == "entity" and isinstance(item.get("entity_id"), str):
            result.add(item["entity_id"])
    return result


def _attributes(data: dict[str, Any], mapping: SemanticMappingSnapshot) -> set[str]:
    known = {item.attribute_id for item in mapping.attributes}
    result: set[str] = set()
    for item in _walk(data):
        candidate = item.get("attribute_id")
        if isinstance(candidate, str) and candidate in known:
            result.add(candidate)
    relationship_keys = {
        attribute_id
        for relationship_id in _relationship_ids(data, mapping)
        for attribute_id in (
            mapping.relationship(relationship_id).from_attribute_id,
            mapping.relationship(relationship_id).to_attribute_id,
        )
    }
    sources = _output_sources(data)
    user_attributes = _direct_attribute_ids(
        {
            "outputs": data.get("outputs"),
            "where": data.get("where"),
            "having": data.get("having"),
            "group_by": data.get("group_by"),
            "order_by": data.get("order_by"),
            "calculation": data.get("calculation_contract"),
        }
    )
    for output_id in tuple(user_attributes):
        user_attributes.update(sources.get(output_id, set()))
    result -= relationship_keys - user_attributes
    return result


def _relationship_ids(data: dict[str, Any], mapping: SemanticMappingSnapshot) -> set[str]:
    result: set[str] = set()
    for item in _walk(data):
        if isinstance(item.get("relationship_id"), str):
            result.add(item["relationship_id"])
        result.update(
            str(value) for value in item.get("relationship_path", []) if isinstance(value, str)
        )
    output_sources = _output_sources(data)
    for item in _walk(data):
        for join_key in item.get("join_keys", []) if isinstance(item, dict) else []:
            if not isinstance(join_key, dict):
                continue
            left = join_key.get("left", {})
            right = join_key.get("right", {})
            left_ids = _resolve_output_sources(left.get("attribute_id"), output_sources)
            right_ids = _resolve_output_sources(right.get("attribute_id"), output_sources)
            for left_id in left_ids:
                for right_id in right_ids:
                    for relationship in mapping.relationships:
                        if {relationship.from_attribute_id, relationship.to_attribute_id} == {
                            left_id,
                            right_id,
                        }:
                            result.add(relationship.relationship_id)
                    left_edges = _attribute_relationships(left_id, mapping)
                    right_edges = _attribute_relationships(right_id, mapping)
                    left_by_target = {_other_entity(item, left_id): item for item in left_edges}
                    right_by_target = {_other_entity(item, right_id): item for item in right_edges}
                    common_targets = set(left_by_target) & set(right_by_target)
                    if len(common_targets) == 1:
                        result.add(left_by_target[next(iter(common_targets))].relationship_id)
                        result.add(right_by_target[next(iter(common_targets))].relationship_id)
    return result


def _attribute_relationships(
    attribute_id: str, mapping: SemanticMappingSnapshot
) -> tuple[Any, ...]:
    return tuple(
        item
        for item in mapping.relationships
        if attribute_id in {item.from_attribute_id, item.to_attribute_id}
    )


def _other_entity(relationship: Any, attribute_id: str) -> str:
    value = (
        relationship.to_entity_id
        if relationship.from_attribute_id == attribute_id
        else relationship.from_entity_id
    )
    return str(value)


def _output_sources(data: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for item in _walk(data):
        if not isinstance(item, dict) or not item.get("ctes"):
            continue
        for cte in item.get("ctes", []):
            if not isinstance(cte, dict):
                continue
            query = cte.get("query", {})
            select = query.get("select", []) if isinstance(query, dict) else []
            for export in cte.get("exported_attributes", []):
                if not isinstance(export, dict):
                    continue
                position = export.get("position")
                if not isinstance(position, int) or position >= len(select):
                    continue
                expression = select[position].get("expression", {})
                result[str(export.get("attribute_id"))] = _direct_attribute_ids(expression)
    return result


def _resolve_output_sources(attribute_id: Any, sources: dict[str, set[str]]) -> set[str]:
    if not isinstance(attribute_id, str):
        return set()
    return sources.get(attribute_id, {attribute_id})


def _direct_attribute_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        if value.get("kind") == "attribute" and isinstance(value.get("attribute_id"), str):
            result.add(value["attribute_id"])
        for child in value.values():
            result.update(_direct_attribute_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_direct_attribute_ids(child))
    return result


def _filter_ref(attribute_id: str, known_attributes: set[str]) -> dict[str, str | None]:
    if attribute_id in known_attributes:
        return {"field_id": attribute_id, "target_kind": "ATTRIBUTE", "result_label": None}
    return {"field_id": None, "target_kind": "DERIVED_RESULT", "result_label": attribute_id}


def _filters(data: dict[str, Any], known_attributes: set[str]) -> list[AlignmentFilter]:
    result: list[AlignmentFilter] = []
    for root_name in ("where", "having"):
        for item in _walk(data.get(root_name)):
            if item.get("kind") == "is_null":
                expression = item.get("expression")
                if isinstance(expression, dict) and expression.get("kind") == "attribute":
                    result.append(
                        AlignmentFilter(
                            **_filter_ref(str(expression["attribute_id"]), known_attributes),
                            operator=(
                                AlignmentFilterOperator.IS_NOT_NULL
                                if item.get("negated")
                                else AlignmentFilterOperator.IS_NULL
                            ),
                        )
                    )
            if item.get("kind") != "binary":
                continue
            operator = str(item.get("operator", ""))
            if operator not in {
                "EQ",
                "NE",
                "LT",
                "LTE",
                "GT",
                "GTE",
            }:
                continue
            left = item.get("left")
            right = item.get("right")
            if (
                isinstance(left, dict)
                and left.get("kind") == "attribute"
                and isinstance(right, dict)
                and right.get("kind") == "literal"
            ):
                result.append(
                    AlignmentFilter(
                        **_filter_ref(str(left["attribute_id"]), known_attributes),
                        operator=AlignmentFilterOperator(operator),
                        value=right.get("value"),
                    )
                )
            elif (
                isinstance(right, dict)
                and right.get("kind") == "attribute"
                and isinstance(left, dict)
                and left.get("kind") == "literal"
            ):
                result.append(
                    AlignmentFilter(
                        **_filter_ref(str(right["attribute_id"]), known_attributes),
                        operator=AlignmentFilterOperator(_OPERATOR_REVERSE.get(operator, operator)),
                        value=left.get("value"),
                    )
                )
    return _unique_models(result)


def _aggregates(data: dict[str, Any], known_attributes: set[str]) -> list[AlignmentAggregate]:
    result: list[AlignmentAggregate] = []
    allowed = {item.value for item in AlignmentAggregateFunction}
    for item in _walk(data):
        if item.get("kind") != "aggregate" or item.get("function") not in allowed:
            continue
        attrs = sorted(_direct_attribute_ids(item.get("expression")))
        result.append(
            AlignmentAggregate(
                function=AlignmentAggregateFunction(item["function"]),
                attribute_id=attrs[0] if attrs and attrs[0] in known_attributes else None,
                distinct=bool(item.get("distinct", False)),
            )
        )
    return _unique_models(result)


def _calculation(contract: Any, data: dict[str, Any]) -> AlignmentCalculation | None:
    if isinstance(contract, dict):
        kind = str(contract.get("kind", "NONE"))
        operand_source: Any = contract.get("numerator")
        denominator_source: Any = contract.get("denominator")
    else:
        kind, operand_source, denominator_source = _expression_calculation(data)
    mapped = {
        "RATIO": AlignmentCalculationKind.RATIO,
        "PERCENTAGE": AlignmentCalculationKind.PERCENTAGE,
        "DIFFERENCE": AlignmentCalculationKind.DIFFERENCE,
    }.get(kind)
    if mapped is None:
        return None
    sources = _output_sources(data)
    operands = _direct_attribute_ids(operand_source) | _direct_attribute_ids(denominator_source)
    resolved_operands = set(operands)
    for operand in operands:
        resolved_operands.update(sources.get(operand, set()))
    operand_ids = sorted(item for item in resolved_operands if item.startswith("attribute:"))
    return AlignmentCalculation(kind=mapped, operand_attribute_ids=tuple(operand_ids))


def _expression_calculation(data: dict[str, Any]) -> tuple[str, Any, Any]:
    for item in _walk(data):
        if item.get("kind") != "binary":
            continue
        operator = str(item.get("operator", ""))
        if operator == "SUBTRACT":
            return "DIFFERENCE", item.get("left"), item.get("right")
        if operator == "DIVIDE":
            return "RATIO", item.get("left"), item.get("right")
    return "NONE", None, None


def _ordering(value: Any, known_attributes: set[str]) -> list[AlignmentOrder]:
    result: list[AlignmentOrder] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        expression = item.get("expression")
        attrs = sorted(_direct_attribute_ids(expression))
        attribute_id = attrs[0] if attrs and attrs[0] in known_attributes else None
        expression_kind = expression.get("kind") if isinstance(expression, dict) else None
        kind = {
            "attribute": "ATTRIBUTE",
            "aggregate": "AGGREGATE",
            "window": "WINDOW",
            "binary": "CALCULATION",
        }.get(str(expression_kind), "EXPRESSION")
        result.append(
            AlignmentOrder(
                attribute_id=attribute_id,
                target_kind=kind,
                result_label=None if attribute_id is not None else (attrs[0] if attrs else None),
                direction=AlignmentDirection(str(item.get("direction", "ASC"))),
            )
        )
    return result


def _temporal(data: dict[str, Any], known_attributes: set[str]) -> list[AlignmentTemporal]:
    result: list[AlignmentTemporal] = []
    for item in _walk(data):
        if item.get("kind") != "function":
            continue
        function = str(item.get("function", ""))
        if not any(token in function for token in ("DATE", "TIME", "TIMESTAMP", "EXTRACT", "AGE")):
            continue
        attrs = sorted(_direct_attribute_ids(item.get("arguments")))
        if attrs and attrs[0] in known_attributes:
            result.append(
                AlignmentTemporal(attribute_id=attrs[0], kind=AlignmentTemporalKind.EXTRACTION)
            )
    return _unique_models(result)


def _shape(data: dict[str, Any]) -> AlignmentQueryShape:
    kinds = {str(item.get("kind")) for item in _walk(data)}
    if "scalar_subquery" in kinds:
        if any(item.get("relation_ref") for item in _walk(data)):
            return AlignmentQueryShape.CORRELATED_SUBQUERY
        return AlignmentQueryShape.SCALAR_SUBQUERY
    if "window" in kinds:
        return AlignmentQueryShape.WINDOW
    if data.get("limit") is not None and data.get("order_by"):
        return AlignmentQueryShape.TOP_K
    if data.get("group_by"):
        return AlignmentQueryShape.GROUPED_AGGREGATE
    if data.get("where"):
        return AlignmentQueryShape.FILTERED_SELECT
    if data.get("outputs") and any(item.get("kind") == "aggregate" for item in _walk(data)):
        return AlignmentQueryShape.AGGREGATE
    return AlignmentQueryShape.SIMPLE_SELECT


def _summary(
    anchor: str,
    population: AlignmentPopulationMode,
    aggregates: list[AlignmentAggregate],
    grouping: set[str],
    calculation: AlignmentCalculation | None,
    ordering: list[AlignmentOrder],
    limit: int | None,
) -> str:
    parts = [population.value.lower().replace("_", " "), "from", anchor]
    if aggregates:
        parts.append("with aggregation")
    if grouping:
        parts.append("grouped by semantic dimensions")
    if calculation is not None:
        parts.append(f"with {calculation.kind.value.lower()} calculation")
    if ordering:
        parts.append("ordered by the requested result")
    if limit is not None:
        parts.append(f"limited to {limit}")
    return " ".join(parts)[:600]


def _unique_models(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        key = value.model_dump_json(exclude_none=True)
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result
