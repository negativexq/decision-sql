"""Local-only protected LiveSQLBench GT merge with a structural runtime boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evaluation.external.livesqlbench.loader import LiveSqlBenchCase, LiveSqlBenchDataset

PROTECTED_REQUIRED_FIELDS = {"instance_id", "sol_sql", "external_knowledge", "test_cases"}


@dataclass(frozen=True)
class LiveSqlBenchRuntimeCase:
    instance_id: str
    database: str
    question: str
    external_knowledge: tuple[Any, ...]
    category: str
    difficulty_tier: str
    conditions: dict[str, Any]

    def provider_payload(self, schema_context: str) -> dict[str, Any]:
        return {
            "question": self.question,
            "external_knowledge": list(self.external_knowledge),
            "schema_context": schema_context,
        }


@dataclass(frozen=True)
class ProtectedRow:
    instance_id: str
    sol_sql: tuple[str, ...]
    external_knowledge: tuple[Any, ...]
    test_cases: tuple[Any, ...]


@dataclass(frozen=True)
class ProtectedArtifact:
    path: Path
    sha256: str
    rows: tuple[ProtectedRow, ...]
    field_names: tuple[str, ...]


@dataclass(frozen=True)
class LiveSqlBenchEvaluationCase:
    public: LiveSqlBenchCase
    evaluation: ProtectedRow

    @property
    def instance_id(self) -> str:
        return self.public.instance_id

    @property
    def database(self) -> str:
        return self.public.selected_database

    @property
    def runtime(self) -> LiveSqlBenchRuntimeCase:
        return LiveSqlBenchRuntimeCase(
            instance_id=self.public.instance_id,
            database=self.public.selected_database,
            question=self.public.query,
            external_knowledge=self.evaluation.external_knowledge,
            category=self.public.category,
            difficulty_tier=self.public.difficulty_tier,
            conditions=dict(self.public.conditions),
        )

    @property
    def sol_sql(self) -> tuple[str, ...]:
        return self.evaluation.sol_sql

    @property
    def test_cases(self) -> tuple[Any, ...]:
        return self.evaluation.test_cases


def _list_field(row: dict[str, Any], field: str, row_number: int) -> list[Any]:
    value = row.get(field)
    if not isinstance(value, list):
        raise ValueError(f"protected row {row_number} field {field} must be a list")
    return value


def load_protected_artifact(path: Path) -> ProtectedArtifact:
    rows: list[ProtectedRow] = []
    seen: set[str] = set()
    fields: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for row_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank protected JSONL row at line {row_number}")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"protected row {row_number} is not an object")
            fields.update(raw)
            missing = PROTECTED_REQUIRED_FIELDS - set(raw)
            if missing:
                raise ValueError(f"protected row {row_number} missing fields: {sorted(missing)}")
            instance_id = raw["instance_id"]
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise ValueError(f"protected row {row_number} has an empty instance_id")
            if instance_id in seen:
                raise ValueError(f"duplicate protected instance_id: {instance_id}")
            sol_sql = _list_field(raw, "sol_sql", row_number)
            if not all(isinstance(sql, str) and sql.strip() for sql in sol_sql):
                raise ValueError(f"protected row {row_number} has invalid sol_sql")
            rows.append(
                ProtectedRow(
                    instance_id=instance_id,
                    sol_sql=tuple(sol_sql),
                    external_knowledge=tuple(_list_field(raw, "external_knowledge", row_number)),
                    test_cases=tuple(_list_field(raw, "test_cases", row_number)),
                )
            )
            seen.add(instance_id)
    return ProtectedArtifact(
        path=path,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        rows=tuple(rows),
        field_names=tuple(sorted(fields)),
    )


def merge_public_and_protected(
    public: LiveSqlBenchDataset, protected: ProtectedArtifact
) -> tuple[tuple[LiveSqlBenchEvaluationCase, ...], dict[str, Any]]:
    public_by_id = {case.instance_id: case for case in public.cases}
    protected_by_id = {row.instance_id: row for row in protected.rows}
    exact_ids = set(public_by_id) & set(protected_by_id)
    merged = tuple(
        LiveSqlBenchEvaluationCase(public_by_id[case_id], protected_by_id[case_id])
        for case_id in sorted(exact_ids)
    )
    return merged, {
        "public_rows": len(public.cases),
        "protected_rows": len(protected.rows),
        "exact_matches": len(exact_ids),
        "unmatched_public": len(set(public_by_id) - set(protected_by_id)),
        "unmatched_protected": len(set(protected_by_id) - set(public_by_id)),
        "duplicate_ids": 0,
        "merge_key": "instance_id",
    }
