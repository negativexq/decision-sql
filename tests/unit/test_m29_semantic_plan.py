from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import (
    AttributeRef,
    BinaryExpression,
    BinaryOperator,
    LiteralExpression,
    PlannedOutput,
    PopulationContract,
    SemanticQueryPlan,
)
from evaluation.external.livesqlbench.protected import LiveSqlBenchEvaluationCase
from evaluation.run_m29_semantic_plan import (
    _component_scores,
    _leakage_free,
    _plan_signature,
)


@pytest.fixture
def mapping() -> SemanticMappingSnapshot:
    return SemanticMappingSnapshot.from_schema(build_default_catalog(Base.metadata))


def _plan(mapping: SemanticMappingSnapshot) -> SemanticQueryPlan:
    del mapping
    return SemanticQueryPlan(
        database_id="test",
        from_entity_id="entity:orders",
        population_contract=PopulationContract(base_entity_ids=("entity:orders",)),
        outputs=(
            PlannedOutput(
                position=0,
                semantic_role="order id",
                attribute_id="attribute:orders.id",
            ),
        ),
    )


def test_semantic_plan_has_no_raw_sql_escape_hatch() -> None:
    with pytest.raises(ValidationError):
        SemanticQueryPlan.model_validate(
            {
                "database_id": "test",
                "from_entity_id": "entity:orders",
                "population_contract": {"base_entity_ids": ["entity:orders"]},
                "outputs": [
                    {"position": 0, "semantic_role": "id", "attribute_id": "attribute:orders.id"}
                ],
                "sql": "SELECT id FROM orders",
            }
        )


def test_plan_signature_ignores_display_only_output_labels(
    mapping: SemanticMappingSnapshot,
) -> None:
    first = _plan(mapping)
    second = first.model_copy(
        update={
            "outputs": (
                PlannedOutput(
                    position=0,
                    semantic_role="requested identifier",
                    alias="display_id",
                    attribute_id="attribute:orders.id",
                ),
            )
        }
    )
    assert _plan_signature(first) == _plan_signature(second)
    assert all(_component_scores(first, second, mapping).values())


def test_plan_signature_retains_semantic_filter_value(mapping: SemanticMappingSnapshot) -> None:
    first = _plan(mapping).model_copy(
        update={
            "where": BinaryExpression(
                operator=BinaryOperator.GTE,
                left=AttributeRef(attribute_id="attribute:orders.id"),
                right=LiteralExpression(value=10, value_type="integer"),
            )
        }
    )
    second = first.model_copy(
        update={
            "where": BinaryExpression(
                operator=BinaryOperator.GTE,
                left=AttributeRef(attribute_id="attribute:orders.id"),
                right=LiteralExpression(value=11, value_type="integer"),
            )
        }
    )
    assert _plan_signature(first)["where"] != _plan_signature(second)["where"]


def test_provider_context_leakage_firewall_rejects_gold_and_old_sql() -> None:
    class Case:
        sol_sql = ("SELECT secret FROM protected",)

    case = cast(LiveSqlBenchEvaluationCase, cast(Any, Case()))
    assert not _leakage_free(case, "question and reference result", None)
    assert not _leakage_free(
        case, "server context SELECT old FROM orders", "SELECT old FROM orders"
    )
    assert _leakage_free(case, "server context", None)
