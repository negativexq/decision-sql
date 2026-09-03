"""Typed, server-owned verified query memory records."""

from __future__ import annotations

import json
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse_one


class MemoryLifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class VerificationType(StrEnum):
    MANUAL_REFERENCE = "MANUAL_REFERENCE"
    DETERMINISTIC_FIXTURE = "DETERMINISTIC_FIXTURE"


class StructuralSignature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tables_count: int = Field(ge=1)
    join_count: int = Field(ge=0)
    multi_join: bool
    aggregation_functions: tuple[str, ...] = ()
    group_by: bool
    having: bool
    subquery: bool
    cte: bool
    case: bool
    arithmetic: bool
    division: bool
    order_by: bool
    limit: bool
    window: bool
    distinct: bool
    set_operation: bool


class VerificationProvenance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verification_type: VerificationType
    schema_version: str = Field(min_length=1)


class VerifiedQueryExample(BaseModel):
    """A trusted reference used only as bounded generation context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    example_id: str = Field(pattern=r"^vq:[a-z0-9_]+:v[1-9][0-9]*$")
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    schema_objects: tuple[str, ...] = Field(min_length=1)
    structural_signature: StructuralSignature
    tags: tuple[str, ...] = ()
    verification_provenance: VerificationProvenance
    lifecycle: MemoryLifecycle = MemoryLifecycle.ACTIVE
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryCorpusError(ValueError):
    """The server-owned verified corpus does not satisfy its contract."""


def build_verified_query_example(
    *,
    example_id: str,
    question: str,
    sql: str,
    tags: tuple[str, ...],
    verification_type: VerificationType = VerificationType.MANUAL_REFERENCE,
    schema_version: str = "decision-sql-demo-schema-v1",
) -> VerifiedQueryExample:
    tree = parse_one(sql, read="postgres")
    signature, schema_objects = _derive_metadata(tree)
    provenance = VerificationProvenance(
        verification_type=verification_type,
        schema_version=schema_version,
    )
    payload = {
        "example_id": example_id,
        "question": question,
        "sql": sql.strip(),
        "schema_objects": schema_objects,
        "structural_signature": signature.model_dump(mode="json"),
        "tags": tuple(sorted(tags)),
        "verification_provenance": provenance.model_dump(mode="json"),
        "lifecycle": MemoryLifecycle.ACTIVE.value,
    }
    return VerifiedQueryExample(
        **payload,
        content_hash=sha256(_canonical_json(payload).encode()).hexdigest(),
    )


def _derive_metadata(tree: exp.Expression) -> tuple[StructuralSignature, tuple[str, ...]]:
    cte_names = {
        cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    tables = sorted(
        {
            table.name.lower()
            for table in tree.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
    )
    columns = sorted(
        {
            f"{column.table.lower()}.{column.name.lower()}"
            if column.table
            else column.name.lower()
            for column in tree.find_all(exp.Column)
        }
    )
    root = tree if isinstance(tree, exp.Select) else next(tree.find_all(exp.Select), tree)
    aggregates = tuple(sorted({function.key.upper() for function in tree.find_all(exp.AggFunc)}))
    arithmetic_nodes = tuple(
        tree.find_all(exp.Add)
    ) + tuple(tree.find_all(exp.Sub)) + tuple(tree.find_all(exp.Mul)) + tuple(
        tree.find_all(exp.Div)
    )
    signature = StructuralSignature(
        tables_count=len(tables),
        join_count=len(list(tree.find_all(exp.Join))),
        multi_join=len(list(tree.find_all(exp.Join))) >= 2,
        aggregation_functions=aggregates,
        group_by=root.args.get("group") is not None,
        having=root.args.get("having") is not None,
        subquery=bool(list(tree.find_all(exp.Subquery))),
        cte=bool(list(tree.find_all(exp.CTE))),
        case=bool(list(tree.find_all(exp.Case))),
        arithmetic=bool(arithmetic_nodes),
        division=bool(list(tree.find_all(exp.Div))),
        order_by=root.args.get("order") is not None,
        limit=root.args.get("limit") is not None,
        window=bool(list(tree.find_all(exp.Window))),
        distinct=bool(list(tree.find_all(exp.Distinct))),
        set_operation=bool(list(tree.find_all(exp.SetOperation))),
    )
    return signature, tuple((*tables, *columns))


def canonical_example_payload(example: VerifiedQueryExample) -> dict[str, Any]:
    data = example.model_dump(mode="json")
    data.pop("content_hash")
    return data


def memory_corpus_hash(examples: tuple[VerifiedQueryExample, ...]) -> str:
    """Hash the corpus in its server-owned, deterministic order."""
    return sha256(
        _canonical_json([canonical_example_payload(example) for example in examples]).encode()
    ).hexdigest()


def validate_memory_corpus(
    examples: tuple[VerifiedQueryExample, ...], expected_hash: str | None = None
) -> str:
    """Validate IDs, content hashes, and optional frozen corpus identity."""
    if len({example.example_id for example in examples}) != len(examples):
        raise MemoryCorpusError("verified memory corpus contains duplicate example IDs")
    for example in examples:
        actual = sha256(_canonical_json(canonical_example_payload(example)).encode()).hexdigest()
        if actual != example.content_hash:
            raise MemoryCorpusError(f"content hash mismatch for {example.example_id}")
        try:
            parse_one(example.sql, read="postgres")
        except Exception as error:
            raise MemoryCorpusError(
                f"verified memory SQL does not parse for {example.example_id}"
            ) from error
    actual_hash = memory_corpus_hash(examples)
    if expected_hash is not None and actual_hash != expected_hash:
        raise MemoryCorpusError("verified memory corpus hash does not match the frozen contract")
    return actual_hash


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
