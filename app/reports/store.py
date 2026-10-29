from __future__ import annotations

import builtins
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine, delete, insert, select, update

from app.metadata.tables import report_snapshots, reports
from app.reports.models import Report, Snapshot, SnapshotSummary


class ReportStore(Protocol):
    def list(self) -> list[Report]: ...

    def get(self, report_id: str) -> Report | None: ...

    def save(self, report: Report) -> None: ...

    def delete(self, report_id: str) -> bool: ...

    def due(self, now: datetime) -> builtins.list[Report]: ...

    def claim(self, report_id: str, expected: datetime, following: datetime | None) -> bool:
        """Move a due report's next refresh on, if no one else has; True if this caller did."""
        ...


class InMemoryReportStore:
    def __init__(self) -> None:
        self._items: dict[str, Report] = {}

    def list(self) -> list[Report]:
        return sorted(self._items.values(), key=lambda item: item.updated_at, reverse=True)

    def get(self, report_id: str) -> Report | None:
        return self._items.get(report_id)

    def save(self, report: Report) -> None:
        self._items[report.id] = report

    def delete(self, report_id: str) -> bool:
        return self._items.pop(report_id, None) is not None

    def due(self, now: datetime) -> builtins.list[Report]:
        return [
            item
            for item in self._items.values()
            if item.next_refresh_at is not None and item.next_refresh_at <= now
        ]

    def claim(self, report_id: str, expected: datetime, following: datetime | None) -> bool:
        item = self._items.get(report_id)
        if item is None or item.next_refresh_at != expected:
            return False
        self._items[report_id] = item.model_copy(update={"next_refresh_at": following})
        return True


class SqlReportStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def list(self) -> list[Report]:
        query = select(reports).order_by(reports.c.updated_at.desc())
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [Report.model_validate(dict(row)) for row in rows]

    def get(self, report_id: str) -> Report | None:
        query = select(reports).where(reports.c.id == report_id)
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        return Report.model_validate(dict(row)) if row is not None else None

    def save(self, report: Report) -> None:
        values = report.model_dump(mode="json", exclude={"created_at", "updated_at"})
        values.update(created_at=report.created_at, updated_at=report.updated_at)
        with self.engine.begin() as connection:
            exists = connection.execute(
                select(reports.c.id).where(reports.c.id == report.id)
            ).first()
            if exists:
                connection.execute(
                    update(reports).where(reports.c.id == report.id).values(**values)
                )
            else:
                connection.execute(insert(reports).values(**values))

    def delete(self, report_id: str) -> bool:
        with self.engine.begin() as connection:
            connection.execute(
                delete(report_snapshots).where(report_snapshots.c.report_id == report_id)
            )
            result = connection.execute(delete(reports).where(reports.c.id == report_id))
        return bool(result.rowcount)

    def due(self, now: datetime) -> builtins.list[Report]:
        query = select(reports).where(reports.c.next_refresh_at <= now)
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [Report.model_validate(dict(row)) for row in rows]

    def claim(self, report_id: str, expected: datetime, following: datetime | None) -> bool:
        # A conditional update: of several workers, only one moves the time on.
        with self.engine.begin() as connection:
            result = connection.execute(
                update(reports)
                .where(reports.c.id == report_id, reports.c.next_refresh_at == expected)
                .values(next_refresh_at=following)
            )
        return bool(result.rowcount)


class SnapshotStore(Protocol):
    def save(self, snapshot: Snapshot, keep: int) -> None:
        """Store a snapshot and drop the report's oldest beyond ``keep``."""
        ...

    def list(self, report_id: str) -> list[SnapshotSummary]: ...

    def get(self, snapshot_id: str) -> Snapshot | None: ...


class InMemorySnapshotStore:
    def __init__(self) -> None:
        self._items: dict[str, Snapshot] = {}

    def save(self, snapshot: Snapshot, keep: int) -> None:
        self._items[snapshot.id] = snapshot
        for old in self._ordered(snapshot.report_id)[keep:]:
            del self._items[old.id]

    def list(self, report_id: str) -> list[SnapshotSummary]:
        return [item.summary() for item in self._ordered(report_id)]

    def get(self, snapshot_id: str) -> Snapshot | None:
        return self._items.get(snapshot_id)

    def _ordered(self, report_id: str) -> builtins.list[Snapshot]:
        return sorted(
            (item for item in self._items.values() if item.report_id == report_id),
            key=lambda item: item.taken_at,
            reverse=True,
        )


class SqlSnapshotStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, snapshot: Snapshot, keep: int) -> None:
        values = snapshot.model_dump(mode="json", exclude={"taken_at"})
        values["taken_at"] = snapshot.taken_at
        with self.engine.begin() as connection:
            connection.execute(insert(report_snapshots).values(**values))
            kept = (
                select(report_snapshots.c.id)
                .where(report_snapshots.c.report_id == snapshot.report_id)
                .order_by(report_snapshots.c.taken_at.desc())
                .limit(keep)
            )
            connection.execute(
                delete(report_snapshots).where(
                    report_snapshots.c.report_id == snapshot.report_id,
                    report_snapshots.c.id.not_in(kept.scalar_subquery()),
                )
            )

    def list(self, report_id: str) -> list[SnapshotSummary]:
        query = (
            select(report_snapshots)
            .where(report_snapshots.c.report_id == report_id)
            .order_by(report_snapshots.c.taken_at.desc())
        )
        with self.engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [Snapshot.model_validate(dict(row)).summary() for row in rows]

    def get(self, snapshot_id: str) -> Snapshot | None:
        query = select(report_snapshots).where(report_snapshots.c.id == snapshot_id)
        with self.engine.connect() as connection:
            row = connection.execute(query).mappings().first()
        return Snapshot.model_validate(dict(row)) if row is not None else None
