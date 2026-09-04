"""Small canonicalization helpers for bounded provenance identities."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize semantic provenance values without timestamps or incidental whitespace."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def semantic_hash(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def bounded_text(value: str | None, limit: int = 16_384) -> str | None:
    """Keep provider output bounded while preserving the exact prefix for diagnostics."""
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]
