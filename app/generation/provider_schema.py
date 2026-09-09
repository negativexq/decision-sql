"""Checks for the repository's strict provider JSON-schema dialect.

This module validates schema shape only.  It does not parse model output,
interpret semantics, or import benchmark/evaluator code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderSchemaViolation:
    code: str
    path: str
    detail: str


def validate_provider_strict_schema(
    schema: Mapping[str, Any],
) -> tuple[ProviderSchemaViolation, ...]:
    """Return violations of the project's strict structured-output dialect.

    For every bounded object, every declared property must be required and
    unknown properties must be rejected.  Nullable values are represented by
    a two-member ``type`` array containing exactly one non-null type.
    """

    violations: list[ProviderSchemaViolation] = []
    visited: set[tuple[int, str]] = set()

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, Mapping):
            return
        marker = (id(node), path)
        if marker in visited:
            return
        visited.add(marker)

        node_type = node.get("type")
        if isinstance(node_type, list):
            if len(node_type) != 2 or node_type.count("null") != 1:
                violations.append(
                    ProviderSchemaViolation(
                        "INVALID_NULLABLE_ENCODING",
                        path,
                        "nullable type arrays must contain exactly one non-null type",
                    )
                )
            elif not isinstance(node_type[0], str) or not isinstance(node_type[1], str):
                violations.append(
                    ProviderSchemaViolation(
                        "INVALID_NULLABLE_ENCODING", path, "type array entries must be strings"
                    )
                )

        if node_type == "object" or (isinstance(node_type, list) and "object" in node_type):
            properties = node.get("properties")
            if not isinstance(properties, Mapping):
                violations.append(
                    ProviderSchemaViolation(
                        "UNBOUNDED_OBJECT", path, "object schema must declare properties"
                    )
                )
            else:
                required = node.get("required")
                if not isinstance(required, list):
                    violations.append(
                        ProviderSchemaViolation(
                            "REQUIRED_NOT_ARRAY",
                            path,
                            "object schema must declare required as an array",
                        )
                    )
                    required_values: set[str] = set()
                else:
                    required_values = {value for value in required if isinstance(value, str)}
                    if len(required_values) != len(required):
                        violations.append(
                            ProviderSchemaViolation(
                                "INVALID_REQUIRED_ENTRY",
                                path,
                                "required entries must be unique strings",
                            )
                        )
                property_values = {str(key) for key in properties}
                missing = sorted(property_values - required_values)
                extra = sorted(required_values - property_values)
                for key in missing:
                    violations.append(
                        ProviderSchemaViolation(
                            "PROPERTY_NOT_REQUIRED",
                            f"{path}.{key}",
                            "property is absent from required",
                        )
                    )
                for key in extra:
                    violations.append(
                        ProviderSchemaViolation(
                            "REQUIRED_KEY_WITHOUT_PROPERTY",
                            f"{path}.{key}",
                            "required key is absent from properties",
                        )
                    )
                if node.get("additionalProperties") is not False:
                    violations.append(
                        ProviderSchemaViolation(
                            "MISSING_ADDITIONAL_PROPERTIES_FALSE",
                            path,
                            "bounded object must set additionalProperties to false",
                        )
                    )
                for key, child in properties.items():
                    visit(child, f"{path}.{key}")

        if node_type == "array":
            visit(node.get("items"), f"{path}[]")
        for key, child in node.items():
            if key == "$defs" and isinstance(child, Mapping):
                for definition_name, definition in child.items():
                    visit(definition, f"{path}.$defs.{definition_name}")
            elif key in {"anyOf", "oneOf", "allOf"} and isinstance(child, list):
                for index, branch in enumerate(child):
                    visit(branch, f"{path}.{key}[{index}]")

    visit(schema, "$")
    return tuple(violations)


def provider_schema_is_strict(schema: Mapping[str, Any]) -> bool:
    return not validate_provider_strict_schema(schema)
