from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.generation.provider import ModelIOCapture, OpenAICompatibleProvider
from app.generation.semantic_plan_protocol import (
    provider_schema_errors,
    provider_semantic_query_plan_schema,
    provider_semantic_query_plan_schema_hash,
    semantic_query_plan_response_format,
)
from app.semantics.semantic_query import SemanticQueryPlan
from evaluation.audit_m29r1_projection import build_report


def test_provider_schema_is_deterministic_and_strict() -> None:
    first = provider_semantic_query_plan_schema()
    second = provider_semantic_query_plan_schema()
    assert first == second
    assert provider_semantic_query_plan_schema_hash()
    assert first["type"] == "object"
    assert first["additionalProperties"] is False
    assert "oneOf" not in json.dumps(first)
    assert "minLength" in json.dumps(first)
    assert "maxLength" in json.dumps(first)
    assert "minItems" in json.dumps(first)
    assert "maxItems" in json.dumps(first)
    assert '"pattern": "^[A-Za-z]' in json.dumps(first)
    assert "(?!" not in json.dumps(first)
    assert set(first["required"]) == set(first["properties"])


def test_native_response_format_contains_provider_schema() -> None:
    response_format = semantic_query_plan_response_format()
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["name"] == "semantic_query_plan"
    assert json_schema["strict"] is True
    assert json_schema["schema"] == provider_semantic_query_plan_schema()


@pytest.mark.asyncio
async def test_semantic_provider_sends_native_strict_schema_and_preserves_full_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = SemanticQueryPlan(
        database_id="db",
        from_entity_id="entity:orders",
        population_contract={"base_entity_ids": ("entity:orders",)},
        outputs=(
            {
                "position": 0,
                "semantic_role": "order id",
                "attribute_id": "attribute:orders.id",
            },
        ),
    )
    provider = OpenAICompatibleProvider(
        Settings(_env_file=None, llm_api_key="test", eval_capture_model_io=True)
    )
    captured: dict[str, object] = {}

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        captured["body"] = body
        return {
            "model": "gpt-5.6-luna",
            "choices": [{"message": {"content": plan.model_dump_json()}}],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    result = await provider.propose_semantic_query_plan("show order ids", "context")
    assert result.plan == plan
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["response_format"] == semantic_query_plan_response_format()
    capture = provider.consume_model_io()
    assert isinstance(capture, ModelIOCapture)
    assert capture.request_config["response_format"] == body["response_format"]
    assert capture.raw_assistant_content_full == plan.model_dump_json()


def test_canonical_schema_roundtrip_is_unchanged() -> None:
    plan = SemanticQueryPlan(
        database_id="db",
        from_entity_id="entity:orders",
        population_contract={"base_entity_ids": ("entity:orders",)},
        outputs=(
            {
                "position": 0,
                "semantic_role": "order id",
                "attribute_id": "attribute:orders.id",
            },
        ),
    )
    assert SemanticQueryPlan.model_validate_json(plan.model_dump_json()) == plan


def test_provider_structural_validator_preserves_required_and_closed_objects() -> None:
    valid = {
        "database_id": "db",
        "from_entity_id": "entity:orders",
        "from_source": None,
        "population_contract": {
            "base_entity_ids": ["entity:orders"],
            "required_relationship_ids": [],
            "inclusion_mode": "BASE_ENTITY",
            "result_grain_attribute_ids": [],
            "aggregation_grain_attribute_ids": [],
            "expected_cardinality": "UNKNOWN",
            "fanout_allowed": False,
        },
        "outputs": [
            {
                "position": 0,
                "semantic_role": "order id",
                "attribute_id": "attribute:orders.id",
                "expression": None,
                "alias": None,
            }
        ],
        "joins": [],
        "where": None,
        "group_by": [],
        "having": None,
        "order_by": [],
        "distinct": False,
        "limit": None,
        "offset": None,
        "calculation_contract": None,
        "description": None,
        "ctes": [],
        "derived_relations": [],
    }
    assert provider_schema_errors(valid) == ()
    missing = dict(valid)
    del missing["outputs"]
    assert any("$.outputs: required" == error for error in provider_schema_errors(missing))
    extra = dict(valid)
    extra["sql"] = "SELECT 1"
    assert any("$.sql: extra" == error for error in provider_schema_errors(extra))


def test_m29r1_projection_parity_preserves_supported_constraints() -> None:
    report = build_report()
    assert report["provider_calls"] == 0
    assert report["canonical_schema_hash"] == (
        "0b7a74cf4df1e6cecaed019ebd677c2229dea527d16e1408f8a933852a3dc30e"
    )
    assert report["provider_schema_hash"] == provider_semantic_query_plan_schema_hash()
    constraints = report["constraints"]
    assert constraints["length_and_array_bounds"]["parity_after"] == "preserved"
    assert constraints["simple_pattern"]["parity_after"] == "preserved_when_provider_supported"
    assert constraints["regex_lookaround"]["provider_enforceable"] is False
