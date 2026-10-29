from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, insert, select, update

from app.metadata.tables import reports
from app.reports.models import Report


class ReportStore(Protocol):
    def list(self) -> list[Report]: ...

    def get(self, report_id: str) -> Report | None: ...

    def save(self, report: Report) -> None: ...

    def delete(self, report_id: str) -> bool: ...


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
            result = connection.execute(delete(reports).where(reports.c.id == report_id))
        return bool(result.rowcount)
