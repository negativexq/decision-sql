from __future__ import annotations

from collections import Counter

import pytest

from app.config import Settings
from app.decision.budget import AttemptBudget, BudgetExhaustedError, ModelCall
from app.observability.run_trace import RunTrace, TraceStageStatus
from app.sql.models import QueryPlan, SqlCandidate, UnmatchedValue
from tests.test_empty_result import (
    FIRST,
    FIXED,
    FailingAfterFirst,
    ScriptedSafety,
    _answer,
    _clarify,
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
