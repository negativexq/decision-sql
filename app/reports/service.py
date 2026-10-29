from __future__ import annotations

import builtins
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.concurrency import run_in_threadpool
from sqlglot import exp

from app.catalog.models import SchemaCatalog
from app.decision.models import ProposalSource
from app.metadata.service import GOVERNED_CONNECTION_ID, ConnectionService, QuestionSource
from app.operator.models import RunRecord, RuntimeOutcome
from app.operator.service import OperatorApplication, _saved_query_tables
from app.reports.basis import changed_tables, table_basis
from app.reports.filters import FilterChoice, FilterKind, apply_filters, check_filters
from app.reports.models import (
    SCHEDULE_INTERVALS,
    SNAPSHOT_ROWS,
    SNAPSHOTS_KEPT,
    CardInput,
    CardRefresh,
    FilterColumn,
    FilterOptions,
    RefreshSchedule,
    Report,
    ReportAccess,
    ReportCard,
    ReportCreate,
    ReportInput,
    ReportRole,
    ReportSummary,
    ReportView,
    Snapshot,
    SnapshotCard,
    SnapshotSummary,
)
from app.reports.store import InMemorySnapshotStore, ReportStore, SnapshotStore

logger = logging.getLogger(__name__)
# The actor the audit log names for a refresh nobody asked for at the time.
SCHEDULER_ACTOR = "scheduler"


class ReportRejectedError(ValueError):
    pass


class ReportForbiddenError(PermissionError):
    """The reader may open the report but not do this to it."""


_EDIT = (ReportRole.OWNER, ReportRole.EDITOR)
_OPTION_LIMIT = 200


def _is_date(type_name: str) -> bool:
    return any(word in type_name.lower() for word in ("date", "time"))


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
        self,
        store: ReportStore,
        operator: OperatorApplication,
        connections: ConnectionService,
        snapshots: SnapshotStore | None = None,
    ) -> None:
        self.store = store
        self.operator = operator
        self.connections = connections
        self.snapshots = snapshots if snapshots is not None else InMemorySnapshotStore()

    def list(self, actor: str) -> list[ReportSummary]:
        return [
            report.summary(role)
            for report in self.store.list()
            if (role := report.role_of(actor)) is not None
        ]

    def get(self, report_id: str, actor: str) -> ReportView | None:
        found = self._open(report_id, actor)
        if found is None:
            return None
        report, role = found
        return ReportView(**report.model_dump(), role=role)

    def _open(
        self, report_id: str, actor: str, need: tuple[ReportRole, ...] = tuple(ReportRole)
    ) -> tuple[Report, ReportRole] | None:
        """The report and the actor's role in it; None when the actor cannot open it.

        A report the actor cannot open reads as missing, so its existence is not told.
        """
        report = self.store.get(report_id)
        role = report.role_of(actor) if report is not None else None
        if report is None or role is None:
            return None
        if role not in need:
            raise ReportForbiddenError(
                "Only the owner can do this."
                if need == (ReportRole.OWNER,)
                else "Only the owner or an editor can change this report."
            )
        return report, role

    def create(self, payload: ReportCreate, actor: str) -> ReportView:
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
        return ReportView(**report.model_dump(), role=ReportRole.OWNER)

    def update(self, report_id: str, payload: ReportInput, actor: str) -> ReportView | None:
        found = self._open(report_id, actor, _EDIT)
        if found is None:
            return None
        report, role = found
        if len({card.id for card in payload.cards}) != len(payload.cards):
            raise ReportRejectedError("Two cards share an id.")
        previous = {card.id: card for card in report.cards}
        # A copy of a card shares its run: it keeps that card's SQL and basis, so copying
        # does not quietly accept tables that changed since.
        by_run = {card.run_id: card for card in report.cards if card.run_id}
        catalog = _Lazy(lambda: self._catalog(report.connection_id))
        cards = [
            self._pin(
                report,
                card,
                previous.get(card.id) or (by_run.get(card.run_id) if card.run_id else None),
                catalog,
            )
            for card in payload.cards
        ]
        if payload.filters != report.filters:
            try:
                check_filters(payload.filters, catalog.get())
            except ValueError as error:
                raise ReportRejectedError(str(error)) from error
        updated = report.model_copy(
            update={
                "title": payload.title.strip(),
                "description": payload.description,
                "cards": cards,
                "filters": payload.filters,
                "updated_by": actor,
                "updated_at": datetime.now(UTC),
            }
        )
        self.store.save(updated)
        return ReportView(**updated.model_dump(), role=role)

    def share(self, report_id: str, access: ReportAccess, actor: str) -> ReportView | None:
        found = self._open(report_id, actor, (ReportRole.OWNER,))
        if found is None:
            return None
        report, role = found
        updated = report.model_copy(
            update={
                "visibility": access.visibility,
                "editors": [person for person in access.editors if person != report.created_by],
                "viewers": [
                    person
                    for person in access.viewers
                    if person != report.created_by and person not in access.editors
                ],
                "updated_by": actor,
                "updated_at": datetime.now(UTC),
            }
        )
        self.store.save(updated)
        return ReportView(**updated.model_dump(), role=role)

    def delete(self, report_id: str, actor: str) -> bool:
        if self._open(report_id, actor, (ReportRole.OWNER,)) is None:
            return False
        return self.store.delete(report_id)

    def set_schedule(
        self, report_id: str, schedule: RefreshSchedule, actor: str
    ) -> ReportView | None:
        found = self._open(report_id, actor, _EDIT)
        if found is None:
            return None
        report, role = found
        now = datetime.now(UTC)
        interval = SCHEDULE_INTERVALS.get(schedule)
        updated = report.model_copy(
            update={
                "schedule": schedule,
                "next_refresh_at": now + interval if interval is not None else None,
                "updated_by": actor,
                "updated_at": now,
            }
        )
        self.store.save(updated)
        return ReportView(**updated.model_dump(), role=role)

    async def take_snapshot(self, report_id: str, actor: str) -> SnapshotSummary | None:
        """Refresh every query card and keep the results, for any reader of the report."""
        found = self._open(report_id, actor)
        if found is None:
            return None
        report, _ = found
        snapshot = await self._snapshot(report, actor, scheduled=False)
        return snapshot.summary()

    def snapshots_of(self, report_id: str, actor: str) -> builtins.list[SnapshotSummary] | None:
        if self._open(report_id, actor) is None:
            return None
        return self.snapshots.list(report_id)

    async def snapshot(self, report_id: str, snapshot_id: str, actor: str) -> Snapshot | None:
        """A kept snapshot, with the rows of any card whose tables changed since held back.

        The rows passed the checks of the day they were read.  A column made sensitive or
        unreadable since then would still show in them, so a card whose tables are no
        longer described the same way shows its outcome but not its rows.
        """
        found = self._open(report_id, actor)
        if found is None:
            return None
        report, _ = found
        snapshot = self.snapshots.get(snapshot_id)
        if snapshot is None or snapshot.report_id != report.id:
            return None
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        cards = {
            card_id: (
                card.model_copy(update={"rows": [], "withheld": True})
                if card.rows and changed_tables(card.basis, catalog)
                else card
            )
            for card_id, card in snapshot.cards.items()
        }
        return snapshot.model_copy(update={"cards": cards})

    async def run_due(self, now: datetime | None = None) -> int:
        """Take a snapshot of every report whose scheduled refresh is due; the count taken.

        Claiming a report moves its next refresh on first, so two workers never take
        the same one.  A refresh that fails is logged and waits for its next turn.
        """
        now = now or datetime.now(UTC)
        taken = 0
        for report in await run_in_threadpool(self.store.due, now):
            interval = SCHEDULE_INTERVALS.get(report.schedule)
            expected = report.next_refresh_at
            if expected is None:
                continue
            following = None
            if interval is not None:
                following = expected + interval
                if following <= now:  # the server was down; do not catch up run by run
                    following = now + interval
            if not await run_in_threadpool(self.store.claim, report.id, expected, following):
                continue
            try:
                await self._snapshot(report, SCHEDULER_ACTOR, scheduled=True)
                taken += 1
            except Exception:
                logger.exception("Scheduled refresh of report %s failed", report.id)
        return taken

    async def _snapshot(self, report: Report, actor: str, *, scheduled: bool) -> Snapshot:
        # Unfiltered: a reader's filter picks live in their browser, not on the report.
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        cards: dict[str, SnapshotCard] = {}
        for card in report.cards:
            if card.kind != "query" or card.sql is None:
                continue
            run = await self.operator.run(
                card.question or card.title or "Saved report query",
                actor=actor,
                source=source,
                saved_sql=card.sql,
            )
            cards[card.id] = SnapshotCard(
                run_id=run.run_id,
                outcome=run.runtime_outcome.value,
                reason=run.runtime_reason,
                columns=run.columns,
                rows=run.rows[:SNAPSHOT_ROWS],
                row_count=run.row_count,
                truncated=run.truncated or len(run.rows) > SNAPSHOT_ROWS,
                basis=table_basis(catalog, _saved_query_tables(card.sql)),
            )
        snapshot = Snapshot(
            id=f"snap_{uuid4().hex[:12]}",
            report_id=report.id,
            taken_at=datetime.now(UTC),
            taken_by=actor,
            scheduled=scheduled,
            cards=cards,
        )
        await run_in_threadpool(self.snapshots.save, snapshot, SNAPSHOTS_KEPT)
        return snapshot

    async def ask(self, report_id: str, question: str, actor: str) -> RunRecord | None:
        """Answer a new card's question the way the Ask page does, with the model."""
        found = self._open(report_id, actor, _EDIT)
        if found is None:
            return None
        report, _ = found
        source = await self._source(report.connection_id)
        return await self.operator.run(question, actor=actor, source=source)

    async def follow_up(
        self, report_id: str, card_id: str, question: str, actor: str
    ) -> RunRecord | None:
        """Ask a new question that builds on a card's question, with the model.

        The model gets the card's question and the SQL it proposed then, never the
        SQL that ran or the reader's filter picks: both can hold stored values that
        unshared columns keep from the model.
        """
        found = self._query_card(report_id, card_id, actor)
        if found is None:
            return None
        report, _, card = found
        record = self.operator.get_run(card.run_id) if card.run_id else None
        if (
            record is None
            or record.proposal_source is not ProposalSource.LIVE_MODEL
            or not record.proposed_sql
        ):
            raise ReportRejectedError(
                "This card's answer was not written by the model, so there is nothing to build on."
            )
        source = await self._source(report.connection_id)
        return await self.operator.run(question, actor=actor, source=source, follow_up=record)

    async def refresh(
        self,
        report_id: str,
        card_id: str,
        actor: str,
        choices: dict[str, FilterChoice] | None = None,
    ) -> CardRefresh | None:
        """Run a card's pinned SQL again, through every check, without a model call.

        The checks say whether the query may still run; the basis says whether the
        tables it reads are still described the way they were when it was pinned.
        Any reader may refresh: the query runs as the connection's reader role, and
        the audit record names the reader.  The reader's filter choices narrow the
        pinned SQL first; the narrowed SQL is what the gates check and what runs.
        """
        found = self._query_card(report_id, card_id, actor)
        if found is None:
            return None
        report, _, card = found
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        filtered = apply_filters(card.sql or "", report.filters, choices or {})
        run = await self.operator.run(
            card.question or card.title or "Saved report query",
            actor=actor,
            source=source,
            saved_sql=filtered.sql,
        )
        return CardRefresh(
            run=run,
            changed_tables=changed_tables(card.basis, catalog),
            applied_filters=list(filtered.applied),
            skipped_filters=list(filtered.skipped),
        )

    async def filter_columns(
        self, report_id: str, actor: str
    ) -> builtins.list[FilterColumn] | None:
        """The columns an editor can make a filter of: every readable column."""
        found = self._open(report_id, actor, _EDIT)
        if found is None:
            return None
        report, _ = found
        source = await self._source(report.connection_id)
        catalog = source.catalog if source is not None else self.operator.catalog
        return [
            FilterColumn(
                table=table.name,
                column=column.name,
                type=column.type,
                kinds=[FilterKind.VALUES]
                + ([FilterKind.DATE_RANGE] if _is_date(column.type) else []),
            )
            for table in catalog.tables
            if table.queryable
            for column in table.columns
            if column.queryable
        ]

    async def filter_options(
        self, report_id: str, filter_id: str, actor: str
    ) -> FilterOptions | None:
        """The values a reader can pick, read by a query that passes every gate."""
        found = self._open(report_id, actor)
        if found is None:
            return None
        report, _ = found
        item = next((entry for entry in report.filters if entry.id == filter_id), None)
        if item is None:
            return None
        if item.kind is not FilterKind.VALUES:
            raise ReportRejectedError("A date filter has no list of values.")
        column = exp.column(item.column, table=item.table)
        sql = (
            exp.select(column.as_("value"))
            .distinct()
            .from_(exp.to_table(item.table))
            .where(exp.Not(this=exp.Is(this=column.copy(), expression=exp.Null())))
            .order_by(exp.Ordered(this=exp.Literal.number(1)))
            .limit(_OPTION_LIMIT + 1)
            .sql(dialect="postgres")
        )
        source = await self._source(report.connection_id)
        run = await self.operator.run(
            f"Values of the report filter {item.label}", actor=actor, source=source, saved_sql=sql
        )
        if run.runtime_outcome is not RuntimeOutcome.EXECUTED:
            raise ReportRejectedError(
                f"The values of {item.label} could not be read ({run.runtime_reason})."
            )
        values = [str(row["value"]) for row in run.rows if row.get("value") is not None]
        return FilterOptions(
            values=values[:_OPTION_LIMIT],
            truncated=len(values) > _OPTION_LIMIT,
            run_id=run.run_id,
        )

    async def accept(self, report_id: str, card_id: str, actor: str) -> ReportView | None:
        """Record that an editor checked the card against the tables as they are now."""
        found = self._query_card(report_id, card_id, actor, _EDIT)
        if found is None:
            return None
        report, role, card = found
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
        return ReportView(**updated.model_dump(), role=role)

    def _query_card(
        self,
        report_id: str,
        card_id: str,
        actor: str,
        need: tuple[ReportRole, ...] = tuple(ReportRole),
    ) -> tuple[Report, ReportRole, ReportCard] | None:
        found = self._open(report_id, actor, need)
        if found is None:
            return None
        report, role = found
        card = next((item for item in report.cards if item.id == card_id), None)
        if card is None:
            return None
        if card.kind != "query" or card.sql is None:
            raise ReportRejectedError("This card has no saved query to run.")
        return report, role, card

    def _pin(
        self,
        report: Report,
        card: CardInput,
        previous: ReportCard | None,
        catalog: _Lazy,
    ) -> ReportCard:
        """Take the card's SQL from its run in the audit log, never from the client."""
        if card.kind != "query" or card.run_id is None:
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
