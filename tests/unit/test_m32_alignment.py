from app.generation.semantic_plan_protocol import (
    logical_synthesis_response_format,
    provider_query_alignment_schema,
    query_alignment_response_format,
    query_alignment_schema_errors,
    query_sql_response_format,
    schema_alignment_response_format,
)
from app.semantics.m32_alignment import (
    AlignmentGrounder,
    AlignmentPopulationMode,
    LogicalSynthesisV1,
    QueryAlignmentV1,
    SchemaAlignmentV1,
    combine_schema_and_logic,
    validate_alignment,
    validate_logical_synthesis,
    validate_schema_alignment,
)
from app.semantics.semantic_mapping import (
    SemanticAttributeMapping,
    SemanticEntityMapping,
    SemanticMappingSnapshot,
    SemanticRelationshipMapping,
)


def _mapping() -> SemanticMappingSnapshot:
    return SemanticMappingSnapshot(
        entities=(
            SemanticEntityMapping(entity_id="entity:a", physical_table="a"),
            SemanticEntityMapping(entity_id="entity:b", physical_table="b"),
        ),
        attributes=(
            SemanticAttributeMapping(
                attribute_id="attribute:a.id",
                entity_id="entity:a",
                physical_column="id",
                data_type="integer",
            ),
            SemanticAttributeMapping(
                attribute_id="attribute:b.a_id",
                entity_id="entity:b",
                physical_column="a_id",
                data_type="integer",
            ),
        ),
        relationships=(
            SemanticRelationshipMapping(
                relationship_id="relationship:b.a_id->a.id",
                from_entity_id="entity:b",
                from_attribute_id="attribute:b.a_id",
                to_entity_id="entity:a",
                to_attribute_id="attribute:a.id",
            ),
        ),
    )


def _alignment() -> QueryAlignmentV1:
    return QueryAlignmentV1(
        anchor_entity_id="entity:a",
        relevant_entity_ids=("entity:a", "entity:b"),
        relevant_attribute_ids=("attribute:a.id", "attribute:b.a_id"),
        relationship_ids=("relationship:b.a_id->a.id",),
        population_mode=AlignmentPopulationMode.MATCHED_ONLY,
        logic_summary="Return matching entities.",
    )


def test_alignment_provider_schema_is_strict_and_small() -> None:
    schema = provider_query_alignment_schema()
    assert len(str(schema)) < 12_000
    assert query_alignment_response_format()["json_schema"]["strict"] is True
    assert query_sql_response_format()["json_schema"]["strict"] is True
    assert schema_alignment_response_format()["json_schema"]["strict"] is True
    assert logical_synthesis_response_format()["json_schema"]["strict"] is True


def test_alignment_rejects_unknown_ids_and_sql_escape_fields() -> None:
    invalid = _alignment().model_dump(mode="json")
    invalid["relevant_attribute_ids"] = ["attribute:unknown"]
    invalid["sql"] = "SELECT 1"
    assert query_alignment_schema_errors(invalid)
    invalid.pop("sql")
    try:
        validate_alignment(QueryAlignmentV1.model_validate(invalid), _mapping())
    except Exception as error:
        assert getattr(error, "code", None) == "UNKNOWN_ATTRIBUTE"
    else:  # pragma: no cover
        raise AssertionError("unknown alignment attribute was accepted")


def test_grounder_maps_selected_semantics_and_authorized_relationship() -> None:
    grounded = AlignmentGrounder(_mapping(), "db").ground("q", _alignment())
    assert {item["physical_table"] for item in grounded.entities} == {"a", "b"}
    assert {item["physical_column"] for item in grounded.attributes} == {"id", "a_id"}
    assert grounded.relationships[0].source_column == "a_id"
    assert grounded.relationships[0].target_column == "id"


def test_grounder_fails_closed_without_relationship_metadata() -> None:
    alignment = _alignment().model_copy(update={"relationship_ids": ()})
    mapping = _mapping().model_copy(update={"relationships": ()})
    try:
        AlignmentGrounder(mapping, "db").ground("q", alignment)
    except Exception as error:
        assert getattr(error, "code", None) == "MISSING_SERVER_RELATIONSHIP"
    else:  # pragma: no cover
        raise AssertionError("missing relationship metadata was silently accepted")


def test_v2a_schema_and_logic_are_separate_and_composable() -> None:
    mapping = _mapping()
    schema = SchemaAlignmentV1(
        anchor_entity_id="entity:a",
        relevant_entity_ids=("entity:a", "entity:b"),
        relevant_attribute_ids=("attribute:a.id", "attribute:b.a_id"),
        logic_summary="Use the related records.",
    )
    validate_schema_alignment(schema, mapping)
    logic = LogicalSynthesisV1(logic_summary="Return matching records.")
    validate_logical_synthesis(logic, mapping, set(schema.relevant_attribute_ids))
    combined = combine_schema_and_logic(schema, logic)
    validate_alignment(combined, mapping)
    assert combined.relationship_ids == ()
    assert combined.relevant_attribute_ids == schema.relevant_attribute_ids


def test_v2a_logic_cannot_reintroduce_unselected_schema_attribute() -> None:
    mapping = _mapping()
    logic = LogicalSynthesisV1(
        grouping_attribute_ids=("attribute:b.a_id",), logic_summary="Group the results."
    )
    try:
        validate_logical_synthesis(logic, mapping, {"attribute:a.id"})
    except Exception as error:
        assert getattr(error, "code", None) == "LOGIC_ATTRIBUTE_NOT_SELECTED"
    else:  # pragma: no cover
        raise AssertionError("logical stage selected an attribute outside schema stage")
