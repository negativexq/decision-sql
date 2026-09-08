from pathlib import Path

import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.semantic_plan_protocol import (
    provider_logical_query_plan_schema,
    provider_logical_query_plan_schema_hash,
)
from app.semantics.logical_plan import (
    LogicalAttribute,
    LogicalOutput,
    LogicalPlanResolver,
    LogicalProject,
    LogicalQueryPlanV1,
    LogicalScan,
)
from app.semantics.semantic_compiler import SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import plan_to_ir


def _mapping() -> SemanticMappingSnapshot:
    return SemanticMappingSnapshot.from_schema(build_default_catalog(Base.metadata))


def _simple_plan() -> LogicalQueryPlanV1:
    return LogicalQueryPlanV1(
        steps=(
            LogicalScan(entity_id="entity:customers"),
            LogicalProject(
                input_step=0,
                outputs=(
                    LogicalOutput(
                        expression=LogicalAttribute(attribute_id="attribute:customers.name")
                    ),
                ),
            ),
        ),
        final_step=1,
    )


def test_logical_plan_resolves_and_compiles_without_physical_model_fields() -> None:
    mapping = _mapping()
    resolved = LogicalPlanResolver(mapping, "test").resolve(_simple_plan()).plan
    compiled = SemanticQueryCompiler(mapping).compile(plan_to_ir(resolved))
    assert "customers" in compiled.sql
    assert "raw_sql" not in LogicalQueryPlanV1.model_json_schema()
    assert "join_condition" not in LogicalQueryPlanV1.model_json_schema()


def test_logical_plan_rejects_forward_result_references() -> None:
    with pytest.raises(ValidationError, match="forward"):
        LogicalQueryPlanV1.model_validate(
            {
                "steps": [
                    {"kind": "SCAN", "entity_id": "entity:customers"},
                    {
                        "kind": "PROJECT",
                        "input_step": 0,
                        "outputs": [{"expression": {"kind": "result", "step": 1, "slot": 0}}],
                    },
                ],
                "final_step": 1,
            }
        )


def test_provider_projection_is_strict_and_has_no_sql_escape_hatches() -> None:
    schema = provider_logical_query_plan_schema()
    serialized = str(schema)
    assert schema["$defs"]
    assert provider_logical_query_plan_schema_hash()
    for forbidden in (
        "raw_sql",
        "expression_sql",
        "join_condition",
        "physical_table",
        "physical_column",
        "arbitrary_json",
    ):
        assert forbidden not in serialized


def test_oracle_fixtures_are_evaluation_only() -> None:
    runtime = Path(__file__).parents[2] / "app"
    assert not any(
        "m31_logical" in path.read_text(encoding="utf-8") for path in runtime.rglob("*.py")
    )
