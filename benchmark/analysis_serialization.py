"""Deterministic JSON serialization for benchmark analysis values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _typed_key(value: Any) -> tuple[int, str, str]:
    if value is None:
        return (0, "", "")
    if isinstance(value, str):
        return (1, value, "")
    return (2, type(value).__name__, repr(value))


def canonicalize_analysis_value(value: Any) -> Any:
    """Return JSON-safe data while preserving mixed mapping key types.

    String-keyed mappings retain their ordinary object representation. A
    mapping containing non-string keys becomes an explicitly typed record
    list, keeping ``None`` distinct from every categorical string.
    """
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {key: canonicalize_analysis_value(value[key]) for key in sorted(value)}
        return {
            "__typed_mapping__": [
                {
                    "key": canonicalize_analysis_value(key),
                    "value": canonicalize_analysis_value(value[key]),
                }
                for key in sorted(value, key=_typed_key)
            ]
        }
    if isinstance(value, list):
        return [canonicalize_analysis_value(item) for item in value]
    if isinstance(value, tuple):
        return [canonicalize_analysis_value(item) for item in value]
    return value


def dumps_analysis(value: Any, *, indent: int | None = None) -> str:
    """Serialize analysis output canonically with typed-key preservation."""
    import json

    return json.dumps(
        canonicalize_analysis_value(value),
        indent=indent,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
