"""M2.12 paired Window IR compiler evaluation.

The IR arm makes one provider request for a semantic IR and then uses only the
deterministic compiler for SQL.  It never falls back to provider SQL.
"""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, cast

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    LLMProviderError,
    ModelIOCapture,
    OpenAICompatibleProvider,
)
from app.generation.window_compiler import WindowIRValidationError, WindowSqlCompiler
from app.generation.window_ir import validate_window_ir
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.metrics import assess_query_results

ROOT = Path(__file__).resolve().parents[1]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _load(name: str) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]], json.loads((ROOT / "evaluation" / "datasets" / name).read_text())
    )


def _json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _error(error: Exception) -> dict[str, Any] | None:
    if isinstance(error, LLMProviderError) and error.detail is not None:
        return error.detail.model_dump(mode="json")
    return None


def _capture(provider: OpenAICompatibleProvider) -> dict[str, Any] | None:
    capture = provider.consume_model_io()
    return capture.model_dump(mode="json") if isinstance(capture, ModelIOCapture) else None


def _base_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": row["id"],
        "category": row["category"],
        "pattern": row["pattern"],
        "question": row["question"],
        "gold_sql": row["gold_sql"],
        "gold_window_ir": row["gold_window_ir"],
        "question_hash": _sha(row["question"]),
    }


def _gold_results(
    rows: list[dict[str, Any]], safety: SqlSafetyService
) -> dict[str, QueryExecution]:
    results: dict[str, QueryExecution] = {}
    for row in rows:
        plan = safety.plan(SqlCandidate(sql=row["gold_sql"]))
        if not isinstance(plan, QueryPlan):
            raise RuntimeError(f"gold plan failed for {row['id']}: {plan}")
        result = safety.execute(plan)
        if not isinstance(result, QueryExecution):
            raise RuntimeError(f"gold execution failed for {row['id']}: {result}")
        results[row["id"]] = result
    return results


def _compare(actual: QueryExecution | None, gold: QueryExecution) -> tuple[bool | None, str | None]:
    if actual is None:
        return None, None
    comparison = assess_query_results(actual, gold, order_sensitive=False)
    return comparison.equivalent, comparison.diagnostic.value if comparison.diagnostic else None


def _baseline_record(
    row: dict[str, Any], result: Any, gold: QueryExecution, capture: dict[str, Any] | None
) -> dict[str, Any]:
    record = _base_record(row)
    proposal = result.proposal
    record.update(
        {
            "arm": "BASELINE_ONE_SHOT",
            "generated_sql": proposal.sql if proposal else None,
            "provider": result.provider,
            "model": result.model,
            "provider_calls_attempted": result.provider_calls_attempted,
            "provider_calls_succeeded": result.provider_calls_succeeded,
            "provider_calls_failed": result.provider_calls_failed,
            "prompt_tokens": proposal.prompt_tokens if proposal else None,
            "completion_tokens": proposal.completion_tokens if proposal else None,
            "reasoning_tokens": proposal.reasoning_tokens if proposal else None,
            "cached_prompt_tokens": proposal.cached_prompt_tokens if proposal else None,
            "generation_latency_ms": result.generation_latency_ms,
            "parse_success": result.candidate is not None
            and result.failure_stage is not FailureStage.SQL_PARSE_ERROR,
            "plan_accepted": isinstance(result.plan, QueryPlan),
            "execution_success": isinstance(result.execution, QueryExecution),
            "failure_stage": result.failure_stage.value if result.failure_stage else None,
            "policy_rejection": result.plan_failure.rejection.model_dump(mode="json")
            if result.plan_failure and result.plan_failure.rejection
            else None,
            "provider_error": result.provider_error.model_dump(mode="json")
            if result.provider_error
            else None,
            "execution": result.execution.model_dump(mode="json") if result.execution else None,
            "plan": result.plan.model_dump(mode="json") if result.plan else None,
            "model_io": capture,
        }
    )
    record["result_equivalent"], record["equivalence_diagnostic"] = _compare(result.execution, gold)
    return record


async def _run(rows: list[dict[str, Any]], run_dir: Path) -> dict[str, Any]:
    base_settings = get_settings()
    settings = base_settings.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    resolver = SchemaContextResolver(safety.catalog)
    provider = OpenAICompatibleProvider(settings)
    baseline_service = TextToSqlService(
        resolver,
        provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=serialize_schema_context,
    )
    compiler = WindowSqlCompiler(safety.catalog)
    gold = _gold_results(rows, safety)
    baseline: list[dict[str, Any]] = []
    ir_records: list[dict[str, Any]] = []
    io_records: list[dict[str, Any]] = []
    for row in rows:
        result = await baseline_service.run(
            TextToSqlRequest(question=row["question"], correlation_id=row["id"], execute=True)
        )
        baseline_capture = _capture(provider)
        baseline.append(_baseline_record(row, result, gold[row["id"]], baseline_capture))
        if baseline_capture is not None:
            io_records.append(baseline_capture)

        record = _base_record(row)
        record["arm"] = "WINDOW_IR_COMPILER"
        record["provider_calls_attempted"] = 1
        record["provider_calls_succeeded"] = 0
        record["provider_calls_failed"] = 0
        record["schema_hash"] = None
        record["prompt_hash"] = None
        record["ir"] = None
        record["generated_sql"] = None
        record["compiled_sql"] = None
        record["model_io"] = None
        record["plan"] = None
        record["execution"] = None
        record["result_equivalent"] = None
        record["parse_success"] = False
        record["plan_accepted"] = False
        record["execution_success"] = False
        record["failure_stage"] = None
        record["provider_error"] = None
        try:
            context = resolver.resolve(row["question"], SchemaContextMode.FULL_COMPACT)
            schema_text = serialize_schema_context(context)
            record["schema_hash"] = _sha(schema_text)
            proposal = await provider.propose_window_ir(row["question"], schema_text)
            record["provider_calls_succeeded"] = 1
            record.update(
                {
                    "model": proposal.model,
                    "provider": proposal.provider,
                    "prompt_tokens": proposal.prompt_tokens,
                    "completion_tokens": proposal.completion_tokens,
                    "reasoning_tokens": proposal.reasoning_tokens,
                    "cached_prompt_tokens": proposal.cached_prompt_tokens,
                    "generation_latency_ms": proposal.latency_ms,
                    "ir": proposal.ir.model_dump(mode="json"),
                    "parse_success": True,
                }
            )
            capture = _capture(provider)
            record["model_io"] = capture
            if capture is not None:
                io_records.append(capture)
            validate_window_ir(proposal.ir, safety.catalog)
            compiled_sql = compiler.compile(proposal.ir)
            record["compiled_sql"] = compiled_sql
            candidate = SqlCandidate(
                sql=compiled_sql,
                source="window_compiler",
                correlation_id=row["id"],
            )
            planned = safety.plan(candidate)
            if isinstance(planned, SqlPlanFailure):
                record["failure_stage"] = (
                    planned.failure_stage.value if planned.failure_stage else planned.status.value
                )
                record["policy_rejection"] = (
                    planned.rejection.model_dump(mode="json") if planned.rejection else None
                )
            else:
                record["plan"] = planned.model_dump(mode="json")
                record["plan_accepted"] = True
                executed = safety.execute(planned)
                if isinstance(executed, QueryExecution):
                    record["execution"] = executed.model_dump(mode="json")
                    record["execution_success"] = True
                    record["result_equivalent"], record["equivalence_diagnostic"] = _compare(
                        executed, gold[row["id"]]
                    )
                else:
                    record["failure_stage"] = FailureStage.EXECUTION_ERROR.value
        except WindowIRValidationError as error:
            record["failure_stage"] = "IR_VALIDATION_ERROR"
            record["error"] = str(error)
        except (ValueError, TypeError) as error:
            record["failure_stage"] = "IR_VALIDATION_ERROR"
            record["error"] = str(error)
        except Exception as error:
            record["provider_calls_failed"] = 1
            record["failure_stage"] = "IR_GENERATION_ERROR"
            record["error"] = type(error).__name__
            record["provider_error"] = _error(error)
            capture = _capture(provider)
            record["model_io"] = capture
            if capture is not None:
                io_records.append(capture)
        ir_records.append(record)

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "metadata.json").write_text(
        json.dumps(
            {
                "milestone": "M2.12",
                "stage": "dev",
                "run_id": run_dir.name,
                "timestamp": datetime.now(UTC).isoformat(),
                "dataset_sha256": _sha(
                    (ROOT / "evaluation/datasets/m212_window_dev.json").read_text()
                ),
                "dataset_size": len(rows),
                "model": "gpt-5.6-luna",
                "reasoning_effort": "none",
                "temperature": None,
                "sampling_mode": "provider_default",
                "endpoint_family": "chat_completions",
                "schema_mode": "FULL_COMPACT",
                "baseline_calls": len(rows),
                "window_ir_calls": len(rows),
                "gold_validation": "100% before provider calls",
            },
            indent=2,
        )
        + "\n"
    )
    _write_json(run_dir / "baseline.json", baseline)
    _write_json(run_dir / "window_ir.json", ir_records)
    _write_jsonl(run_dir / "model_io.jsonl", io_records)
    comparison = _comparison(baseline, ir_records)
    _write_json(run_dir / "comparison.json", comparison)
    _write_json(run_dir / "pattern_analysis.json", _pattern_analysis(baseline, ir_records))
    _write_json(run_dir / "failure_analysis.json", _failure_analysis(baseline, ir_records))
    print(json.dumps(comparison, indent=2))
    return comparison


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _write_jsonl(path: Path, values: list[Any]) -> None:
    path.write_text("".join(json.dumps(value, default=str) + "\n" for value in values))


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    return {
        "total": total,
        "correct": sum(record.get("result_equivalent") is True for record in records),
        "result_equivalence_rate": sum(
            record.get("result_equivalent") is True for record in records
        )
        / total
        if total
        else 0,
        "parse_success": sum(record.get("parse_success") is True for record in records),
        "plan_acceptance": sum(record.get("plan_accepted") is True for record in records),
        "execution_success": sum(record.get("execution_success") is True for record in records),
        "policy_rejections": sum(
            record.get("failure_stage") == "POLICY_REJECTION" for record in records
        ),
        "provider_failures": sum(record.get("provider_calls_failed", 0) > 0 for record in records),
        "input_tokens": sum(record.get("prompt_tokens") or 0 for record in records),
        "output_tokens": sum(record.get("completion_tokens") or 0 for record in records),
        "reasoning_tokens": sum(record.get("reasoning_tokens") or 0 for record in records),
        "avg_latency_ms": mean(
            [
                record["generation_latency_ms"]
                for record in records
                if record.get("generation_latency_ms") is not None
            ]
        )
        if any(record.get("generation_latency_ms") is not None for record in records)
        else None,
    }


def _comparison(baseline: list[dict[str, Any]], ir: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {record["question_id"]: record for record in ir}
    pair: Counter[str] = Counter()
    for record in baseline:
        left = record.get("result_equivalent") is True
        right = by_id[record["question_id"]].get("result_equivalent") is True
        pair[
            "BOTH_CORRECT"
            if left and right
            else "BASELINE_ONLY_CORRECT"
            if left
            else "WINDOW_IR_ONLY_CORRECT"
            if right
            else "BOTH_INCORRECT"
        ] += 1
    return {
        "baseline": _summary(baseline),
        "window_ir": _summary(ir),
        "paired_outcomes": dict(pair),
    }


def _pattern_analysis(baseline: list[dict[str, Any]], ir: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for pattern in sorted({record["pattern"] for record in baseline}):
        left = [record for record in baseline if record["pattern"] == pattern]
        right = [record for record in ir if record["pattern"] == pattern]
        output[pattern] = {"baseline": _summary(left), "window_ir": _summary(right)}
    return output


def _failure_analysis(baseline: list[dict[str, Any]], ir: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "baseline_failure_stages": dict(
            Counter(
                record.get("failure_stage") for record in baseline if record.get("failure_stage")
            )
        ),
        "window_ir_failure_stages": dict(
            Counter(record.get("failure_stage") for record in ir if record.get("failure_stage"))
        ),
        "ir_structured_response_failures": sum(
            record.get("failure_stage") == "IR_GENERATION_ERROR" for record in ir
        ),
        "ir_validation_failures": sum(
            record.get("failure_stage") == "IR_VALIDATION_ERROR" for record in ir
        ),
        "compiler_errors": sum(record.get("failure_stage") == "COMPILER_ERROR" for record in ir),
        "policy_rejections": sum(
            record.get("failure_stage") == "POLICY_REJECTION" for record in ir
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    if args.stage == "holdout":
        path = ROOT / "evaluation/datasets/m212_window_holdout.json"
    else:
        path = ROOT / "evaluation/datasets/m212_window_dev.json"
    rows = json.loads(path.read_text())
    run_root = ROOT / "evaluation/results/m212" / args.stage
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    asyncio.run(_run(rows, run_root / run_id))


if __name__ == "__main__":
    main()
