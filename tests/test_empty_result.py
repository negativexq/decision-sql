from __future__ import annotations

from uuid import uuid4

import pytest
from sqlglot import exp

from app.catalog.default import build_default_catalog
from app.config import Settings
from app.db.models import Base
from app.decision.service import DecisionSqlApplication, _looks_empty
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionType,
    ProductionDecision,
)
from app.models.domain import FailureStage
from app.observability.run_trace import RunTraceCollector, TraceStageStatus
from app.retrieval.context import SchemaContextResolver
from app.sql.models import (
    ExplainEstimate,
    PolicyCode,
    PolicyRejection,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
    UnmatchedValue,
)
from app.sql.parser import SQLParser
from app.sql.values import ValueCheck
from tests.test_production_decision import FakeDecisionProvider


def _answer(sql: str) -> ProductionDecision:
    return ProductionDecision(case_id=PRODUCTION_DECISION_CASE_ID, decision="ANSWER", sql=sql)


class ScriptedSafety:
    """Plans every candidate, returns scripted rows per SQL, and a scripted value probe."""

    def __init__(
        self,
        rows: dict[str, list[dict[str, object]]],
        probe: ValueCheck | None = None,
    ) -> None:
        self.rows = rows
        self.probe = probe
        self.planned: list[str] = []
        self.probed: list[str] = []

    def plan(self, candidate: SqlCandidate) -> QueryPlan:
        self.planned.append(candidate.sql)
        return QueryPlan(
            plan_id=uuid4(),
            normalized_sql=candidate.sql,
            statement_type="Select",
            estimate=ExplainEstimate(total_cost=1, plan_rows=1, top_level_node_type="Result"),
        )

    def execute(self, plan: QueryPlan) -> QueryExecution:
        rows = self.rows.get(plan.normalized_sql, [])
        return QueryExecution(
            plan_id=plan.plan_id,
            columns=list(rows[0]) if rows else ["n"],
            rows=rows,
            row_count=len(rows),
            latency_ms=1.0,
        )

    def probe_values(self, sql: str) -> ValueCheck | None:
        self.probed.append(sql)
        return self.probe


async def _run(provider: FakeDecisionProvider, safety: ScriptedSafety, **settings: object):
    config = Settings(_env_file=None, reader_role="reader", **settings)  # type: ignore[arg-type]
    collector = RunTraceCollector("run_empty")
    collector.record_stage("request", TraceStageStatus.PASS)
    return await DecisionSqlApplication(
        SchemaContextResolver(build_default_catalog(Base.metadata)),
        provider,  # type: ignore[arg-type]
        safety,  # type: ignore[arg-type]
        config,
    ).run("How many products are in the office category?", collector)


FIRST = "SELECT COUNT(*) AS n FROM products WHERE category = 'office'"
FIXED = "SELECT COUNT(*) AS n FROM products WHERE category = 'Office'"


def _rewritten() -> ValueCheck:
    return ValueCheck(
        SQLParser().parse(FIXED).expression,
        (
            "The filter on products.category compared it with 'office'; "
            "the column stores that value as 'Office'.",
        ),
        (),
        1,
    )


@pytest.mark.asyncio
async def test_a_result_with_rows_is_not_checked() -> None:
    safety = ScriptedSafety({FIRST: [{"n": 4}]})
    result = await _run(FakeDecisionProvider(_answer(FIRST)), safety)

    assert result.rows == [{"n": 4}] and safety.probed == []
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["result_check"].status is TraceStageStatus.SKIPPED


@pytest.mark.asyncio
async def test_a_stored_spelling_found_by_the_probe_runs_again_without_the_model() -> None:
    provider = FakeDecisionProvider(_answer(FIRST))
    safety = ScriptedSafety({FIRST: [{"n": 0}], FIXED: [{"n": 4}]}, probe=_rewritten())
    result = await _run(provider, safety)

    assert provider.calls == 1
    assert safety.planned == [FIRST, FIXED]
    assert result.rows == [{"n": 4}]
    assert result.proposed_sql == FIRST and result.repaired_from_sql is None
    assert result.executed_sql == FIXED
    assert result.sql_changes[0].endswith("stores that value as 'Office'.")
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["result_check"].reason == "VALUE_REWRITTEN"


@pytest.mark.asyncio
async def test_an_unexplained_empty_result_gets_the_repair_round() -> None:
    second = "SELECT COUNT(*) AS n FROM products WHERE category ILIKE '%office%'"
    provider = FakeDecisionProvider(_answer(FIRST), _answer(second))
    missing = UnmatchedValue(table="products", column="category", literal="office", shared=False)
    safety = ScriptedSafety(
        {FIRST: [], second: [{"n": 4}]},
        probe=ValueCheck(exp.select("1"), (), (missing,), 1),
    )
    result = await _run(provider, safety)

    assert provider.calls == 2
    [feedback] = provider.feedback
    assert feedback.stage == "result_check" and feedback.code == "EMPTY_RESULT"
    assert "products.category = 'office'" in feedback.message
    assert result.repaired_from_sql == FIRST and result.proposed_sql == second
    assert result.rows == [{"n": 4}]
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["result_check"].reason == "VALUE_NOT_STORED"
    assert stages["repair"].status is TraceStageStatus.PASS


@pytest.mark.asyncio
async def test_the_model_may_keep_the_empty_result_or_ask_instead() -> None:
    kept = await _run(
        FakeDecisionProvider(_answer(FIRST), _answer(FIRST)), ScriptedSafety({FIRST: []})
    )
    assert kept.runtime_outcome == "EXECUTED" and kept.row_count == 0

    asked = await _run(
        FakeDecisionProvider(
            _answer(FIRST),
            ProductionDecision(
                case_id=PRODUCTION_DECISION_CASE_ID,
                decision="NEEDS_CLARIFICATION",
                reason_code="AMBIGUOUS_SEMANTICS",
                clarification_question="Which category?",
                interpretations=["Office supplies", "Office furniture"],
            ),
        ),
        ScriptedSafety({FIRST: []}),
    )
    assert asked.model_decision is DecisionType.NEEDS_CLARIFICATION
    assert asked.runtime_outcome == "NOT_ENTERED"
    assert asked.repaired_from_sql == FIRST


@pytest.mark.asyncio
async def test_no_second_repair_and_no_check_when_turned_off() -> None:
    # The run already used its repair round on a policy rejection.
    provider = FakeDecisionProvider(_answer("SELECT name FROM external_directory"), _answer(FIRST))

    class RejectFirst(ScriptedSafety):
        def plan(self, candidate: SqlCandidate):  # type: ignore[override]
            if "external_directory" in candidate.sql:
                return SqlPlanFailure(
                    status=SqlSafetyStatus.POLICY_REJECTION,
                    failure_stage=FailureStage.POLICY_REJECTION,
                    rejection=PolicyRejection(code=PolicyCode.UNKNOWN_TABLE, message="Unknown."),
                )
            return super().plan(candidate)

    safety = RejectFirst({FIRST: []})
    result = await _run(provider, safety)
    assert provider.calls == 2 and result.row_count == 0
    assert {stage.name: stage for stage in result.trace.stages}["result_check"].reason == (
        "EMPTY_RESULT"
    )

    off = ScriptedSafety({FIRST: []})
    result = await _run(FakeDecisionProvider(_answer(FIRST)), off, result_check_enabled=False)
    assert off.probed == [] and result.row_count == 0


@pytest.mark.parametrize(
    ("rows", "empty"),
    (
        ([], True),
        ([{"n": 0}], True),
        ([{"total": None, "n": 0.0}], True),
        ([{"exists": False}], True),
        ([{"n": 3}], False),
        ([{"name": ""}], False),
        ([{"n": 0}, {"n": 0}], False),
    ),
)
def test_what_counts_as_empty(rows: list[dict[str, object]], empty: bool) -> None:
    execution = QueryExecution(plan_id=uuid4(), rows=rows, row_count=len(rows), latency_ms=1.0)
    assert _looks_empty(execution) is empty


SHARE = "SELECT 100.0 * SUM(id) / COUNT(*) AS share_percent FROM products"


@pytest.mark.asyncio
async def test_a_percentage_outside_0_to_100_gets_the_repair_round() -> None:
    second = "SELECT 100.0 * COUNT(*) / COUNT(*) AS share_percent FROM products"
    provider = FakeDecisionProvider(_answer(SHARE), _answer(second))
    safety = ScriptedSafety({SHARE: [{"share_percent": 988.4}], second: [{"share_percent": 94.0}]})
    result = await _run(provider, safety)

    [feedback] = provider.feedback
    assert feedback.code == "OUT_OF_RANGE" and "share_percent" in feedback.message
    assert "988" not in feedback.message
    assert result.repaired_from_sql == SHARE and result.rows == [{"share_percent": 94.0}]
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["result_check"].reason == "OUT_OF_RANGE"
    assert safety.probed == []


@pytest.mark.asyncio
async def test_rows_that_tie_for_the_extreme_are_flagged() -> None:
    tied = "SELECT name FROM products WHERE price = (SELECT MAX(price) FROM products)"
    safety = ScriptedSafety({tied: [{"name": "a"}, {"name": "b"}]})
    result = await _run(FakeDecisionProvider(_answer(tied)), safety)

    stage = {stage.name: stage for stage in result.trace.stages}["result_check"]
    assert stage.reason == "TIED_ROWS" and stage.metadata["tied"] == 2


@pytest.mark.asyncio
async def test_a_fixable_execution_error_gets_the_repair_round() -> None:
    cast = "SELECT MAX(number::numeric) AS n FROM products"
    fixed = "SELECT MAX(number) AS n FROM products"

    class FailCast(ScriptedSafety):
        def execute(self, plan: QueryPlan):  # type: ignore[override]
            if plan.normalized_sql == cast:
                return SqlExecutionError(
                    plan_id=plan.plan_id, error="hidden", error_class="INVALID_TEXT_CONVERSION"
                )
            return super().execute(plan)

    provider = FakeDecisionProvider(_answer(cast), _answer(fixed))
    result = await _run(provider, FailCast({fixed: [{"n": 7}]}))

    [feedback] = provider.feedback
    assert feedback.stage == "execution" and feedback.code == "INVALID_TEXT_CONVERSION"
    assert "hidden" not in feedback.message
    assert result.repaired_from_sql == cast and result.rows == [{"n": 7}]
    assert result.runtime_outcome == "EXECUTED"

    # An unnamed failure is final.
    class FailHard(ScriptedSafety):
        def execute(self, plan: QueryPlan):  # type: ignore[override]
            return SqlExecutionError(plan_id=plan.plan_id, error="hidden")

    provider = FakeDecisionProvider(_answer(cast))
    result = await _run(provider, FailHard({}))
    assert provider.calls == 1 and result.runtime_outcome == "EXECUTION_ERROR"


def _explore(*probes: str) -> ProductionDecision:
    return ProductionDecision(
        case_id=PRODUCTION_DECISION_CASE_ID,
        decision="EXPLORE",
        explanation="Check how categories are spelled.",
        probes=list(probes),
    )


class ExploringSafety(ScriptedSafety):
    def __init__(self, rows: dict[str, list[dict[str, object]]]) -> None:
        super().__init__(rows)
        self.explored: list[str] = []
        self.catalog = build_default_catalog(Base.metadata)

    def explore(self, candidate: SqlCandidate):  # type: ignore[no-untyped-def]
        self.explored.append(candidate.sql)
        if "secret_table" in candidate.sql:
            return (
                SqlPlanFailure(
                    status=SqlSafetyStatus.POLICY_REJECTION,
                    failure_stage=FailureStage.POLICY_REJECTION,
                    rejection=PolicyRejection(code=PolicyCode.UNKNOWN_TABLE, message="Unknown."),
                ),
                None,
            )
        plan = ScriptedSafety.plan(self, candidate)
        self.planned.remove(candidate.sql)
        return plan, ScriptedSafety.execute(self, plan)


PROBE = "SELECT category, COUNT(*) AS n FROM products GROUP BY category"


@pytest.mark.asyncio
async def test_the_model_looks_at_the_data_before_it_answers() -> None:
    provider = FakeDecisionProvider(_explore(PROBE, "SELECT x FROM secret_table"), _answer(FIXED))
    safety = ExploringSafety({PROBE: [{"category": "Office", "n": 4}], FIXED: [{"n": 4}]})
    result = await _run(provider, safety)

    assert safety.explored == [PROBE, "SELECT x FROM secret_table"]
    assert safety.planned == [FIXED] and result.rows == [{"n": 4}]
    first, second = provider.explorations
    assert first is not None and first.open and not first.results
    assert second is not None and second.open  # two rounds by default: it may look once more
    ok, rejected = second.results
    # The connection shares no values, so the model hears counts only.
    assert ok.row_count == 1 and ok.rows is None
    assert rejected.status == "REJECTED" and rejected.reason == "UNKNOWN_TABLE"
    stage = {stage.name: stage for stage in result.trace.stages}["exploration"]
    assert stage.status is TraceStageStatus.PASS and stage.metadata["rounds"] == 1
    assert stage.metadata["probe_status"] == ["ROWS", "REJECTED"]
    assert stage.metadata["probe_rows_shared"] == ["False", "False"]


async def _run_rounds(provider: FakeDecisionProvider, safety: ScriptedSafety, rounds: int):
    return await _run(provider, safety, exploration_rounds=rounds)


@pytest.mark.asyncio
async def test_exploration_is_bounded_and_a_model_still_exploring_fails_the_run() -> None:
    provider = FakeDecisionProvider(_explore(PROBE), _explore(PROBE), _explore(PROBE))
    safety = ExploringSafety({PROBE: [{"category": "Office", "n": 4}]})
    result = await _run_rounds(provider, safety, 2)

    assert provider.calls == 3 and len(safety.explored) == 2
    assert result.runtime_outcome == "GENERATION_ERROR"
    assert result.runtime_reason == "EXPLORATION_FAILURE"
    stage = {stage.name: stage for stage in result.trace.stages}["exploration"]
    assert stage.reason == "EXPLORATION_NOT_CLOSED"


@pytest.mark.asyncio
async def test_a_question_answered_directly_skips_exploration_and_off_means_closed() -> None:
    provider = FakeDecisionProvider(_answer(FIXED))
    result = await _run(provider, ScriptedSafety({FIXED: [{"n": 4}]}))
    stage = {stage.name: stage for stage in result.trace.stages}["exploration"]
    assert stage.status is TraceStageStatus.SKIPPED and stage.reason == "NOT_REQUESTED"

    provider = FakeDecisionProvider(_answer(FIXED))
    await _run(provider, ScriptedSafety({FIXED: [{"n": 4}]}), exploration_enabled=False)
    assert provider.explorations == [None]
