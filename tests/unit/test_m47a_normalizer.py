from __future__ import annotations

import sqlite3

from app.semantics.grain import (
    AggregationBehavior,
    GrainEntity,
    GrainKey,
    GrainRelationship,
    GrainSafetyValidator,
    MeasureCatalog,
    MeasureSemantics,
)
from app.semantics.grain_normalizer import (
    GrainSafeNormalizer,
    NormalizationReason,
    NormalizationStatus,
)


def _attribute(entity: str, column: str) -> str:
    return f"attribute:test:{entity}:{column}"


def _catalog(*, second_child: bool = False) -> MeasureCatalog:
    parent = GrainEntity(
        entity_id="entity:test:parent",
        physical_table="parent",
        key_attribute_ids=(_attribute("parent", "parent_id"),),
    )
    child = GrainEntity(
        entity_id="entity:test:child",
        physical_table="child",
        key_attribute_ids=(_attribute("child", "child_id"),),
    )
    entities = [parent, child]
    relationships = [
        GrainRelationship(
            relationship_id="relationship:test:child_parent",
            from_entity_id=child.entity_id,
            from_attribute_ids=(_attribute("child", "parent_id"),),
            to_entity_id=parent.entity_id,
            to_attribute_ids=(_attribute("parent", "parent_id"),),
            cardinality="many_to_one",
            provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
        )
    ]
    if second_child:
        child_two = GrainEntity(
            entity_id="entity:test:child_two",
            physical_table="child_two",
            key_attribute_ids=(_attribute("child_two", "child_two_id"),),
        )
        entities.append(child_two)
        relationships.append(
            GrainRelationship(
                relationship_id="relationship:test:child_two_parent",
                from_entity_id=child_two.entity_id,
                from_attribute_ids=(_attribute("child_two", "parent_id"),),
                to_entity_id=parent.entity_id,
                to_attribute_ids=(_attribute("parent", "parent_id"),),
                cardinality="many_to_one",
                provenance=("PUBLIC_RELATIONSHIP_CARDINALITY",),
            )
        )
    parent_measure = MeasureSemantics(
        measure_id="measure:test:parent:parent_amount",
        source_attribute_id=_attribute("parent", "parent_amount"),
        entity_id=parent.entity_id,
        physical_table="parent",
        physical_column_or_path="parent_amount",
        native_grain=GrainKey(
            entity_id=parent.entity_id, key_attribute_ids=parent.key_attribute_ids
        ),
        aggregation_behavior=AggregationBehavior.ADDITIVE,
        provenance=("PUBLIC_SCHEMA",),
    )
    child_measure = MeasureSemantics(
        measure_id="measure:test:child:child_amount",
        source_attribute_id=_attribute("child", "child_amount"),
        entity_id=child.entity_id,
        physical_table="child",
        physical_column_or_path="child_amount",
        native_grain=GrainKey(entity_id=child.entity_id, key_attribute_ids=child.key_attribute_ids),
        aggregation_behavior=AggregationBehavior.ADDITIVE,
        provenance=("PUBLIC_SCHEMA",),
    )
    measures = [parent_measure, child_measure]
    if second_child:
        measures.append(
            child_measure.model_copy(
                update={
                    "measure_id": "measure:test:child_two:child_amount",
                    "source_attribute_id": _attribute("child_two", "child_amount"),
                    "entity_id": "entity:test:child_two",
                    "physical_table": "child_two",
                    "native_grain": GrainKey(
                        entity_id="entity:test:child_two",
                        key_attribute_ids=(_attribute("child_two", "child_two_id"),),
                    ),
                }
            )
        )
    return MeasureCatalog(
        entities=tuple(entities), relationships=tuple(relationships), measures=tuple(measures)
    )


UNSAFE_SQL = """
SELECT p.group_id,
       SUM(p.parent_amount) - COALESCE(SUM(c.child_amount), 0) AS net
FROM parent AS p
LEFT JOIN child AS c ON c.parent_id = p.parent_id
GROUP BY p.group_id
"""


def test_generic_fanout_is_normalized_and_idempotent() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    result = normalizer.normalize(UNSAFE_SQL)

    assert result.status is NormalizationStatus.NORMALIZED
    assert result.reason_code is NormalizationReason.NORMALIZED_CHILD_PREAGGREGATION
    assert result.input_diagnostic.code.value == "PARENT_MEASURE_FANOUT"
    assert result.output_diagnostic.code.value in {"PASS", "NOT_APPLICABLE"}
    assert "GROUP BY c__source.parent_id" in result.output_sql
    assert "SUM(c__grain.child_amount__grain_sum)" in result.output_sql

    second = normalizer.normalize(result.output_sql)
    assert second.status is NormalizationStatus.UNCHANGED
    assert second.reason_code is NormalizationReason.UNCHANGED_ALREADY_SAFE
    assert second.output_sql == result.output_sql


def test_normalized_sql_corrects_multi_child_semantics_without_distinct() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    result = normalizer.normalize(UNSAFE_SQL)
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE parent(parent_id INTEGER, group_id INTEGER, parent_amount INTEGER);
        CREATE TABLE child(child_id INTEGER, parent_id INTEGER, child_amount INTEGER);
        INSERT INTO parent VALUES (1, 10, 100), (2, 10, 100), (3, 20, 50);
        INSERT INTO child VALUES (1, 1, 30), (2, 1, 20), (3, 3, 10);
        """
    )
    raw = connection.execute(UNSAFE_SQL).fetchall()
    normalized = connection.execute(result.output_sql).fetchall()
    assert raw == [(10, 250), (20, 40)]
    assert normalized == [(10, 150), (20, 40)]
    assert "SUM(DISTINCT" not in result.output_sql.upper()


def test_safe_preaggregation_and_existence_are_byte_identical() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    safe = """
    SELECT p.group_id, SUM(p.parent_amount) - COALESCE(SUM(c.child_total), 0)
    FROM parent p
    LEFT JOIN (
        SELECT parent_id, SUM(child_amount) AS child_total
        FROM child GROUP BY parent_id
    ) c ON c.parent_id = p.parent_id
    GROUP BY p.group_id
    """
    existence = """
    SELECT p.group_id, COUNT(*)
    FROM parent p
    WHERE EXISTS (SELECT 1 FROM child c WHERE c.parent_id = p.parent_id)
    GROUP BY p.group_id
    """
    assert normalizer.normalize(safe).status is NormalizationStatus.UNCHANGED
    assert normalizer.normalize(safe).output_sql == safe
    assert normalizer.normalize(existence).status is NormalizationStatus.UNCHANGED
    assert normalizer.normalize(existence).output_sql == existence


def test_child_only_aggregation_is_unchanged() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    sql = """
    SELECT p.group_id, SUM(c.child_amount)
    FROM parent p
    LEFT JOIN child c ON c.parent_id = p.parent_id
    GROUP BY p.group_id
    """
    result = normalizer.normalize(sql)
    assert result.status is NormalizationStatus.UNCHANGED
    assert result.output_sql == sql


def test_distinct_mask_and_unsupported_shapes_abstain() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    distinct_sql = UNSAFE_SQL.replace("SUM(p.parent_amount)", "SUM(DISTINCT p.parent_amount)")
    filtered_sql = UNSAFE_SQL.replace(
        "GROUP BY p.group_id", "WHERE c.child_amount > 0 GROUP BY p.group_id"
    )
    assert normalizer.normalize(distinct_sql).status is NormalizationStatus.ABSTAIN
    assert normalizer.normalize(filtered_sql).status is NormalizationStatus.ABSTAIN


def test_multiple_fanout_edges_abstain_without_partial_rewrite() -> None:
    normalizer = GrainSafeNormalizer(_catalog(second_child=True))
    sql = UNSAFE_SQL.replace(
        "GROUP BY p.group_id",
        "LEFT JOIN child_two c2 ON c2.parent_id = p.parent_id GROUP BY p.group_id",
    )
    result = normalizer.normalize(sql)
    assert result.status is NormalizationStatus.ABSTAIN
    assert result.reason_code is NormalizationReason.ABSTAIN_MULTIPLE_FANOUT_EDGES
    assert result.output_sql == sql


def test_validator_and_normalizer_are_case_independent() -> None:
    normalizer = GrainSafeNormalizer(_catalog())
    validator = GrainSafetyValidator(_catalog())
    for value in (UNSAFE_SQL, "SELECT 1"):
        assert "warehouse_08" not in value
        assert "subscription_04" not in value
        assert validator.validate(value).code is not None
        assert normalizer.normalize(value).output_sql == (normalizer.normalize(value).output_sql)
