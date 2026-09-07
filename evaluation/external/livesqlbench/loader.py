"""Immutable adapter for the public LiveSQLBench Base-Lite JSONL release."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUERY_CATEGORY = "Query"
MANAGEMENT_CATEGORY = "Management"
DATASET_FILENAME = "livesqlbench_data.jsonl"
REQUIRED_FIELDS = {
    "instance_id",
    "selected_database",
    "query",
    "preprocess_sql",
    "clean_up_sqls",
    "sol_sql",
    "external_knowledge",
    "test_cases",
    "category",
    "conditions",
    "difficulty_tier",
}


@dataclass(frozen=True)
class LiveSqlBenchCase:
    """The upstream fields needed by the adapter, retaining raw provenance."""

    instance_id: str
    selected_database: str
    query: str
    preprocess_sql: tuple[str, ...]
    clean_up_sqls: tuple[str, ...]
    sol_sql: tuple[str, ...]
    external_knowledge: tuple[Any, ...]
    test_cases: tuple[Any, ...]
    category: str
    high_level: bool | None
    conditions: dict[str, Any]
    difficulty_tier: str
    raw: dict[str, Any]

    @property
    def eligible_select(self) -> bool:
        return self.category == QUERY_CATEGORY

    @property
    def runtime_payload(self) -> dict[str, Any]:
        """Return only legitimate context; reference fields never enter runtime input."""
        return {
            "instance_id": self.instance_id,
            "selected_database": self.selected_database,
            "question": self.query,
            "external_knowledge": list(self.external_knowledge),
            "difficulty_tier": self.difficulty_tier,
            "category": self.category,
        }


@dataclass(frozen=True)
class LiveSqlBenchDataset:
    source_path: Path
    cases: tuple[LiveSqlBenchCase, ...]
    source_sha256: str

    @property
    def databases(self) -> tuple[str, ...]:
        return tuple(sorted({case.selected_database for case in self.cases}))

    @property
    def eligible_select_cases(self) -> tuple[LiveSqlBenchCase, ...]:
        return tuple(case for case in self.cases if case.eligible_select)

    @property
    def management_cases(self) -> tuple[LiveSqlBenchCase, ...]:
        return tuple(case for case in self.cases if case.category == MANAGEMENT_CATEGORY)

    @property
    def unclassified_cases(self) -> tuple[LiveSqlBenchCase, ...]:
        return tuple(
            case
            for case in self.cases
            if case.category not in {QUERY_CATEGORY, MANAGEMENT_CATEGORY}
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _string_tuple(value: Any, field: str, row_number: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"row {row_number} field {field} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"row {row_number} field {field} contains a non-string value")
    return tuple(value)


def _json_tuple(value: Any, field: str, row_number: int) -> tuple[Any, ...]:
    if not isinstance(value, list):
        raise ValueError(f"row {row_number} field {field} must be a list")
    return tuple(value)


def load_dataset(path: Path) -> LiveSqlBenchDataset:
    rows: list[LiveSqlBenchCase] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for row_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"blank JSONL row at line {row_number}")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"row {row_number} is not an object")
            missing = REQUIRED_FIELDS - set(raw)
            if missing:
                raise ValueError(f"row {row_number} missing fields: {sorted(missing)}")
            instance_id = raw["instance_id"]
            database = raw["selected_database"]
            query = raw["query"]
            category = raw["category"]
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise ValueError(f"row {row_number} has an empty instance_id")
            if instance_id in seen:
                raise ValueError(f"duplicate instance_id: {instance_id}")
            if not isinstance(database, str) or not database.strip():
                raise ValueError(f"row {row_number} has an empty selected_database")
            if not isinstance(query, str) or not query.strip():
                raise ValueError(f"row {row_number} has an empty query")
            if not isinstance(category, str):
                raise ValueError(f"row {row_number} category must be a string")
            if not isinstance(raw["conditions"], dict):
                raise ValueError(f"row {row_number} conditions must be an object")
            knowledge = _json_tuple(raw["external_knowledge"], "external_knowledge", row_number)
            test_cases = _json_tuple(raw["test_cases"], "test_cases", row_number)
            rows.append(
                LiveSqlBenchCase(
                    instance_id=instance_id,
                    selected_database=database,
                    query=query,
                    preprocess_sql=_string_tuple(
                        raw["preprocess_sql"], "preprocess_sql", row_number
                    ),
                    clean_up_sqls=_string_tuple(raw["clean_up_sqls"], "clean_up_sqls", row_number),
                    sol_sql=_string_tuple(raw["sol_sql"], "sol_sql", row_number),
                    external_knowledge=knowledge,
                    test_cases=test_cases,
                    category=category,
                    high_level=raw.get("high_level")
                    if isinstance(raw.get("high_level"), bool)
                    else None,
                    conditions=dict(raw["conditions"]),
                    difficulty_tier=str(raw.get("difficulty_tier") or "unknown"),
                    raw=dict(raw),
                )
            )
            seen.add(instance_id)
    return LiveSqlBenchDataset(path, tuple(rows), sha256_file(path))


def metadata_files(root: Path, database: str) -> tuple[Path, Path, Path]:
    directory = root / database
    return (
        directory / f"{database}_schema.txt",
        directory / f"{database}_kb.jsonl",
        directory / f"{database}_column_meaning_base.json",
    )


def validate_database_assets(root: Path, databases: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    for database in databases:
        for path in metadata_files(root, database):
            if not path.is_file():
                errors.append(f"missing database asset: {path}")
    return errors
