"""How many model calls one question may make, by the step that makes them.

Every model call of a run goes through ``AttemptBudget.take``. A step with no
calls left is refused before the provider is asked, so a new step cannot add
calls without being given an allowance here, and the most a question can cost
is the sum of these allowances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.config import Settings


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


class BudgetExhaustedError(RuntimeError):
    def __init__(self, call: ModelCall) -> None:
        super().__init__(f"This run has no {call.value} model call left.")
        self.call = call


@dataclass
class AttemptBudget:
    allowed: dict[ModelCall, int]
    used: dict[ModelCall, int] = field(default_factory=dict)

    @classmethod
    def for_run(cls, settings: Settings, *, replay: bool) -> AttemptBudget:
        """A frozen replay is never sent to the model; a live run gets what settings allow."""
        if replay:
            return cls({})
        exploring = settings.exploration_enabled
        return cls(
            {
                ModelCall.GENERATION: 1,
                ModelCall.CONTEXT_EXPANSION: int(settings.context_expansion_enabled),
                ModelCall.LOOK_FIRST: int(exploring),
                ModelCall.EXPLORATION: settings.exploration_rounds if exploring else 0,
                ModelCall.READING_CHECK: int(settings.reading_check_enabled),
                ModelCall.REPAIR: int(settings.repair_enabled),
            }
        )

    def left(self, call: ModelCall) -> int:
        return self.allowed.get(call, 0) - self.used.get(call, 0)

    def take(self, call: ModelCall) -> None:
        """Count one call, or refuse it when the step has none left."""
        if self.left(call) <= 0:
            raise BudgetExhaustedError(call)
        self.used[call] = self.used.get(call, 0) + 1

    @property
    def limit(self) -> int:
        return sum(self.allowed.values())

    @property
    def spent(self) -> int:
        return sum(self.used.values())
