from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def database_root(database_id: str) -> Path:
    return ROOT / "databases" / database_id


def load_authority(database_id: str) -> dict[str, Any]:
    root = database_root(database_id) / "authority"
    result: dict[str, Any] = {}
    for name in (
        "entities",
        "attributes",
        "relationships",
        "metrics",
        "business_rules",
        "temporal_rules",
        "policy",
    ):
        result[name] = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
    return result


def render_governed_context(database_id: str) -> dict[str, Any]:
    """Return the exact model-facing context; evaluator-only files are not read here."""
    authority = load_authority(database_id)
    return {
        "context_profile": "GOVERNED_CONTEXT_V1",
        "database_id": database_id,
        "schema_catalog": authority["entities"],
        "attributes": authority["attributes"],
        "authorized_relationships": [
            relationship
            for relationship in authority["relationships"]
            if relationship.get("authorized") is True
        ],
        "metrics": authority["metrics"],
        "business_rules": authority["business_rules"],
        "temporal_rules": authority["temporal_rules"],
        "policy": authority["policy"],
    }
