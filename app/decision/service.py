from __future__ import annotations

import asyncio
from time import perf_counter

from app.config import Settings
from app.decision.budget import AttemptBudget, ModelCall
from app.decision.exploration import (
    Exploration,
    NoValueAccess,
    ProbeReport,
    ValueAccess,
    report_rows,
    rows_may_be_shared,
)
from app.decision.feedback import (
    EXECUTION_MESSAGES,
    comparable,
    empty_result_feedback,
    execution_feedback,
    failure_code,
    looks_empty,
    range_feedback,
    repair_feedback,
    repair_skip_reason,
    runtime_from_plan,
    tied,
)
from app.decision.models import (
    AlternativeReading,
    DecisionApplicationResult,
    ProposalSource,
    RuntimeOutcome,
)
from app.decision.state import Attempt, DecisionState
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
from app.observability.run_trace import (
    TRACE_STAGE_ORDER,
    RunTrace,
    RunTraceCollector,
    TraceStageStatus,
)
from app.retrieval.context import (
    SchemaContextMode,
    SchemaContextResolver,
    SchemaResolutionError,
)
from app.sql.authority import ExecutionAuthority
from app.sql.models import (
    CandidateSource,
    QueryExecution,
    QueryPlan,
    SqlCandidate,
    SqlExecutionError,
    SqlPlanFailure,
)
from app.sql.results import percent_out_of_range
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

    async def run(
        self,
        question: str,
        collector: RunTraceCollector,
        *,
        proposal_source: ProposalSource = ProposalSource.LIVE_MODEL,
        replay_decision: ProductionDecision | None = None,
    ) -> DecisionApplicationResult:
        """Answer one question, stage by stage.

        The context and the first decision open the run. Every later stage either
        moves the state on or returns the finished result: a refusal, a question
        back, or a rejection. The last stage runs the query. Each model call is
        taken from the run's budget.
        """
        replay = replay_decision is not None
        budget = AttemptBudget.for_run(self.settings, replay=replay)
        context = self._resolve_context(question, collector, proposal_source, budget)
        if isinstance(context, DecisionApplicationResult):
            return context
        model_context, model_context_hash = context
        schema_text = self._model_view(model_context)
        exploration = (
            Exploration(open=True) if not replay and self.settings.exploration_enabled else None
        )
        proposal = await self._first_decision(
            question, schema_text, collector, budget, exploration, replay_decision, proposal_source
        )
        if not isinstance(proposal, ProductionDecisionProposal):
            return DecisionApplicationResult(
                proposal_source=proposal_source,
                model_context=model_context,
                model_context_hash=model_context_hash,
                runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
                runtime_reason=proposal,
                trace=_close(
                    collector,
                    budget,
                    skip_reason="PROVIDER_FAILURE",
                    response=proposal,
                    outcome="GENERATION_ERROR",
                ),
            )
        state = DecisionState(
            question=question,
            collector=collector,
            proposal_source=proposal_source,
            replay=replay,
            budget=budget,
            model_context=model_context,
            model_context_hash=model_context_hash,
            schema_text=schema_text,
            proposal=proposal,
            decision=proposal.decision,
            exploration=exploration,
        )
        for stage in (
            self._admit,
            self._widen_context,
            self._look_first,
            self._explore,
            self._check_readings,
        ):
            finished = await stage(state)
            if finished is not None:
                return finished
        return await self._run_query(state)

    # Opening: context and the first decision

    def _resolve_context(
        self,
        question: str,
        collector: RunTraceCollector,
        proposal_source: ProposalSource,
        budget: AttemptBudget,
    ) -> tuple[GovernedContext, str] | DecisionApplicationResult:
        started = perf_counter()
        try:
            context = self.resolver.resolve(question, mode=SchemaContextMode.RETRIEVED)
        except SchemaResolutionError as error:
            collector.record_stage(
                "context",
                TraceStageStatus.FAILED,
                reason="CONTEXT_RESOLUTION_FAILURE",
                metadata={"message": str(error)},
                duration_ms=(perf_counter() - started) * 1000,
            )
            return DecisionApplicationResult(
                proposal_source=proposal_source,
                runtime_outcome=RuntimeOutcome.NOT_ENTERED,
                runtime_reason="CONTEXT_RESOLUTION_FAILURE",
                trace=_close(
                    collector,
                    budget,
                    skip_reason="CONTEXT_RESOLUTION_FAILURE",
                    response="CONTEXT_RESOLUTION_FAILURE",
                    outcome="NOT_ENTERED",
                    status=TraceStageStatus.PASS,
                ),
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
            duration_ms=(perf_counter() - started) * 1000,
        )
        return model_context, model_context_hash

    async def _first_decision(
        self,
        question: str,
        schema_text: str,
        collector: RunTraceCollector,
        budget: AttemptBudget,
        exploration: Exploration | None,
        replay_decision: ProductionDecision | None,
        proposal_source: ProposalSource,
    ) -> ProductionDecisionProposal | str:
        """The model's decision, or a frozen one replayed; a failure returns its reason."""
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
            return ProductionDecisionProposal(
                decision=replay_decision, provider="replay", model="frozen-scenario"
            )
        started = perf_counter()
        try:
            proposal = await self._propose(
                budget, ModelCall.GENERATION, question, schema_text, exploration=exploration
            )
        except MalformedProviderResponse:
            collector.record_stage(
                "generation", TraceStageStatus.PASS, reason="TRANSPORT_RESPONSE_RECEIVED"
            )
            collector.record_stage(
                "decision_admission", TraceStageStatus.FAILED, reason="INVALID_TYPED_DECISION"
            )
            return "INVALID_TYPED_DECISION"
        except Exception as error:
            collector.record_stage(
                "generation",
                TraceStageStatus.FAILED,
                reason="PROVIDER_FAILURE",
                metadata={"error_type": type(error).__name__},
                duration_ms=(perf_counter() - started) * 1000,
            )
            return "PROVIDER_FAILURE"
        collector.record_stage(
            "generation",
            TraceStageStatus.PASS,
            metadata={
                "provider": proposal.provider,
                "model": proposal.model,
                "model_decision": proposal.decision.decision.value,
                # Token usage is kept with the run so cost is auditable per question.
                **_tokens(proposal),
            },
            duration_ms=proposal.latency_ms,
        )
        collector.record_stage("proposal_replay", TraceStageStatus.SKIPPED, reason="LIVE_MODEL")
        return proposal

    # Stages before the query: each returns the finished result, or None to go on

    async def _admit(self, state: DecisionState) -> DecisionApplicationResult | None:
        decision = state.decision
        if decision.case_id != PRODUCTION_DECISION_CASE_ID:
            state.collector.record_stage(
                "decision_admission",
                TraceStageStatus.FAILED,
                reason="CASE_ID_MISMATCH",
                metadata={"expected_case_id": PRODUCTION_DECISION_CASE_ID},
            )
            return self._stopped(
                state, skip_reason="DECISION_ADMISSION_FAILURE", reason="CASE_ID_MISMATCH"
            )
        state.collector.record_stage(
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
        return None

    async def _widen_context(self, state: DecisionState) -> DecisionApplicationResult | None:
        """Add the omitted tables the model asked for and ask once more.

        The model can only name tables from ``other_tables``; the runtime rebuilds
        the context from the catalog, so authority still comes from the catalog
        and the relationships it holds, never from the model's request.
        """
        collector, decision = state.collector, state.decision
        if state.replay:
            # Frozen replays must stay exactly as recorded.
            collector.record_stage(
                "context_expansion", TraceStageStatus.SKIPPED, reason=state.proposal_source.value
            )
            return None
        if not self.settings.context_expansion_enabled:
            collector.record_stage(
                "context_expansion", TraceStageStatus.SKIPPED, reason="EXPANSION_NOT_ALLOWED"
            )
            return None
        omitted = set(state.model_context.other_tables)
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
                state.question, mode=SchemaContextMode.RETRIEVED, include=requested
            )
            expanded = governed_context_from_schema_context(context)
            proposal = await self._propose(
                state.budget,
                ModelCall.CONTEXT_EXPANSION,
                state.question,
                self._model_view(expanded),
                exploration=state.exploration,
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
                **_tokens(proposal),
            },
            duration_ms=proposal.latency_ms,
        )
        state.model_context = expanded
        state.model_context_hash = governed_context_hash(expanded)
        state.schema_text = self._model_view(expanded)
        state.proposal, state.decision = proposal, proposal.decision
        return None

    async def _look_first(self, state: DecisionState) -> DecisionApplicationResult | None:
        """Send a question or refusal returned before any probe back once, to look first.

        A clarification the data could settle, or a refusal an authorized path in
        the context could answer, costs the user a turn. The model may still return
        the same decision; on a provider failure the original one stands.
        """
        if (
            state.exploration is None
            or not state.exploration.open
            or state.exploration.results
            or state.decision.decision not in _LOOK_FIRST
        ):
            return None
        returned = state.decision.decision.value
        try:
            proposal = await self._propose(
                state.budget,
                ModelCall.LOOK_FIRST,
                state.question,
                state.schema_text,
                exploration=Exploration(open=True, returned_without_looking=returned),
            )
        except Exception as error:
            state.looked_first = {
                "looked_first": returned,
                "look_first_error": type(error).__name__,
            }
            return None
        if proposal.decision.case_id != PRODUCTION_DECISION_CASE_ID:
            state.looked_first = {"looked_first": returned, "look_first_error": "CASE_ID_MISMATCH"}
            return None
        state.looked_first = {
            "looked_first": returned,
            "look_first_decision": proposal.decision.decision.value,
            "look_first_prompt_tokens": proposal.prompt_tokens,
            "look_first_completion_tokens": proposal.completion_tokens,
        }
        state.proposal, state.decision = proposal, proposal.decision
        return None

    async def _explore(self, state: DecisionState) -> DecisionApplicationResult | None:
        """Run the model's probes and ask again, for a bounded number of rounds.

        The run stops when the provider failed, or the model was still exploring
        when exploration closed.
        """
        if state.decision.decision is DecisionType.EXPLORE:
            explored = await self._probe_rounds(state)
            if explored is None:
                return self._stopped(
                    state, skip_reason="EXPLORATION_FAILURE", reason="EXPLORATION_FAILURE"
                )
            state.proposal, state.decision = explored, explored.decision
        else:
            state.collector.record_stage(
                "exploration",
                TraceStageStatus.SKIPPED,
                reason=(
                    state.proposal_source.value
                    if state.replay
                    else "DECLINED_TO_LOOK"
                    if state.looked_first
                    else "NOT_REQUESTED"
                    if state.exploration is not None
                    else "EXPLORATION_NOT_ALLOWED"
                ),
                metadata=state.looked_first,
            )
        if state.exploration is not None:
            # Later calls (repair, empty result) still see the results, but may not explore.
            state.exploration = state.exploration.model_copy(update={"open": False})
        return None

    async def _probe_rounds(self, state: DecisionState) -> ProductionDecisionProposal | None:
        collector = state.collector
        started = perf_counter()
        authority = state.authority
        decision = state.decision
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
                    **state.looked_first,
                },
                duration_ms=(perf_counter() - started) * 1000,
            )

        while (
            decision.decision is DecisionType.EXPLORE
            and state.exploration is not None
            and state.exploration.open
        ):
            rounds += 1
            found = await asyncio.to_thread(self._run_probes, decision, authority, collector.run_id)
            reports.extend(found)
            state.exploration = Exploration(
                open=rounds < self.settings.exploration_rounds,
                results=(*state.exploration.results, *found),
            )
            try:
                proposal = await self._propose(
                    state.budget,
                    ModelCall.EXPLORATION,
                    state.question,
                    state.schema_text,
                    exploration=state.exploration,
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

    async def _check_readings(self, state: DecisionState) -> DecisionApplicationResult | None:
        """Run every reading of a clarification, and answer instead of asking when possible.

        A question whose readings give the same result on this data does not change the
        answer, so asking it only costs the user a turn.  The model writes one SQL per
        reading in one more call; each runs through every gate as a probe, and the
        results are compared in full, never shown to the model.  When they differ and
        ``ambiguity_mode`` is ``assume``, the answer takes the model's first reading,
        says so, and carries the other readings with their results for the user to
        switch to.  The clarification stands when the mode is ``ask`` and the readings
        differ, or one could not be checked.
        """
        collector, decision = state.collector, state.decision
        if state.replay:
            collector.record_stage(
                "reading_check", TraceStageStatus.SKIPPED, reason=state.proposal_source.value
            )
            return None
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
            proposal = await self._propose(
                state.budget,
                ModelCall.READING_CHECK,
                state.question,
                state.schema_text,
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
        executions = await asyncio.to_thread(
            self._run_readings, probes, state.authority, collector.run_id
        )
        if executions is None:
            finish("READING_NOT_RUN")
            return None
        metadata["row_counts"] = [execution.row_count for execution in executions]
        answer = ProductionDecision(
            case_id=PRODUCTION_DECISION_CASE_ID, decision=DecisionType.ANSWER, sql=probes[0]
        )
        compared = [comparable(execution) for execution in executions]
        if all(result == compared[0] for result in compared[1:]):
            finish("READINGS_AGREE")
            state.decision = answer
            return None
        if self.settings.ambiguity_mode == "ask":
            finish("READINGS_DIFFER")
            return None
        metadata["assumed"] = readings[0]
        finish("READINGS_DIFFER_ASSUMED")
        state.assumption = readings[0]
        state.alternatives = tuple(
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
        state.decision = answer
        return None

    # The query: admit, run, check the result, respond

    async def _run_query(self, state: DecisionState) -> DecisionApplicationResult:
        if state.decision.decision is not DecisionType.ANSWER:
            return self._non_answer(state)
        planned = await self._admit_sql(state)
        if isinstance(planned, DecisionApplicationResult):
            return planned
        attempt = await self._execute(state, planned)
        if isinstance(attempt, DecisionApplicationResult):
            return attempt
        return self._respond(state, attempt)

    async def _admit_sql(
        self, state: DecisionState
    ) -> QueryPlan | SqlPlanFailure | DecisionApplicationResult:
        """Plan the SQL; a rejection the model can fix spends the repair round."""
        planned = await self._plan(state, state.decision)
        repairable = state.budget.left(ModelCall.REPAIR) > 0
        feedback = repair_feedback(planned, state.decision, self.settings) if repairable else None
        if feedback is None:
            state.collector.record_stage(
                "repair", TraceStageStatus.SKIPPED, reason=repair_skip_reason(planned, repairable)
            )
            return planned
        repaired = await self._repair(state, feedback)
        if repaired is None:
            return planned
        state.repaired_from_sql = state.decision.sql
        state.decision = repaired
        state.collector.forget(_RUNTIME_STAGES)
        if repaired.decision is not DecisionType.ANSWER:
            return self._non_answer(state)
        return await self._plan(state, repaired)

    async def _execute(
        self, state: DecisionState, planned: QueryPlan | SqlPlanFailure
    ) -> Attempt | DecisionApplicationResult:
        """Run an admitted plan and check its result; a second attempt may take its place."""
        if isinstance(planned, SqlPlanFailure):
            state.collector.skip_unrecorded(
                (*_EXECUTION_STAGES, "result_check"), "EARLIER_RUNTIME_REJECTION"
            )
            return Attempt(state.decision, planned, None, ())
        execution = await asyncio.to_thread(self.safety_service.execute, planned)
        first = Attempt(state.decision, planned, execution, planned.sql_changes)
        second = await self._check_result(state, first)
        if second is None:
            return first
        if second.decision is not state.decision:
            state.repaired_from_sql = state.decision.sql
            state.decision = second.decision
            if second.decision.decision is not DecisionType.ANSWER:
                return self._non_answer(state)
        return second

    async def _check_result(self, state: DecisionState, attempt: Attempt) -> Attempt | None:
        """Decide whether a result gets a second attempt; None keeps it as it is."""
        collector, execution = state.collector, attempt.execution
        repairable = state.budget.left(ModelCall.REPAIR) > 0
        checking = not state.replay and self.settings.result_check_enabled
        if checking and isinstance(execution, QueryExecution):
            empty = looks_empty(execution)
            out_of_range = percent_out_of_range(execution.columns, execution.rows)
            if out_of_range and not empty:
                return await self._check_range(state, out_of_range, repairable=repairable)
            if empty:
                return await self._check_empty_result(state, repairable=repairable)
            if attempt.planned is not None and tied(execution, attempt.planned):
                collector.record_stage(
                    "result_check",
                    TraceStageStatus.PASS,
                    reason="TIED_ROWS",
                    metadata={"tied": execution.row_count},
                )
                return None
        elif (
            isinstance(execution, SqlExecutionError)
            and execution.error_class in EXECUTION_MESSAGES
            and repairable
        ):
            collector.record_stage(
                "result_check", TraceStageStatus.SKIPPED, reason="EXECUTION_ERROR"
            )
            return await self._repair_result(
                state, execution_feedback(state.decision, execution.error_class or "")
            )
        collector.record_stage("result_check", TraceStageStatus.SKIPPED, reason="NOT_NEEDED")
        return None

    async def _check_empty_result(
        self, state: DecisionState, *, repairable: bool
    ) -> Attempt | None:
        """Run once more when an empty result has a cause the runtime can name.

        A text literal the column stores in exactly one other spelling is rewritten
        and the query runs again through every check, with no model call.  Otherwise,
        if the run has not used its repair round, the model hears that the query
        returned no rows (never the rows of any query, nor the values a column
        stores) and may rewrite it or keep it.  None keeps the empty result.
        """
        collector, decision = state.collector, state.decision
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
                execution_authority=state.authority,
            )
            planned = await asyncio.to_thread(self.safety_service.plan, candidate)
            return await self._attempt(state, decision, planned, probed.changes)
        feedback = empty_result_feedback(decision, unmatched) if repairable else None
        collector.record_stage(
            "result_check",
            TraceStageStatus.PASS,
            reason="VALUE_NOT_STORED" if unmatched else "EMPTY_RESULT",
            metadata=metadata,
            duration_ms=(perf_counter() - started) * 1000,
        )
        return await self._repair_result(state, feedback)

    async def _check_range(
        self, state: DecisionState, columns: tuple[str, ...], *, repairable: bool
    ) -> Attempt | None:
        """Give the model its repair round when a percentage falls outside 0 to 100.

        The model hears which columns are out of range and in which direction, never
        the values themselves.  None keeps the result, which the user sees flagged.
        """
        state.collector.record_stage(
            "result_check",
            TraceStageStatus.PASS,
            reason="OUT_OF_RANGE",
            metadata={"columns": ", ".join(columns)},
        )
        feedback = range_feedback(state.decision, columns) if repairable else None
        return await self._repair_result(state, feedback)

    async def _repair_result(
        self, state: DecisionState, feedback: RuntimeRejectionFeedback | None
    ) -> Attempt | None:
        if feedback is None:
            return None
        state.collector.forget(("repair",))
        repaired = await self._repair(state, feedback)
        if repaired is None:
            return None
        state.collector.forget(_RUNTIME_STAGES)
        if repaired.decision is not DecisionType.ANSWER:
            return Attempt(repaired, None, None, ())
        planned = await self._plan(state, repaired)
        return await self._attempt(state, repaired, planned, ())

    async def _attempt(
        self,
        state: DecisionState,
        decision: ProductionDecision,
        planned: QueryPlan | SqlPlanFailure,
        changes: tuple[str, ...],
    ) -> Attempt:
        if isinstance(planned, SqlPlanFailure):
            state.collector.skip_unrecorded(_EXECUTION_STAGES, "EARLIER_RUNTIME_REJECTION")
            return Attempt(decision, planned, None, ())
        execution = await asyncio.to_thread(self.safety_service.execute, planned)
        return Attempt(decision, planned, execution, (*changes, *planned.sql_changes))

    def _respond(self, state: DecisionState, attempt: Attempt) -> DecisionApplicationResult:
        planned, execution = attempt.planned, attempt.execution
        runtime_outcome, runtime_reason = runtime_from_plan(planned)
        if isinstance(execution, SqlExecutionError):
            runtime_outcome, runtime_reason = "EXECUTION_ERROR", "EXECUTION_ERROR"
        elif isinstance(execution, QueryExecution):
            runtime_outcome, runtime_reason = "EXECUTED", None
        state.collector.record_stage(
            "response", TraceStageStatus.PASS, reason=runtime_reason, metadata=_calls(state.budget)
        )
        ran = execution if isinstance(execution, QueryExecution) else None
        return DecisionApplicationResult(
            model_decision=state.decision.decision,
            model_reason_code=None,
            proposed_sql=state.decision.sql,
            repaired_from_sql=state.repaired_from_sql,
            executed_sql=planned.normalized_sql if isinstance(planned, QueryPlan) else None,
            sql_changes=attempt.sql_changes if isinstance(planned, QueryPlan) else (),
            proposal_source=state.proposal_source,
            provider=state.proposal.provider,
            model=state.proposal.model,
            model_context=state.model_context,
            model_context_hash=state.model_context_hash,
            runtime_outcome=RuntimeOutcome(runtime_outcome),
            runtime_reason=runtime_reason,
            rows=ran.rows if ran else [],
            columns=ran.columns if ran else [],
            row_count=ran.row_count if ran else 0,
            truncated=ran.truncated if ran else False,
            assumption=state.assumption,
            alternatives=state.alternatives,
            trace=state.collector.finish(runtime_outcome),
        )

    def _non_answer(self, state: DecisionState) -> DecisionApplicationResult:
        decision = state.decision
        return DecisionApplicationResult(
            model_decision=decision.decision,
            model_reason_code=decision.reason_code,
            model_detail=decision.detail,
            proposal_source=state.proposal_source,
            proposed_sql=None,
            repaired_from_sql=state.repaired_from_sql,
            provider=state.proposal.provider,
            model=state.proposal.model,
            model_context=state.model_context,
            model_context_hash=state.model_context_hash,
            runtime_outcome=RuntimeOutcome.NOT_ENTERED,
            trace=_close(
                state.collector,
                state.budget,
                skip_reason="MODEL_NON_ANSWER",
                response="MODEL_NON_ANSWER",
                outcome="NOT_ENTERED",
                status=TraceStageStatus.PASS,
            ),
        )

    def _stopped(
        self, state: DecisionState, *, skip_reason: str, reason: str
    ) -> DecisionApplicationResult:
        """The model's output could not be used: no SQL ran."""
        return DecisionApplicationResult(
            proposal_source=state.proposal_source,
            provider=state.proposal.provider,
            model=state.proposal.model,
            model_context=state.model_context,
            model_context_hash=state.model_context_hash,
            runtime_outcome=RuntimeOutcome.GENERATION_ERROR,
            runtime_reason=reason,
            trace=_close(
                state.collector,
                state.budget,
                skip_reason=skip_reason,
                response=reason,
                outcome="GENERATION_ERROR",
            ),
        )

    # Model calls and SQL

    async def _propose(
        self,
        budget: AttemptBudget,
        call: ModelCall,
        question: str,
        schema_text: str,
        *,
        exploration: Exploration | None,
        repair: RuntimeRejectionFeedback | None = None,
    ) -> ProductionDecisionProposal:
        """The one way a run asks the model; refused when ``call`` has no budget left."""
        budget.take(call)
        return await self.provider.propose_decision(
            question, schema_text, repair=repair, earlier=self.earlier, exploration=exploration
        )

    async def _repair(
        self, state: DecisionState, feedback: RuntimeRejectionFeedback
    ) -> ProductionDecision | None:
        """Ask once for a replacement decision; None keeps the original rejection."""
        collector = state.collector
        started = perf_counter()
        base = {"rejected_stage": feedback.stage, "rejected_code": feedback.code}
        try:
            proposal = await self._propose(
                state.budget,
                ModelCall.REPAIR,
                state.question,
                state.schema_text,
                exploration=state.exploration,
                repair=feedback,
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
                **_tokens(proposal),
            },
            duration_ms=proposal.latency_ms,
        )
        return proposal.decision

    async def _plan(
        self, state: DecisionState, decision: ProductionDecision
    ) -> QueryPlan | SqlPlanFailure:
        # Planning and execution use blocking database drivers; keep them off the event loop.
        return await asyncio.to_thread(
            self.safety_service.plan,
            SqlCandidate(
                sql=decision.sql or "",
                source=CandidateSource.LLM,
                correlation_id=state.collector.run_id,
                execution_authority=state.authority,
            ),
        )

    def _model_view(self, context: GovernedContext) -> str:
        if self.settings.context_format == "compact":
            return render_governed_context(context)
        return serialize_governed_context(context)

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
                feedback = repair_feedback(planned, decision, self.settings)
                reports.append(
                    ProbeReport(
                        sql=sql,
                        status="REJECTED",
                        reason=feedback.code if feedback else failure_code(planned),
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
                        message=EXECUTION_MESSAGES.get(error_class or ""),
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


def _close(
    collector: RunTraceCollector,
    budget: AttemptBudget,
    *,
    skip_reason: str,
    response: str,
    outcome: str,
    status: TraceStageStatus = TraceStageStatus.FAILED,
) -> RunTrace:
    """End a run that stopped early: the stages it never reached are skipped."""
    collector.skip_unrecorded(_AFTER_CONTEXT, skip_reason)
    collector.record_stage("response", status, reason=response, metadata=_calls(budget))
    return collector.finish(outcome)


def _calls(budget: AttemptBudget) -> dict[str, object]:
    return {"model_calls": budget.spent, "model_call_limit": budget.limit}


def _tokens(proposal: ProductionDecisionProposal) -> dict[str, object]:
    return {
        "prompt_tokens": proposal.prompt_tokens,
        "completion_tokens": proposal.completion_tokens,
        "reasoning_tokens": proposal.reasoning_tokens,
        "cached_prompt_tokens": proposal.cached_prompt_tokens,
    }


# Decisions sent back once when returned before any probe ran.
_LOOK_FIRST = {DecisionType.NEEDS_CLARIFICATION, DecisionType.BLOCKED_AUTHORITY}

_AFTER_CONTEXT = tuple(
    name for name in TRACE_STAGE_ORDER if name not in {"request", "context", "response"}
)

_EXECUTION_STAGES = (
    "planning_connection",
    "explain",
    "cost_gate",
    "execution_connection",
    "execution",
)

# Recorded again when a repaired or respelled query is planned.
_RUNTIME_STAGES = (
    "sql_parse",
    "global_policy",
    "execution_authority",
    "grain_safety",
    "value_check",
    "null_ordering",
    *_EXECUTION_STAGES,
)
