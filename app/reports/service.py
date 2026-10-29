from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool

from app.catalog.models import SchemaCatalog
from app.metadata.service import GOVERNED_CONNECTION_ID, ConnectionService, QuestionSource
from app.operator.models import RunRecord, RuntimeOutcome
from app.operator.service import OperatorApplication, _saved_query_tables
from app.reports.basis import changed_tables, table_basis
from app.reports.models import (
    CardInput,
    CardRefresh,
    Report,
    ReportCard,
    ReportCreate,
    ReportInput,
    ReportSummary,
)
from app.reports.store import ReportStore


class ReportRejectedError(ValueError):
    pass


class _Lazy:
    """Read a connection's catalog once, and only when a new card is pinned."""

    def __init__(self, load: Callable[[], SchemaCatalog]) -> None:
        self._load = load
        self._value: SchemaCatalog | None = None

    def get(self) -> SchemaCatalog:
        if self._value is None:
            self._value = self._load()
        return self._value


class ReportService:
    def __init__(
        self, store: ReportStore, operator: OperatorApplication, connections: ConnectionService
    ) -> None:
        self.store = store
        self.operator = operator
        self.connections = connections

    def list(self) -> list[ReportSummary]:
        return [report.summary() for report in self.store.list()]

    def get(self, report_id: str) -> Report | None:
        return self.store.get(report_id)

    def create(self, payload: ReportCreate, actor: str) -> Report:
        known = {item.id for item in self.connections.connections() if item.queryable}
        if payload.connection_id not in known:
            raise ReportRejectedError("Reports run on a connection that can answer questions.")
        now = datetime.now(UTC)
        report = Report(
            id=f"rep_{uuid4().hex[:12]}",
            connection_id=payload.connection_id,
            title=payload.title.strip(),
            description="",
            cards=[],
            created_by=actor,
            created_at=now,
            updated_by=actor,
            updated_at=now,
        )
        self.store.save(report)
        return report

    def update(self, report_id: str, payload: ReportInput, actor: str) -> Report | None:
        report = self.store.get(report_id)
        if report is None:
            return None
        if len({card.id for card in payload.cards}) != len(payload.cards):
            raise ReportRejectedError("Two cards share an id.")
        previous = {card.id: card for card in report.cards}
        catalog = _Lazy(lambda: self._catalog(report.connection_id))
        cards = [self._pin(report, card, previous.get(card.id), catalog) for card in payload.cards]
        updated = report.model_copy(
            update={
                "title": payload.title.strip(),
                "description": payload.description,
                "cards": cards,
                "updated_by": actor,
                "updated_at": datetime.now(UTC),
            }
        )
        self.store.save(updated)
        return updated

    def delete(self, report_id: str) -> bool:
        return self.store.delete(report_id)

    async def ask(self, report_id: str, question: str, actor: str) -> RunRecord | None:
        """Answer a new card's question the way the Ask page does, with the model."""
        report = self.store.get(report_id)
        if report is None:
            return None
        source = await self._source(report.connection_id)
        return await self.operator.run(question, actor=actor, source=source)

    async def refresh(self, report_id: str, card_id: str, actor: str) -> CardRefresh | None:
        """Run a card's pinned SQL again, through every check, without a model call.

        The checks say whether the query may still run; the basis says whether the
        tables it reads are still described the way they were when it was pinned.
        """
        found = self._query_card(report_id, card_id)
        if found is None:
            return None
        report, card = found
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        run = await self.operator.run(
            card.question or card.title or "Saved report query",
            actor=actor,
            source=source,
            saved_sql=card.sql,
        )
        return CardRefresh(run=run, changed_tables=changed_tables(card.basis, catalog))

    async def accept(self, report_id: str, card_id: str, actor: str) -> Report | None:
        """Record that a reader checked the card against the tables as they are now."""
        found = self._query_card(report_id, card_id)
        if found is None:
            return None
        report, card = found
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        basis = table_basis(catalog, _saved_query_tables(card.sql or ""))
        updated = report.model_copy(
            update={
                "cards": [
                    item.model_copy(update={"basis": basis}) if item.id == card.id else item
                    for item in report.cards
                ],
                "updated_by": actor,
                "updated_at": datetime.now(UTC),
            }
        )
        await run_in_threadpool(self.store.save, updated)
        return updated

    def _query_card(self, report_id: str, card_id: str) -> tuple[Report, ReportCard] | None:
        report = self.store.get(report_id)
        if report is None:
            return None
        card = next((item for item in report.cards if item.id == card_id), None)
        if card is None:
            return None
        if card.kind != "query" or card.sql is None:
            raise ReportRejectedError("This card has no saved query to run.")
        return report, card

    def _pin(
        self,
        report: Report,
        card: CardInput,
        previous: ReportCard | None,
        catalog: _Lazy,
    ) -> ReportCard:
        """Take the card's SQL from its run in the audit log, never from the client."""
        if card.kind == "text" or card.run_id is None:
            return ReportCard(**card.model_dump(), sql=None)
        if previous is not None and previous.run_id == card.run_id:
            return ReportCard(
                **card.model_dump(exclude={"question"}),
                question=previous.question,
                sql=previous.sql,
                basis=previous.basis,
            )
        run = self.operator.get_run(card.run_id)
        if run is None:
            raise ReportRejectedError(f"Run {card.run_id} is not in the audit log.")
        if run.connection_id != report.connection_id:
            raise ReportRejectedError("A card's run must come from the report's connection.")
        if run.runtime_outcome is not RuntimeOutcome.EXECUTED or not run.executed_sql:
            raise ReportRejectedError("Only a question that ran can become a card.")
        return ReportCard(
            **card.model_dump(exclude={"question"}),
            question=run.question,
            sql=run.executed_sql,
            basis=table_basis(catalog.get(), _saved_query_tables(run.executed_sql)),
        )

    def _catalog(self, connection_id: str) -> SchemaCatalog:
        if connection_id == GOVERNED_CONNECTION_ID:
            return self.operator.catalog
        source = self.connections.question_source(connection_id)
        if source is None:
            raise ReportRejectedError("The report's connection no longer exists.")
        return source.catalog

    async def _source(self, connection_id: str) -> QuestionSource | None:
        if connection_id == GOVERNED_CONNECTION_ID:
            return None
        source = await run_in_threadpool(self.connections.question_source, connection_id)
        if source is None:
            raise ReportRejectedError("The report's connection no longer exists.")
        return source
