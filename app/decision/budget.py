"""What one question may spend: model calls by step, time, tokens, and probe queries.

Every model call of a run goes through ``AttemptBudget.take``. A step with no
calls left is refused before the provider is asked, so a new step cannot add
calls without being given an allowance here, and the most a question can cost
is the sum of these allowances.

The run also has a deadline, a token limit, and a limit on the probe and reading
queries the model writes. Each is checked before the step that would spend it, and
a model call is cut off when the deadline passes. The first decision and the query
that answers are never refused: the limits stop optional steps, so a run past its
deadline still answers with what it has.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic

from app.config import Settings
from app.generation.decision_contract import MAX_PROBES


class ModelCall(StrEnum):
    # The first decision.
    GENERATION = "generation"
    # Again with the omitted tables the model named added to the context.
    CONTEXT_EXPANSION = "context_expansion"
    # A clarification or refusal returned before any probe is sent back to look first.
    LOOK_FIRST = "look_first"
    # One call per round of probes.
    EXPLORATION = "exploration"
    # One SQL per reading of a clarification.
    READING_CHECK = "reading_check"
    # The one repair round: after a fixable rejection, an empty result, an
    # out-of-range percentage, or an execution error.
    REPAIR = "repair"


class BudgetLimit(StrEnum):
    CALLS = "CALLS"
    DEADLINE = "DEADLINE"
    TOKENS = "TOKENS"
    PROBE_QUERIES = "PROBE_QUERIES"


class BudgetExhaustedError(RuntimeError):
    def __init__(self, limit: BudgetLimit, call: ModelCall | None = None) -> None:
        step = call.value if call is not None else "probe query"
        super().__init__(f"This run's {limit.value.lower()} budget does not allow a {step}.")
        self.limit = limit
        self.call = call

    @property
    def reason(self) -> str:
        """The trace reason of the step it stopped."""
        return f"BUDGET_{self.limit.value}"


@dataclass
class AttemptBudget:
    allowed: dict[ModelCall, int]
    used: dict[ModelCall, int] = field(default_factory=dict)
    deadline_seconds: float | None = None
    token_limit: int | None = None
    probe_query_limit: int | None = None
    tokens: int = 0
    probe_queries: int = 0
    # The first limit that stopped a step, if any.
    stopped: BudgetLimit | None = None
    started: float = field(default_factory=monotonic)

    @classmethod
    def for_run(cls, settings: Settings, *, replay: bool) -> AttemptBudget:
        """A frozen replay is never sent to the model; a live run gets what settings allow."""
        if replay:
            return cls({})
        exploring = settings.exploration_enabled
        rounds = settings.exploration_rounds if exploring else 0
        probe_queries = settings.run_probe_query_limit
        if probe_queries is None:
            probe_queries = MAX_PROBES * (rounds + int(settings.reading_check_enabled))
        return cls(
            {
                ModelCall.GENERATION: 1,
                ModelCall.CONTEXT_EXPANSION: int(settings.context_expansion_enabled),
                ModelCall.LOOK_FIRST: int(exploring),
                ModelCall.EXPLORATION: rounds,
                ModelCall.READING_CHECK: int(settings.reading_check_enabled),
                ModelCall.REPAIR: int(settings.repair_enabled),
            },
            deadline_seconds=settings.run_deadline_seconds,
            token_limit=settings.run_token_limit,
            probe_query_limit=probe_queries,
        )

    def left(self, call: ModelCall) -> int:
        return self.allowed.get(call, 0) - self.used.get(call, 0)

    def remaining(self) -> float | None:
        """Seconds left before the deadline; None when the run has none."""
        if self.deadline_seconds is None:
            return None
        return self.deadline_seconds - (monotonic() - self.started)

    def take(self, call: ModelCall) -> None:
        """Count one call, or refuse it when the step, the time, or the tokens are spent.

        The first decision is refused only for want of an allowance.
        """
        if self.left(call) <= 0:
            raise self.stop(BudgetLimit.CALLS, call)
        if call is not ModelCall.GENERATION:
            self._check_time_and_tokens(call)
        self.used[call] = self.used.get(call, 0) + 1

    def take_probe_query(self) -> None:
        """Count one probe or reading query, or refuse it."""
        if self.probe_query_limit is not None and self.probe_queries >= self.probe_query_limit:
            raise self.stop(BudgetLimit.PROBE_QUERIES)
        self._check_time_and_tokens(None)
        self.probe_queries += 1

    def spend_tokens(self, count: int) -> None:
        self.tokens += count

    def stop(self, limit: BudgetLimit, call: ModelCall | None = None) -> BudgetExhaustedError:
        """The refusal for ``limit``, remembered as the run's first stop."""
        if self.stopped is None:
            self.stopped = limit
        return BudgetExhaustedError(limit, call)

    def _check_time_and_tokens(self, call: ModelCall | None) -> None:
        remaining = self.remaining()
        if remaining is not None and remaining <= 0:
            raise self.stop(BudgetLimit.DEADLINE, call)
        if self.token_limit is not None and self.tokens >= self.token_limit:
            raise self.stop(BudgetLimit.TOKENS, call)

    @property
    def limit(self) -> int:
        return sum(self.allowed.values())

    @property
    def spent(self) -> int:
        return sum(self.used.values())

    def summary(self) -> dict[str, object]:
        """What the run spent against its limits, as flat trace metadata."""
        summary: dict[str, object] = {
            "model_calls": self.spent,
            "model_call_limit": self.limit,
            "tokens": self.tokens,
            "probe_queries": self.probe_queries,
            "elapsed_ms": round((monotonic() - self.started) * 1000),
            "budget_stopped": self.stopped.value if self.stopped is not None else "",
        }
        if self.token_limit is not None:
            summary["token_limit"] = self.token_limit
        if self.probe_query_limit is not None:
            summary["probe_query_limit"] = self.probe_query_limit
        if self.deadline_seconds is not None:
            summary["deadline_ms"] = round(self.deadline_seconds * 1000)
        return summary
