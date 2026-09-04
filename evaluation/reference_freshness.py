"""Bounded PostgreSQL reference identity for evaluation freshness checks.

This module deliberately defines identity, not semantic SQL equivalence.  It
parses one PostgreSQL statement and uses the parser's deterministic rendering
as the representation hashed for freshness.  No execution, result comparison,
model call, or algebraic rewrite is involved.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse

POSTGRES_DIALECT = "postgres"
CANONICALIZER_VERSION = "decision-sql-reference-freshness-v1"


class ReferenceCanonicalizationError(ValueError):
    """Raised when a reference cannot receive a safe canonical identity."""


@dataclass(frozen=True)
class ReferenceFingerprint:
    """Raw and canonical identities for one reference SQL string."""

    raw_hash: str
    canonical_sql: str
    fingerprint: str


def canonicalize_reference_sql(sql: str) -> str:
    """Return one deterministic PostgreSQL rendering without semantic rewrites."""
    if not isinstance(sql, str) or not sql.strip():
        raise ReferenceCanonicalizationError("reference SQL is empty")
    try:
        statements = parse(sql, read=POSTGRES_DIALECT)
    except Exception as error:  # sqlglot exposes parser-specific exception types.
        raise ReferenceCanonicalizationError("reference SQL failed PostgreSQL parsing") from error
    if len(statements) != 1 or statements[0] is None:
        raise ReferenceCanonicalizationError("reference SQL must contain exactly one statement")
    statement = statements[0]
    if not isinstance(statement, exp.Expression):
        raise ReferenceCanonicalizationError("reference SQL did not produce an expression")
    try:
        return statement.sql(dialect=POSTGRES_DIALECT, pretty=False, comments=False)
    except Exception as error:
        raise ReferenceCanonicalizationError("reference SQL could not be rendered") from error


def canonical_reference_fingerprint(sql: str) -> ReferenceFingerprint:
    """Compute the single shared freshness identity for reference SQL."""
    canonical = canonicalize_reference_sql(sql)
    return ReferenceFingerprint(
        raw_hash=sha256(sql.encode("utf-8")).hexdigest(),
        canonical_sql=canonical,
        fingerprint=sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _source_role(name: str) -> str:
    lower = name.lower()
    if name == "m104_corpus.json":
        return "INVALID_EXPOSED"
    if lower.startswith("m3"):
        return "M3"
    if lower.startswith("m4"):
        return "M4"
    if lower.startswith("m7"):
        return "M7"
    if lower.startswith(("m8", "m34")):
        return "M8"
    if lower.startswith(("m9", "m91", "m92", "m93", "m95", "m96")):
        return "M9"
    if lower.startswith("m10r"):
        return "M10R"
    if lower.startswith("m10"):
        return "M10"
    return "OTHER_INTERNAL"


def _walk_references(value: Any, path: str = "$") -> list[tuple[str, str, str | None, str]]:
    found: list[tuple[str, str, str | None, str]] = []
    if isinstance(value, dict):
        case_id = value.get("case_id") if isinstance(value.get("case_id"), str) else None
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in {"reference_sql", "gold_sql"} and isinstance(item, str):
                found.append((key, item, case_id, child_path))
            elif key == "reference_sql_variants" and isinstance(item, list):
                for index, variant in enumerate(item):
                    if isinstance(variant, str):
                        found.append((key, variant, case_id, f"{child_path}[{index}]"))
            elif isinstance(item, (dict, list)):
                found.extend(_walk_references(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk_references(item, f"{path}[{index}]"))
    return found


def consumed_reference_entries(root: Path) -> tuple[dict[str, Any], ...]:
    """Build a deterministic inventory from existing internal artifacts and M4."""
    fixtures = root / "evaluation" / "fixtures"
    entries: list[dict[str, Any]] = []
    for path in sorted(fixtures.glob("*.json")):
        if path.name.startswith("m104r_"):
            continue
        if path.name.startswith("m104_") and path.name != "m104_corpus.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key, sql, case_id, json_path in _walk_references(payload):
            try:
                fingerprint = canonical_reference_fingerprint(sql)
                error = None
            except ReferenceCanonicalizationError as exc:
                fingerprint = None
                error = str(exc)
            entries.append(
                {
                    "source_milestone": _source_role(path.name),
                    "source_artifact": str(path.relative_to(root)),
                    "source_case_id": case_id,
                    "json_field": key,
                    "json_path": json_path,
                    "raw_reference_hash": sha256(sql.encode("utf-8")).hexdigest(),
                    "canonical_reference_fingerprint": fingerprint.fingerprint if fingerprint else None,
                    "canonical_reference_sql": fingerprint.canonical_sql if fingerprint else None,
                    "canonicalization_error": error,
                    "consumption_role": "INVALID_EXPOSED" if path.name == "m104_corpus.json" else "HISTORICAL_REFERENCE",
                }
            )

    # The verified M4 corpus is source code, not a fixture JSON file.
    from evaluation.m4_benchmark import build_memory_corpus

    for example in build_memory_corpus():
        fingerprint = canonical_reference_fingerprint(example.sql)
        entries.append(
            {
                "source_milestone": "M4",
                "source_artifact": "evaluation/m4_benchmark.py",
                "source_case_id": example.example_id,
                "json_field": "sql",
                "json_path": None,
                "raw_reference_hash": fingerprint.raw_hash,
                "canonical_reference_fingerprint": fingerprint.fingerprint,
                "canonical_reference_sql": fingerprint.canonical_sql,
                "canonicalization_error": None,
                "consumption_role": "MEMORY",
            }
        )
    entries.sort(key=lambda item: (item["source_artifact"], item["json_path"] or "", item["source_case_id"] or ""))
    return tuple(entries)


def consumed_reference_raw_values(root: Path, include_invalid_exposed: bool = False) -> dict[str, str]:
    """Return raw historical SQL by hash for legacy-defect reproduction only."""
    values: dict[str, str] = {}
    fixtures = root / "evaluation" / "fixtures"
    for path in sorted(fixtures.glob("*.json")):
        if path.name.startswith("m104r_"):
            continue
        if path.name == "m104_corpus.json" and not include_invalid_exposed:
            continue
        if path.name.startswith("m104_") and path.name != "m104_corpus.json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key, sql, _, _ in _walk_references(payload):
            if key in {"reference_sql", "gold_sql", "reference_sql_variants"}:
                values[sha256(sql.encode("utf-8")).hexdigest()] = sql
    from evaluation.m4_benchmark import build_memory_corpus

    for example in build_memory_corpus():
        values[sha256(example.sql.encode("utf-8")).hexdigest()] = example.sql
    return values


def canonical_index(entries: tuple[dict[str, Any], ...]) -> dict[str, tuple[dict[str, Any], ...]]:
    """Index every fingerprint while retaining all source provenance."""
    index: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        fingerprint = entry["canonical_reference_fingerprint"]
        if fingerprint is not None:
            index.setdefault(fingerprint, []).append(entry)
    return {key: tuple(value) for key, value in sorted(index.items())}


def canonical_overlap(sql: str, entries: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """Return all prior sources sharing the candidate's canonical fingerprint."""
    fingerprint = canonical_reference_fingerprint(sql).fingerprint
    return canonical_index(entries).get(fingerprint, ())


def pre_run_canonical_overlap(
    sql: str, entries: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """Authoritative pre-exposure freshness verdict."""
    return canonical_overlap(sql, entries)


def post_run_canonical_overlap(
    sql: str, entries: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    """Authoritative post-run integrity verdict using the same implementation."""
    return canonical_overlap(sql, entries)
