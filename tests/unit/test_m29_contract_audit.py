from __future__ import annotations

from app.generation.provider import _semantic_query_plan_messages
from evaluation.audit_m29_contract_acquisition import (
    _error_category,
    _partial_top_level_keys,
    _prompt_audit,
)


def test_partial_top_level_keys_are_structural_only() -> None:
    raw = '{"database_id":"x","outputs":[{"kind":"attribute"},'
    assert _partial_top_level_keys(raw) == ["database_id", "outputs"]


def test_schema_error_categories_are_bounded() -> None:
    assert _error_category({"type": "missing"}) == "MISSING_REQUIRED_FIELD"
    assert _error_category({"type": "extra_forbidden"}) == "EXTRA_FIELD"
    assert _error_category({"type": "union_tag_invalid"}) == "INVALID_DISCRIMINATOR"
    assert _error_category({"type": "literal_error"}) == "INVALID_ENUM"


def test_semantic_prompt_has_no_legacy_contract_names_or_examples() -> None:
    prompt = _semantic_query_plan_messages("QUESTION", "CONTEXT")[0]["content"]
    assert "QueryPlanV1" not in prompt
    assert "QueryPlanWireV2" not in prompt
    assert "SemanticQueryPlan" not in prompt
    assert "{" not in prompt
    audit = _prompt_audit()
    assert audit["legacy_and_hardened_identical"] is True
    assert audit["explicit_json_output_examples"] == 0
