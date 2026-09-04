"""Provider-facing evaluation arms for M5R.1.

This module is deliberately evaluation-only.  It contains no SQL generation,
no execution authority, and no production answerability runtime.  R0 is a
small deterministic baseline; R2 validates a bounded interpretation DTO.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.generation.provider import (
    MalformedProviderResponse,
    OpenAICompatibleProvider,
)
from app.semantics.catalog import build_m3_catalog
from app.semantics.contract import SemanticContractSnapshot, build_semantic_contract
from evaluation.m5r1_benchmark import SelectiveAction, SelectiveAnsweringCase

R1_DTO_VERSION = "m5r1-r1-decision-v1"
R2_DTO_VERSION = "m5r1-r2-interpretation-v1"
R2_VALIDATOR_VERSION = "m5r1-r2-validator-v1"


class SemanticKind(StrEnum):
    METRIC = "METRIC"
    MEASURE = "MEASURE"
    ENTITY = "ENTITY"
    DIMENSION = "DIMENSION"
    FILTER = "FILTER"
    RANKING = "RANKING"


class R1Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SelectiveAction
    reason_code: str = Field(min_length=1, max_length=80)
    resolved_semantic_ids: tuple[str, ...] = Field(max_length=8)


class CandidateInterpretation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    interpretation_key: str = Field(min_length=1, max_length=120)
    semantic_kind: SemanticKind
    metric_id: str | None = None
    measure_id: str | None = None
    entity_id: str | None = None
    dimension_ids: tuple[str, ...] = Field(default=(), max_length=8)
    filter_target_ids: tuple[str, ...] = Field(default=(), max_length=8)
    ranking_basis: str | None = Field(default=None, max_length=120)


class R2Response(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    interpretations: tuple[CandidateInterpretation, ...] = Field(default=(), max_length=8)


class R2Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: SelectiveAction
    reason_code: str = Field(min_length=1, max_length=80)
    valid_interpretation_keys: tuple[str, ...] = Field(default=(), max_length=8)
    invalid_interpretation_count: int = Field(default=0, ge=0)
    canonical_dedup_count: int = Field(default=0, ge=0)
    proposed_interpretation_count: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class ArmResult:
    case_id: str
    action: SelectiveAction
    reason_code: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    r2: R2Decision | None = None


def _hash_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def response_schema(arm: Literal["R1", "R2"]) -> dict[str, Any]:
    """Return the strict machine response contract sent to the provider."""
    if arm == "R1":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "reason_code", "resolved_semantic_ids"],
            "properties": {
                "action": {"type": "string", "enum": ["ANSWER", "CLARIFY", "ABSTAIN"]},
                "reason_code": {"type": "string"},
                "resolved_semantic_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
            },
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["interpretations"],
        "properties": {
            "interpretations": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "interpretation_key",
                        "semantic_kind",
                        "metric_id",
                        "measure_id",
                        "entity_id",
                        "dimension_ids",
                        "filter_target_ids",
                        "ranking_basis",
                    ],
                    "properties": {
                        "interpretation_key": {"type": "string"},
                        "semantic_kind": {
                            "type": "string",
                            "enum": [
                                "METRIC",
                                "MEASURE",
                                "ENTITY",
                                "DIMENSION",
                                "FILTER",
                                "RANKING",
                            ],
                        },
                        "metric_id": {"type": ["string", "null"]},
                        "measure_id": {"type": ["string", "null"]},
                        "entity_id": {"type": ["string", "null"]},
                        "dimension_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 8,
                        },
                        "filter_target_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 8,
                        },
                        "ranking_basis": {"type": ["string", "null"]},
                    },
                },
            }
        },
    }


def response_schema_hash(arm: Literal["R1", "R2"]) -> str:
    encoded = json.dumps(
        response_schema(arm), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _hash_text(encoded)


def local_r1_schema_hash() -> str:
    encoded = json.dumps(
        R1Decision.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _hash_text(encoded)


def render_server_context(contract: SemanticContractSnapshot | None = None) -> str:
    """Render bounded public semantic context shared by R1 and R2."""
    contract = contract or build_semantic_contract(build_m3_catalog())
    lines = [
        "SERVER-OWNED DECISIONSQL CONTEXT",
        f"semantic_contract_hash={contract.semantic_contract_hash}",
        "ACTIVE SEMANTIC OBJECTS:",
    ]
    for item in contract.objects:
        if item.lifecycle_status.value != "ACTIVE":
            continue
        description = item.description or "(no description)"
        lines.append(f"- {item.stable_id}: {description}")
    lines.extend(
        (
            "SUPPORTED ACTIONS:",
            "ANSWER = exactly one supported interpretation, including a canonical "
            "governed default.",
            "CLARIFY = two or more materially different supported interpretations remain.",
            "ABSTAIN = the requested concept or relationship is unavailable in this context.",
            "Do not output SQL, prose reasoning, hidden reasoning, or clarification wording.",
        )
    )
    return "\n".join(lines)


def prompt_hash(arm: Literal["R1", "R2"], context: str) -> str:
    if arm == "R1":
        template = (
            "You are a selective-answering classifier. Return one JSON object with "
            "action, reason_code, and resolved_semantic_ids."
        )
    else:
        template = (
            "You are an interpretation proposer. Return one JSON object with an "
            "interpretations array using only the supplied DTO fields."
        )
    return _hash_text(template + "\n" + context)


_GOVERNED_ALIASES: tuple[tuple[str, str], ...] = (
    ("refund rate", "metric:refunded_order_rate"),
    ("completed revenue", "metric:completed_revenue"),
    ("average completed order value", "metric:average_completed_order_value"),
    ("average payment amount", "metric:average_payment_amount"),
    ("payment success rate", "metric:payment_success_rate"),
    ("refunded order rate", "metric:refunded_order_rate"),
    ("refund to revenue rate", "metric:refund_to_revenue_rate"),
    ("average items per completed order", "metric:average_items_per_completed_order"),
    ("completed order rate", "metric:completed_order_rate"),
    ("customer order coverage rate", "metric:customer_order_coverage_rate"),
)

_UNSUPPORTED_CUES = (
    "marital",
    "supplier",
    "employee",
    "department",
    "warehouse",
    "carrier",
    "credit score",
    "commission",
    "warranty",
    "satisfaction",
    "loyalty",
    "payroll",
    "website",
    "campaign",
    "ticket",
    "churn",
    "inventory",
    "forecast",
    "occupation",
    "industry",
    "card network",
)

_AMBIGUOUS_CUES = (
    "best",
    "strongest",
    "leading",
    "most valuable",
    "high-value",
    "important",
    "expensive",
    "preferred",
    "performance",
    "successful",
    "sales",
    "active",
    "large",
    "top",
)


def deterministic_r0(question: str) -> R1Decision:
    """A deliberately narrow, non-provider baseline."""
    normalized = question.casefold()
    for cue in _UNSUPPORTED_CUES:
        if cue in normalized:
            return R1Decision(
                action=SelectiveAction.ABSTAIN,
                reason_code="UNSUPPORTED_CONCEPT",
                resolved_semantic_ids=(),
            )
    for phrase, semantic_id in _GOVERNED_ALIASES:
        if phrase in normalized:
            return R1Decision(
                action=SelectiveAction.ANSWER,
                reason_code="GOVERNED_DEFAULT_OR_EXPLICIT",
                resolved_semantic_ids=(semantic_id,),
            )
    if any(cue in normalized for cue in _AMBIGUOUS_CUES):
            return R1Decision(
                action=SelectiveAction.CLARIFY,
                reason_code="POSSIBLE_MULTI_MATCH",
                resolved_semantic_ids=(),
            )
    return R1Decision(
        action=SelectiveAction.ANSWER,
        reason_code="EXACT_OR_CLEAR_DIRECT_MATCH",
        resolved_semantic_ids=(),
    )


class InterpretationValidator:
    """Validate R2 DTOs against the immutable M3 contract."""

    def __init__(self, contract: SemanticContractSnapshot | None = None) -> None:
        self.contract = contract or build_semantic_contract(build_m3_catalog())
        self._objects = {item.stable_id: item for item in self.contract.objects}

    def _valid_id(self, stable_id: str, prefix: str) -> bool:
        item = self._objects.get(stable_id)
        return (
            item is not None
            and stable_id.startswith(prefix + ":")
            and item.lifecycle_status.value == "ACTIVE"
        )

    def _canonical_key(self, item: CandidateInterpretation) -> str:
        values = (
            item.metric_id or "",
            item.measure_id or "",
            item.entity_id or "",
            ",".join(sorted(item.dimension_ids)),
            ",".join(sorted(item.filter_target_ids)),
            item.ranking_basis or "",
        )
        return "|".join(values)

    def validate(self, response: R2Response, question: str = "") -> R2Decision:
        del question
        valid: list[tuple[str, str]] = []
        invalid_count = 0
        for item in response.interpretations:
            valid_item = True
            if item.metric_id and not self._valid_id(item.metric_id, "metric"):
                valid_item = False
            if item.measure_id and not self._valid_id(item.measure_id, "measure"):
                valid_item = False
            if item.entity_id and not self._valid_id(item.entity_id, "entity"):
                valid_item = False
            if any(not self._valid_id(value, "dimension") for value in item.dimension_ids):
                valid_item = False
            if any(not self._valid_id(value, "entity") for value in item.filter_target_ids):
                valid_item = False
            if not any((item.metric_id, item.measure_id, item.entity_id, item.dimension_ids)):
                valid_item = False
            if not valid_item:
                invalid_count += 1
                continue
            valid.append((self._canonical_key(item), item.interpretation_key))
        unique: dict[str, str] = {}
        for canonical, display_key in valid:
            unique.setdefault(canonical, display_key)
        dedup_count = len(valid) - len(unique)
        keys = tuple(unique.values())
        if len(keys) == 0:
            action = SelectiveAction.ABSTAIN
            reason = "NO_VALID_SUPPORTED_INTERPRETATION"
        elif len(keys) == 1:
            action = SelectiveAction.ANSWER
            reason = "ONE_VALID_SUPPORTED_INTERPRETATION"
        else:
            action = SelectiveAction.CLARIFY
            reason = "MULTIPLE_MATERIALLY_DIFFERENT_INTERPRETATIONS"
        return R2Decision(
            action=action,
            reason_code=reason,
            valid_interpretation_keys=keys,
            invalid_interpretation_count=invalid_count,
            canonical_dedup_count=dedup_count,
            proposed_interpretation_count=len(response.interpretations),
        )


def _contains_sql(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.search(r"\b(select|insert|update|delete|with)\b", value, re.IGNORECASE))


def parse_r1_content(content: str) -> R1Decision:
    try:
        payload = json.loads(content)
        if any(key in payload for key in ("sql", "query", "candidate_sql")):
            raise MalformedProviderResponse("M5R.1 R1 response contained SQL fields")
        if _contains_sql(content):
            raise MalformedProviderResponse("M5R.1 R1 response contained SQL text")
        return R1Decision.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, MalformedProviderResponse):
            raise
        raise MalformedProviderResponse("M5R.1 R1 response did not match the frozen DTO") from error


def parse_r2_content(content: str) -> R2Response:
    try:
        payload = json.loads(content)
        if any(key in payload for key in ("sql", "query", "candidate_sql", "action")):
            raise MalformedProviderResponse("M5R.1 R2 response contained decision or SQL fields")
        if _contains_sql(content):
            raise MalformedProviderResponse("M5R.1 R2 response contained SQL text")
        return R2Response.model_validate(payload)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        if isinstance(error, MalformedProviderResponse):
            raise
        raise MalformedProviderResponse("M5R.1 R2 response did not match the frozen DTO") from error


def _messages(arm: Literal["R1", "R2"], question: str, context: str) -> list[dict[str, str]]:
    if arm == "R1":
        system = (
            "Classify whether the question should proceed before SQL generation. "
            "Return JSON only: {action: ANSWER|CLARIFY|ABSTAIN, reason_code: string, "
            "resolved_semantic_ids: string[]}. Never output SQL.\n\n"
        )
    else:
        system = (
            "Propose zero or more minimal supported interpretations before SQL generation. "
            "Return JSON only with interpretations[]. Never output SQL or a final action.\n\n"
        )
    return [{"role": "system", "content": system + context}, {"role": "user", "content": question}]


def r1_request_body(
    provider: OpenAICompatibleProvider, question: str, context: str
) -> dict[str, Any]:
    return {
        "model": provider.settings.llm_model,
        "messages": _messages("R1", question, context),
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "m5r1_r1_decision",
                "strict": True,
                "schema": response_schema("R1"),
            },
        },
        "reasoning_effort": "none",
    }


async def provider_r1(
    provider: OpenAICompatibleProvider, question: str, context: str
) -> tuple[R1Decision, int | None, int | None, float]:
    started = perf_counter()
    payload = await provider._post(r1_request_body(provider, question, context))
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    decision = parse_r1_content(content)
    usage = payload.get("usage") or {}
    return (
        decision,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        (perf_counter() - started) * 1000,
    )


async def provider_r2(
    provider: OpenAICompatibleProvider, question: str, context: str
) -> tuple[R2Response, int | None, int | None, float]:
    started = perf_counter()
    payload = await provider._post(
        {
            "model": provider.settings.llm_model,
            "messages": _messages("R2", question, context),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "m5r1_r2_interpretation",
                    "strict": True,
                    "schema": response_schema("R2"),
                },
            },
            "reasoning_effort": "none",
        }
    )
    content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
    response = parse_r2_content(content)
    usage = payload.get("usage") or {}
    return (
        response,
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        (perf_counter() - started) * 1000,
    )


def metrics(
    cases: tuple[SelectiveAnsweringCase, ...], results: tuple[ArmResult, ...]
) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    counts = {"ANSWER": 0, "CLARIFY": 0, "ABSTAIN": 0}
    matrix = {gold.value: {pred.value: 0 for pred in SelectiveAction} for gold in SelectiveAction}
    for result in results:
        case = by_id[result.case_id]
        counts[result.action.value] += 1
        matrix[case.gold_action.value][result.action.value] += 1
    total = len(results)
    gold_non_answer = sum(case.gold_action is not SelectiveAction.ANSWER for case in cases)
    predicted_answer = counts[SelectiveAction.ANSWER.value]
    correct = sum(by_id[result.case_id].gold_action is result.action for result in results)
    unsafe = sum(
        by_id[result.case_id].gold_action is not SelectiveAction.ANSWER
        and result.action is SelectiveAction.ANSWER
        for result in results
    )
    unnecessary = sum(
        by_id[result.case_id].gold_action is SelectiveAction.ANSWER
        and result.action is not SelectiveAction.ANSWER
        for result in results
    )
    gold_answer = sum(case.gold_action is SelectiveAction.ANSWER for case in cases)

    def precision(action: SelectiveAction) -> float | None:
        predicted = counts[action.value]
        return (
            None
            if predicted == 0
            else sum(
                by_id[result.case_id].gold_action is action and result.action is action
                for result in results
            )
            / predicted
        )

    return {
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "unsafe_answer_rate": unsafe / gold_non_answer if gold_non_answer else None,
        "answer_precision": precision(SelectiveAction.ANSWER),
        "clarify_precision": precision(SelectiveAction.CLARIFY),
        "abstain_precision": precision(SelectiveAction.ABSTAIN),
        "unnecessary_intervention_rate": unnecessary / gold_answer if gold_answer else None,
        "coverage": predicted_answer / total if total else None,
        "selective_risk": None
        if predicted_answer == 0
        else 1 - (precision(SelectiveAction.ANSWER) or 0),
        "prediction_counts": counts,
        "confusion_matrix": matrix,
    }


def provider_settings(settings: Settings) -> Settings:
    """Return the precommitted M5R.1 provider configuration."""
    return settings.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
        }
    )


__all__ = [
    "ArmResult",
    "CandidateInterpretation",
    "InterpretationValidator",
    "R1Decision",
    "R1_DTO_VERSION",
    "R2Decision",
    "R2Response",
    "R2_VALIDATOR_VERSION",
    "R2_DTO_VERSION",
    "deterministic_r0",
    "metrics",
    "parse_r1_content",
    "parse_r2_content",
    "prompt_hash",
    "provider_r1",
    "provider_r2",
    "provider_settings",
    "render_server_context",
    "r1_request_body",
    "local_r1_schema_hash",
    "response_schema",
    "response_schema_hash",
]
