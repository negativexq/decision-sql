"""Emit the provider-schema parity evidence for M29R.1.

This is deliberately provider-free.  The provider capability claims in this
artifact are backed by the M29R.1 preflight smoke; this script only inspects
the deterministic canonical-to-provider projection and records its hashes and
constraint counts.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.generation.semantic_plan_protocol import (
    provider_semantic_query_plan_schema,
    provider_semantic_query_plan_schema_hash,
)
from app.provenance.canonical import semantic_hash
from app.semantics.semantic_query import SemanticQueryPlan

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evaluation/fixtures/m29r1_projection_parity.json"


def _walk(value: Any, count: Counter[str]) -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            count[child_key] += 1
            _walk(child, count)
    elif isinstance(value, list):
        for child in value:
            _walk(child, count)


def _counts(schema: dict[str, Any]) -> dict[str, int]:
    count: Counter[str] = Counter()
    _walk(schema, count)
    for key in (
        "$ref",
        "oneOf",
        "anyOf",
        "required",
        "additionalProperties",
        "enum",
        "const",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "default",
        "discriminator",
    ):
        count.setdefault(key, 0)
    return {key: count[key] for key in count}


def build_report() -> dict[str, Any]:
    canonical = SemanticQueryPlan.model_json_schema()
    provider = provider_semantic_query_plan_schema()
    canonical_counts = _counts(canonical)
    provider_counts = _counts(provider)
    constraints = {
        "oneOf": {
            "canonical": canonical_counts["oneOf"],
            "provider": provider_counts["oneOf"],
            "parity_before": "normalized_to_anyOf",
            "parity_after": "normalized_equivalent",
            "provider_enforceable": True,
        },
        "required": {
            "canonical": canonical_counts["required"],
            "provider": provider_counts["required"],
            "parity_before": "normalized_for_strict_provider_objects",
            "parity_after": "preserved_with_all_properties_required",
            "provider_enforceable": True,
        },
        "additionalProperties": {
            "canonical": canonical_counts["additionalProperties"],
            "provider": provider_counts["additionalProperties"],
            "parity_before": "preserved",
            "parity_after": "preserved",
            "provider_enforceable": True,
        },
        "enum_const": {
            "canonical": canonical_counts["enum"] + canonical_counts["const"],
            "provider": provider_counts["enum"] + provider_counts["const"],
            "parity_before": "preserved",
            "parity_after": "preserved",
            "provider_enforceable": True,
        },
        "length_and_array_bounds": {
            "canonical": sum(
                canonical_counts[key] for key in ("minLength", "maxLength", "minItems", "maxItems")
            ),
            "provider": sum(
                provider_counts[key] for key in ("minLength", "maxLength", "minItems", "maxItems")
            ),
            "parity_before": "accidentally_dropped",
            "parity_after": "preserved",
            "provider_enforceable": True,
        },
        "simple_pattern": {
            "canonical": canonical_counts["pattern"],
            "provider": provider_counts["pattern"],
            "parity_before": "accidentally_dropped",
            "parity_after": "preserved_when_provider_supported",
            "provider_enforceable": True,
        },
        "regex_lookaround": {
            "canonical": "present_in_canonical_schema_where_used",
            "provider": "removed",
            "parity_before": "removed",
            "parity_after": "removed_as_provider_unsupported",
            "provider_enforceable": False,
        },
        "default_and_discriminator_metadata": {
            "canonical": canonical_counts["default"] + canonical_counts["discriminator"],
            "provider": provider_counts["default"] + provider_counts["discriminator"],
            "parity_before": "removed",
            "parity_after": "removed_as_provider_unsupported_metadata",
            "provider_enforceable": False,
        },
    }
    return {
        "milestone": "M29R.1",
        "provider_calls": 0,
        "canonical_schema_hash": semantic_hash(canonical),
        "provider_schema_hash": provider_semantic_query_plan_schema_hash(),
        "canonical_schema_bytes": len(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        ),
        "provider_schema_bytes": len(
            json.dumps(provider, sort_keys=True, separators=(",", ":")).encode()
        ),
        "canonical_counts": canonical_counts,
        "provider_counts": provider_counts,
        "constraints": constraints,
        "evidence": {
            "provider_capability": "M29R.1 protocol preflight",
            "bounds_and_simple_pattern_smoke": "accepted by provider",
            "regex_lookaround_smoke": "rejected by provider",
        },
    }


def main() -> None:
    report = build_report()
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
