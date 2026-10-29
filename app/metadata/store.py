"""Audit-log and connection persistence.

``SqlRunStore`` and ``SqlConnectionStore`` are the production stores. The
in-memory variants exist for tests and for running the API without a
metadata database; the composition root logs which one is active.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import Engine, delete, func, insert, select, update

from app.metadata.connectors import (
    QUESTION_KINDS,
    ConnectionConfig,
    ConnectionSummary,
    ConnectionTestResult,
)
from app.metadata.tables import audit_runs, connections
from app.observability.run_trace import RunTrace
from app.operator.models import RunRecord, RunSummary

_AUDIT_FIELDS = (
    "run_id",
    "actor",
    "connection_id",
    "question",
    "preset_id",
    "proposal_source",
    "model_decision",
    "model_reason_code",
    "runtime_outcome",
    "runtime_reason",
    "provider",
    "model",
    "proposed_sql",
    "repaired_from_sql",
    "executed_sql",
    "sql_changes",
    "follow_up_of",
    "edited_from",
    "columns",
    "row_count",
    "truncated",
    "duration_ms",
    "model_context_hash",
)


class RunStore(Protocol):
    durable: bool

    def save(self, record: RunRecord) -> None: ...

    def get(self, run_id: str) -> RunRecord | None: ...

    def list(
        self, *, actor: str | None = None, outcome: str | None = None, limit: int = 200
    ) -> list[RunSummary]: ...


def summarize(record: RunRecord) -> RunSummary:
    return RunSummary.model_validate(record.model_dump(include=set(RunSummary.model_fields)))


def _matches(record: RunRecord, actor: str | None, outcome: str | None) -> bool:
    return (actor is None or record.actor == actor) and (
        outcome is None or record.runtime_outcome.value == outcome
    )


class InMemoryRunStore:
    """Bounded process-local store; history is lost on restart."""

    durable = False

    def __init__(self, max_runs: int = 100) -> None:
        self._runs: OrderedDict[str, RunRecord] = OrderedDict()
        self._max_runs = max_runs

    def save(self, record: RunRecord) -> None:
        self._runs[record.run_id] = record
        self._runs.move_to_end(record.run_id)
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list(
        self, *, actor: str | None = None, outcome: str | None = None, limit: int = 200
    ) -> list[RunSummary]:
        records = [run for run in reversed(self._runs.values()) if _matches(run, actor, outcome)]
        return [summarize(run) for run in records[:limit]]


class SqlRunStore:
    """Append-only audit log. Result rows are never persisted, only their shape."""

    durable = True

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, record: RunRecord) -> None:
        values: dict[str, Any] = record.model_dump(mode="json", include=set(_AUDIT_FIELDS))
        values["model_context"] = (
            record.model_context.model_dump(mode="json") if record.model_context else None
        )
        values["model_detail"] = (
            record.model_detail.model_dump(mode="json") if record.model_detail else None
        )
        values["trace"] = record.trace.model_dump(mode="json")
        values["created_at"] = datetime.fromisoformat(record.created_at)
        with self.engine.begin() as connection:
            connection.execute(insert(audit_runs).values(**values))

    def get(self, run_id: str) -> RunRecord | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(audit_runs).where(audit_runs.c.run_id == run_id))
                .mappings()
                .first()
            )
        if row is None:
            return None
        data = dict(row)
        data["created_at"] = data["created_at"].isoformat()
        # Runs recorded before 0005 have no change list.
        data["sql_changes"] = data.get("sql_changes") or []
        data["trace"] = RunTrace.model_validate(data["trace"])
        data["trace_id"] = data["trace"].trace_id
        return RunRecord.model_validate({**data, "rows": [], "results_retained": False})

    def list(
        self, *, actor: str | None = None, outcome: str | None = None, limit: int = 200
    ) -> list[RunSummary]:
        query = select(audit_runs).order_by(audit_runs.c.created_at.desc()).limit(limit)
        if actor is not None:
            query = query.where(audit_runs.c.actor == actor)
        if outcome is not None:
            query = query.where(audit_runs.c.runtime_outcome == outcome)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            RunSummary.model_validate(
                {
                    **{key: row[key] for key in RunSummary.model_fields if key in row},
                    "trace_id": row["trace"]["trace_id"],
                    "created_at": row["created_at"].isoformat(),
                }
            )
            for row in rows
        ]


class StoredConnection(ConnectionSummary):
    password_encrypted: bytes | None = None

    def config(self) -> ConnectionConfig:
        return ConnectionConfig(
            kind=self.kind,
            name=self.name,
            host=self.host,
            port=self.port,
            database=self.database,
            username=self.username,
            schema_name=self.schema_name,
            ssl_mode=self.ssl_mode,
            options=self.options,
        )

    def summary(self) -> ConnectionSummary:
        return ConnectionSummary.model_validate(
            {
                **self.model_dump(exclude={"password_encrypted"}),
                "queryable": self.kind in QUESTION_KINDS,
            }
        )


class DuplicateConnectionError(ValueError):
    pass


class ConnectionStore(Protocol):
    def list(self) -> list[StoredConnection]: ...

    def get(self, connection_id: str) -> StoredConnection | None: ...

    def create(
        self, config: ConnectionConfig, password_encrypted: bytes | None, actor: str
    ) -> StoredConnection: ...

    def delete(self, connection_id: str) -> bool: ...

    def record_test(self, connection_id: str, result: ConnectionTestResult) -> None: ...


def _new_connection(
    config: ConnectionConfig, password_encrypted: bytes | None, actor: str
) -> StoredConnection:
    return StoredConnection(
        id=f"conn_{uuid4().hex[:12]}",
        **config.model_dump(exclude={"password"}),
        has_password=password_encrypted is not None,
        password_encrypted=password_encrypted,
        created_by=actor,
        created_at=datetime.now(UTC),
    )


class InMemoryConnectionStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredConnection] = {}

    def list(self) -> list[StoredConnection]:
        return sorted(self._items.values(), key=lambda item: item.name.lower())

    def get(self, connection_id: str) -> StoredConnection | None:
        return self._items.get(connection_id)

    def create(
        self, config: ConnectionConfig, password_encrypted: bytes | None, actor: str
    ) -> StoredConnection:
        if any(item.name.lower() == config.name.lower() for item in self._items.values()):
            raise DuplicateConnectionError(f"A connection named {config.name} already exists.")
        item = _new_connection(config, password_encrypted, actor)
        self._items[item.id] = item
        return item

    def delete(self, connection_id: str) -> bool:
        return self._items.pop(connection_id, None) is not None

    def record_test(self, connection_id: str, result: ConnectionTestResult) -> None:
        item = self._items.get(connection_id)
        if item is not None:
            self._items[connection_id] = item.model_copy(
                update={
                    "last_tested_at": datetime.now(UTC),
                    "last_test_ok": result.ok,
                    "last_test_message": result.message,
                }
            )


class SqlConnectionStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self) -> list[StoredConnection]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(select(connections).order_by(connections.c.name))
                .mappings()
                .all()
            )
        return [self._row(row) for row in rows]

    def get(self, connection_id: str) -> StoredConnection | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(connections).where(connections.c.id == connection_id))
                .mappings()
                .first()
            )
        return self._row(row) if row is not None else None

    def create(
        self, config: ConnectionConfig, password_encrypted: bytes | None, actor: str
    ) -> StoredConnection:
        item = _new_connection(config, password_encrypted, actor)
        values = item.model_dump(
            exclude={
                "has_password",
                "built_in",
                "governed",
                "queryable",
                "last_tested_at",
                "last_test_ok",
                "last_test_message",
            }
        )
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(connections.c.id).where(
                    func.lower(connections.c.name) == config.name.lower()
                )
            ).first()
            if exists:
                raise DuplicateConnectionError(f"A connection named {config.name} already exists.")
            connection.execute(insert(connections).values(**values))
        return item

    def delete(self, connection_id: str) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(
                delete(connections).where(connections.c.id == connection_id)
            )
        return bool(result.rowcount)

    def record_test(self, connection_id: str, result: ConnectionTestResult) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(connections)
                .where(connections.c.id == connection_id)
                .values(
                    last_tested_at=datetime.now(UTC),
                    last_test_ok=result.ok,
                    last_test_message=result.message,
                )
            )

    @staticmethod
    def _row(row: Any) -> StoredConnection:
        data = dict(row)
        return StoredConnection.model_validate(
            {**data, "has_password": data["password_encrypted"] is not None}
        )
