from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from app.config import Settings
from app.decision.budget import AttemptBudget, BudgetExhaustedError, BudgetLimit, ModelCall
from app.observability.run_trace import RunTrace, TraceStageStatus
from app.sql.models import QueryPlan, SqlCandidate, UnmatchedValue
from tests.test_empty_result import (
    FIRST,
    FIXED,
    PROBE,
    ExploringSafety,
    FailingAfterFirst,
    ScriptedSafety,
    _answer,
    _clarify,
    _explore,
    _run,
)
from tests.test_production_decision import FakeDecisionProvider


def _settings(**values: object) -> Settings:
    return Settings(_env_file=None, reader_role="reader", **values)  # type: ignore[arg-type]


def test_a_run_may_make_at_most_the_sum_of_its_allowances() -> None:
    budget = AttemptBudget.for_run(_settings(), replay=False)

    # generation, expansion, look first, two probe rounds, readings, repair
    assert budget.limit == 7
    assert AttemptBudget.for_run(_settings(), replay=True).limit == 0
    narrow = AttemptBudget.for_run(
        _settings(exploration_enabled=False, repair_enabled=False, reading_check_enabled=False),
        replay=False,
    )
    assert narrow.left(ModelCall.LOOK_FIRST) == 0 and narrow.left(ModelCall.REPAIR) == 0
    assert narrow.limit == 2


def test_a_step_with_no_calls_left_is_refused() -> None:
    budget = AttemptBudget.for_run(_settings(), replay=False)
    budget.take(ModelCall.REPAIR)

    with pytest.raises(BudgetExhaustedError):
        budget.take(ModelCall.REPAIR)
    assert budget.spent == 1 and budget.left(ModelCall.REPAIR) == 0


def _calls(trace: RunTrace) -> tuple[object, object]:
    response = {stage.name: stage for stage in trace.stages}["response"]
    return response.metadata["model_calls"], response.metadata["model_call_limit"]


def _recorded_once(trace: RunTrace) -> bool:
    counts = Counter(stage.name for stage in trace.stages)
    return all(count == 1 for count in counts.values()) and trace.stages[-1].name == "response"


@pytest.mark.asyncio
async def test_the_response_says_how_many_model_calls_the_run_made() -> None:
    provider = FakeDecisionProvider(_answer(FIRST), _answer(FIXED))
    result = await _run(provider, ScriptedSafety({FIXED: [{"n": 4}]}))

    assert result.executed_sql == FIXED and provider.calls == 2
    assert _calls(result.trace) == (2, 7)
    assert _recorded_once(result.trace)


@pytest.mark.asyncio
async def test_a_question_back_counts_every_call_it_took() -> None:
    provider = FakeDecisionProvider(_clarify(), _clarify())
    result = await _run(provider, ScriptedSafety({}), reading_check_enabled=False)

    assert _calls(result.trace) == (provider.calls, 6)
    assert _recorded_once(result.trace)


class GuessedValueSafety(ScriptedSafety):
    """The first plan filters on a value the column does not store, so it is sent back."""

    def plan(self, candidate: SqlCandidate) -> QueryPlan:
        plan = super().plan(candidate)
        if len(self.planned) > 1:
            return plan
        guessed = UnmatchedValue(
            table="products", column="category", literal="office", shared=False
        )
        return plan.model_copy(update={"unmatched_values": (guessed,)})


@pytest.mark.asyncio
async def test_a_failed_repair_still_spends_the_repair_round() -> None:
    provider = FailingAfterFirst(_answer(FIRST))
    result = await _run(provider, GuessedValueSafety({}))

    # The repair call failed; the empty result that follows does not get a second one.
    assert provider.calls == 2
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["repair"].status is TraceStageStatus.FAILED
    assert stages["result_check"].reason == "EMPTY_RESULT"
    assert result.executed_sql == FIRST and result.row_count == 0
    assert _calls(result.trace) == (2, 7)
    assert _recorded_once(result.trace)


def test_time_and_tokens_stop_every_step_but_the_first_decision() -> None:
    late = AttemptBudget({ModelCall.GENERATION: 1, ModelCall.REPAIR: 1}, deadline_seconds=0.0)
    late.take(ModelCall.GENERATION)
    with pytest.raises(BudgetExhaustedError) as refused:
        late.take(ModelCall.REPAIR)
    assert refused.value.limit is BudgetLimit.DEADLINE and late.stopped is BudgetLimit.DEADLINE

    costly = AttemptBudget({ModelCall.REPAIR: 1}, token_limit=100)
    costly.spend_tokens(100)
    with pytest.raises(BudgetExhaustedError, match="tokens"):
        costly.take(ModelCall.REPAIR)

    probing = AttemptBudget({}, probe_query_limit=1)
    probing.take_probe_query()
    with pytest.raises(BudgetExhaustedError) as refused:
        probing.take_probe_query()
    assert refused.value.reason == "BUDGET_PROBE_QUERIES"


def test_by_default_the_limits_sit_above_every_probe_a_run_can_ask_for() -> None:
    budget = AttemptBudget.for_run(_settings(), replay=False)
    # Three probes in each of two rounds, and three reading queries.
    assert budget.probe_query_limit == 9
    assert budget.deadline_seconds == 120.0 and budget.token_limit == 150_000
    assert AttemptBudget.for_run(_settings(), replay=True).deadline_seconds is None


class SlowAfterFirst(FakeDecisionProvider):
    async def propose_decision(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.calls >= 1:
            self.calls += 1
            await asyncio.sleep(5)
        return await super().propose_decision(*args, **kwargs)


@pytest.mark.asyncio
async def test_a_call_past_the_deadline_is_cut_off_and_the_run_still_answers() -> None:
    provider = SlowAfterFirst(_answer(FIRST))
    result = await asyncio.wait_for(
        _run(provider, GuessedValueSafety({}), run_deadline_seconds=0.5), timeout=3
    )

    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["repair"].reason == "BUDGET_DEADLINE"
    assert result.executed_sql == FIRST
    response = stages["response"].metadata
    assert response["budget_stopped"] == "DEADLINE" and int(str(response["elapsed_ms"])) < 3000
    assert _recorded_once(result.trace)


class CostlyFirst(FakeDecisionProvider):
    async def propose_decision(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        proposal = await super().propose_decision(*args, **kwargs)
        return proposal.model_copy(update={"prompt_tokens": 2_000, "completion_tokens": 100})


@pytest.mark.asyncio
async def test_a_run_over_its_token_limit_makes_no_further_call() -> None:
    provider = CostlyFirst(_answer(FIRST), _answer(FIXED))
    result = await _run(provider, GuessedValueSafety({}), run_token_limit=1_000)

    assert provider.calls == 1 and result.executed_sql == FIRST
    stages = {stage.name: stage for stage in result.trace.stages}
    assert stages["repair"].reason == "BUDGET_TOKENS"
    assert stages["response"].metadata["tokens"] == 2_100


@pytest.mark.asyncio
async def test_probes_past_the_limit_are_not_run_and_the_model_is_told() -> None:
    other = "SELECT COUNT(*) AS n FROM products"
    provider = FakeDecisionProvider(_explore(PROBE, other), _answer(FIXED))
    safety = ExploringSafety({PROBE: [{"category": "Office", "n": 4}], FIXED: [{"n": 4}]})
    result = await _run(provider, safety, run_probe_query_limit=1)

    assert safety.explored == [PROBE] and result.rows == [{"n": 4}]
    seen = provider.explorations[1]
    assert seen is not None
    assert [item.reason for item in seen.results] == [None, "BUDGET_PROBE_QUERIES"]
    response = {stage.name: stage for stage in result.trace.stages}["response"].metadata
    assert response["probe_queries"] == 1 and response["budget_stopped"] == "PROBE_QUERIES"
