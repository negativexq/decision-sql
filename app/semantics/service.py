"""Explicit SEMANTIC runtime mode for the canonical query engine."""

from collections.abc import Callable
from time import perf_counter

from app.generation.provider import LLMProvider, LLMProviderError
from app.models.domain import FailureStage, TextToSqlRequest
from app.provenance.canonical import semantic_hash, text_hash
from app.semantics.semantic_compiler import SemanticQueryCompiler
from app.semantics.semantic_errors import (
    SemanticFailureCode,
    SemanticValidationError,
)
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import SemanticQueryIR, SemanticQueryProvenance, plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator
from app.sql.models import CandidateSource, SqlCandidate, SqlExecutionError, SqlPlanFailure
from app.sql.service import SqlSafetyService
from app.text_to_sql.models import GenerationPath, TextToSqlResult, TextToSqlStatus


class SemanticQueryService:
    """Run one typed semantic proposal through deterministic server-owned stages.

    This service is only entered through explicit ``ExecutionMode.SEMANTIC``;
    it never falls back to raw DIRECT SQL generation.
    """

    def __init__(
        self,
        provider: LLMProvider,
        safety_service: SqlSafetyService,
        schema_context: Callable[[str], str],
        database_id: str = "schema",
    ) -> None:
        self.provider = provider
        self.safety_service = safety_service
        self.schema_context = schema_context
        self.database_id = database_id

    async def run(self, request: TextToSqlRequest) -> TextToSqlResult:
        started = perf_counter()
        provider_attempted = False
        try:
            context = self.schema_context(request.question)
            provider_attempted = True
            proposal = await self.provider.propose_semantic_query_plan(request.question, context)
            if proposal.plan.database_id != self.database_id:
                raise SemanticValidationError(
                    code=SemanticFailureCode.INVALID_POPULATION_CONTRACT,
                    message="semantic plan database_id does not match the server context",
                )
            mapping = SemanticMappingSnapshot.from_schema(self.safety_service.catalog)
            plan = proposal.plan
            ir: SemanticQueryIR = plan_to_ir(plan)
            compiler = SemanticQueryCompiler(mapping)
            compiled = compiler.compile(ir)
            validation = SemanticConsistencyValidator(mapping).validate(ir, compiled)
            metadata = SemanticQueryProvenance(
                mapping_hash=mapping.content_hash,
                plan_hash=semantic_hash(plan.model_dump(mode="json")),
                ir_hash=semantic_hash(ir.model_dump(mode="json")),
                compiler_version=compiled.compiler_version,
                compiled_sql_hash=text_hash(compiled.sql),
                relationship_ids=tuple(item.relationship_id for item in ir.joins),
                semantic_validation="PASS" if validation.accepted else "FAIL",
            )
            base = {
                "correlation_id": request.correlation_id,
                "provider": proposal.provider,
                "model": proposal.model,
                "semantic_plan": plan,
                "semantic_ir": ir,
                "semantic_provenance": metadata,
                "generation_latency_ms": proposal.latency_ms,
                "provider_calls_attempted": 1,
                "provider_calls_succeeded": 1,
                "provider_calls_failed": 0,
                "generation_path": GenerationPath.SEMANTIC_QUERY_COMPILER,
                "diagnostics": {"semantic_compiler_version": compiled.compiler_version},
            }
            if not validation.accepted:
                return TextToSqlResult(
                    status=TextToSqlStatus.SEMANTIC_CONSISTENCY_ERROR,
                    failure_stage=FailureStage.SEMANTIC_RESOLUTION_ERROR,
                    error="; ".join(message for _, message in validation.failures),
                    **base,
                )
            candidate = SqlCandidate(
                sql=compiled.sql,
                source=CandidateSource.SEMANTIC_QUERY_COMPILER,
                correlation_id=request.correlation_id,
            )
            planned = self.safety_service.plan(candidate)
            if isinstance(planned, SqlPlanFailure):
                return TextToSqlResult(
                    status=TextToSqlStatus.SEMANTIC_PLAN_REJECTED,
                    failure_stage=planned.failure_stage,
                    error=planned.error,
                    candidate=candidate,
                    plan_failure=planned,
                    **base,
                )
            if not request.execute:
                return TextToSqlResult(
                    status=TextToSqlStatus.PLANNED,
                    candidate=candidate,
                    plan=planned,
                    **base,
                )
            execution = self.safety_service.execute(planned)
            if isinstance(execution, SqlExecutionError):
                return TextToSqlResult(
                    status=TextToSqlStatus.EXECUTION_ERROR,
                    failure_stage=FailureStage.EXECUTION_ERROR,
                    error=execution.error,
                    candidate=candidate,
                    plan=planned,
                    execution_error=execution,
                    **base,
                )
            return TextToSqlResult(
                status=TextToSqlStatus.SUCCEEDED,
                candidate=candidate,
                plan=planned,
                execution=execution,
                diagnostics={
                    "semantic_total_latency_ms": (perf_counter() - started) * 1000,
                    "semantic_compiler_version": compiled.compiler_version,
                },
                **{key: value for key, value in base.items() if key != "diagnostics"},
            )
        except Exception as error:
            detail = error.detail if isinstance(error, LLMProviderError) else None
            if isinstance(error, (LLMProviderError, NotImplementedError)):
                status = TextToSqlStatus.SEMANTIC_PLAN_GENERATION_ERROR
            elif isinstance(error, SemanticValidationError):
                status = TextToSqlStatus.SEMANTIC_PLAN_REJECTED
            else:
                status = TextToSqlStatus.SEMANTIC_COMPILATION_ERROR
            stage = (
                FailureStage.SQL_GENERATION_ERROR
                if status is TextToSqlStatus.SEMANTIC_PLAN_GENERATION_ERROR
                else FailureStage.SEMANTIC_RESOLUTION_ERROR
            )
            return TextToSqlResult(
                status=status,
                correlation_id=request.correlation_id,
                failure_stage=stage,
                error=str(error),
                provider_error=detail,
                provider_calls_attempted=1
                if provider_attempted or isinstance(error, LLMProviderError)
                else 0,
                provider_calls_failed=1 if isinstance(error, LLMProviderError) else 0,
            )
