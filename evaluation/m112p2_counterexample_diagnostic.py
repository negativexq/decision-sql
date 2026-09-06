"""Bounded PostgreSQL counterexample differential diagnostic.

This module is evaluation-only.  It reuses the application's M1 planner and
read-only executor, but never turns finite fixture agreement into equivalence.
The only positive semantic result is a witnessed result difference under a
versioned, governed fixture and comparison contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, text
from sqlglot import parse_one

from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlExecutionError
from app.sql.service import SqlSafetyService


class DiagnosticState(StrEnum):
    NON_EQUIVALENCE_WITNESSED = "NON_EQUIVALENCE_WITNESSED"
    NO_COUNTEREXAMPLE_FOUND = "NO_COUNTEREXAMPLE_FOUND"
    EXECUTION_INCONCLUSIVE = "EXECUTION_INCONCLUSIVE"
    FIXTURE_INVALID = "FIXTURE_INVALID"
    QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT = (
        "QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT"
    )


class ComparisonMode(StrEnum):
    VALUE_BAG = "VALUE_BAG"
    VALUE_ORDERED = "VALUE_ORDERED"
    SCALAR = "SCALAR"


class DiagnosticQueryPair(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair_id: str
    candidate_sql: str
    reference_sql: str
    comparison_mode: ComparisonMode = ComparisonMode.VALUE_BAG
    order_entitled: bool = False
    witness_required: bool = False
    corpus_class: str
    scenario_tags: tuple[str, ...] = ()

    @property
    def candidate_sql_hash(self) -> str:
        return text_hash(self.candidate_sql)

    @property
    def reference_sql_hash(self) -> str:
        return text_hash(self.reference_sql)


class DiagnosticFixture(BaseModel):
    model_config = ConfigDict(frozen=True)

    fixture_id: str
    fixture_version: str
    schema_version: str
    seed: str
    content_hash: str
    scenario_tags: tuple[str, ...] = ()
    valid: bool = True
    validation_note: str | None = None


class DiagnosticExecutionSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    query_hash: str
    fixture_id: str
    columns: tuple[str, ...]
    typed_rows: tuple[tuple[tuple[str, Any], ...], ...]
    row_count: int
    truncated: bool
    result_hash: str
    latency_ms: float


class CounterexampleWitness(BaseModel):
    model_config = ConfigDict(frozen=True)

    fixture_id: str
    fixture_hash: str
    scenario_tags: tuple[str, ...]
    candidate_sql_hash: str
    reference_sql_hash: str
    comparison_mode: ComparisonMode
    candidate_result_hash: str
    reference_result_hash: str
    engine_version: str
    schema_hash: str
    diagnostic_state: str = DiagnosticState.NON_EQUIVALENCE_WITNESSED
    bounded_summary: dict[str, Any] = Field(default_factory=dict)


class CounterexampleDiagnosticResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    pair_id: str
    state: DiagnosticState
    candidate_sql_hash: str
    reference_sql_hash: str
    fixture_attempts: tuple[str, ...]
    witness: CounterexampleWitness | None = None
    reason: str | None = None
    result_hash: str


class DiagnosticFixtureBank(BaseModel):
    model_config = ConfigDict(frozen=True)

    bank_version: str
    schema_version: str
    fixtures: tuple[DiagnosticFixture, ...]
    content_hash: str

    def fixture(self, fixture_id: str) -> DiagnosticFixture:
        for fixture in self.fixtures:
            if fixture.fixture_id == fixture_id:
                return fixture
        raise KeyError(fixture_id)


def text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _typed_value(value: object) -> dict[str, Any]:
    if value is None:
        return {"type": "NULL", "value": None}
    if isinstance(value, bool):
        return {"type": "BOOLEAN", "value": value}
    if isinstance(value, int):
        return {"type": "INTEGER", "value": value}
    if isinstance(value, Decimal):
        return {"type": "DECIMAL", "value": str(value)}
    if isinstance(value, float):
        return {"type": "FLOAT", "value": repr(value)}
    if isinstance(value, datetime):
        return {"type": "DATETIME", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "DATE", "value": value.isoformat()}
    if isinstance(value, str):
        return {"type": "STRING", "value": value}
    if isinstance(value, (list, tuple)):
        return {"type": "ARRAY", "value": [_typed_value(item) for item in value]}
    raise TypeError(f"unsupported diagnostic result value: {type(value).__name__}")


def _snapshot(
    execution: QueryExecution,
    query_hash: str,
    fixture: DiagnosticFixture,
    *,
    order_sensitive: bool = False,
) -> DiagnosticExecutionSnapshot:
    typed_rows = tuple(
        tuple((column, _typed_value(row.get(column))) for column in execution.columns)
        for row in execution.rows
    )
    hash_rows = typed_rows if order_sensitive else tuple(sorted(typed_rows, key=_row_key))
    payload = {
        "columns": list(execution.columns),
        "rows": hash_rows,
        "row_count": execution.row_count,
        "truncated": execution.truncated,
    }
    return DiagnosticExecutionSnapshot(
        query_hash=query_hash,
        fixture_id=fixture.fixture_id,
        columns=tuple(execution.columns),
        typed_rows=typed_rows,
        row_count=execution.row_count,
        truncated=execution.truncated,
        result_hash=stable_hash(payload),
        latency_ms=execution.latency_ms,
    )


def _row_key(row: tuple[tuple[str, Any], ...]) -> str:
    return json.dumps(_row_values(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row_values(row: tuple[tuple[str, Any], ...]) -> tuple[Any, ...]:
    """Drop output aliases while retaining ordinal position and typed values."""
    return tuple(value for _, value in row)


def compare_snapshots(
    candidate: DiagnosticExecutionSnapshot,
    reference: DiagnosticExecutionSnapshot,
    mode: ComparisonMode,
    *,
    order_entitled: bool,
) -> bool:
    """Compare typed rows, preserving bag multiplicity and exact scalar types."""
    if candidate.truncated or reference.truncated:
        raise ValueError("truncated results are not comparable")
    if mode is ComparisonMode.SCALAR:
        if len(candidate.typed_rows) != 1 or len(reference.typed_rows) != 1:
            return False
        return _row_values(candidate.typed_rows[0]) == _row_values(reference.typed_rows[0])
    if mode is ComparisonMode.VALUE_ORDERED and order_entitled:
        return len(candidate.columns) == len(reference.columns) and tuple(
            _row_values(row) for row in candidate.typed_rows
        ) == tuple(_row_values(row) for row in reference.typed_rows)
    # Without explicit order entitlement, top-level row ordering is not a
    # semantic witness. VALUE_BAG preserves row multiplicity while ignoring order.
    return sorted((_row_values(row) for row in candidate.typed_rows), key=repr) == sorted(
        (_row_values(row) for row in reference.typed_rows), key=repr
    )


def _has_order_by(sql: str) -> bool:
    return parse_one(sql, read="postgres").args.get("order") is not None


def _fixture_hash(fixture: DiagnosticFixture) -> str:
    return stable_hash(fixture.model_dump(exclude={"content_hash"}))


def validate_fixture_bank(bank: DiagnosticFixtureBank) -> None:
    expected_bank_hash = stable_hash(
        {
            "bank_version": bank.bank_version,
            "schema_version": bank.schema_version,
            "fixtures": [fixture.model_dump(exclude={"content_hash"}) for fixture in bank.fixtures],
        }
    )
    if bank.content_hash != expected_bank_hash:
        raise ValueError("diagnostic fixture bank hash mismatch")
    for fixture in bank.fixtures:
        if fixture.content_hash != _fixture_hash(fixture):
            raise ValueError(f"fixture hash mismatch: {fixture.fixture_id}")


class DiagnosticExecutionHarness:
    """Bounded D2B execution over an isolated PostgreSQL reader engine."""

    def __init__(
        self,
        service: SqlSafetyService,
        fixture_bank: DiagnosticFixtureBank,
        *,
        engine_version: str,
        schema_hash: str,
    ) -> None:
        validate_fixture_bank(fixture_bank)
        self.service = service
        self.fixture_bank = fixture_bank
        self.engine_version = engine_version
        self.schema_hash = schema_hash

    def run_pair(self, pair: DiagnosticQueryPair) -> CounterexampleDiagnosticResult:
        attempts: list[str] = []
        first_inconclusive: DiagnosticState | None = None
        reason: str | None = None
        for fixture in self.fixture_bank.fixtures:
            attempts.append(fixture.fixture_id)
            if not fixture.valid:
                return self._result(
                    pair,
                    DiagnosticState.FIXTURE_INVALID,
                    tuple(attempts),
                    "fixture is outside the governed constraint contract",
                )
            if pair.comparison_mode is ComparisonMode.VALUE_ORDERED and pair.order_entitled:
                try:
                    if not _has_order_by(pair.candidate_sql) or not _has_order_by(
                        pair.reference_sql
                    ):
                        first_inconclusive = DiagnosticState.EXECUTION_INCONCLUSIVE
                        reason = "order-entitled comparison lacks explicit ORDER BY"
                        continue
                except Exception:
                    return self._result(
                        pair,
                        DiagnosticState.QUERY_NOT_EXECUTABLE_UNDER_DIAGNOSTIC_CONTRACT,
                        tuple(attempts),
                        "SQL could not be parsed for order contract",
                    )
            candidate = self._execute(pair.candidate_sql)
            reference = self._execute(pair.reference_sql)
            if not isinstance(candidate, QueryExecution) or not isinstance(
                reference, QueryExecution
            ):
                first_inconclusive = DiagnosticState.EXECUTION_INCONCLUSIVE
                reason = "one or both read-only diagnostic executions failed"
                continue
            if candidate.truncated or reference.truncated:
                first_inconclusive = DiagnosticState.EXECUTION_INCONCLUSIVE
                reason = "result cap truncated a diagnostic result"
                continue
            try:
                order_sensitive = (
                    pair.comparison_mode is ComparisonMode.VALUE_ORDERED
                    and pair.order_entitled
                )
                candidate_snapshot = _snapshot(
                    candidate,
                    pair.candidate_sql_hash,
                    fixture,
                    order_sensitive=order_sensitive,
                )
                reference_snapshot = _snapshot(
                    reference,
                    pair.reference_sql_hash,
                    fixture,
                    order_sensitive=order_sensitive,
                )
                equal = compare_snapshots(
                    candidate_snapshot,
                    reference_snapshot,
                    pair.comparison_mode,
                    order_entitled=pair.order_entitled,
                )
            except (TypeError, ValueError) as error:
                return self._result(
                    pair,
                    DiagnosticState.EXECUTION_INCONCLUSIVE,
                    tuple(attempts),
                    str(error),
                )
            if not equal:
                witness = CounterexampleWitness(
                    fixture_id=fixture.fixture_id,
                    fixture_hash=fixture.content_hash,
                    scenario_tags=fixture.scenario_tags,
                    candidate_sql_hash=pair.candidate_sql_hash,
                    reference_sql_hash=pair.reference_sql_hash,
                    comparison_mode=pair.comparison_mode,
                    candidate_result_hash=candidate_snapshot.result_hash,
                    reference_result_hash=reference_snapshot.result_hash,
                    engine_version=self.engine_version,
                    schema_hash=self.schema_hash,
                    bounded_summary={
                        "candidate_rows": candidate_snapshot.row_count,
                        "reference_rows": reference_snapshot.row_count,
                        "candidate_columns": list(candidate_snapshot.columns),
                        "reference_columns": list(reference_snapshot.columns),
                    },
                )
                return self._result(
                    pair,
                    DiagnosticState.NON_EQUIVALENCE_WITNESSED,
                    tuple(attempts),
                    witness=witness,
                )
        if first_inconclusive is not None:
            return self._result(pair, first_inconclusive, tuple(attempts), reason)
        return self._result(pair, DiagnosticState.NO_COUNTEREXAMPLE_FOUND, tuple(attempts))

    def run_pairs(
        self, pairs: Iterable[DiagnosticQueryPair]
    ) -> tuple[CounterexampleDiagnosticResult, ...]:
        return tuple(self.run_pair(pair) for pair in pairs)

    def _execute(self, sql: str) -> QueryExecution | SqlExecutionError:
        plan = self.service.plan(SqlCandidate(sql=sql, correlation_id="m112p2-diagnostic"))
        if not isinstance(plan, QueryPlan):
            return SqlExecutionError(error="query was rejected by the diagnostic admission path")
        return self.service.execute(plan)

    @staticmethod
    def _result(
        pair: DiagnosticQueryPair,
        state: DiagnosticState,
        attempts: tuple[str, ...],
        reason: str | None = None,
        *,
        witness: CounterexampleWitness | None = None,
    ) -> CounterexampleDiagnosticResult:
        payload = {
            "pair_id": pair.pair_id,
            "state": state.value,
            "attempts": list(attempts),
            "witness": witness.model_dump() if witness else None,
            "reason": reason,
        }
        return CounterexampleDiagnosticResult(
            pair_id=pair.pair_id,
            state=state,
            candidate_sql_hash=pair.candidate_sql_hash,
            reference_sql_hash=pair.reference_sql_hash,
            fixture_attempts=attempts,
            witness=witness,
            reason=reason,
            result_hash=stable_hash(payload),
        )


def postgres_metadata(engine: Engine) -> dict[str, str]:
    """Capture bounded provenance for the isolated diagnostic database."""
    with engine.connect() as connection:
        with connection.begin():
            row = connection.execute(
                text(
                    "SELECT version(), current_database(), current_user, "
                    "current_setting('transaction_read_only')"
                )
            ).one()
            schema_rows = connection.execute(
                text(
                    "SELECT table_name, column_name, data_type "
                    "FROM information_schema.columns WHERE table_schema='public' "
                    "ORDER BY table_name, ordinal_position"
                )
            ).all()
    schema_hash = stable_hash([tuple(row) for row in schema_rows])
    return {
        "engine_version": str(row[0]),
        "database": str(row[1]),
        "reader_role": str(row[2]),
        "transaction_read_only": str(row[3]),
        "schema_hash": schema_hash,
    }
