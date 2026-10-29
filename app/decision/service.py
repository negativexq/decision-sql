from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter

from app.config import Settings
from app.decision.exploration import (
    Exploration,
    NoValueAccess,
    ProbeReport,
    ValueAccess,
    report_rows,
    rows_may_be_shared,
)
from app.decision.models import (
    AlternativeReading,
    DecisionApplicationResult,
    ProposalSource,
    RuntimeOutcome,
)
from app.generation.decision_contract import (
    MAX_PROBES,
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
    render_governed_context,
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
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
    SqlSafetyStatus,
    UnmatchedValue,
)
from app.sql.results import keeps_ties, percent_out_of_range
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
        value_access: ValueAccess | None = None,
    ) -> None:
        self.resolver = resolver
        self.provider = provider
        self.safety_service = safety_service
        self.settings = settings
        # A follow-up's earlier answer goes with every model call of this run.
        self.earlier = earlier
        # Whether probe results may show stored values: the connection's value sharing.
        self.value_access: ValueAccess = value_access or NoValueAccess()
        # What the model learned from its probes; goes with every later model call.
        self.exploration: Exploration | None = None
        # Set when a question or refusal returned without probes was sent back once.
        self.looked_first: dict[str, object] = {}
        # Set when the answer assumed one of several readings that give different results.
        self.assumption: str | None = None
        self.alternatives: tuple[AlternativeReading, ...] = ()

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
        schema_text = self._model_view(model_context)

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
            if self.settings.exploration_enabled:
                self.exploration = Exploration(open=True)
            started = perf_counter()
            try:
                proposal = await self.provider.propose_decision(
                    question, schema_text, earlier=self.earlier, exploration=self.exploration
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
            schema_text = self._model_view(model_context)
            decision = proposal.decision
        if (
            self.exploration is not None
            and self.exploration.open
            and not self.exploration.results
            and decision.decision in _LOOK_FIRST
        ):
            looked = await self._look_first(question, schema_text, decision)
            if looked is not None:
                proposal = looked
                decision = proposal.decision
        if decision.decision is DecisionType.EXPLORE:
            explored = await self._explore(
                question, schema_text, model_context, decision, collector
            )
            if explored is None:
                collector.skip_unrecorded(
                    tuple(
                        name
                        for name in TRACE_STAGE_ORDER
                        if name not in {"request", "context", "response"}
                    ),
                    "EXPLORATION_FAILURE",
                )
                collector.record_stage(
                    "response", TraceStageStatus.FAILED, reason="EXPLORATION_FAILURE"
                )
                return DecisionApplicationResult(
                    proposal_source=proposal_source,
                    provider=proposal.provider,
                    model=proposal.model,
                    model_context=model_context,
                    model_context_hash=model_context_hash,
                    runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
                    runtime_reason="EXPLORATION_FAILURE",
                    trace=collector.finish("GENERATION_ERROR"),
                )
            proposal = explored
            decision = proposal.decision
        else:
            collector.record_stage(
                "exploration",
                TraceStageStatus.SKIPPED,
                reason=(
                    proposal_source.value
                    if replay_decision is not None
                    else "DECLINED_TO_LOOK"
                    if self.looked_first
                    else "NOT_REQUESTED"
                    if self.exploration is not None
                    else "EXPLORATION_NOT_ALLOWED"
                ),
                metadata=self.looked_first,
            )
        if self.exploration is not None:
            # Later calls (repair, empty result) still see the results, but may not explore.
            self.exploration = self.exploration.model_copy(update={"open": False})
        if replay_decision is None:
            agreed = await self._check_readings(
                question, schema_text, model_context, decision, collector
            )
            if agreed is not None:
                decision = agreed
        else:
            collector.record_stage(
                "reading_check", TraceStageStatus.SKIPPED, reason=proposal_source.value
            )
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
        execution: QueryExecution | SqlExecutionError | None = None
        sql_changes: tuple[str, ...] = ()
        if isinstance(planned, SqlPlanFailure):
            collector.skip_unrecorded(
                (
                    "planning_connection",
                    "explain",
                    "cost_gate",
                    "execution_connection",
                    "execution",
                    "result_check",
                ),
                "EARLIER_RUNTIME_REJECTION",
            )
        else:
            execution = await asyncio.to_thread(self.safety_service.execute, planned)
            sql_changes = planned.sql_changes
            checking = (
                replay_decision is None
                and self.settings.result_check_enabled
                and isinstance(execution, QueryExecution)
            )
            out_of_range = (
                percent_out_of_range(execution.columns, execution.rows)
                if checking and isinstance(execution, QueryExecution)
                else ()
            )
            if (
                checking
                and isinstance(execution, QueryExecution)
                and (_looks_empty(execution) or out_of_range)
            ):
                if out_of_range and not _looks_empty(execution):
                    second = await self._check_range(
                        question,
                        schema_text,
                        out_of_range,
                        decision,
                        authority,
                        collector,
                        repairable=repairable and repaired_from_sql is None,
                    )
                else:
                    second = await self._check_empty_result(
                        question,
                        schema_text,
                        decision,
                        authority,
                        collector,
                        repairable=repairable and repaired_from_sql is None,
                    )
                if second is not None and second.decision is not decision:
                    repaired_from_sql = decision.sql
                    decision = second.decision
                    if decision.decision is not DecisionType.ANSWER:
                        return self._non_answer(
                            decision,
                            proposal,
                            proposal_source,
                            model_context,
                            model_context_hash,
                            collector,
                            repaired_from_sql=repaired_from_sql,
                        )
                if second is not None and second.planned is not None:
                    planned, execution = second.planned, second.execution
                    sql_changes = second.sql_changes
            elif (
                isinstance(execution, SqlExecutionError)
                and execution.error_class in _EXECUTION_MESSAGES
                and repairable
                and repaired_from_sql is None
            ):
                collector.record_stage(
                    "result_check", TraceStageStatus.SKIPPED, reason="EXECUTION_ERROR"
                )
                second = await self._repair_result(
                    question,
                    schema_text,
                    _execution_feedback(decision, execution.error_class or ""),
                    authority,
                    collector,
                )
                if second is not None and second.decision is not decision:
                    repaired_from_sql = decision.sql
                    decision = second.decision
                    if decision.decision is not DecisionType.ANSWER:
                        return self._non_answer(
                            decision,
                            proposal,
                            proposal_source,
                            model_context,
                            model_context_hash,
                            collector,
                            repaired_from_sql=repaired_from_sql,
                        )
                if second is not None and second.planned is not None:
                    planned, execution = second.planned, second.execution
                    sql_changes = second.sql_changes
            elif checking and isinstance(execution, QueryExecution) and _tied(execution, planned):
                collector.record_stage(
                    "result_check",
                    TraceStageStatus.PASS,
                    reason="TIED_ROWS",
                    metadata={"tied": execution.row_count},
                )
            else:
                collector.record_stage(
                    "result_check", TraceStageStatus.SKIPPED, reason="NOT_NEEDED"
                )
        runtime_outcome, runtime_reason = _runtime_from_plan(planned)
        if isinstance(execution, SqlExecutionError):
            runtime_outcome, runtime_reason = "EXECUTION_ERROR", "EXECUTION_ERROR"
        elif isinstance(execution, QueryExecution):
            runtime_outcome, runtime_reason = "EXECUTED", None
        collector.record_stage("response", TraceStageStatus.PASS, reason=runtime_reason)
        trace = collector.finish(runtime_outcome)
        return DecisionApplicationResult(
            model_decision=decision.decision,
            model_reason_code=None,
            proposed_sql=decision.sql,
            repaired_from_sql=repaired_from_sql,
            executed_sql=planned.normalized_sql if isinstance(planned, QueryPlan) else None,
            sql_changes=sql_changes if isinstance(planned, QueryPlan) else (),
            proposal_source=proposal_source,
            provider=proposal.provider,
            model=proposal.model,
            model_context=model_context,
            model_context_hash=model_context_hash,
            runtime_outcome=RuntimeOutcome(runtime_outcome),
            runtime_reason=runtime_reason,
            rows=execution.rows if isinstance(execution, QueryExecution) else [],
            columns=execution.columns if isinstance(execution, QueryExecution) else [],
            row_count=execution.row_count if isinstance(execution, QueryExecution) else 0,
            truncated=execution.truncated if isinstance(execution, QueryExecution) else False,
            assumption=self.assumption,
            alternatives=self.alternatives,
            trace=trace,
        )

    async def _check_empty_result(
        self,
        question: str,
        schema_text: str,
        decision: ProductionDecision,
        authority: ExecutionAuthority,
        collector: RunTraceCollector,
        *,
        repairable: bool,
    ) -> _SecondAttempt | None:
        """Run once more when an empty result has a cause the runtime can name.

        A text literal the column stores in exactly one other spelling is rewritten
        and the query runs again through every check, with no model call.  Otherwise,
        if the run has not used its repair round, the model hears that the query
        returned no rows (never the rows of any query, nor the values a column
        stores) and may rewrite it or keep it.  None keeps the empty result.
        """
        started = perf_counter()
        probed = await asyncio.to_thread(self.safety_service.probe_values, decision.sql or "")
        unmatched = probed.unmatched if probed is not None else ()
        metadata: dict[str, object] = {
            "checked": probed.checked if probed is not None else 0,
            "rewritten": len(probed.changes) if probed is not None else 0,
            "unmatched": ", ".join(sorted({f"{item.table}.{item.column}" for item in unmatched})),
        }
        if probed is not None and probed.changes:
            collector.record_stage(
                "result_check",
                TraceStageStatus.PASS,
                reason="VALUE_REWRITTEN",
                metadata=metadata,
                duration_ms=(perf_counter() - started) * 1000,
            )
            collector.forget(_RUNTIME_STAGES)
            candidate = SqlCandidate(
                sql=probed.expression.sql(dialect="postgres"),
                source=CandidateSource.LLM,
                correlation_id=collector.run_id,
                execution_authority=authority,
            )
            planned = await asyncio.to_thread(self.safety_service.plan, candidate)
            return await self._second_attempt(decision, planned, probed.changes, collector)
        feedback = _empty_result_feedback(decision, unmatched) if repairable else None
        collector.record_stage(
            "result_check",
            TraceStageStatus.PASS,
            reason="VALUE_NOT_STORED" if unmatched else "EMPTY_RESULT",
            metadata=metadata,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return await self._repair_result(question, schema_text, feedback, authority, collector)

    async def _check_range(
        self,
        question: str,
        schema_text: str,
        columns: tuple[str, ...],
        decision: ProductionDecision,
        authority: ExecutionAuthority,
        collector: RunTraceCollector,
        *,
        repairable: bool,
    ) -> _SecondAttempt | None:
        """Give the model its repair round when a percentage falls outside 0 to 100.

        The model hears which columns are out of range and in which direction, never
        the values themselves.  None keeps the result, which the user sees flagged.
        """
        collector.record_stage(
            "result_check",
            TraceStageStatus.PASS,
            reason="OUT_OF_RANGE",
            metadata={"columns": ", ".join(columns)},
        )
        feedback = _range_feedback(decision, columns) if repairable else None
        return await self._repair_result(question, schema_text, feedback, authority, collector)

    async def _repair_result(
        self,
        question: str,
        schema_text: str,
        feedback: RuntimeRejectionFeedback | None,
        authority: ExecutionAuthority,
        collector: RunTraceCollector,
    ) -> _SecondAttempt | None:
        if feedback is None:
            return None
        collector.forget(("repair",))
        repaired = await self._repair(question, schema_text, feedback, collector)
        if repaired is None:
            return None
        if repaired.decision is not DecisionType.ANSWER:
            collector.forget(_RUNTIME_STAGES)
            return _SecondAttempt(repaired, None, None, ())
        collector.forget(_RUNTIME_STAGES)
        planned = await asyncio.to_thread(
            self.safety_service.plan, self._candidate(repaired, authority, collector)
        )
        return await self._second_attempt(repaired, planned, (), collector)

    async def _second_attempt(
        self,
        decision: ProductionDecision,
        planned: QueryPlan | SqlPlanFailure,
        changes: tuple[str, ...],
        collector: RunTraceCollector,
    ) -> _SecondAttempt:
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
            return _SecondAttempt(decision, planned, None, ())
        execution = await asyncio.to_thread(self.safety_service.execute, planned)
        return _SecondAttempt(decision, planned, execution, (*changes, *planned.sql_changes))

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
                question,
                self._model_view(expanded),
                earlier=self.earlier,
                exploration=self.exploration,
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
                question,
                schema_text,
                repair=feedback,
                earlier=self.earlier,
                exploration=self.exploration,
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
        if (
            proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID
            or proposal.decision.decision is DecisionType.EXPLORE
        ):
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

    def _model_view(self, context: GovernedContext) -> str:
        if self.settings.context_format == "compact":
            return render_governed_context(context)
        return serialize_governed_context(context)

    async def _look_first(
        self, question: str, schema_text: str, decision: ProductionDecision
    ) -> ProductionDecisionProposal | None:
        """Send a question or refusal returned before any probe back once, to look first.

        A clarification the data could settle, or a refusal an authorized path in
        the context could answer, costs the user a turn. The model may still return
        the same decision; None keeps the original one (the provider failed).
        """
        returned = decision.decision.value
        try:
            proposal = await self.provider.propose_decision(
                question,
                schema_text,
                earlier=self.earlier,
                exploration=Exploration(open=True, returned_without_looking=returned),
            )
        except Exception as error:
            self.looked_first = {"looked_first": returned, "look_first_error": type(error).__name__}
            return None
        if proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID:
            self.looked_first = {"looked_first": returned, "look_first_error": "CASE_ID_MISMATCH"}
            return None
        self.looked_first = {
            "looked_first": returned,
            "look_first_decision": proposal.decision.decision.value,
            "look_first_prompt_tokens": proposal.prompt_tokens,
            "look_first_completion_tokens": proposal.completion_tokens,
        }
        return proposal

    async def _check_readings(
        self,
        question: str,
        schema_text: str,
        model_context: GovernedContext,
        decision: ProductionDecision,
        collector: RunTraceCollector,
    ) -> ProductionDecision | None:
        """Run every reading of a clarification, and answer instead of asking when possible.

        A question whose readings give the same result on this data does not change the
        answer, so asking it only costs the user a turn.  The model writes one SQL per
        reading in one more call; each runs through every gate as a probe, and the
        results are compared in full, never shown to the model.  When they differ and
        ``ambiguity_mode`` is ``assume``, the answer takes the model's first reading,
        says so, and carries the other readings with their results for the user to
        switch to.  None keeps the clarification: the mode is ``ask`` and the readings
        differ, or one could not be checked.
        """
        readings = tuple(decision.detail.interpretations if decision.detail else ())
        if decision.decision is not DecisionType.NEEDS_CLARIFICATION:
            collector.record_stage("reading_check", TraceStageStatus.SKIPPED, reason="NOT_ASKED")
            return None
        if not self.settings.reading_check_enabled:
            collector.record_stage(
                "reading_check", TraceStageStatus.SKIPPED, reason="READING_CHECK_NOT_ALLOWED"
            )
            return None
        if not 2 <= len(readings) <= MAX_PROBES:
            collector.record_stage(
                "reading_check", TraceStageStatus.SKIPPED, reason="READINGS_NOT_COUNTABLE"
            )
            return None
        started = perf_counter()
        metadata: dict[str, object] = {"readings": list(readings)}

        def finish(reason: str, status: TraceStageStatus = TraceStageStatus.PASS) -> None:
            collector.record_stage(
                "reading_check",
                status,
                reason=reason,
                metadata=metadata,
                duration_ms=(perf_counter() - started) * 1000,
            )

        try:
            proposal = await self.provider.propose_decision(
                question,
                schema_text,
                earlier=self.earlier,
                exploration=Exploration(open=True, readings=readings),
            )
        except Exception:
            finish("PROVIDER_FAILURE", TraceStageStatus.FAILED)
            return None
        metadata.update(
            prompt_tokens=proposal.prompt_tokens,
            completion_tokens=proposal.completion_tokens,
            reasoning_tokens=proposal.reasoning_tokens,
        )
        probes = tuple(proposal.decision.probes or ())
        if (
            proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID
            or proposal.decision.decision is not DecisionType.EXPLORE
            or len(probes) != len(readings)
        ):
            finish("NO_SQL_PER_READING")
            return None
        metadata["reading_sql"] = list(probes)
        authority = ExecutionAuthority.from_governed_context(model_context)
        executions = await asyncio.to_thread(
            self._run_readings, probes, authority, collector.run_id
        )
        if executions is None:
            finish("READING_NOT_RUN")
            return None
        metadata["row_counts"] = [execution.row_count for execution in executions]
        answer = ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID, decision=DecisionType.ANSWER, sql=probes[0]
        )
        compared = [_comparable(execution) for execution in executions]
        if all(result == compared[0] for result in compared[1:]):
            finish("READINGS_AGREE")
            return answer
        if self.settings.ambiguity_mode == "ask":
            finish("READINGS_DIFFER")
            return None
        metadata["assumed"] = readings[0]
        finish("READINGS_DIFFER_ASSUMED")
        self.assumption = readings[0]
        self.alternatives = tuple(
            AlternativeReading(
                reading=reading,
                sql=sql,
                rows=execution.rows,
                columns=execution.columns,
                row_count=execution.row_count,
                truncated=execution.truncated,
            )
            for reading, sql, execution in zip(
                readings[1:], probes[1:], executions[1:], strict=True
            )
        )
        return answer

    def _run_readings(
        self, probes: tuple[str, ...], authority: ExecutionAuthority, run_id: str
    ) -> list[QueryExecution] | None:
        """Each reading's full result; None if one did not run or was cut off."""
        results: list[QueryExecution] = []
        for sql in probes:
            planned, executed = self.safety_service.explore(
                SqlCandidate(
                    sql=sql,
                    source=CandidateSource.LLM,
                    correlation_id=run_id,
                    execution_authority=authority,
                )
            )
            if isinstance(planned, SqlPlanFailure) or not isinstance(executed, QueryExecution):
                return None
            if executed.truncated:
                return None  # rows past the cap could still differ
            results.append(executed)
        return results

    async def _explore(
        self,
        question: str,
        schema_text: str,
        model_context: GovernedContext,
        decision: ProductionDecision,
        collector: RunTraceCollector,
    ) -> ProductionDecisionProposal | None:
        """Run the model's probes and ask again, for a bounded number of rounds.

        None means the run cannot continue: the provider failed, or the model was
        still exploring when exploration closed.
        """
        started = perf_counter()
        authority = ExecutionAuthority.from_governed_context(model_context)
        rounds = 0
        reports: list[ProbeReport] = []
        tokens = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
        proposal: ProductionDecisionProposal | None = None

        def finish(status: TraceStageStatus, reason: str | None) -> None:
            collector.record_stage(
                "exploration",
                status,
                reason=reason,
                metadata={
                    "rounds": rounds,
                    # Trace metadata holds flat values only: one list per field, by probe.
                    "probe_sql": [item.sql for item in reports],
                    "probe_status": [item.status for item in reports],
                    "probe_reason": [item.reason or "" for item in reports],
                    "probe_row_count": [
                        "" if item.row_count is None else item.row_count for item in reports
                    ],
                    "probe_rows_shared": [item.rows is not None for item in reports],
                    **tokens,
                    **self.looked_first,
                },
                duration_ms=(perf_counter() - started) * 1000,
            )

        while (
            decision.decision is DecisionType.EXPLORE
            and self.exploration is not None
            and self.exploration.open
        ):
            rounds += 1
            found = await asyncio.to_thread(self._run_probes, decision, authority, collector.run_id)
            reports.extend(found)
            self.exploration = Exploration(
                open=rounds < self.settings.exploration_rounds,
                results=(*self.exploration.results, *found),
            )
            try:
                proposal = await self.provider.propose_decision(
                    question, schema_text, earlier=self.earlier, exploration=self.exploration
                )
            except Exception:
                finish(TraceStageStatus.FAILED, "PROVIDER_FAILURE")
                return None
            for key in tokens:
                tokens[key] += getattr(proposal, key) or 0
            if proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID:
                finish(TraceStageStatus.FAILED, "CASE_ID_MISMATCH")
                return None
            decision = proposal.decision
        if proposal is None or decision.decision is DecisionType.EXPLORE:
            finish(TraceStageStatus.FAILED, "EXPLORATION_NOT_CLOSED")
            return None
        finish(TraceStageStatus.PASS, decision.decision.value)
        return proposal

    def _run_probes(
        self, decision: ProductionDecision, authority: ExecutionAuthority, run_id: str
    ) -> list[ProbeReport]:
        reports: list[ProbeReport] = []
        for sql in (decision.probes or ())[:MAX_PROBES]:
            planned, executed = self.safety_service.explore(
                SqlCandidate(
                    sql=sql,
                    source=CandidateSource.LLM,
                    correlation_id=run_id,
                    execution_authority=authority,
                )
            )
            if isinstance(planned, SqlPlanFailure):
                feedback = _repair_feedback(planned, decision, self.settings)
                reports.append(
                    ProbeReport(
                        sql=sql,
                        status="REJECTED",
                        reason=feedback.code if feedback else _failure_code(planned),
                        message=feedback.message if feedback else None,
                    )
                )
            elif isinstance(executed, QueryExecution):
                report = report_rows(
                    sql,
                    executed.columns,
                    executed.rows,
                    truncated=executed.truncated,
                    shared=rows_may_be_shared(
                        planned.normalized_sql, self.safety_service.catalog, self.value_access
                    ),
                )
                if not executed.rows:
                    report = self._respell_probe(report, authority, run_id)
                reports.append(report)
            else:
                error_class = executed.error_class if executed is not None else None
                reports.append(
                    ProbeReport(
                        sql=sql,
                        status="FAILED",
                        reason=error_class or "EXECUTION_ERROR",
                        message=_EXECUTION_MESSAGES.get(error_class or ""),
                    )
                )
        return reports

    def _respell_probe(
        self, report: ProbeReport, authority: ExecutionAuthority, run_id: str
    ) -> ProbeReport:
        """Check the literals of a probe that matched nothing, as the final query is checked.

        A literal the column stores in exactly one other spelling is rewritten and the
        probe runs again; the model hears that it was respelled, and sees the rows only
        where value sharing allows.  A literal the column does not store is named.
        """
        probed = self.safety_service.probe_values(report.sql)
        if probed is None:
            return report
        if not probed.changes:
            if not probed.unmatched:
                return report
            named = "; ".join(
                f"{item.table}.{item.column} = '{item.literal}'" for item in probed.unmatched[:3]
            )
            return report.model_copy(
                update={
                    "reason": "VALUE_NOT_STORED",
                    "message": f"The column stores no such value for {named}, even ignoring "
                    "case, spacing, punctuation, and zero padding.",
                }
            )
        respelled = probed.expression.sql(dialect="postgres")
        planned, executed = self.safety_service.explore(
            SqlCandidate(
                sql=respelled,
                source=CandidateSource.LLM,
                correlation_id=run_id,
                execution_authority=authority,
            )
        )
        if isinstance(planned, SqlPlanFailure) or not isinstance(executed, QueryExecution):
            return report
        rerun = report_rows(
            report.sql,
            executed.columns,
            executed.rows,
            truncated=executed.truncated,
            shared=rows_may_be_shared(
                planned.normalized_sql, self.safety_service.catalog, self.value_access
            ),
        )
        return rerun.model_copy(
            update={
                "reason": "VALUE_RESPELLED",
                "message": "A literal matched nothing as written; the column stores it in "
                "another spelling (case, spacing, punctuation, or zero padding), and these "
                "are the results with that spelling. The runtime applies the same spelling "
                "when the final query runs.",
            }
        )

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


@dataclass(frozen=True)
class _SecondAttempt:
    """What ran after an empty result: the decision, its plan, and its execution."""

    decision: ProductionDecision
    planned: QueryPlan | SqlPlanFailure | None
    execution: QueryExecution | SqlExecutionError | None
    sql_changes: tuple[str, ...]


# Decisions sent back once when returned before any probe ran.
_LOOK_FIRST = {DecisionType.NEEDS_CLARIFICATION, DecisionType.BLOCKED_AUTHORITY}


def _looks_empty(execution: QueryExecution) -> bool:
    """No rows, or one row of only zeros and NULLs: what a filter matching nothing returns."""
    if execution.row_count == 0:
        return True
    if execution.row_count != 1 or not execution.rows:
        return False
    return all(
        value is None or (isinstance(value, int | float | Decimal) and value == 0)
        for value in execution.rows[0].values()
    )


def _empty_result_feedback(
    previous: ProductionDecision, unmatched: tuple[UnmatchedValue, ...]
) -> RuntimeRejectionFeedback:
    named = "; ".join(f"{item.table}.{item.column} = '{item.literal}'" for item in unmatched[:3])
    missing = (
        f" The column stores no such value for {named}, even ignoring case, spacing, and "
        "punctuation."
        if named
        else ""
    )
    message = (
        "The query ran and matched no rows (or only zeros and NULLs)."
        f"{missing} Check each filter against the question: a condition the question asks "
        "you to report, rather than to require, belongs in the selected columns, and a "
        "literal should be spelled as the column stores it. If the empty result is the "
        "answer, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="result_check", code="EMPTY_RESULT", message=message[:600]
    )


def _range_feedback(
    previous: ProductionDecision, columns: tuple[str, ...]
) -> RuntimeRejectionFeedback:
    named = ", ".join(columns[:3])
    message = (
        f"The query ran, and the percentage column {named} holds a value below 0 or above "
        "100. A share of a whole lies between 0 and 100: check the numerator, the "
        "denominator, and the factor of 100 against the question. If the question asks for "
        "a change or a formula that can leave that range, keep the query."
    )
    return RuntimeRejectionFeedback(
        previous=previous, stage="result_check", code="OUT_OF_RANGE", message=message[:600]
    )


_EXECUTION_MESSAGES = {
    "INVALID_TEXT_CONVERSION": (
        "A cast or comparison met a stored text value that is not in the target type's "
        "format, such as a letter in a column cast to a number. A WHERE filter does not "
        "protect a cast: PostgreSQL may evaluate the cast first. Guard the cast itself, "
        "for example CASE WHEN col <> '' AND TRANSLATE(col, '0123456789', '') = '' "
        "THEN col::numeric END, or compare as text. Regular expressions (~, SIMILAR TO) "
        "are not allowed."
    ),
    "NUMERIC_OUT_OF_RANGE": "A number grew beyond its type's range. Use a wider type.",
    "DIVISION_BY_ZERO": "A division met a zero divisor. Guard the divisor with NULLIF.",
    "INVALID_DATETIME_FORMAT": (
        "A text value did not fit the date or time format it was cast to. Match the "
        "stored format, or convert only the values that fit."
    ),
    "DATETIME_OUT_OF_RANGE": "A date or time value fell outside its valid range.",
    "SUBQUERY_RETURNED_SEVERAL_ROWS": (
        "A subquery used as a single value returned more than one row. Make it return "
        "one row, or compare with IN or EXISTS."
    ),
    "TYPE_MISMATCH": "An expression has a different data type than its context requires.",
    "NO_SUCH_FUNCTION_FOR_TYPES": (
        "A function or operator was applied to argument types it does not accept. Cast the "
        "arguments to the types it takes."
    ),
    "STATEMENT_TIMEOUT": (
        "The query ran past the time limit. Read fewer rows, or filter before joining."
    ),
}


def _execution_feedback(previous: ProductionDecision, error_class: str) -> RuntimeRejectionFeedback:
    """The runtime's own words for a database failure; the database's text never goes back."""
    message = f"The query failed while running: {_EXECUTION_MESSAGES[error_class]}"
    return RuntimeRejectionFeedback(
        previous=previous, stage="execution", code=error_class, message=message[:600]
    )


def _tied(execution: QueryExecution, planned: QueryPlan | SqlPlanFailure) -> bool:
    """Several rows that all share the extreme value the query keeps."""
    return (
        execution.row_count > 1
        and isinstance(planned, QueryPlan)
        and keeps_ties(planned.normalized_sql)
    )


_RUNTIME_STAGES = (
    "sql_parse",
    "global_policy",
    "execution_authority",
    "grain_safety",
    "value_check",
    "null_ordering",
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


def _failure_code(planned: SqlPlanFailure) -> str:
    if planned.rejection is not None:
        return planned.rejection.code.value
    return planned.semantic_reason or planned.status.value


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


def _comparable(execution: QueryExecution) -> tuple[int, list[str]]:
    """A result's column count and rows, row order ignored, for comparing readings."""
    rows = sorted(
        repr(tuple(row.get(name) for name in execution.columns)) for row in execution.rows
    )
    return len(execution.columns), rows
