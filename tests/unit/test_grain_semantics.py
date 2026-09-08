from __future__ import annotations

from app.semantics.grain import (
    AggregationBehavior,
    GrainAlignmentAnalyzer,
    GrainDiagnosticCode,
    GrainEntity,
    GrainKey,
    GrainRelationship,
    GrainSafetyValidator,
    MeasureCatalog,
    MeasureSemantics,
)


def _catalog() -> MeasureCatalog:
    parent = GrainEntity(
        entity_id="entity:parent",
        physical_table="parents",
        key_attribute_ids=("attribute:parent:parent_id",),
    )
    child = GrainEntity(
        entity_id="entity:child",
        physical_table="children",
        key_attribute_ids=("attribute:child:child_id",),
    )
    relationship = GrainRelationship(
        relationship_id="relationship:child_parent",
        from_entity_id=child.entity_id,
        from_attribute_ids=("attribute:child:parent_id",),
        to_entity_id=parent.entity_id,
        to_attribute_ids=("attribute:parent:parent_id",),
        cardinality="many_to_one",
        provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
    )
    parent_measure = MeasureSemantics(
        measure_id="measure:parent_amount",
        source_attribute_id="attribute:parent:amount",
        entity_id=parent.entity_id,
        physical_table="parents",
        physical_column_or_path="amount",
        native_grain=GrainKey(
            entity_id=parent.entity_id,
            key_attribute_ids=("attribute:parent:parent_id",),
        ),
        aggregation_behavior=AggregationBehavior.ADDITIVE,
        provenance=("PUBLIC_ATTRIBUTE_SEMANTICS",),
    )
    child_measure = MeasureSemantics(
        measure_id="measure:child_amount",
        source_attribute_id="attribute:child:amount",
        entity_id=child.entity_id,
        physical_table="children",
        physical_column_or_path="amount",
        native_grain=GrainKey(
            entity_id=child.entity_id,
            key_attribute_ids=("attribute:child:child_id",),
        ),
        aggregation_behavior=AggregationBehavior.ADDITIVE,
        provenance=("PUBLIC_ATTRIBUTE_SEMANTICS",),
    )
    return MeasureCatalog(
        entities=(parent, child),
        relationships=(relationship,),
        measures=(parent_measure, child_measure),
    )


def test_numeric_identifier_is_not_implicitly_a_measure() -> None:
    catalog = _catalog()
    assert all(
        not measure.source_attribute_id.endswith("parent_id") for measure in catalog.measures
    )


def test_child_rolls_up_to_parent_grain() -> None:
    result = GrainAlignmentAnalyzer(_catalog()).align(
        "measure:parent_amount", "measure:child_amount"
    )
    assert result.alignment_status.value == "REQUIRES_ROLLUP"
    assert result.alignment_grain is not None
    assert result.alignment_grain.entity_id == "entity:parent"


def test_direct_parent_fanout_is_detected() -> None:
    sql = """
        SELECT p.group_id, SUM(p.amount) - SUM(c.amount)
        FROM parents p
        LEFT JOIN children c ON c.parent_id = p.parent_id
        GROUP BY p.group_id
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.PARENT_MEASURE_FANOUT


def test_preaggregated_child_is_safe() -> None:
    sql = """
        SELECT p.group_id, SUM(p.amount - COALESCE(c.child_total, 0))
        FROM parents p
        LEFT JOIN (
            SELECT parent_id, SUM(amount) AS child_total
            FROM children
            GROUP BY parent_id
        ) c ON c.parent_id = p.parent_id
        GROUP BY p.group_id
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}


def test_distinct_value_mask_is_not_accepted_as_safety() -> None:
    sql = """
        SELECT p.group_id, SUM(DISTINCT p.amount)
        FROM parents p
        LEFT JOIN children c ON c.parent_id = p.parent_id
        GROUP BY p.group_id
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.DISTINCT_VALUE_FANOUT_MASK


def test_child_measure_aggregation_is_safe() -> None:
    sql = """
        SELECT p.group_id, SUM(c.amount)
        FROM parents p
        JOIN children c ON c.parent_id = p.parent_id
        GROUP BY p.group_id
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.PASS
