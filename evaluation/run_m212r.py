"""M2.12R: minimal pattern-specific plain-JSON Window IR rerun."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    LLMProviderError,
    ModelIOCapture,
    OpenAICompatibleProvider,
)
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR, validate_window_ir
from app.generation.window_minimal_adapter import MinimalWindowProviderAdapter
from app.generation.window_provider_patterns import PATTERN_DTOS, MinimalProviderDTO
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.metrics import assess_query_results

ROOT = Path(__file__).resolve().parents[1]
DEV_PATH = ROOT / "evaluation/datasets/m212_window_dev.json"
HOLDOUT_PATH = ROOT / "evaluation/datasets/m212_window_holdout.json"
DEV_SHA = "bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3"
HOLDOUT_SHA = "e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4"
FAMILY_MAP = {
    "LATEST_PER_GROUP": "LATEST_PER_GROUP",
    "TOP_N_PER_GROUP": "TOP_N_PER_GROUP",
    "LAG": "LAG_LEAD",
    "LEAD": "LAG_LEAD",
    "RUNNING_AGGREGATE": "RUNNING_AGGREGATE",
    "MOVING_AGGREGATE": "MOVING_AGGREGATE",
    "RANKING": "RANKING",
    "SHARE_OF_TOTAL": "SHARE_OF_TOTAL",
    "MIXED_MULTI_WINDOW": "MIXED_MULTI_WINDOW",
}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _load(path: Path) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(path.read_text()))


def _settings() -> Any:
    return get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )


def _capture(provider: OpenAICompatibleProvider) -> dict[str, Any] | None:
    capture = provider.consume_model_io()
    return capture.model_dump(mode="json") if isinstance(capture, ModelIOCapture) else None


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _write_jsonl(path: Path, values: list[Any]) -> None:
    path.write_text("".join(json.dumps(value, default=str) + "\n" for value in values))


def _compare(actual: QueryExecution | None, expected: QueryExecution) -> bool | None:
    if actual is None:
        return None
    return assess_query_results(actual, expected, order_sensitive=False).equivalent


def _validate_gold(
    rows: list[dict[str, Any]], safety: SqlSafetyService, compiler: WindowSqlCompiler
) -> dict[str, Any]:
    gold_results: dict[str, QueryExecution] = {}
    records: list[dict[str, Any]] = []
    for row in rows:
        gold_plan = safety.plan(SqlCandidate(sql=row["gold_sql"], source=CandidateSource.INTERNAL))
        if not isinstance(gold_plan, QueryPlan):
            raise RuntimeError(f"gold SQL plan failed: {row['id']}")
        gold_execution = safety.execute(gold_plan)
        if not isinstance(gold_execution, QueryExecution):
            raise RuntimeError(f"gold SQL execution failed: {row['id']}")
        gold_results[row["id"]] = gold_execution
        gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
        validate_window_ir(gold_ir, safety.catalog)
        compiled_sql = compiler.compile(gold_ir)
        compiled_plan = safety.plan(
            SqlCandidate(sql=compiled_sql, source=CandidateSource.WINDOW_COMPILER)
        )
        if not isinstance(compiled_plan, QueryPlan):
            raise RuntimeError(f"gold compiler plan failed: {row['id']}")
        compiled_execution = safety.execute(compiled_plan)
        if not isinstance(compiled_execution, QueryExecution):
            raise RuntimeError(f"gold compiler execution failed: {row['id']}")
        equivalent = _compare(compiled_execution, gold_execution)
        if equivalent is not True:
            raise RuntimeError(f"gold compiler result mismatch: {row['id']}")
        records.append(
            {
                "question_id": row["id"],
                "ir_validation": True,
                "compiler_success": True,
                "m1_plan_success": True,
                "execution_success": True,
                "result_equivalent": True,
            }
        )
    return {
        "questions": len(rows),
        "ir_validation": len(records),
        "compiler_success": len(records),
        "m1_plan_success": len(records),
        "execution_success": len(records),
        "result_equivalent": len(records),
        "records": records,
        "gold_results": gold_results,
    }


def _field_inventory() -> dict[str, Any]:
    reasons: dict[str, str] = {
        "source": "model must select the single source relation",
        "outputs": "model must select requested physical outputs",
        "partition_by": "model must select the window partition",
        "order_by": "model must select the window order column(s)",
        "direction": "model must select ordering direction",
        "target": "model must select the computed target",
        "function": "model must select LAG/LEAD or ranking function where applicable",
        "aggregate": "model must select the window aggregate",
        "offset": "model must select LAG/LEAD offset",
        "n": "model must select top-N bound",
        "tie_policy": "model must select exact-N versus ties semantics",
        "tie_breaker": "model must select deterministic tie-breaking column where needed",
        "tie_breaker_direction": "model must select tie-breaking direction where needed",
        "preceding": "model must select moving-frame width",
        "include_current": "model must select moving-frame endpoint semantics",
        "scale": "model must select requested share scale",
        "operations": "model must select the bounded mixed computations",
    }
    result: dict[str, Any] = {}
    for name, model in PATTERN_DTOS.items():
        fields = []
        for field_name, _field in model.model_fields.items():
            fields.append(
                {
                    "field": field_name,
                    "why_model_chooses": reasons.get(field_name, "pattern-specific semantic slot"),
                    "compiler_can_own": field_name in {"tie_breaker_direction"},
                    "adapter_can_derive": field_name in {"source"},
                    "removed_from_m2121_generic": field_name
                    in {
                        "alias",
                        "frame_mode",
                        "frame_start_kind",
                        "frame_start_value",
                        "frame_end_kind",
                        "frame_end_value",
                        "ranking_function",
                    },
                }
            )
        schema_text = json.dumps(model.model_json_schema(), sort_keys=True, separators=(",", ":"))
        result[name] = {
            "field_count": len(fields),
            "fields": fields,
            "schema_sha256": _sha(schema_text),
            "schema_version": "m212r-v1",
            "schema": model.model_json_schema(),
        }
    return result


def _dto_prompt(family: str, model: type[BaseModel]) -> str:
    keys = ", ".join(model.model_fields)
    schema = json.dumps(model.model_json_schema(), sort_keys=True)
    return (
        f"Map the question to the {family} provider DTO. Return only one JSON object, no markdown, "
        f"no explanation, no SQL, and no SQL fragments. Use exactly these keys: {keys}. "
        "Use unqualified column names only when they belong to the selected source table. "
        "Do not add aliases; aliases are compiler-owned. The original question remains the "
        "authority for requested outputs. DTO schema:\n" + schema
    )


def _family_model(pattern: str) -> type[BaseModel]:
    return PATTERN_DTOS[pattern]


def _semantic_parts(candidate: WindowQueryIR, gold: WindowQueryIR) -> dict[str, bool]:
    def strip_alias(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: strip_alias(item) for key, item in value.items() if key != "alias"}
        if isinstance(value, list):
            return [strip_alias(item) for item in value]
        return value

    candidate_data = strip_alias(candidate.model_dump(mode="json"))
    gold_data = strip_alias(gold.model_dump(mode="json"))
    candidate_comps = candidate_data.pop("computations")
    gold_comps = gold_data.pop("computations")
    parts = {
        "pattern": candidate_data["pattern"] == gold_data["pattern"],
        "source_relation": candidate_data["source_relation"] == gold_data["source_relation"],
        # Result equivalence is ordinal for columns.  Keep the diagnostic
        # matcher aligned with that contract; a set comparison would label a
        # projection-order error as a gold IR match.
        "physical_outputs": candidate_data["physical_outputs"]
        == gold_data["physical_outputs"],
    }
    if len(candidate_comps) != len(gold_comps):
        parts["computations"] = False
        return parts
    for index, (candidate_comp, gold_comp) in enumerate(
        zip(candidate_comps, gold_comps, strict=True)
    ):
        for key, value in gold_comp.items():
            parts[f"computation_{index}_{key}"] = candidate_comp.get(key) == value
    parts["computations"] = candidate_comps == gold_comps
    return parts


def _semantic_failure(parts: dict[str, bool]) -> str:
    if not parts.get("source_relation", True):
        return "SOURCE_RELATION_ERROR"
    if not parts.get("physical_outputs", True):
        return "OUTPUT_GROUNDING_ERROR"
    keys = " ".join(parts)
    if "target" in keys and not all(value for key, value in parts.items() if "target" in key):
        return "TARGET_GROUNDING_ERROR"
    if "partition_by" in keys and not all(
        value for key, value in parts.items() if "partition_by" in key
    ):
        return "PARTITION_GROUNDING_ERROR"
    if "order_by" in keys and not all(value for key, value in parts.items() if "order_by" in key):
        return "ORDER_GROUNDING_ERROR"
    if "direction" in keys and not all(value for key, value in parts.items() if "direction" in key):
        return "DIRECTION_ERROR"
    if any(key.endswith(("offset", "n", "scale")) and not value for key, value in parts.items()):
        return "PARAMETER_ERROR"
    if "frame" in keys and not all(value for key, value in parts.items() if "frame" in key):
        return "FRAME_ERROR"
    if "function" in keys and not all(value for key, value in parts.items() if "function" in key):
        return "FUNCTION_ERROR"
    if "computations" in parts and not parts["computations"]:
        return "MULTI_WINDOW_ERROR"
    return "RESULT_MISMATCH_AFTER_CORRECT_IR"


async def _run(rows: list[dict[str, Any]], run_dir: Path, stage: str) -> dict[str, Any]:
    settings = _settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    resolver = SchemaContextResolver(safety.catalog)
    provider = OpenAICompatibleProvider(settings)
    compiler = WindowSqlCompiler(safety.catalog)
    gold = _validate_gold(rows, safety, compiler)
    field_inventory = _field_inventory()
    schema_by_id: dict[str, str] = {}
    for row in rows:
        schema_by_id[row["id"]] = serialize_schema_context(
            resolver.resolve(row["question"], SchemaContextMode.FULL_COMPACT)
        )
    run_dir.mkdir(parents=True, exist_ok=False)
    dataset_path = DEV_PATH if stage == "dev" else HOLDOUT_PATH
    dataset_sha = _sha(dataset_path.read_text())
    expected_sha = DEV_SHA if stage == "dev" else HOLDOUT_SHA
    if dataset_sha != expected_sha:
        raise RuntimeError(f"dataset SHA mismatch before provider calls: {dataset_sha}")
    _write(run_dir / "provider_field_inventory.json", field_inventory)
    _write(
        run_dir / "provider_schema_hashes.json",
        {name: details["schema_sha256"] for name, details in field_inventory.items()},
    )
    _write(
        run_dir / "gold_validation.json",
        {key: value for key, value in gold.items() if key != "gold_results"},
    )
    _write(
        run_dir / "metadata.json",
        {
            "milestone": "M2.12R",
            "stage": stage,
            "run_id": run_dir.name,
            "timestamp": datetime.now(UTC).isoformat(),
            "dataset_sha256": dataset_sha,
            "dataset_size": len(rows),
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "temperature": None,
            "sampling_mode": "provider_default",
            "schema_mode": "FULL_COMPACT",
            "transport": "pattern_specific_plain_json",
            "one_provider_call_per_question_per_arm": True,
            "precommitted_screening": {
                "minimum_delta_points": 10,
                "minimum_transport_rate": 0.95,
                "window_ir_only_correct_gt_baseline_only": True,
                "families_improved_minimum": 4,
            },
            "holdout_used": stage == "holdout",
            "m212_holdout_sha256": HOLDOUT_SHA,
        },
    )
    baseline_service = TextToSqlService(
        resolver,
        provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=serialize_schema_context,
    )
    adapter = MinimalWindowProviderAdapter(safety.catalog)
    baseline: list[dict[str, Any]] = []
    ir_records: list[dict[str, Any]] = []
    io_records: list[dict[str, Any]] = []
    for row in rows:
        baseline_result = await baseline_service.run(
            TextToSqlRequest(question=row["question"], correlation_id=row["id"], execute=True)
        )
        capture = _capture(provider)
        if capture is not None:
            io_records.append(capture)
        proposal = baseline_result.proposal
        baseline_record = {
            "question_id": row["id"],
            "family": FAMILY_MAP[row["pattern"]],
            "question": row["question"],
            "arm": "BASELINE_ONE_SHOT",
            "generated_sql": proposal.sql if proposal else None,
            "provider_calls_attempted": baseline_result.provider_calls_attempted,
            "provider_calls_succeeded": baseline_result.provider_calls_succeeded,
            "provider_calls_failed": baseline_result.provider_calls_failed,
            "prompt_tokens": proposal.prompt_tokens if proposal else None,
            "completion_tokens": proposal.completion_tokens if proposal else None,
            "reasoning_tokens": proposal.reasoning_tokens if proposal else None,
            "generation_latency_ms": baseline_result.generation_latency_ms,
            "schema_hash": _sha(schema_by_id[row["id"]]),
            "question_hash": _sha(row["question"]),
            "parse_success": baseline_result.candidate is not None,
            "plan_accepted": isinstance(baseline_result.plan, QueryPlan),
            "execution_success": isinstance(baseline_result.execution, QueryExecution),
            "failure_stage": baseline_result.failure_stage.value
            if baseline_result.failure_stage
            else None,
            "policy_rejection": baseline_result.plan_failure.rejection.model_dump(mode="json")
            if baseline_result.plan_failure and baseline_result.plan_failure.rejection
            else None,
            "result_equivalent": _compare(
                baseline_result.execution, gold["gold_results"][row["id"]]
            ),
            "model_io": capture,
        }
        baseline.append(baseline_record)

        record: dict[str, Any] = {
            "question_id": row["id"],
            "family": FAMILY_MAP[row["pattern"]],
            "question": row["question"],
            "arm": "MINIMAL_JSON_WINDOW_IR",
            "question_hash": _sha(row["question"]),
            "schema_hash": _sha(schema_by_id[row["id"]]),
            "provider_calls_attempted": 1,
            "provider_calls_succeeded": 0,
            "provider_calls_failed": 0,
            "json_parse_success": False,
            "dto_validation_success": False,
            "catalog_qualification_success": False,
            "adapter_conversion_success": False,
            "internal_ir_validation_success": False,
            "ir": None,
            "compiled_sql": None,
            "plan_accepted": False,
            "execution_success": False,
            "result_equivalent": None,
            "model_ir_matches_gold": None,
            "failure_taxonomy": None,
            "model_io": None,
        }
        try:
            dto_model = _family_model(row["pattern"])
            instruction = _dto_prompt(FAMILY_MAP[row["pattern"]], dto_model)
            response = await provider.propose_window_transport(
                row["question"],
                schema_by_id[row["id"]],
                instruction,
                response_format={"type": "json_object"},
                operation="m212r_ir",
            )
            record["provider_calls_succeeded"] = 1
            record.update(
                {
                    "provider": response.provider,
                    "model": response.model,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    "reasoning_tokens": response.reasoning_tokens,
                    "generation_latency_ms": response.latency_ms,
                    "raw_json": response.content,
                }
            )
            try:
                data = json.loads(response.content or "")
                if not isinstance(data, dict):
                    raise ValueError("provider JSON must be an object")
                record["json_parse_success"] = True
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                record["failure_taxonomy"] = "JSON_DECODE_FAILED"
                record["error"] = str(error)
                record["model_io"] = _capture(provider)
                if record["model_io"] is not None:
                    io_records.append(record["model_io"])
                ir_records.append(record)
                continue
            try:
                dto = cast(MinimalProviderDTO, dto_model.model_validate(data))
                record["dto_validation_success"] = True
            except ValidationError as error:
                record["failure_taxonomy"] = "DTO_VALIDATION_FAILED"
                record["error"] = str(error)
                record["model_io"] = _capture(provider)
                if record["model_io"] is not None:
                    io_records.append(record["model_io"])
                ir_records.append(record)
                continue
            try:
                ir = adapter.convert(dto)
                # The adapter performs catalog qualification before producing
                # the compiler-facing IR.  Keep this metric conservative: a
                # DTO that cannot be converted has not reached a usable
                # qualified representation.
                record["catalog_qualification_success"] = True
                record["adapter_conversion_success"] = True
                record["internal_ir_validation_success"] = True
                record["ir"] = ir.model_dump(mode="json")
            except ValueError as error:
                message = str(error)
                record["failure_taxonomy"] = (
                    "COLUMN_RESOLUTION_FAILED"
                    if "column" in message or "source relation" in message
                    else "ADAPTER_FAILED"
                )
                record["error"] = message
                record["model_io"] = _capture(provider)
                if record["model_io"] is not None:
                    io_records.append(record["model_io"])
                ir_records.append(record)
                continue
            gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
            parts = _semantic_parts(ir, gold_ir)
            record["semantic_parts"] = parts
            record["model_ir_matches_gold"] = all(parts.values())
            compiled_sql = compiler.compile(ir)
            record["compiled_sql"] = compiled_sql
            planned = safety.plan(
                SqlCandidate(
                    sql=compiled_sql,
                    source=CandidateSource.WINDOW_COMPILER,
                    correlation_id=row["id"],
                )
            )
            if isinstance(planned, SqlPlanFailure):
                record["failure_taxonomy"] = "POLICY_REJECTION"
                record["policy_rejection"] = (
                    planned.rejection.model_dump(mode="json") if planned.rejection else None
                )
            else:
                record["plan_accepted"] = True
                executed = safety.execute(planned)
                if isinstance(executed, QueryExecution):
                    record["execution_success"] = True
                    record["result_equivalent"] = _compare(
                        executed, gold["gold_results"][row["id"]]
                    )
                    if record["result_equivalent"] is not True:
                        record["failure_taxonomy"] = _semantic_failure(parts)
                else:
                    record["failure_taxonomy"] = "EXECUTION_ERROR"
        except LLMProviderError as error:
            record["provider_calls_failed"] = 1
            record["failure_taxonomy"] = "PROVIDER_FAILURE"
            record["provider_error"] = (
                error.detail.model_dump(mode="json") if error.detail else None
            )
        except Exception as error:
            record["failure_taxonomy"] = "INTERNAL_IR_VALIDATION_FAILED"
            record["error"] = type(error).__name__ + ": " + str(error)
        finally:
            capture = _capture(provider)
            if capture is not None:
                record["model_io"] = capture
                io_records.append(capture)
        ir_records.append(record)

    comparison = _comparison(baseline, ir_records)
    _write(run_dir / "baseline.json", baseline)
    _write(run_dir / "window_ir.json", ir_records)
    _write(run_dir / "comparison.json", comparison)
    _write(run_dir / "transport_analysis.json", _transport_analysis(ir_records))
    _write(run_dir / "ir_quality.json", _ir_quality(ir_records))
    _write(run_dir / "pattern_analysis.json", _pattern_analysis(baseline, ir_records))
    _write(run_dir / "failure_analysis.json", _failure_analysis(ir_records))
    _write_jsonl(run_dir / "model_io.jsonl", io_records)
    print(json.dumps(comparison, indent=2))
    return comparison


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    parse_key = (
        "json_parse_success"
        if any("json_parse_success" in record for record in records)
        else "parse_success"
    )
    latencies = [
        record["generation_latency_ms"]
        for record in records
        if record.get("generation_latency_ms") is not None
    ]
    return {
        "total": len(records),
        "correct": sum(record.get("result_equivalent") is True for record in records),
        "result_equivalence_rate": sum(
            record.get("result_equivalent") is True for record in records
        )
        / len(records)
        if records
        else 0,
        "parse_success": sum(record.get(parse_key) is True for record in records),
        "plan_acceptance": sum(record.get("plan_accepted") is True for record in records),
        "execution_success": sum(record.get("execution_success") is True for record in records),
        "provider_failures": sum(record.get("provider_calls_failed", 0) > 0 for record in records),
        "policy_rejections": sum(
            record.get("failure_stage") == "POLICY_REJECTION"
            or record.get("failure_taxonomy") == "POLICY_REJECTION"
            for record in records
        ),
        "input_tokens": sum(record.get("prompt_tokens") or 0 for record in records),
        "output_tokens": sum(record.get("completion_tokens") or 0 for record in records),
        "avg_latency_ms": mean(latencies) if latencies else None,
        "p50_latency_ms": median(latencies) if latencies else None,
        "p95_latency_ms": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)]
        if latencies
        else None,
    }


def _comparison(baseline: list[dict[str, Any]], ir: list[dict[str, Any]]) -> dict[str, Any]:
    ir_by_id = {record["question_id"]: record for record in ir}
    pair: Counter[str] = Counter()
    for record in baseline:
        left = record.get("result_equivalent") is True
        right = ir_by_id[record["question_id"]].get("result_equivalent") is True
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
    for family in sorted(set(record["family"] for record in baseline)):
        left = [record for record in baseline if record["family"] == family]
        right = [record for record in ir if record["family"] == family]
        output[family] = {"baseline": _summary(left), "window_ir": _summary(right)}
    return output


def _transport_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "json_parse_success",
        "dto_validation_success",
        "catalog_qualification_success",
        "adapter_conversion_success",
        "internal_ir_validation_success",
    )
    return {
        "attempted": len(records),
        **{key: sum(record.get(key) is True for record in records) for key in keys},
        "full_transport_success": sum(
            record.get("internal_ir_validation_success") is True for record in records
        ),
        "failure_taxonomy": dict(
            Counter(
                record.get("failure_taxonomy")
                for record in records
                if record.get("failure_taxonomy")
            )
        ),
    }


def _ir_quality(records: list[dict[str, Any]]) -> dict[str, Any]:
    transported = [record for record in records if record.get("semantic_parts")]
    keys = sorted({key for record in transported for key in record["semantic_parts"]})
    return {
        "transported": len(transported),
        "field_accuracy": {
            key: sum(record["semantic_parts"].get(key) is True for record in transported)
            / len(transported)
            for key in keys
        },
        "model_ir_matches_gold": sum(
            record.get("model_ir_matches_gold") is True for record in records
        ),
    }


def _failure_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "counts": dict(
            Counter(
                record.get("failure_taxonomy")
                for record in records
                if record.get("failure_taxonomy")
            )
        ),
        "policy_rejections": sum(
            record.get("failure_taxonomy") == "POLICY_REJECTION" for record in records
        ),
        "compiler_errors": sum(
            record.get("failure_taxonomy") == "COMPILER_ERROR" for record in records
        ),
        "result_mismatch_after_correct_ir": sum(
            record.get("failure_taxonomy") == "RESULT_MISMATCH_AFTER_CORRECT_IR"
            for record in records
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    path = DEV_PATH if args.stage == "dev" else HOLDOUT_PATH
    rows = _load(path)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = ROOT / "evaluation/results/m212r" / args.stage
    asyncio.run(_run(rows, run_root / run_id, args.stage))


if __name__ == "__main__":
    main()
