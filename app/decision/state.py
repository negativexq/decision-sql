"""What one question carries from stage to stage of the decision service."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.decision.budget import AttemptBudget
from app.decision.exploration import Exploration
from app.decision.models import AlternativeReading, ProposalSource
from app.generation.decision_contract import ProductionDecision
from app.generation.provider import ProductionDecisionProposal
from app.governance.context import GovernedContext
from app.observability.run_trace import RunTraceCollector
from app.sql.authority import ExecutionAuthority
from app.sql.models import QueryExecution, QueryPlan, SqlExecutionError, SqlPlanFailure


@dataclass
class DecisionState:
    question: str
    collector: RunTraceCollector
    proposal_source: ProposalSource
    # A frozen replay stays exactly as recorded: no model call, repair, or result check.
    replay: bool
    budget: AttemptBudget
    model_context: GovernedContext
    model_context_hash: str
    # The context as the model reads it.
    schema_text: str
    # The proposal the run's provider and model are taken from.
    proposal: ProductionDecisionProposal
    # The decision in force: the proposal's, or one a later stage put in its place.
    decision: ProductionDecision
    # What the model learned from its probes; goes with every later model call.
    exploration: Exploration | None = None
    # Set when a question or refusal returned without probes was sent back once.
    looked_first: dict[str, object] = field(default_factory=dict)
    # Set when the answer assumed one of several readings that give different results.
    assumption: str | None = None
    alternatives: tuple[AlternativeReading, ...] = ()
    # The SQL the repair round replaced.
    repaired_from_sql: str | None = None

    @property
    def authority(self) -> ExecutionAuthority:
        return ExecutionAuthority.from_governed_context(self.model_context)


@dataclass(frozen=True)
class Attempt:
    """A decision, its plan, and its execution when the plan passed."""

    decision: ProductionDecision
    planned: QueryPlan | SqlPlanFailure | None
    execution: QueryExecution | SqlExecutionError | None
    sql_changes: tuple[str, ...]
