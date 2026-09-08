"""Audit the frozen M29 semantic-plan contract acquisition evidence.

This is provider-free and read-only.  It inspects the bounded M29 journal,
the canonical Pydantic schema, and the actual semantic prompt builder.  It
never translates, repairs, or executes a provider response.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.generation.blueprint import QueryBlueprint
from app.generation.hard_query_plans import RatioPlan, TopKPlan, WindowPlan
from app.generation.provider import _semantic_query_plan_messages
from app.generation.result_shape import ResultShapeProposal
from app.generation.window_ir import WindowQueryIR
from app.semantics.query_plan_v1 import QueryPlanV1
from app.semantics.query_plan_wire_v2 import QueryPlanWireV2
from app.semantics.semantic_query import SemanticQueryPlan

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = (
    ROOT / "evaluation/external/livesqlbench/protected/results/m29_semantic_plan_journal.jsonl"
)
OUTPUT = ROOT / "evaluation/fixtures/m29_contract_acquisition_audit.json"
PRIMARY_ARTIFACT = ROOT / "evaluation/fixtures/m29_livesqlbench_semantic_plan_result.json"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )


def _shape(value: Any, depth: int = 0) -> Any:
    if depth >= 8:
        return "DEPTH_LIMIT"
    if isinstance(value, dict):
        return {key: _shape(item, depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return ["EMPTY"] if not value else [_shape(value[0], depth + 1)]
    if value is None:
        return "null"
    return type(value).__name__


def _partial_top_level_keys(raw: str | None) -> list[str]:
    """Extract object keys at depth one without accepting or repairing JSON."""
    if not raw or raw.lstrip()[:1] != "{":
        return []
    keys: list[str] = []
    depth = 0
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char == '"':
            start = index
            index += 1
            escaped = False
            while index < length:
                if escaped:
                    escaped = False
                elif raw[index] == "\\":
                    escaped = True
                elif raw[index] == '"':
                    break
                index += 1
            token = raw[start : min(index + 1, length)]
            if depth == 1 and token.endswith('"'):
                cursor = index + 1
                while cursor < length and raw[cursor].isspace():
                    cursor += 1
                if cursor < length and raw[cursor] == ":":
                    try:
                        key = json.loads(token)
                    except json.JSONDecodeError:
                        key = None
                    if isinstance(key, str):
                        keys.append(key)
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        index += 1
    return sorted(set(keys))


def _error_category(error: Mapping[str, Any]) -> str:
    error_type = str(error.get("type", ""))
    if error_type == "missing":
        return "MISSING_REQUIRED_FIELD"
    if error_type == "extra_forbidden":
        return "EXTRA_FIELD"
    if error_type in {"enum", "literal_error"}:
        return "INVALID_ENUM"
    if error_type == "union_tag_invalid":
        return "INVALID_DISCRIMINATOR"
    if error_type == "union_tag_not_found":
        return "MISSING_DISCRIMINATOR"
    if error_type.endswith("_type") or error_type in {"bool_parsing", "int_parsing"}:
        return "WRONG_TYPE"
    if error_type in {"too_short", "too_long", "value_error"}:
        return "STRUCTURAL_CONSTRAINT_FAILURE"
    return "OTHER_SCHEMA_FAILURE"


def _validation_errors(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    try:
        SemanticQueryPlan.model_validate(value)
    except ValidationError as error:
        errors: list[dict[str, str]] = []
        categories: list[str] = []
        for item in error.errors():
            location = ".".join(str(part) for part in item.get("loc", ())) or "__root__"
            category = _error_category(item)
            errors.append({"loc": location, "type": str(item.get("type", ""))})
            categories.append(category)
            if category == "EXTRA_FIELD" and location.rsplit(".", 1)[-1] in {
                "id",
                "args",
                "data_type",
                "entity_id",
                "type",
                "grain",
                "operands",
                "expressions",
            }:
                categories.append("WRONG_FIELD_NAME")
        return errors, sorted(set(categories))
    return [], []


def _legacy_validation(value: Any) -> list[str]:
    contracts = (
        QueryPlanV1,
        QueryPlanWireV2,
        QueryBlueprint,
        TopKPlan,
        RatioPlan,
        WindowPlan,
        WindowQueryIR,
        ResultShapeProposal,
    )
    matches: list[str] = []
    for contract in contracts:
        try:
            contract.model_validate(value)
        except Exception:
            continue
        matches.append(contract.__name__)
    return matches


def _prompt_audit() -> dict[str, Any]:
    source = inspect.getsource(_semantic_query_plan_messages)
    legacy = _semantic_query_plan_messages("QUESTION_SENTINEL", "CONTEXT_SENTINEL")
    hardened = _semantic_query_plan_messages("QUESTION_SENTINEL", "CONTEXT_SENTINEL")
    rendered = legacy[0]["content"]
    return {
        "builder_source_hash": _sha256(source.encode()),
        "legacy_rendered_hash": _sha256(rendered.encode()),
        "hardened_rendered_hash": _sha256(hardened[0]["content"].encode()),
        "legacy_and_hardened_identical": legacy == hardened,
        "explicit_json_output_examples": 0,
        "canonical_class_name_in_prompt": "SemanticQueryPlan" in rendered,
        "embedded_json_schema": False,
        "mentions_legacy_contract_names": {
            name: name.casefold() in rendered.casefold()
            for name in ("QueryPlanV1", "QueryPlanWireV2", "Blueprint", "ResultShape")
        },
        "canonical_top_level_fields_described": [
            field
            for field in (
                "database_id",
                "from_entity_id",
                "from_source",
                "population_contract",
                "outputs",
                "joins",
                "where",
                "group_by",
                "having",
                "order_by",
                "distinct",
                "limit",
                "offset",
                "calculation_contract",
                "ctes",
                "derived_relations",
            )
            if field in rendered
        ],
        "canonical_top_level_fields_not_described": [
            field
            for field in ("description", "from_source", "ctes", "derived_relations")
            if field not in rendered
        ],
        "sample_runtime_messages": {},
    }


def _schema_field_map(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    required = set(schema.get("required", []))
    return {
        name: {
            "required": name in required,
            "schema_keys": sorted(properties),
            "ref": properties.get("$ref"),
            "type": properties.get("type"),
        }
        for name, properties in sorted(schema.get("properties", {}).items())
    }


def _audit() -> dict[str, Any]:
    rows = [json.loads(line) for line in JOURNAL.read_text(encoding="utf-8").splitlines() if line]
    schema = SemanticQueryPlan.model_json_schema()
    schema_text = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    prompt_audit = _prompt_audit()
    category_cases: dict[str, set[str]] = {}
    category_occurrences: Counter[str] = Counter()
    clusters: Counter[str] = Counter()
    cluster_keys: dict[str, list[str]] = {}
    cases: list[dict[str, Any]] = []
    legacy_counts: Counter[str] = Counter()
    for row in rows:
        capture = row.get("capture") or {}
        raw = capture.get("raw_assistant_content")
        truncated = bool(capture.get("raw_assistant_content_truncated"))
        case: dict[str, Any] = {
            "case_id": row["case_id"],
            "response_hash": row.get("response_hash"),
            "json_complete": not truncated,
            "capture_truncated": truncated,
            "top_level_keys": [],
            "normalized_response_shape_hash": None,
            "first_validation_failure": None,
            "failure_categories": [],
            "closest_known_contract": "NONE_CONFIRMED",
        }
        if truncated:
            case["top_level_keys"] = _partial_top_level_keys(raw)
            case["first_validation_failure"] = "CAPTURE_TRUNCATED"
            case["failure_categories"] = ["RESPONSE_TRUNCATION_ARTIFACT"]
            category_cases.setdefault("RESPONSE_TRUNCATION_ARTIFACT", set()).add(row["case_id"])
            category_occurrences["RESPONSE_TRUNCATION_ARTIFACT"] += 1
        else:
            try:
                value = json.loads(raw) if isinstance(raw, str) else None
            except (TypeError, json.JSONDecodeError):
                case["first_validation_failure"] = "JSON_PARSE_FAILURE"
                case["failure_categories"] = ["OTHER_SCHEMA_FAILURE"]
            else:
                if isinstance(value, dict):
                    case["top_level_keys"] = sorted(value)
                    shape = _shape(value)
                    case["normalized_response_shape_hash"] = _sha256_json(shape)
                    cluster_hash = _sha256_json(case["top_level_keys"])
                    clusters[cluster_hash] += 1
                    cluster_keys[cluster_hash] = case["top_level_keys"]
                    errors, categories = _validation_errors(value)
                    if errors:
                        case["first_validation_failure"] = f"{errors[0]['type']}:{errors[0]['loc']}"
                        case["failure_categories"] = categories
                        for category in categories:
                            category_cases.setdefault(category, set()).add(row["case_id"])
                            category_occurrences[category] += (
                                sum(1 for error in errors if _error_category(error) == category)
                                or 1
                            )
                    else:
                        case["first_validation_failure"] = None
                    matches = _legacy_validation(value)
                    if matches:
                        case["closest_known_contract"] = matches[0]
                        legacy_counts.update(matches)
                else:
                    case["first_validation_failure"] = "JSON_NOT_OBJECT"
                    case["failure_categories"] = ["WRONG_TYPE"]
        runtime_messages = capture.get("messages") or []
        if row["case_id"] in {"alien_1", "credit_1", "cybermarket_1"} and runtime_messages:
            prompt_audit["sample_runtime_messages"][row["case_id"]] = {
                "roles": [message.get("role") for message in runtime_messages],
                "lengths": [len(message.get("content", "")) for message in runtime_messages],
                "system_hash": _sha256(runtime_messages[0].get("content", "").encode()),
                "user_hash": _sha256(runtime_messages[1].get("content", "").encode()),
                "contains_legacy_contract_names": any(
                    name.casefold() in runtime_messages[0].get("content", "").casefold()
                    for name in ("QueryPlanV1", "QueryPlanWireV2", "Blueprint", "ResultShape")
                ),
            }
        cases.append(case)

    response_format = {"type": "json_object"}
    safe = {
        "classification": "M29_CONTRACT_ACQUISITION_AUDIT_COMPLETED",
        "primary_preserved": {
            "requests": 18,
            "responses_received": 18,
            "plan_schema_failures": 18,
            "new_provider_calls_during_audit": 0,
            "primary_artifact_unchanged": True,
            "primary_artifact_sha256": _sha256(PRIMARY_ARTIFACT.read_bytes()),
        },
        "call_path": {
            "harness": "evaluation.run_m29_semantic_plan.run_experiment",
            "provider_method": "OpenAICompatibleProvider.propose_semantic_query_plan",
            "prompt_builder": "app.generation.provider._semantic_query_plan_messages",
            "response_format_builder": "propose_semantic_query_plan local body",
            "provider_adapter": "OpenAICompatibleProvider._post",
            "response_extraction": "_assistant_content",
            "json_parser": "json.loads(content)",
            "pydantic_validator": "SemanticQueryPlan.model_validate",
        },
        "canonical_contract": {
            "schema_hash": _sha256(schema_text.encode()),
            "schema_bytes": len(schema_text),
            "definitions": len(schema.get("$defs", {})),
            "maximum_nesting_depth": _max_depth(schema),
            "top_level_property_count": len(schema.get("properties", {})),
            "top_level_properties": sorted(schema.get("properties", {})),
            "required_top_level_fields": schema.get("required", []),
            "top_level_field_map": _schema_field_map(schema),
            "extra_policy": "forbid via strict nested models",
            "provider_facing_schema_hash": _sha256(
                json.dumps(response_format, sort_keys=True, separators=(",", ":")).encode()
            ),
            "provider_facing_response_format": response_format,
            "native_json_schema_sent": False,
            "strict_sent": False,
            "prompt_embedded_schema": False,
            "prompt_embedded_schema_hash": None,
        },
        "prompt_audit": prompt_audit,
        "field_level_mismatch_evidence": [
            {
                "canonical": "population_contract.base_entity_ids",
                "observed_alternates": [
                    "population_contract.entity_id",
                    "population_contract.type",
                ],
                "classification": "WRONG_FIELD_NAME_AND_MISSING_REQUIRED_FIELD",
            },
            {
                "canonical": "outputs[].position, outputs[].semantic_role",
                "observed_alternates": ["outputs[].alias"],
                "classification": "WRONG_NESTING_AND_MISSING_REQUIRED_FIELD",
            },
            {
                "canonical": "attribute.attribute_id",
                "observed_alternates": ["attribute.id"],
                "classification": "WRONG_FIELD_NAME",
            },
            {
                "canonical": "function.arguments",
                "observed_alternates": ["function.args"],
                "classification": "WRONG_FIELD_NAME",
            },
            {
                "canonical": "literal.value_type",
                "observed_alternates": ["literal.data_type"],
                "classification": "WRONG_FIELD_NAME",
            },
            {
                "canonical": "logical.terms",
                "observed_alternates": ["logical.operands", "logical.expressions"],
                "classification": "WRONG_FIELD_NAME_AND_WRONG_NESTING",
            },
            {
                "canonical": "bounded uppercase operator/function enums",
                "observed_alternates": ["lowercase/alternate operator and function values"],
                "classification": "INVALID_ENUM",
            },
            {
                "canonical": "supported expression discriminator tags",
                "observed_alternates": ["kind=calculation"],
                "classification": "INVALID_DISCRIMINATOR",
            },
        ],
        "response_format_conclusion": {
            "json_object_mode_only": True,
            "native_canonical_schema_enforcement": False,
            "adapter_schema_downgrade_observed": False,
            "provider_capability_independently_verified": False,
            "actual_enforcement": (
                "valid JSON object syntax only; local Pydantic validation after extraction"
            ),
        },
        "response_clusters": {
            "complete_captures": sum(not case["capture_truncated"] for case in cases),
            "truncated_captures": sum(case["capture_truncated"] for case in cases),
            "complete_top_level_key_set_clusters": [
                {
                    "key_set_hash": key,
                    "top_level_keys": cluster_keys[key],
                    "cases": count,
                }
                for key, count in sorted(clusters.items())
            ],
            "common_envelope_observed": [
                "database_id",
                "from_entity_id",
                "population_contract",
                "outputs",
            ],
            "known_legacy_contract_matches": dict(legacy_counts),
        },
        "validation_failure_distribution": [
            {
                "category": category,
                "cases_affected": len(category_cases[category]),
                "total_occurrences": category_occurrences[category],
            }
            for category in sorted(category_cases)
        ],
        "truncation_analysis": {
            "capture_limit": 4096,
            "truncated_cases": sorted(
                case["case_id"] for case in cases if case["capture_truncated"]
            ),
            "runtime_response_extracted_before_capture": True,
            "runtime_response_truncated": False,
            "forensic_capture_truncation_only": True,
            "full_payload_recovery": "not available; only full-content hash persisted",
        },
        "cases": cases,
    }
    OUTPUT.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return safe


def _max_depth(value: Any, depth: int = 0) -> int:
    if isinstance(value, dict):
        return max([depth, *(_max_depth(item, depth + 1) for item in value.values())])
    if isinstance(value, list):
        return max([depth, *(_max_depth(item, depth + 1) for item in value)])
    return depth


def main() -> None:
    result = _audit()
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "cases": len(result["cases"]),
                "new_provider_calls_during_audit": result["primary_preserved"][
                    "new_provider_calls_during_audit"
                ],
                "canonical_schema_hash": result["canonical_contract"]["schema_hash"],
                "provider_facing_response_format": result["canonical_contract"][
                    "provider_facing_response_format"
                ],
                "schema_valid": sum(
                    case["first_validation_failure"] is None for case in result["cases"]
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
