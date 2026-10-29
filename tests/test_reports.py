from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.observability.run_trace import RunTraceCollector
from app.operator.models import RunRecord, RuntimeOutcome
from app.operator.service import _saved_query_tables
from app.reports.models import CardInput, CardLayout, ReportCreate, ReportInput
from app.reports.service import ReportRejectedError, ReportService
from app.reports.store import InMemoryReportStore


def _record(run_id: str, *, connection: str = "governed", executed: bool = True) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        trace_id="tr",
        question=f"question of {run_id}",
        proposal_source="LIVE_MODEL",
        runtime_outcome=RuntimeOutcome.EXECUTED if executed else RuntimeOutcome.POLICY_REJECTED,
        proposed_sql="SELECT 1",
        executed_sql=f"SELECT {run_id}" if executed else None,
        created_at=datetime.now(UTC).isoformat(),
        trace=RunTraceCollector(run_id).finish("EXECUTED"),
        connection_id=connection,
    )


class FakeOperator:
    def __init__(self, *records: RunRecord) -> None:
        self.records = {record.run_id: record for record in records}
        self.calls: list[dict[str, object]] = []

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

    async def run(self, question: str, **kwargs: object) -> RunRecord:
        self.calls.append({"question": question, **kwargs})
        return _record("refreshed")


class FakeConnections:
    def connections(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(id="governed", queryable=True)]


def _card(card_id: str, run_id: str | None = None, **values: object) -> CardInput:
    return CardInput(
        id=card_id,
        kind="query" if run_id or values.get("kind") != "text" else "text",
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
    assert report.title == "Sales" and service.list()[0].card_count == 0


def test_card_sql_comes_from_the_audit_log_never_from_the_client() -> None:
    service, _ = _service(_record("run_a"), _record("run_b"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    updated = service.update(
        report.id,
        ReportInput(
            title="Sales",
            cards=[
                _card("c1", "run_a", question="DROP TABLE orders", chart="pie"),
                _card("t1", kind="text", title="Notes", note="Revenue is net of refunds."),
            ],
        ),
        "mehmet",
    )
    assert updated is not None
    query, text = updated.cards
    assert query.sql == "SELECT run_a" and query.question == "question of run_a"
    assert query.chart == "pie" and text.sql is None and text.note.startswith("Revenue")
    assert updated.updated_by == "mehmet"

    moved = _card("c1", "run_a").model_copy(update={"layout": CardLayout(x=6, y=2, w=6, h=5)})
    again = service.update(report.id, ReportInput(title="Sales", cards=[moved]), "ayse")
    assert again is not None and again.cards[0].sql == "SELECT run_a"
    assert again.cards[0].layout.x == 6


def test_cards_reject_runs_that_did_not_execute_or_belong_elsewhere() -> None:
    service, _ = _service(_record("failed", executed=False), _record("other", connection="conn_x"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    for run_id in ("failed", "other", "missing"):
        with pytest.raises(ReportRejectedError):
            service.update(report.id, ReportInput(title="S", cards=[_card("c", run_id)]), "a")
    with pytest.raises(ReportRejectedError, match="share an id"):
        service.update(
            report.id,
            ReportInput(title="S", cards=[_card("c", kind="text"), _card("c", kind="text")]),
            "a",
        )


@pytest.mark.asyncio
async def test_refresh_runs_the_pinned_sql_through_the_runtime() -> None:
    service, operator = _service(_record("run_a"))
    report = service.create(ReportCreate(title="Sales", connection_id="governed"), "ayse")
    service.update(report.id, ReportInput(title="S", cards=[_card("c1", "run_a")]), "ayse")
    record = await service.refresh(report.id, "c1", "ayse")
    assert record is not None
    [call] = operator.calls
    assert call["saved_sql"] == "SELECT run_a" and call["source"] is None
    assert await service.refresh(report.id, "missing", "ayse") is None


def test_saved_query_tables_skip_cte_names() -> None:
    sql = "WITH t AS (SELECT id FROM orders) SELECT c.name FROM customers c JOIN t ON t.id = c.id"
    assert _saved_query_tables(sql) == ("customers", "orders")
    assert _saved_query_tables("not sql at all (") == ()
