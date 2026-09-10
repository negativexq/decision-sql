from __future__ import annotations

from time import perf_counter

from app.config import Settings
from app.decision.models import DecisionApplicationResult, ProposalSource, RuntimeOutcome
from app.generation.decision_contract import DecisionType, ProductionDecision
from app.generation.provider import (
    LLMProvider,
    MalformedProviderResponse,
    ProductionDecisionProposal,
)
from app.observability.run_trace import TRACE_STAGE_ORDER, RunTraceCollector, TraceStageStatus
from app.retrieval.context import (
    SchemaContextMode,
    SchemaContextResolver,
    SchemaResolutionError,
    serialize_schema_context,
)
from app.sql.authority import ExecutionAuthority
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError, SqlPlanFailure
from app.sql.service import SqlSafetyService


class DecisionSqlApplication:
    """Canonical product request lifecycle: decision first, runtime second."""

    def __init__(
        self,
        resolver: SchemaContextResolver,
        provider: LLMProvider,
        safety_service: SqlSafetyService,
        settings: Settings,
    ) -> None:
        self.resolver = resolver
        self.provider = provider
        self.safety_service = safety_service
        self.settings = settings

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
        collector.record_stage(
            "context",
            TraceStageStatus.PASS,
            metadata={
                "selected_tables": context.context_metadata.selected_table_count,
                "selected_columns": context.context_metadata.selected_column_count,
                "relationship_count": context.context_metadata.relationship_count,
            },
            duration_ms=(perf_counter() - context_started) * 1000,
        )
        schema_text = serialize_schema_context(context)

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
                proposal = await self.provider.propose_decision(question, schema_text)
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
                },
                duration_ms=proposal.latency_ms,
            )
            collector.record_stage("proposal_replay", TraceStageStatus.SKIPPED, reason="LIVE_MODEL")

        decision = proposal.decision
        collector.record_stage(
            "decision_admission",
            TraceStageStatus.PASS,
            reason=decision.decision.value,
            metadata={
                "decision": decision.decision.value,
                "reason_code": decision.reason_code.value if decision.reason_code else None,
            },
        )
        if decision.decision is not DecisionType.ANSWER:
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
                "MODEL_NON_ANSWER",
            )
            collector.record_stage("response", TraceStageStatus.PASS, reason="MODEL_NON_ANSWER")
            trace = collector.finish("NOT_ENTERED")
            return DecisionApplicationResult(
                model_decision=decision.decision,
                model_reason_code=decision.reason_code,
                proposal_source=proposal_source,
                proposed_sql=None,
                provider=proposal.provider,
                model=proposal.model,
                runtime_outcome=RuntimeOutcome.NOT_ENTERED,
                trace=trace,
            )

        candidate = SqlCandidate(
            sql=decision.sql or "",
            source=CandidateSource.LLM,
            correlation_id=collector.run_id,
            execution_authority=ExecutionAuthority.from_context(context),
        )
        planned = self.safety_service.plan(candidate)
        runtime_outcome, runtime_reason = _runtime_from_plan(planned)
        execution = None
        if isinstance(planned, SqlPlanFailure):
            collector.skip_unrecorded(
                ("database_connection", "explain", "cost_gate", "execution"),
                "EARLIER_RUNTIME_REJECTION",
            )
        else:
            execution = self.safety_service.execute(planned)
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
            proposal_source=proposal_source,
            provider=proposal.provider,
            model=proposal.model,
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
