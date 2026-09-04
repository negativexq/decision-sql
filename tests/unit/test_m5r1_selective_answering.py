from __future__ import annotations

import pytest

from app.config import Settings
from app.generation.provider import MalformedProviderResponse, OpenAICompatibleProvider
from app.semantics.catalog import build_m3_catalog
from app.semantics.contract import build_semantic_contract
from evaluation.m4_benchmark import _MEMORY_SOURCES, _TARGET_SOURCES
from evaluation.m5r1_benchmark import (
    SelectiveAction,
    benchmark_hash,
    build_benchmark,
    split_hash,
    validate_benchmark,
)
from evaluation.selective_answering import (
    CandidateInterpretation,
    InterpretationValidator,
    R1Decision,
    R2Response,
    SemanticKind,
    deterministic_r0,
    parse_r1_content,
    parse_r2_content,
    provider_r1,
    provider_r2,
    r1_request_body,
    response_schema,
    response_schema_hash,
)


def test_m5r1_benchmark_is_balanced_and_frozen_shape() -> None:
    cases = build_benchmark()
    validation = validate_benchmark(cases)

    assert len(cases) == 150
    assert validation["dev"] == 100
    assert validation["holdout"] == 50
    assert validation["action_counts"] == {"ANSWER": 50, "CLARIFY": 50, "ABSTAIN": 50}
    assert validation["contrast_group_count"] == 50
    assert benchmark_hash(cases) == validation["corpus_hash"]
    assert split_hash(cases, "DEV") == validation["dev_hash"]
    assert split_hash(cases, "HOLDOUT") == validation["holdout_hash"]
    assert all(case.naturalness_status.value == "NATURAL" for case in cases)


def test_m5r1_semantic_ids_are_server_owned_and_questions_do_not_reuse_m4() -> None:
    cases = build_benchmark()
    contract_ids = {item.stable_id for item in build_semantic_contract(build_m3_catalog()).objects}
    referenced = {stable_id for case in cases for stable_id in case.relevant_semantic_ids}
    assert referenced <= contract_ids

    m4_questions = {
        question
        for source in (_MEMORY_SOURCES, _TARGET_SOURCES)
        for family in source.values()
        for question, _ in family
    }
    assert not ({case.question for case in cases} & m4_questions)


def test_r0_is_deterministic_and_does_not_require_gold_metadata() -> None:
    question = "Show refund rate by region."
    first = deterministic_r0(question)
    second = deterministic_r0(question)
    assert first == second
    assert first.action is SelectiveAction.ANSWER
    assert first.resolved_semantic_ids == ("metric:refunded_order_rate",)
    assert deterministic_r0("Show supplier ratings.").action is SelectiveAction.ABSTAIN
    assert deterministic_r0("Show the best products.").action is SelectiveAction.CLARIFY


def test_r2_validator_has_frozen_zero_one_many_policy_and_canonical_dedup() -> None:
    validator = InterpretationValidator()
    zero = validator.validate(R2Response())
    one = validator.validate(
        R2Response(
            interpretations=(
                CandidateInterpretation(
                    interpretation_key="refund rate",
                    semantic_kind=SemanticKind.METRIC,
                    metric_id="metric:refunded_order_rate",
                ),
            )
        )
    )
    many = validator.validate(
        R2Response(
            interpretations=(
                CandidateInterpretation(
                    interpretation_key="refund rate",
                    semantic_kind=SemanticKind.METRIC,
                    metric_id="metric:refunded_order_rate",
                ),
                CandidateInterpretation(
                    interpretation_key="refunded order rate",
                    semantic_kind=SemanticKind.METRIC,
                    metric_id="metric:refunded_order_rate",
                ),
                CandidateInterpretation(
                    interpretation_key="revenue",
                    semantic_kind=SemanticKind.METRIC,
                    metric_id="metric:completed_revenue",
                ),
            )
        )
    )
    assert zero.action is SelectiveAction.ABSTAIN
    assert one.action is SelectiveAction.ANSWER
    assert many.action is SelectiveAction.CLARIFY
    assert many.canonical_dedup_count == 1
    assert many.valid_interpretation_keys == ("refund rate", "revenue")


def test_r2_rejects_unknown_ids_and_duplicate_or_sql_protocol_fields() -> None:
    validator = InterpretationValidator()
    result = validator.validate(
        R2Response(
            interpretations=(
                CandidateInterpretation(
                    interpretation_key="invented",
                    semantic_kind=SemanticKind.METRIC,
                    metric_id="metric:not_in_contract",
                ),
            )
        )
    )
    assert result.action is SelectiveAction.ABSTAIN
    assert result.invalid_interpretation_count == 1

    assert parse_r1_content(
        '{"action":"ANSWER","reason_code":"clear","resolved_semantic_ids":[]}'
    ) == R1Decision(
        action=SelectiveAction.ANSWER,
        reason_code="clear",
        resolved_semantic_ids=(),
    )
    with pytest.raises(MalformedProviderResponse):
        parse_r1_content('{"action":"ANSWER","reason_code":"x","sql":"SELECT 1"}')
    with pytest.raises(MalformedProviderResponse):
        parse_r2_content('{"action":"ANSWER","interpretations":[]}')


def test_protocol_schemas_are_strict_and_stable() -> None:
    r1 = response_schema("R1")
    r2 = response_schema("R2")
    assert r1["additionalProperties"] is False
    assert r2["additionalProperties"] is False
    assert r1["required"] == ["action", "reason_code", "resolved_semantic_ids"]
    assert r2["required"] == ["interpretations"]
    assert (
        response_schema_hash("R1")
        == "6ebc494d7897182fd2744e79441aa2e12fa12125fae49bba9b7b7d85f3e0e800"
    )


def test_r1_parser_rejects_noncompliant_dto_shapes_without_coercion() -> None:
    invalid_payloads = (
        '{"action":"ANSWER","reason_code":"clear"}',
        '{"action":"MAYBE","reason_code":"clear","resolved_semantic_ids":[]}',
        '{"action":"ANSWER","reason_code":"clear",'
        '"resolved_semantic_ids":[],"extra":"not-allowed"}',
    )
    for payload in invalid_payloads:
        with pytest.raises(MalformedProviderResponse):
            parse_r1_content(payload)


def test_r1_request_contract_is_attached_on_every_request() -> None:
    provider = OpenAICompatibleProvider(Settings(llm_api_key="test-only"))
    body = r1_request_body(provider, "question", "context")

    response_format = body["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["schema"] == response_schema("R1")
    assert body["reasoning_effort"] == "none"
    assert (
        response_schema_hash("R2")
        == "d93fc828be70cf7564ac0e0e39ee74b0b12fddbc44cdc61f8f63104460f9b019"
    )


@pytest.mark.asyncio
async def test_provider_boundary_sends_native_strict_schema_without_coercion() -> None:
    provider = OpenAICompatibleProvider(Settings(llm_api_key="test-only"))
    sent: list[dict[str, object]] = []

    async def fake_post(body: dict[str, object]) -> dict[str, object]:
        sent.append(body)
        if len(sent) == 1:
            content = '{"action":"ANSWER","reason_code":"clear","resolved_semantic_ids":[]}'
        else:
            content = (
                '{"interpretations":[{"interpretation_key":"x",'
                '"semantic_kind":"METRIC","metric_id":"metric:completed_revenue",'
                '"measure_id":null,"entity_id":null,"dimension_ids":[],'
                '"filter_target_ids":[],"ranking_basis":null}]}'
            )
        return {
            "model": body["model"],
            "choices": [{"message": {"content": content}}],
            "usage": {},
        }

    provider._post = fake_post  # type: ignore[method-assign]
    await provider_r1(provider, "smoke", "context")
    await provider_r2(provider, "smoke", "context")
    assert all(body["response_format"]["type"] == "json_schema" for body in sent)  # type: ignore[index]
    assert all(body["response_format"]["json_schema"]["strict"] is True for body in sent)  # type: ignore[index]
