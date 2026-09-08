import json
from pathlib import Path

import pytest

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.semantics.logical_plan import (
    LogicalAttribute,
    LogicalOuterAttribute,
    LogicalOutput,
    LogicalPlanResolver,
    LogicalProject,
    LogicalQueryPlanV1,
    LogicalRelate,
    LogicalRelationMode,
    LogicalScan,
)
from app.semantics.semantic_compiler import SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator


def test_server_hidden_relationship_key_survives_cte_scope_without_becoming_output() -> None:
    mapping = SemanticMappingSnapshot.from_schema(
        build_default_catalog(Base.metadata), database_id="test"
    )
    logical = LogicalQueryPlanV1(
        steps=(
            LogicalScan(entity_id="entity:orders"),
            LogicalProject(
                input_step=0,
                outputs=(
                    LogicalOutput(expression=LogicalAttribute(attribute_id="attribute:orders.id")),
                ),
            ),
            LogicalRelate(
                input_step=1,
                entity_id="entity:customers",
                mode=LogicalRelationMode.MATCHING,
            ),
            LogicalProject(
                input_step=2,
                outputs=(
                    LogicalOutput(
                        expression=LogicalAttribute(attribute_id="attribute:customers.name")
                    ),
                ),
            ),
        ),
        final_step=3,
    )
    resolved = LogicalPlanResolver(mapping, "test").resolve(logical).plan
    ir = plan_to_ir(resolved)
    compiled = SemanticQueryCompiler(mapping).compile(ir)
    assert len(ir.select) == 1
    assert "__m31_hidden" in compiled.sql
    assert SemanticConsistencyValidator(mapping).validate(ir, compiled).accepted


def test_provider_contract_has_no_server_scope_or_key_fields() -> None:
    from app.generation.semantic_plan_protocol import provider_logical_query_plan_schema

    schema_text = str(provider_logical_query_plan_schema())
    for forbidden in (
        "hidden_join_key",
        "scope_id",
        "cte_name",
        "physical_join_key",
        "join_condition",
    ):
        assert forbidden not in schema_text


def test_outer_attribute_is_typed_and_bounded_without_physical_scope_fields() -> None:
    reference = LogicalOuterAttribute(attribute_id="attribute:customers.id", scope_depth=1)
    assert reference.kind == "outer_attribute"
    assert reference.scope_depth == 1
    schema_text = str(LogicalQueryPlanV1.model_json_schema())
    assert "outer_attribute" in schema_text
    for forbidden in ("physical_alias", "sql_alias", "join_key", "cte_name"):
        assert forbidden not in schema_text


def test_authority_normalized_manifest_excludes_mental_from_resolver_denominator() -> None:
    path = Path("evaluation/fixtures/m31_authority_normalized_manifest.json")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    cases = {item["case_id"]: item for item in manifest["cases"]}
    assert manifest["legitimately_server_representable_count"] == 15
    assert cases["mental_1"]["authority_normalized_classification"] == "METADATA_BLOCKED"
    assert cases["mental_1"]["gold_derived_metadata_added"] is False


def test_outer_attribute_scope_depth_is_bounded() -> None:
    with pytest.raises(ValueError):
        LogicalOuterAttribute(attribute_id="attribute:customers.id", scope_depth=0)
    with pytest.raises(ValueError):
        LogicalOuterAttribute(attribute_id="attribute:customers.id", scope_depth=5)
