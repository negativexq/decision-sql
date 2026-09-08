"""Provider-boundary protocol for canonical semantic query plans.

The internal :class:`SemanticQueryPlan` schema is intentionally richer than
the subset accepted by OpenAI-compatible strict JSON-schema responses. This
module performs only a deterministic transport projection. It does not rename
fields, infer semantics, or repair provider output.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, cast

from app.provenance.canonical import semantic_hash
from app.semantics.semantic_query import SemanticQueryPlan

_PROVIDER_UNSUPPORTED_KEYS = frozenset(
    {
        "default",
        "discriminator",
    }
)


def provider_semantic_query_plan_schema() -> dict[str, Any]:
    """Return the strict provider-compatible schema for the canonical plan."""

    return cast(dict[str, Any], _project_schema(deepcopy(SemanticQueryPlan.model_json_schema())))


def provider_semantic_query_plan_schema_hash() -> str:
    """Return the stable hash of the provider-facing schema."""

    return semantic_hash(provider_semantic_query_plan_schema())


def semantic_query_plan_response_format() -> dict[str, Any]:
    """Build the exact native structured-output request configuration."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "semantic_query_plan",
            "strict": True,
            "schema": provider_semantic_query_plan_schema(),
        },
    }


def provider_schema_errors(value: Any) -> tuple[str, ...]:
    """Validate the provider-facing structural subset locally.

    This deliberately does not run canonical model validators. Those belong to
    the next acquisition stage and include cross-field semantic invariants.
    """

    schema = provider_semantic_query_plan_schema()
    errors: list[str] = []
    _validate_value(value, schema, schema, "$", errors)
    return tuple(errors)


def _project_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_project_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    projected: dict[str, Any] = {}
    for key, item in value.items():
        if key in _PROVIDER_UNSUPPORTED_KEYS:
            continue
        if key == "pattern" and isinstance(item, str) and "(?" in item:
            continue
        projected["anyOf" if key == "oneOf" else key] = _project_schema(item)

    if projected.get("type") == "object" and isinstance(projected.get("properties"), dict):
        projected["required"] = list(projected["properties"])
        projected["additionalProperties"] = False
    return projected


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    path: str,
    errors: list[str],
) -> bool:
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/$defs/"):
            errors.append(f"{path}: unsupported reference")
            return False
        definition = root.get("$defs", {}).get(reference.removeprefix("#/$defs/"))
        if not isinstance(definition, dict):
            errors.append(f"{path}: unknown reference")
            return False
        return _validate_value(value, definition, root, path, errors)

    if "anyOf" in schema:
        branch_errors: list[tuple[str, ...]] = []
        for branch in schema["anyOf"]:
            if isinstance(branch, dict):
                candidate_errors: list[str] = []
                if _validate_value(value, branch, root, path, candidate_errors):
                    return True
                branch_errors.append(tuple(candidate_errors))
        errors.append(f"{path}: no anyOf branch matched")
        return False

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: const mismatch")
        return False
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: enum mismatch")
        return False

    expected_type = schema.get("type")
    if expected_type == "null":
        valid_type = value is None
    elif expected_type == "object":
        valid_type = isinstance(value, dict)
    elif expected_type == "array":
        valid_type = isinstance(value, list)
    elif expected_type == "string":
        valid_type = isinstance(value, str)
    elif expected_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "number":
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_type == "boolean":
        valid_type = isinstance(value, bool)
    else:
        valid_type = True
    if not valid_type:
        errors.append(f"{path}: type mismatch")
        return False

    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            if re.fullmatch(pattern, value) is None:
                errors.append(f"{path}: pattern mismatch")
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems")

    if expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(value, dict) or not isinstance(properties, dict):
            return True
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: extra")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _validate_value(value[key], child_schema, root, f"{path}.{key}", errors)
    elif expected_type == "array":
        items = schema.get("items")
        if isinstance(value, list) and isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, root, f"{path}[{index}]", errors)
    return not errors
