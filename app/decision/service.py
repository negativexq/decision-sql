from __future__ import annotations

import asyncio
from time import perf_counter

from app.config import Settings
from app.decision.models import DecisionApplicationResult, ProposalSource, RuntimeOutcome
from app.generation.decision_contract import (
    PRODUCTION_DECISION_CASE_ID,
    DecisionType,
    ProductionDecision,
)
from app.generation.provider import (
    EarlierAnswer,
    LLMProvider,
    MalformedProviderResponse,
    ProductionDecisionProposal,
    RuntimeRejectionFeedback,
)
from app.governance.context import (
    GovernedContext,
    governed_context_from_schema_context,
    governed_context_hash,
    serialize_governed_context,
)
from app.observability.run_trace import TRACE_STAGE_ORDER, RunTraceCollector, TraceStageStatus
from app.retrieval.context import (
    SchemaContextMode,
    SchemaContextResolver,
    SchemaResolutionError,
)
from app.sql.authority import ExecutionAuthority
from app.sql.models import (
    CandidateSource,
    PolicyCode,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
)
from app.sql.service import SqlSafetyService


class DecisionSqlApplication:
    """Canonical product request lifecycle: decision first, runtime second."""

    def __init__(
        self,
        resolver: SchemaContextResolver,
        provider: LLMProvider,
        safety_service: SqlSafetyService,
        settings: Settings,
        earlier: EarlierAnswer | None = None,
    ) -> None:
        self.resolver = resolver
        self.provider = provider
        self.safety_service = safety_service
        self.settings = settings
        # A follow-up's earlier answer goes with every model call of this run.
        self.earlier = earlier

    async def run(
        self,
        question: str,
        collector: RunTraceCollector,
        *,
        proposal_source: ProposalSource = ProposalSource.LIVE_MODEL,
        replay_decision: ProductionDecision | None = None,
    ) -> DecisionApplicationResult:
        context_started = perf_counter()
        try:
            context = self.resolver.resolve(question, mode=SchemaContextMode.RETRIEVED)
        except SchemaResolutionError as error:
            collector.record_stage(
                "context",
                TraceStageStatus.FAILED,
                reason="CONTEXT_RESOLUTION_FAILURE",
                metadata={"message": str(error)},
                duration_ms=(perf_counter() - context_started) * 1000,
            )
            collector.skip_unrecorded(
                tuple(
                    name
                    for name in TRACE_STAGE_ORDER
                    if name not in {"request", "context", "response"}
                ),
                "CONTEXT_RESOLUTION_FAILURE",
            )
            collector.record_stage(
                "response", TraceStageStatus.PASS, reason="CONTEXT_RESOLUTION_FAILURE"
            )
            trace = collector.finish("NOT_ENTERED")
            return DecisionApplicationResult(
                proposal_source=proposal_source,
                runtime_outcome=RuntimeOutcome.NOT_ENTERED,
                runtime_reason="CONTEXT_RESOLUTION_FAILURE",
                trace=trace,
            )
        model_context = governed_context_from_schema_context(context)
        model_context_hash = governed_context_hash(model_context)
        collector.record_stage(
            "context",
            TraceStageStatus.PASS,
            metadata={
                "context_hash": model_context_hash,
                "entity_count": len(model_context.schema_catalog),
                "attribute_count": len(model_context.attributes),
                "relationship_count": len(model_context.authorized_relationships),
                "metric_count": len(model_context.metrics),
                "business_rule_count": len(model_context.business_rules),
                "temporal_rule_count": len(model_context.temporal_rules),
                "policy_mode": model_context.policy.mode,
            },
            duration_ms=(perf_counter() - context_started) * 1000,
        )
        schema_text = serialize_governed_context(model_context)

        if replay_decision is not None:
            collector.record_stage(
                "generation", TraceStageStatus.SKIPPED, reason=proposal_source.value
            )
            collector.record_stage(
                "proposal_replay",
                TraceStageStatus.PASS,
                reason="SERVER_BOUND_FROZEN_PROPOSAL",
                metadata={"proposal_source": proposal_source.value},
            )
            proposal = ProductionDecisionProposal(
                decision=replay_decision,
                provider="replay",
                model="frozen-scenario",
            )
        else:
            started = perf_counter()
            try:
                proposal = await self.provider.propose_decision(
                    question, schema_text, earlier=self.earlier
                )
            except MalformedProviderResponse:
                collector.record_stage(
                    "generation", TraceStageStatus.PASS, reason="TRANSPORT_RESPONSE_RECEIVED"
                )
                collector.record_stage(
                    "decision_admission", TraceStageStatus.FAILED, reason="INVALID_TYPED_DECISION"
                )
                collector.skip_unrecorded(
                    tuple(
                        name
                        for name in TRACE_STAGE_ORDER
                        if name
                        not in {
                            "request",
                            "context",
                            "generation",
                            "decision_admission",
                            "response",
                        }
                    ),
                    "PROVIDER_FAILURE",
                )
                collector.record_stage(
                    "response", TraceStageStatus.FAILED, reason="INVALID_TYPED_DECISION"
                )
                trace = collector.finish("GENERATION_ERROR")
                return DecisionApplicationResult(
                    proposal_source=proposal_source,
                    model_context=model_context,
                    model_context_hash=model_context_hash,
                    runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
                    runtime_reason="INVALID_TYPED_DECISION",
                    trace=trace,
                )
            except Exception as error:
                collector.record_stage(
                    "generation",
                    TraceStageStatus.FAILED,
                    reason="PROVIDER_FAILURE",
                    metadata={"error_type": type(error).__name__},
                    duration_ms=(perf_counter() - started) * 1000,
                )
                collector.skip_unrecorded(
                    tuple(
                        name
                        for name in TRACE_STAGE_ORDER
                        if name not in {"request", "context", "generation", "response"}
                    ),
                    "PROVIDER_FAILURE",
                )
                collector.record_stage(
                    "response", TraceStageStatus.FAILED, reason="PROVIDER_FAILURE"
                )
                trace = collector.finish("GENERATION_ERROR")
                return DecisionApplicationResult(
                    proposal_source=proposal_source,
                    provider=None,
                    model=None,
                    model_context=model_context,
                    model_context_hash=model_context_hash,
                    runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
                    runtime_reason="PROVIDER_FAILURE",
                    trace=trace,
                )
            collector.record_stage(
                "generation",
                TraceStageStatus.PASS,
                metadata={
                    "provider": proposal.provider,
                    "model": proposal.model,
                    "model_decision": proposal.decision.decision.value,
                    # Token usage is kept with the run so cost is auditable per question.
                    "prompt_tokens": proposal.prompt_tokens,
                    "completion_tokens": proposal.completion_tokens,
                    "reasoning_tokens": proposal.reasoning_tokens,
                    "cached_prompt_tokens": proposal.cached_prompt_tokens,
                },
                duration_ms=proposal.latency_ms,
            )
            collector.record_stage("proposal_replay", TraceStageStatus.SKIPPED, reason="LIVE_MODEL")

        decision = proposal.decision
        if decision.case_id != PRODUCTION_DECISION_CASE_ID:
            collector.record_stage(
                "decision_admission",
                TraceStageStatus.FAILED,
                reason="CASE_ID_MISMATCH",
                metadata={"expected_case_id": PRODUCTION_DECISION_CASE_ID},
            )
            collector.skip_unrecorded(
                tuple(
                    name
                    for name in TRACE_STAGE_ORDER
                    if name
                    not in {
                        "request",
                        "context",
                        "generation",
                        "proposal_replay",
                        "decision_admission",
                        "response",
                    }
                ),
                "DECISION_ADMISSION_FAILURE",
            )
            collector.record_stage("response", TraceStageStatus.FAILED, reason="CASE_ID_MISMATCH")
            trace = collector.finish("GENERATION_ERROR")
            return DecisionApplicationResult(
                proposal_source=proposal_source,
                provider=proposal.provider,
                model=proposal.model,
                model_context=model_context,
                model_context_hash=model_context_hash,
                runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
                runtime_reason="CASE_ID_MISMATCH",
                trace=trace,
            )
        collector.record_stage(
            "decision_admission",
            TraceStageStatus.PASS,
            reason=decision.decision.value,
            metadata={
                "decision": decision.decision.value,
                "reason_code": decision.reason_code.value if decision.reason_code else None,
                "missing_relationships": [
                    f"{item.from_table}->{item.to_table}"
                    for item in (decision.detail.missing_relationships if decision.detail else ())
                ],
            },
        )
        expanded = None
        if replay_decision is not None:
            # Frozen replays must stay exactly as recorded.
            collector.record_stage(
                "context_expansion", TraceStageStatus.SKIPPED, reason=proposal_source.value
            )
        elif not self.settings.context_expansion_enabled:
            collector.record_stage(
                "context_expansion", TraceStageStatus.SKIPPED, reason="EXPANSION_NOT_ALLOWED"
            )
        else:
            expanded = await self._expand_context(question, model_context, decision, collector)
        if expanded is not None:
            model_context, proposal = expanded
            model_context_hash = governed_context_hash(model_context)
            schema_text = serialize_governed_context(model_context)
            decision = proposal.decision
        if decision.decision is not DecisionType.ANSWER:
            return self._non_answer(
                decision, proposal, proposal_source, model_context, model_context_hash, collector
            )

        authority = ExecutionAuthority.from_governed_context(model_context)
        # Planning and execution use blocking database drivers; keep them off the event loop.
        planned = await asyncio.to_thread(
            self.safety_service.plan, self._candidate(decision, authority, collector)
        )
        repaired_from_sql: str | None = None
        # Frozen replays must stay exactly as recorded, so they never get a repair round.
        repairable = replay_decision is None and self.settings.repair_enabled
        feedback = _repair_feedback(planned, decision, self.settings) if repairable else None
        if feedback is not None:
            repaired = await self._repair(question, schema_text, feedback, collector)
            if repaired is not None:
                repaired_from_sql = decision.sql
                decision = repaired
                if decision.decision is not DecisionType.ANSWER:
                    collector.forget(_RUNTIME_STAGES)
                    return self._non_answer(
                        decision,
                        proposal,
                        proposal_source,
                        model_context,
                        model_context_hash,
                        collector,
                        repaired_from_sql=repaired_from_sql,
                    )
                collector.forget(_RUNTIME_STAGES)
                planned = await asyncio.to_thread(
                    self.safety_service.plan, self._candidate(decision, authority, collector)
                )
        else:
            collector.record_stage(
                "repair", TraceStageStatus.SKIPPED, reason=_repair_skip_reason(planned, repairable)
            )
        runtime_outcome, runtime_reason = _runtime_from_plan(planned)
        execution = None
        if isinstance(planned, SqlPlanFailure):
            collector.skip_unrecorded(
                (
                    "planning_connection",
                    "explain",
                    "cost_gate",
                    "execution_connection",
                    "execution",
                ),
                "EARLIER_RUNTIME_REJECTION",
            )
        else:
            execution = await asyncio.to_thread(self.safety_service.execute, planned)
            if isinstance(execution, SqlExecutionError):
                runtime_outcome, runtime_reason = "EXECUTION_ERROR", "EXECUTION_ERROR"
            else:
                runtime_outcome, runtime_reason = "EXECUTED", None
        collector.record_stage("response", TraceStageStatus.PASS, reason=runtime_reason)
        trace = collector.finish(runtime_outcome)
        return DecisionApplicationResult(
            model_decision=decision.decision,
            model_reason_code=None,
            proposed_sql=decision.sql,
            repaired_from_sql=repaired_from_sql,
            executed_sql=planned.normalized_sql if isinstance(planned, QueryPlan) else None,
            sql_changes=planned.sql_changes if isinstance(planned, QueryPlan) else (),
            proposal_source=proposal_source,
            provider=proposal.provider,
            model=proposal.model,
            model_context=model_context,
            model_context_hash=model_context_hash,
            runtime_outcome=RuntimeOutcome(runtime_outcome),
            runtime_reason=runtime_reason,
            rows=execution.rows
            if execution is not None and not isinstance(execution, SqlExecutionError)
            else [],
            columns=execution.columns
            if execution is not None and not isinstance(execution, SqlExecutionError)
            else [],
            row_count=execution.row_count
            if execution is not None and not isinstance(execution, SqlExecutionError)
            else 0,
            truncated=execution.truncated
            if execution is not None and not isinstance(execution, SqlExecutionError)
            else False,
            trace=trace,
        )

    def _candidate(
        self,
        decision: ProductionDecision,
        authority: ExecutionAuthority,
        collector: RunTraceCollector,
    ) -> SqlCandidate:
        return SqlCandidate(
            sql=decision.sql or "",
            source=CandidateSource.LLM,
            correlation_id=collector.run_id,
            execution_authority=authority,
        )

    async def _expand_context(
        self,
        question: str,
        model_context: GovernedContext,
        decision: ProductionDecision,
        collector: RunTraceCollector,
    ) -> tuple[GovernedContext, ProductionDecisionProposal] | None:
        """Add the omitted tables the model asked for and ask once more.

        The model can only name tables from ``other_tables``; the runtime rebuilds
        the context from the catalog, so authority still comes from the catalog
        and the relationships it holds, never from the model's request.
        """
        omitted = set(model_context.other_tables)
        requested = sorted(
            {
                table
                for item in (decision.detail.missing_relationships if decision.detail else ())
                for table in (item.from_table, item.to_table)
                if table in omitted
            }
        )
        if decision.decision is not DecisionType.BLOCKED_AUTHORITY or not requested:
            collector.record_stage(
                "context_expansion", TraceStageStatus.SKIPPED, reason="NOT_NEEDED"
            )
            return None
        started = perf_counter()
        base = {"requested_tables": requested}
        try:
            context = self.resolver.resolve(
                question, mode=SchemaContextMode.RETRIEVED, include=requested
            )
            expanded = governed_context_from_schema_context(context)
            proposal = await self.provider.propose_decision(
                question, serialize_governed_context(expanded), earlier=self.earlier
            )
        except Exception as error:
            collector.record_stage(
                "context_expansion",
                TraceStageStatus.FAILED,
                reason="EXPANSION_FAILURE",
                metadata={**base, "error_type": type(error).__name__},
                duration_ms=(perf_counter() - started) * 1000,
            )
            return None
        if proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID:
            collector.record_stage(
                "context_expansion",
                TraceStageStatus.FAILED,
                reason="CASE_ID_MISMATCH",
                metadata=base,
                duration_ms=proposal.latency_ms,
            )
            return None
        collector.record_stage(
            "context_expansion",
            TraceStageStatus.PASS,
            reason=proposal.decision.decision.value,
            metadata={
                **base,
                "context_hash": governed_context_hash(expanded),
                "entity_count": len(expanded.schema_catalog),
                "model_decision": proposal.decision.decision.value,
                "prompt_tokens": proposal.prompt_tokens,
                "completion_tokens": proposal.completion_tokens,
                "reasoning_tokens": proposal.reasoning_tokens,
                "cached_prompt_tokens": proposal.cached_prompt_tokens,
            },
            duration_ms=proposal.latency_ms,
        )
        return expanded, proposal

    async def _repair(
        self,
        question: str,
        schema_text: str,
        feedback: RuntimeRejectionFeedback,
        collector: RunTraceCollector,
    ) -> ProductionDecision | None:
        """Ask once for a replacement decision; None keeps the original rejection."""
        started = perf_counter()
        base = {"rejected_stage": feedback.stage, "rejected_code": feedback.code}
        try:
            proposal = await self.provider.propose_decision(
                question, schema_text, repair=feedback, earlier=self.earlier
            )
        except Exception as error:
            collector.record_stage(
                "repair",
                TraceStageStatus.FAILED,
                reason="PROVIDER_FAILURE",
                metadata={**base, "error_type": type(error).__name__},
                duration_ms=(perf_counter() - started) * 1000,
            )
            return None
        if proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID:
            collector.record_stage(
                "repair",
                TraceStageStatus.FAILED,
                reason="CASE_ID_MISMATCH",
                metadata=base,
                duration_ms=proposal.latency_ms,
            )
            return None
        collector.record_stage(
            "repair",
            TraceStageStatus.PASS,
            reason=feedback.code,
            metadata={
                **base,
                "model_decision": proposal.decision.decision.value,
                "prompt_tokens": proposal.prompt_tokens,
                "completion_tokens": proposal.completion_tokens,
                "reasoning_tokens": proposal.reasoning_tokens,
                "cached_prompt_tokens": proposal.cached_prompt_tokens,
            },
            duration_ms=proposal.latency_ms,
        )
        return proposal.decision

    @staticmethod
    def _non_answer(
        decision: ProductionDecision,
        proposal: ProductionDecisionProposal,
        proposal_source: ProposalSource,
        model_context: GovernedContext,
        model_context_hash: str,
        collector: RunTraceCollector,
        *,
        repaired_from_sql: str | None = None,
    ) -> DecisionApplicationResult:
        collector.skip_unrecorded(
            tuple(
                name
                for name in TRACE_STAGE_ORDER
                if name
                not in {
                    "request",
                    "context",
                    "generation",
                    "proposal_replay",
                    "decision_admission",
                    "context_expansion",
                    "repair",
                    "response",
                }
            ),
            "MODEL_NON_ANSWER",
        )
        if repaired_from_sql is None:
            collector.skip_unrecorded(("repair",), "MODEL_NON_ANSWER")
        collector.record_stage("response", TraceStageStatus.PASS, reason="MODEL_NON_ANSWER")
        trace = collector.finish("NOT_ENTERED")
        return DecisionApplicationResult(
            model_decision=decision.decision,
            model_reason_code=decision.reason_code,
            model_detail=decision.detail,
            proposal_source=proposal_source,
            proposed_sql=None,
            repaired_from_sql=repaired_from_sql,
            provider=proposal.provider,
            model=proposal.model,
            model_context=model_context,
            model_context_hash=model_context_hash,
            runtime_outcome=RuntimeOutcome.NOT_ENTERED,
            trace=trace,
        )


_RUNTIME_STAGES = (
    "sql_parse",
    "global_policy",
    "execution_authority",
    "grain_safety",
    "value_check",
    "planning_connection",
    "explain",
    "cost_gate",
    "execution_connection",
    "execution",
)

# Rejections the model can fix by rewriting its own SQL.  Authority, write
# statements, forbidden tables or columns, and system catalogs are final: a
# second attempt there would only probe the boundary.
_REPAIRABLE_POLICY_CODES = frozenset(
    {
        PolicyCode.EMPTY_SQL,
        PolicyCode.UNKNOWN_TABLE,
        PolicyCode.UNKNOWN_COLUMN,
        PolicyCode.FORBIDDEN_FUNCTION,
        PolicyCode.UNAUTHORIZED_JOIN,
    }
)

_FANOUT_MESSAGE = (
    "A measure from a parent table is aggregated after a join to a child table that repeats "
    "its rows, so the result would count it more than once. Aggregate the measure at its own "
    "table's grain, or test the child rows with EXISTS instead of joining them."
)


_GRAIN_MESSAGES = {
    "DISTINCT_VALUE_FANOUT_MASK": (
        "DISTINCT inside an aggregate hides the rows a join repeats, but it also merges "
        "equal values from different rows, so the result is still wrong. Aggregate the "
        "measure at its own table's grain, or test the child rows with EXISTS."
    ),
    "UNSAFE_ROLLUP": (
        "A measure that cannot be added up (a ratio, balance, or derived value) is summed. "
        "Use the aggregation its definition allows."
    ),
    "INCOMPATIBLE_MEASURE_GRAINS": (
        "Measures from tables at different grains are combined in one aggregate. Aggregate "
        "each at its own grain before combining them."
    ),
}


def _repair_feedback(
    planned: object, previous: ProductionDecision, settings: Settings
) -> RuntimeRejectionFeedback | None:
    """Describe a fixable rejection in the runtime's own words, or None when it is final.

    Execution errors are never sent back: database error text can quote stored values.
    """
    if isinstance(planned, QueryPlan):
        return _value_feedback(planned, previous)
    if not isinstance(planned, SqlPlanFailure):
        return None
    if planned.status is SqlSafetyStatus.SQL_PARSE_ERROR:
        stage, code = "sql_parse", SqlSafetyStatus.SQL_PARSE_ERROR.value
        message = planned.error or "The SQL could not be parsed."
    elif (
        planned.status is SqlSafetyStatus.POLICY_REJECTION
        and planned.rejection is not None
        and planned.rejection.code in _REPAIRABLE_POLICY_CODES
    ):
        stage, code, message = (
            "global_policy",
            planned.rejection.code.value,
            planned.rejection.message,
        )
    elif planned.status is SqlSafetyStatus.QUERY_COST_REJECTION and planned.estimate is not None:
        stage, code = "cost_gate", PolicyCode.QUERY_TOO_EXPENSIVE.value
        message = (
            f"The query plan is estimated at {planned.estimate.plan_rows} rows and cost "
            f"{planned.estimate.total_cost:.0f}; the limits are {settings.max_plan_rows} rows "
            f"and cost {settings.max_plan_cost:.0f}. Write a query whose plan stays within them."
        )
    elif planned.status is SqlSafetyStatus.SEMANTIC_REJECTION:
        stage, code = "grain_safety", planned.semantic_reason or planned.status.value
        message = _GRAIN_MESSAGES.get(code, _FANOUT_MESSAGE)
    else:
        return None
    return RuntimeRejectionFeedback(
        previous=previous, stage=stage, code=code, message=" ".join(message.split())[:600]
    )


def _value_feedback(
    plan: QueryPlan, previous: ProductionDecision
) -> RuntimeRejectionFeedback | None:
    """Tell the model a text it guessed is not stored, without naming the values that are.

    A model that saw the column's values chose the text knowingly (the value may be
    absent from the data), so only guesses at unshared columns are sent back.
    """
    blind = [item for item in plan.unmatched_values if not item.shared]
    if not blind:
        return None
    named = "; ".join(f"{item.table}.{item.column} = '{item.literal}'" for item in blind[:3])
    message = (
        f"The query filters on {named}, but the column stores no such value, even ignoring "
        "case, spacing, and punctuation, so the filter matches no rows. If the question "
        "refers to a value the column spells differently, write a filter that finds it; "
        "if the value is absent from the data, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="value_check", code="VALUE_NOT_STORED", message=message[:600]
    )


def _repair_skip_reason(planned: object, repairable: bool) -> str:
    if not isinstance(planned, SqlPlanFailure):
        return "NOT_NEEDED"
    return "FINAL_REJECTION" if repairable else "REPAIR_NOT_ALLOWED"


def _runtime_from_plan(result: object) -> tuple[str, str | None]:
    if not isinstance(result, SqlPlanFailure):
        return "PLANNED", None
    if result.authority_rejection is not None:
        return "AUTHORITY_REJECTED", result.authority_rejection.code.value
    if result.rejection is not None:
        if result.status.value == "POLICY_REJECTION":
            return "POLICY_REJECTED", result.rejection.code.value
        return "RUNTIME_REJECTED", result.rejection.code.value
    return "RUNTIME_REJECTED", result.semantic_reason or result.status.value
