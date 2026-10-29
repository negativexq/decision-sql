from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.catalog.models import ColumnMetadata, SchemaCatalog, Sensitivity, TableMetadata
from app.observability.run_trace import RunTraceCollector
from app.operator.models import RunRecord, RuntimeOutcome
from app.operator.service import _saved_query_tables
from app.reports.filters import FilterChoice, FilterKind, ReportFilter
from app.reports.models import (
    CardDisplay,
    CardInput,
    CardLayout,
    ReportAccess,
    ReportCreate,
    ReportInput,
    ReportRole,
    Visibility,
)
from app.reports.service import ReportForbiddenError, ReportRejectedError, ReportService
from app.reports.store import InMemoryReportStore


def _record(run_id: str, *, connection: str = "governed", executed: bool = True) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        trace_id="tr",
        question=f"question of {run_id}",
        proposal_source="LIVE_MODEL",
        runtime_outcome=RuntimeOutcome.EXECUTED if executed else RuntimeOutcome.POLICY_REJECTED,
        proposed_sql="SELECT 1",
        executed_sql=f"SELECT total FROM orders -- {run_id}" if executed else None,
        created_at=datetime.now(UTC).isoformat(),
        trace=RunTraceCollector(run_id).finish("EXECUTED"),
        connection_id=connection,
    )


def _orders(**column: object) -> SchemaCatalog:
    total = ColumnMetadata(name="total", type="numeric", description="Order total.")
    return SchemaCatalog(
        tables=(
            TableMetadata(
                name="orders",
                description="Orders.",
                columns=(total.model_copy(update=column),),
            ),
        )
    )


class FakeOperator:
    def __init__(self, *records: RunRecord) -> None:
        self.records = {record.run_id: record for record in records}
        self.calls: list[dict[str, object]] = []
        self.catalog = _orders()
        self.rows: list[dict[str, object]] = []

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

    async def run(self, question: str, **kwargs: object) -> RunRecord:
        self.calls.append({"question": question, **kwargs})
        return _record("refreshed").model_copy(update={"rows": self.rows})


class FakeConnections:
    def connections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id="governed", queryable=True)]


def _card(card_id: str, run_id: str | None = None, **values: object) -> CardInput:
    return CardInput(
        id=card_id,
        kind=values.get("kind", "query"),
        run_id=run_id,
        layout=CardLayout(x=0, y=0, w=6, h=4),
        **{key: value for key, value in values.items() if key != "kind"},
    )


def _service(*records: RunRecord) -> tuple[ReportService, FakeOperator]:
    operator = FakeOperator(*records)
    return ReportService(InMemoryReportStore(), operator, FakeConnections()), operator  # type: ignore[arg-type]


def test_reports_need_a_connection_that_answers_questions() -> None:
    service, _ = _service()
    with pytest.raises(ReportRejectedError):
        service.create(ReportCreate(title="Sales", connection_id="nope"), "ayse")
    report = service.create(ReportCreate(title=" Sales ", connection_id="governed"), "ayse")
    assert report.title == "Sales" and service.list("ayse")[0].card_count == 0


def test_card_sql_comes_from_the_audit_log_never_from_the_client() -> None:
    service, _ = _service(_record("run_a"), _record("run_b"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    service.share(report.id, ReportAccess(editors=["Mehmet"]), "ayse")
    updated = service.update(
        report.id,
        ReportInput(
            title="Sales",
            cards=[
                _card("c1", "run_a", question="DROP TABLE orders", chart="pie"),
                _card("t1", kind="text", title="Notes", note="Revenue is net of refunds."),
                _card("h1", kind="heading", title="Revenue"),
            ],
        ),
        "mehmet",
    )
    assert updated is not None
    query, text, heading = updated.cards
    assert heading.kind == "heading" and heading.sql is None
    assert (
        query.sql == "SELECT total FROM orders -- run_a" and query.question == "question of run_a"
    )
    assert query.chart == "pie" and text.sql is None and text.note.startswith("Revenue")
    assert updated.updated_by == "mehmet"

    moved = _card("c1", "run_a").model_copy(update={"layout": CardLayout(x=6, y=2, w=6, h=5)})
    again = service.update(report.id, ReportInput(title="Sales", cards=[moved]), "ayse")
    assert again is not None and again.cards[0].sql == "SELECT total FROM orders -- run_a"
    assert again.cards[0].layout.x == 6


def test_cards_reject_runs_that_did_not_execute_or_belong_elsewhere() -> None:
    service, _ = _service(_record("failed", executed=False), _record("other", connection="conn_x"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    for run_id in ("failed", "other", "missing"):
        with pytest.raises(ReportRejectedError):
            service.update(report.id, ReportInput(title="S", cards=[_card("c", run_id)]), "ayse")
    with pytest.raises(ReportRejectedError, match="share an id"):
        service.update(
            report.id,
            ReportInput(title="S", cards=[_card("c", kind="text"), _card("c", kind="text")]),
            "ayse",
        )


@pytest.mark.asyncio
async def test_refresh_runs_the_pinned_sql_through_the_runtime() -> None:
    service, operator = _service(_record("run_a"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    service.update(report.id, ReportInput(title="S", cards=[_card("c1", "run_a")]), "ayse")
    refreshed = await service.refresh(report.id, "c1", "ayse")
    assert refreshed is not None and refreshed.changed_tables == []
    [call] = operator.calls
    assert call["saved_sql"] == "SELECT total FROM orders -- run_a" and call["source"] is None
    assert await service.refresh(report.id, "missing", "ayse") is None


@pytest.mark.asyncio
async def test_refresh_names_tables_described_differently_since_the_card_was_pinned() -> None:
    service, operator = _service(_record("run_a"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    service.update(report.id, ReportInput(title="S", cards=[_card("c1", "run_a")]), "ayse")
    pinned = service.get(report.id, "ayse")
    assert pinned is not None and set(pinned.cards[0].basis) == {"orders"}

    # Values and value hints change with the data; they do not change the meaning.
    operator.catalog = _orders(value_domain=("1", "2"), known_values=("1",))
    refreshed = await service.refresh(report.id, "c1", "ayse")
    assert refreshed is not None and refreshed.changed_tables == []

    for change in (
        {"description": "Order total in cents."},
        {"type": "integer"},
        {"sensitivity": Sensitivity.CONFIDENTIAL},
        {"name": "amount"},
    ):
        operator.catalog = _orders(**change)
        refreshed = await service.refresh(report.id, "c1", "ayse")
        assert refreshed is not None and refreshed.changed_tables == ["orders"], change

    operator.catalog = SchemaCatalog()
    refreshed = await service.refresh(report.id, "c1", "ayse")
    assert refreshed is not None and refreshed.changed_tables == ["orders"]

    # A reader who checked the card takes the tables as they are now; moving the card
    # afterwards keeps that basis.
    operator.catalog = _orders(description="Order total in cents.")
    assert await service.accept(report.id, "c1", "ayse") is not None
    service.update(report.id, ReportInput(title="S", cards=[_card("c1", "run_a")]), "ayse")
    refreshed = await service.refresh(report.id, "c1", "ayse")
    assert refreshed is not None and refreshed.changed_tables == []


@pytest.mark.asyncio
async def test_a_report_opens_only_for_its_owner_editors_and_viewers() -> None:
    service, operator = _service(_record("run_a"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    service.update(report.id, ReportInput(title="S", cards=[_card("c1", "run_a")]), "ayse")

    # Private by default: to anyone else the report does not exist.
    assert service.list("mehmet") == [] and service.get(report.id, "mehmet") is None
    assert await service.refresh(report.id, "c1", "mehmet") is None
    assert service.update(report.id, ReportInput(title="Mine"), "mehmet") is None
    assert not service.delete(report.id, "mehmet")

    shared = service.share(
        report.id, ReportAccess(editors=["Mehmet", "ayse"], viewers=["zeynep", "mehmet"]), "ayse"
    )
    assert shared is not None
    # The owner is never listed, and an editor is not also a viewer.
    assert shared.editors == ["mehmet"] and shared.viewers == ["zeynep"]
    view = service.get(report.id, "zeynep")
    assert view is not None and view.role is ReportRole.VIEWER

    # A viewer reads and refreshes, and the audit record names the viewer.
    assert await service.refresh(report.id, "c1", "zeynep") is not None
    assert operator.calls[-1]["actor"] == "zeynep"
    for change in (
        lambda: service.update(report.id, ReportInput(title="Hers"), "zeynep"),
        lambda: service.share(report.id, ReportAccess(), "zeynep"),
        lambda: service.delete(report.id, "zeynep"),
    ):
        with pytest.raises(ReportForbiddenError):
            change()
    with pytest.raises(ReportForbiddenError):
        await service.ask(report.id, "Revenue", "zeynep")
    with pytest.raises(ReportForbiddenError):
        await service.accept(report.id, "c1", "zeynep")

    # An editor changes the report but not who may open it, and cannot delete it.
    assert service.update(report.id, ReportInput(title="Sales 2"), "mehmet") is not None
    with pytest.raises(ReportForbiddenError, match="owner"):
        service.share(report.id, ReportAccess(), "mehmet")
    with pytest.raises(ReportForbiddenError):
        service.delete(report.id, "mehmet")

    service.share(report.id, ReportAccess(visibility=Visibility.WORKSPACE), "ayse")
    [summary] = service.list("someone")
    assert summary.role is ReportRole.VIEWER and summary.owner == "ayse"
    with pytest.raises(ValueError):
        ReportAccess(viewers=["not a name"])
    assert service.delete(report.id, "ayse") and service.list("ayse") == []


def test_saved_query_tables_skip_cte_names() -> None:
    sql = "WITH t AS (SELECT id FROM orders) SELECT c.name FROM customers c JOIN t ON t.id = c.id"
    assert _saved_query_tables(sql) == ("customers", "orders")
    assert _saved_query_tables("not sql at all (") == ()


@pytest.mark.asyncio
async def test_filters_narrow_the_pinned_sql_before_the_gates_see_it() -> None:
    service, operator = _service(_record("run_a"))
    operator.catalog = _orders()
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    total = ReportFilter(id="total", label="Total", table="orders", column="total", kind="values")
    with pytest.raises(ReportRejectedError, match="not a column"):
        service.update(
            report.id,
            ReportInput(title="S", filters=[total.model_copy(update={"column": "nope"})]),
            "ayse",
        )
    with pytest.raises(ReportRejectedError, match="not dates"):
        service.update(
            report.id,
            ReportInput(
                title="S", filters=[total.model_copy(update={"kind": FilterKind.DATE_RANGE})]
            ),
            "ayse",
        )
    service.update(
        report.id, ReportInput(title="S", cards=[_card("c1", "run_a")], filters=[total]), "ayse"
    )

    refreshed = await service.refresh(
        report.id, "c1", "ayse", {"total": FilterChoice(values=["10"]), "gone": FilterChoice()}
    )
    assert refreshed is not None and refreshed.applied_filters == ["total"]
    assert (
        operator.calls[-1]["saved_sql"]
        == "SELECT total FROM orders /* run_a */ WHERE orders.total IN ('10')"
    )
    # The saved card keeps its own SQL; the choice lasts for this refresh only.
    stored = service.get(report.id, "ayse")
    assert stored is not None and stored.cards[0].sql == "SELECT total FROM orders -- run_a"

    operator.rows = [{"value": "10"}, {"value": "20"}]
    options = await service.filter_options(report.id, "total", "ayse")
    assert options is not None and options.values == ["10", "20"] and not options.truncated
    assert operator.calls[-1]["saved_sql"] == (
        "SELECT DISTINCT orders.total AS value FROM orders "
        "WHERE NOT orders.total IS NULL ORDER BY 1 LIMIT 201"
    )
    columns = await service.filter_columns(report.id, "ayse")
    assert columns is not None and [(c.table, c.column) for c in columns] == [("orders", "total")]


def test_display_settings_are_checked_and_kept() -> None:
    service, _ = _service(_record("run_a"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    shown = CardDisplay(format="currency", currency="EUR", sort="value_desc", limit=5)
    updated = service.update(
        report.id, ReportInput(title="S", cards=[_card("c1", "run_a", display=shown)]), "ayse"
    )
    assert updated is not None and updated.cards[0].display == shown
    for bad in ({"currency": "euro"}, {"limit": 0}, {"decimals": 9}, {"format": "stars"}):
        with pytest.raises(ValueError):
            CardDisplay.model_validate(bad)
