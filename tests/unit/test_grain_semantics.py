from __future__ import annotations

import pytest

from app.semantics.grain import (
    AggregationBehavior,
    DerivedMeasureSemantics,
    GrainContractError,
    GrainDiagnosticCode,
    GrainEntity,
    GrainKey,
    GrainRelationship,
    GrainSafetyValidator,
    MeasureCatalog,
    MeasureSemantics,
)
from app.semantics.grain_runtime import GrainRuntimeStatus, RuntimeGrainSafetyCoordinator


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


def test_governed_child_grain_derived_measure_is_safe() -> None:
    catalog = _catalog().model_copy(
        update={
            "derived_measures": (
                DerivedMeasureSemantics(
                    measure_id="derived:test:line_value",
                    metric_id="metric:test:line_value",
                    expression_signature="children.amount * parents.amount",
                    source_attribute_ids=(
                        "attribute:child:amount",
                        "attribute:parent:amount",
                    ),
                    source_entity_ids=("entity:child", "entity:parent"),
                    authorized_relationship_ids=("relationship:child_parent",),
                    provenance=("PUBLIC_METRIC_DEFINITION",),
                ),
            )
        }
    )
    result = GrainSafetyValidator(catalog).validate(
        "SELECT p.group_id, SUM(c.amount * p.amount) FROM parents p "
        "JOIN children c ON c.parent_id=p.parent_id GROUP BY p.group_id"
    )
    assert result.code is GrainDiagnosticCode.PASS


def test_ungoverned_parent_expression_remains_rejected() -> None:
    catalog = _catalog().model_copy(
        update={
            "derived_measures": (
                DerivedMeasureSemantics(
                    measure_id="derived:test:line_value",
                    metric_id="metric:test:line_value",
                    expression_signature="children.amount * parents.amount",
                    source_attribute_ids=(
                        "attribute:child:amount",
                        "attribute:parent:amount",
                    ),
                    source_entity_ids=("entity:child", "entity:parent"),
                    authorized_relationship_ids=("relationship:child_parent",),
                    provenance=("PUBLIC_METRIC_DEFINITION",),
                ),
            )
        }
    )
    result = GrainSafetyValidator(catalog).validate(
        "SELECT p.group_id, SUM(p.amount * 2) FROM parents p "
        "JOIN children c ON c.parent_id=p.parent_id GROUP BY p.group_id"
    )
    assert result.code is GrainDiagnosticCode.PARENT_MEASURE_FANOUT


def test_governed_derived_measure_requires_the_declared_relationship_path() -> None:
    base_catalog = _catalog()
    catalog = base_catalog.model_copy(
        update={
            "relationships": (
                *base_catalog.relationships,
                GrainRelationship(
                    relationship_id="relationship:child_parent_alternate",
                    from_entity_id="entity:child",
                    from_attribute_ids=("attribute:child:other_parent_id",),
                    to_entity_id="entity:parent",
                    to_attribute_ids=("attribute:parent:parent_id",),
                    cardinality="many_to_one",
                    provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
                ),
            ),
            "derived_measures": (
                DerivedMeasureSemantics(
                    measure_id="derived:test:line_value",
                    metric_id="metric:test:line_value",
                    expression_signature="children.amount * parents.amount",
                    source_attribute_ids=(
                        "attribute:child:amount",
                        "attribute:parent:amount",
                    ),
                    source_entity_ids=("entity:child", "entity:parent"),
                    authorized_relationship_ids=("relationship:child_parent",),
                    provenance=("PUBLIC_METRIC_DEFINITION",),
                ),
            ),
        }
    )
    result = GrainSafetyValidator(catalog).validate(
        "SELECT p.group_id, SUM(c.amount * p.amount) FROM parents p "
        "JOIN children c ON c.other_parent_id=p.parent_id GROUP BY p.group_id"
    )
    assert result.code is GrainDiagnosticCode.PARENT_MEASURE_FANOUT


_JOINED = "FROM parents p JOIN children c ON c.parent_id = p.parent_id"


@pytest.mark.parametrize(
    "sql",
    [
        f"WITH j AS (SELECT p.parent_id, p.amount {_JOINED}) SELECT SUM(amount) FROM j",
        f"SELECT SUM(d.amount) FROM (SELECT p.amount AS amount {_JOINED}) d",
        f"WITH j AS (SELECT p.* {_JOINED}) SELECT SUM(j.amount) FROM j",
        f"WITH a AS (SELECT p.amount {_JOINED}), b AS (SELECT amount AS x FROM a) "
        "SELECT SUM(x) FROM b",
        "SELECT SUM(d.amount) FROM (SELECT parent_id, amount FROM parents) d "
        "JOIN children c ON c.parent_id = d.parent_id",
        "WITH t AS (SELECT parent_id, SUM(amount) AS total FROM parents GROUP BY parent_id) "
        "SELECT SUM(t.total) FROM t JOIN children c ON c.parent_id = t.parent_id",
    ],
)
def test_fanout_behind_a_cte_or_derived_table_is_detected(sql: str) -> None:
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    assert result.evidence["child_table"] == "children"
    # The normalizer cannot rewrite these, so the plan is refused and sent back for repair.
    decision = RuntimeGrainSafetyCoordinator(_catalog()).inspect(sql)
    assert decision.status is GrainRuntimeStatus.REJECTED


def test_distinct_behind_a_cte_still_masks_the_fanout() -> None:
    sql = f"WITH j AS (SELECT p.amount {_JOINED}) SELECT SUM(DISTINCT amount) FROM j"
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.DISTINCT_VALUE_FANOUT_MASK


@pytest.mark.parametrize(
    "sql",
    [
        f"WITH j AS (SELECT p.parent_id, p.amount {_JOINED} GROUP BY p.parent_id, p.amount) "
        "SELECT SUM(amount) FROM j",
        f"WITH j AS (SELECT DISTINCT p.parent_id, p.amount {_JOINED}) SELECT SUM(amount) FROM j",
        f"WITH j AS (SELECT DISTINCT c.parent_id, p.amount {_JOINED}) SELECT SUM(amount) FROM j",
        f"WITH j AS (SELECT c.amount {_JOINED}) SELECT SUM(amount) FROM j",
        "WITH j AS (SELECT amount FROM parents WHERE amount > 0) SELECT SUM(amount) FROM j",
        "WITH j AS (SELECT p.amount FROM parents p WHERE EXISTS "
        "(SELECT 1 FROM children c WHERE c.parent_id = p.parent_id)) SELECT SUM(amount) FROM j",
        "WITH t AS (SELECT p.parent_id, SUM(p.amount) AS total FROM parents p "
        "GROUP BY p.parent_id) SELECT SUM(t.total) FROM t JOIN (SELECT DISTINCT parent_id "
        "FROM children) c ON c.parent_id = t.parent_id",
    ],
)
def test_a_cte_at_the_parent_grain_is_safe(sql: str) -> None:
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}


def test_existence_query_is_not_misclassified_as_measure_fanout() -> None:
    sql = """
        SELECT COUNT(*) FILTER (
            WHERE EXISTS (SELECT 1 FROM children c WHERE c.parent_id = p.parent_id)
        )
        FROM parents p
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code is GrainDiagnosticCode.NOT_APPLICABLE


def test_correlated_child_aggregate_is_safe() -> None:
    sql = """
        SELECT p.group_id,
               SUM(p.amount - COALESCE(
                   (SELECT SUM(c.amount) FROM children c WHERE c.parent_id = p.parent_id), 0
               ))
        FROM parents p
        GROUP BY p.group_id
    """
    result = GrainSafetyValidator(_catalog()).validate(sql)
    assert result.code in {GrainDiagnosticCode.PASS, GrainDiagnosticCode.NOT_APPLICABLE}


def test_sum_of_non_additive_measure_is_rejected() -> None:
    catalog = _catalog()
    ratio = MeasureSemantics(
        measure_id="measure:parent_ratio",
        source_attribute_id="attribute:parent:ratio",
        entity_id="entity:parent",
        physical_table="parents",
        physical_column_or_path="ratio",
        native_grain=GrainKey(
            entity_id="entity:parent",
            key_attribute_ids=("attribute:parent:parent_id",),
        ),
        aggregation_behavior=AggregationBehavior.NON_ADDITIVE,
        provenance=("PUBLIC_ATTRIBUTE_SEMANTICS",),
    )
    catalog = catalog.model_copy(update={"measures": (*catalog.measures, ratio)})
    result = GrainSafetyValidator(catalog).validate("SELECT SUM(p.ratio) FROM parents p")
    assert result.code is GrainDiagnosticCode.UNSAFE_ROLLUP


def test_rollup_entities_are_validated() -> None:
    catalog = _catalog()
    parent_measure = catalog.measures[0].model_copy(
        update={
            "allowed_rollup_grains": (
                GrainKey(entity_id="entity:missing", key_attribute_ids=("x",)),
            )
        }
    )
    invalid = catalog.model_copy(update={"measures": (parent_measure, catalog.measures[1])})
    try:
        invalid.validate_contract()
    except GrainContractError:
        pass
    else:
        raise AssertionError("unknown rollup entity must fail closed")
