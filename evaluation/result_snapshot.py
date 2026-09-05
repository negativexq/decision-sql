"""Versioned, type-preserving persistence for bounded query results.

This module is intentionally evaluation-only.  It does not change SQL runtime
semantics and it does not attempt to recover types from legacy JSON values.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.sql.models import QueryExecution

SNAPSHOT_CONTRACT_ID = "decision-sql-query-result-snapshot-v1"
SNAPSHOT_CONTRACT_VERSION = 1


class SnapshotCellType(StrEnum):
    """Bounded database scalar types supported by the snapshot contract."""

    NULL = "NULL"
    BOOLEAN = "BOOLEAN"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    DECIMAL = "DECIMAL"
    STRING = "STRING"
    DATE = "DATE"
    DATETIME = "DATETIME"


class SnapshotClassification(StrEnum):
    """Classification of a persisted result payload before restoration."""

    TYPED_V1 = "TYPED_V1"
    LEGACY_UNTYPED_RESULT_SNAPSHOT = "LEGACY_UNTYPED_RESULT_SNAPSHOT"


class UnsupportedSnapshotCell(TypeError):
    """Raised when a result cell is outside the bounded persistence contract."""


class InvalidSnapshotContract(ValueError):
    """Raised when a payload is not a valid typed-v1 snapshot."""


def _cell(value: object) -> dict[str, object]:
    """Encode one supported cell with an explicit type tag."""
    if value is None:
        return {"type": SnapshotCellType.NULL.value, "value": None}
    if isinstance(value, bool):
        return {"type": SnapshotCellType.BOOLEAN.value, "value": value}
    if isinstance(value, int):
        return {"type": SnapshotCellType.INTEGER.value, "value": value}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsupportedSnapshotCell("non-finite FLOAT values are not supported")
        return {"type": SnapshotCellType.FLOAT.value, "value": value}
    if isinstance(value, Decimal):
        return {"type": SnapshotCellType.DECIMAL.value, "value": str(value)}
    if isinstance(value, datetime):
        return {"type": SnapshotCellType.DATETIME.value, "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": SnapshotCellType.DATE.value, "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": SnapshotCellType.STRING.value, "value": value}
    raise UnsupportedSnapshotCell(f"unsupported result cell type: {type(value).__name__}")


def _decode_cell(value: object) -> object:
    if not isinstance(value, Mapping) or set(value) != {"type", "value"}:
        raise InvalidSnapshotContract("typed result cell must contain exactly type and value")
    cell_type = value["type"]
    raw = value["value"]
    if not isinstance(cell_type, str):
        raise InvalidSnapshotContract("typed result cell type must be a string")
    if cell_type == SnapshotCellType.NULL:
        if raw is not None:
            raise InvalidSnapshotContract("NULL cell must have a null value")
        return None
    if cell_type == SnapshotCellType.BOOLEAN:
        if not isinstance(raw, bool):
            raise InvalidSnapshotContract("BOOLEAN cell must have a boolean value")
        return raw
    if cell_type == SnapshotCellType.INTEGER:
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise InvalidSnapshotContract("INTEGER cell must have an integer value")
        return raw
    if cell_type == SnapshotCellType.FLOAT:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise InvalidSnapshotContract("FLOAT cell must have a numeric value")
        result = float(raw)
        if not math.isfinite(result):
            raise InvalidSnapshotContract("FLOAT cell must be finite")
        return result
    if cell_type == SnapshotCellType.DECIMAL:
        if not isinstance(raw, str):
            raise InvalidSnapshotContract("DECIMAL cell must have decimal text")
        try:
            return Decimal(raw)
        except ArithmeticError as error:
            raise InvalidSnapshotContract("DECIMAL cell has invalid decimal text") from error
    if cell_type == SnapshotCellType.STRING:
        if not isinstance(raw, str):
            raise InvalidSnapshotContract("STRING cell must have a string value")
        return raw
    if cell_type == SnapshotCellType.DATE:
        if not isinstance(raw, str):
            raise InvalidSnapshotContract("DATE cell must have ISO text")
        try:
            return date.fromisoformat(raw)
        except ValueError as error:
            raise InvalidSnapshotContract("DATE cell has invalid ISO text") from error
    if cell_type == SnapshotCellType.DATETIME:
        if not isinstance(raw, str):
            raise InvalidSnapshotContract("DATETIME cell must have ISO text")
        try:
            return datetime.fromisoformat(raw)
        except ValueError as error:
            raise InvalidSnapshotContract("DATETIME cell has invalid ISO text") from error
    raise InvalidSnapshotContract(f"unsupported typed result cell: {cell_type}")


def snapshot_query_execution(execution: QueryExecution) -> dict[str, object]:
    """Return a JSON-compatible typed-v1 snapshot of ``execution``."""
    return {
        "snapshot_contract": SNAPSHOT_CONTRACT_ID,
        "snapshot_version": SNAPSHOT_CONTRACT_VERSION,
        "execution": {
            "plan_id": str(execution.plan_id),
            "correlation_id": execution.correlation_id,
            "status": execution.status.value,
            "columns": list(execution.columns),
            "rows": [
                {column: _cell(row.get(column)) for column in execution.columns}
                for row in execution.rows
            ],
            "row_count": execution.row_count,
            "truncated": execution.truncated,
            "latency_ms": execution.latency_ms,
        },
    }


def classify_snapshot_payload(payload: object) -> SnapshotClassification:
    """Identify typed-v1 payloads without guessing the types of legacy values."""
    if not isinstance(payload, Mapping):
        return SnapshotClassification.LEGACY_UNTYPED_RESULT_SNAPSHOT
    if (
        payload.get("snapshot_contract") == SNAPSHOT_CONTRACT_ID
        and payload.get("snapshot_version") == SNAPSHOT_CONTRACT_VERSION
    ):
        return SnapshotClassification.TYPED_V1
    return SnapshotClassification.LEGACY_UNTYPED_RESULT_SNAPSHOT


def restore_query_execution(payload: Mapping[str, object]) -> QueryExecution:
    """Restore a typed-v1 snapshot, rejecting unversioned legacy payloads."""
    if classify_snapshot_payload(payload) is not SnapshotClassification.TYPED_V1:
        raise InvalidSnapshotContract(SnapshotClassification.LEGACY_UNTYPED_RESULT_SNAPSHOT.value)
    execution = payload.get("execution")
    if not isinstance(execution, Mapping):
        raise InvalidSnapshotContract("typed snapshot execution is missing")
    required = {
        "plan_id",
        "correlation_id",
        "status",
        "columns",
        "rows",
        "row_count",
        "truncated",
        "latency_ms",
    }
    if set(execution) != required:
        raise InvalidSnapshotContract(
            "typed snapshot execution fields are incomplete or unexpected"
        )
    columns = execution["columns"]
    rows = execution["rows"]
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise InvalidSnapshotContract("typed snapshot columns must be a list of strings")
    if not isinstance(rows, list):
        raise InvalidSnapshotContract("typed snapshot rows must be a list")
    restored_rows: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != set(columns):
            raise InvalidSnapshotContract("typed snapshot row columns do not match columns")
        restored_rows.append({column: _decode_cell(row[column]) for column in columns})
    plan_id = execution["plan_id"]
    correlation_id = execution["correlation_id"]
    status = execution["status"]
    row_count = execution["row_count"]
    truncated = execution["truncated"]
    latency_ms = execution["latency_ms"]
    if not isinstance(plan_id, str) or not isinstance(correlation_id, (str, type(None))):
        raise InvalidSnapshotContract("typed snapshot metadata has invalid identifiers")
    if not isinstance(status, str) or isinstance(row_count, bool) or not isinstance(row_count, int):
        raise InvalidSnapshotContract("typed snapshot metadata has invalid status or row count")
    if (
        not isinstance(truncated, bool)
        or isinstance(latency_ms, bool)
        or not isinstance(latency_ms, (int, float))
    ):
        raise InvalidSnapshotContract("typed snapshot metadata has invalid truncation or latency")
    try:
        parsed_plan_id = UUID(plan_id)
    except ValueError as error:
        raise InvalidSnapshotContract("typed snapshot plan_id is not a UUID") from error
    return QueryExecution(
        plan_id=parsed_plan_id,
        correlation_id=correlation_id,
        status=status,
        columns=columns,
        rows=restored_rows,
        row_count=row_count,
        truncated=truncated,
        latency_ms=float(latency_ms),
    )


def serialize_query_execution(execution: QueryExecution) -> str:
    """Serialize a snapshot with stable JSON ordering and separators."""
    return json.dumps(
        snapshot_query_execution(execution),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
