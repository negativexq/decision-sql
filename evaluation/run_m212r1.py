"""M2.12R.1 role-explicit slot diagnostic and conditional rerun.

Phase A is deliberately small.  The script has no provider fallback: a
representation failure is a failure of this arm.  Phase B is guarded by the
precommitted Phase A gate and is not started automatically by this module.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.provider import LLMProviderError, ModelIOCapture, OpenAICompatibleProvider
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR
from app.generation.window_provider_roles import (
    ROLE_PATTERN_DTOS,
    RoleProviderDTO,
)
from app.generation.window_role_adapter import RoleExplicitWindowProviderAdapter
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.metrics import assess_query_results

ROOT = Path(__file__).resolve().parents[1]
DEV_PATH = ROOT / "evaluation/datasets/m212_window_dev.json"
MANIFEST_PATH = ROOT / "evaluation/datasets/m212r1_role_slot_diagnostic_manifest.json"
SOURCE_SHA = "bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3"
TARGET_IDS = {
    "m212-dev-laglead-001",
    "m212-dev-laglead-002",
    "m212-dev-laglead-003",
    "m212-dev-laglead-004",
    "m212-dev-laglead-005",
    "m212-dev-laglead-006",
    "m212-dev-running-001",
    "m212-dev-running-002",
    "m212-dev-running-003",
    "m212-dev-running-005",
    "m212-dev-running-006",
    "m212-dev-moving-001",
    "m212-dev-moving-002",
    "m212-dev-moving-003",
    "m212-dev-moving-006",
}
CONTROL_IDS = {
    "m212-dev-latest-005",
    "m212-dev-topn-002",
    "m212-dev-running-004",
    "m212-dev-moving-004",
    "m212-dev-ranking-001",
}


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode())


def load_dev() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(DEV_PATH.read_text()))


def git_sha() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def role_inventory() -> dict[str, Any]:
    old_names = {
        "source",
        "outputs",
        "target",
        "partition_by",
        "order_by",
        "direction",
        "offset",
        "n",
        "aggregate",
        "function",
        "preceding",
        "include_current",
        "scale",
    }
    result: dict[str, Any] = {}
    for pattern, model in ROLE_PATTERN_DTOS.items():
        fields = []
        for name, field in model.model_fields.items():
            fields.append(
                {
                    "field": name,
                    "semantic_role": name,
                    "why_model_chooses": name not in {"source_table"},
                    "compiler_can_own": name in {"source_table"},
                    "adapter_can_derive": name in {"source_table"},
                    "ambiguous_m212r_name": name in old_names,
                    "renamed_or_new": name not in old_names,
                    "required": field.is_required(),
                }
            )
        schema = model.model_json_schema()
        result[pattern] = {
            "dto_name": model.__name__,
            "version": "m212r1-role-v1",
            "field_count": len(fields),
            "fields": fields,
            "schema": schema,
            "schema_sha256": sha_text(json.dumps(schema, sort_keys=True, separators=(",", ":"))),
        }
    return result


def prepare_manifest() -> dict[str, Any]:
    if sha_bytes(DEV_PATH.read_bytes()) != SOURCE_SHA:
        raise RuntimeError("M2.12 development SHA mismatch")
    rows = load_dev()
    by_id = {row["id"]: row for row in rows}
    missing = (TARGET_IDS | CONTROL_IDS) - set(by_id)
    if missing:
        raise RuntimeError(f"diagnostic IDs missing from frozen dev set: {sorted(missing)}")
    if TARGET_IDS & CONTROL_IDS:
        raise RuntimeError("diagnostic target/control overlap")
    entries = []
    for role, ids in (("FAILURE_TARGET", sorted(TARGET_IDS)), ("CONTROL", sorted(CONTROL_IDS))):
        for question_id in ids:
            row = by_id[question_id]
            entries.append(
                {
                    "question_id": question_id,
                    "pattern": row["pattern"],
                    "role": role,
                    "source_m212r_classification": (
                        "COMPUTED_ALIAS_USED_AS_PHYSICAL_COLUMN"
                        if role == "FAILURE_TARGET"
                        else "TRANSPORTED_CONTROL"
                    ),
                }
            )
    manifest = {
        "milestone": "M2.12R.1",
        "phase": "A",
        "source_dataset": "evaluation/datasets/m212_window_dev.json",
        "source_dataset_sha256": SOURCE_SHA,
        "question_count": len(entries),
        "target_count": len(TARGET_IDS),
        "control_count": len(CONTROL_IDS),
        "entries": entries,
        "model": "gpt-5.6-luna",
        "reasoning_effort": "none",
        "temperature": None,
        "schema_mode": "FULL_COMPACT",
        "transport": "pattern_specific_role_explicit_plain_json",
        "one_provider_call_per_question": True,
        "holdout_used": False,
        "code_sha": git_sha(),
        "frozen_before_provider_calls": True,
        "precommitted_gate": {
            "overall_transport_minimum": 0.90,
            "target_transport_minimum": 13,
            "control_transport_minimum": 4,
            "max_target_role_confusion": 2,
        },
    }
    write_json(MANIFEST_PATH, manifest)
    inventory_path = ROOT / "evaluation/datasets/role_slot_field_inventory.json"
    write_json(inventory_path, role_inventory())
    return manifest


def role_prompt(pattern: str, model: type[BaseModel]) -> str:
    keys = ", ".join(model.model_fields)
    schema = json.dumps(model.model_json_schema(), sort_keys=True)
    return (
        f"Map the question to the {pattern} role-explicit provider DTO. Return one JSON object "
        f"only, with no markdown, explanation, SQL, or SQL fragments. Use exactly these keys: "
        f"{keys}. Every field ending in _column or _columns must name an existing physical "
        "column of source_table. Never put a computed Window result name in those fields. "
        "Computed output aliases are generated by software and must not be returned. Use only "
        "identifiers visible in the supplied schema. Do not explain reasoning. DTO schema:\n"
        + schema
    )


def capture(provider: OpenAICompatibleProvider) -> dict[str, Any] | None:
    value = provider.consume_model_io()
    return value.model_dump(mode="json") if isinstance(value, ModelIOCapture) else None


def family(pattern: str) -> str:
    if pattern in {"LAG", "LEAD"}:
        return "LAG_LEAD"
    return pattern


def alias_confusion(dto: BaseModel, catalog: Any) -> bool:
    source = getattr(dto, "source_table", "")
    table = catalog.get_table(source)
    if table is None:
        return False
    physical = {column.name.lower() for column in table.columns if column.queryable}
    for name, value in dto.model_dump().items():
        if name.endswith("column") or name.endswith("columns"):
            values = value if isinstance(value, (list, tuple)) else (value,)
            if any(
                isinstance(item, str) and item.lower().split(".")[-1] not in physical
                for item in values
            ):
                return True
    return False


def compare_ir(candidate: WindowQueryIR, gold: WindowQueryIR) -> bool:
    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k != "alias"}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return bool(clean(candidate.model_dump(mode="json")) == clean(gold.model_dump(mode="json")))


def _result_equivalent(actual: QueryExecution | None, expected: QueryExecution) -> bool | None:
    if actual is None:
        return None
    return assess_query_results(actual, expected, order_sensitive=False).equivalent


def _semantic_parts_for_rerun(candidate: WindowQueryIR, gold: WindowQueryIR) -> dict[str, bool]:
    """Keep IR comparison aligned with the corrected historical diagnostic."""
    from evaluation.run_m212r import _semantic_parts

    return _semantic_parts(candidate, gold)


def _phase_b_pattern_results(
    baseline: list[dict[str, Any]], ir_records: list[dict[str, Any]]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for pattern in sorted({record["pattern"] for record in ir_records}):
        ids = {record["question_id"] for record in ir_records if record["pattern"] == pattern}
        base = [record for record in baseline if record["question_id"] in ids]
        ir = [record for record in ir_records if record["question_id"] in ids]
        result[pattern] = {
            "questions": len(ids),
            "baseline_correct": sum(record.get("result_equivalent") is True for record in base),
            "ir_correct": sum(record.get("result_equivalent") is True for record in ir),
        }
    return result


def _phase_b_pairwise(
    baseline: list[dict[str, Any]], ir_records: list[dict[str, Any]]
) -> dict[str, int]:
    left = {record["question_id"]: record for record in baseline}
    right = {record["question_id"]: record for record in ir_records}
    counts: Counter[str] = Counter()
    for question_id in left.keys() & right.keys():
        base = left[question_id].get("result_equivalent") is True
        ir = right[question_id].get("result_equivalent") is True
        counts[
            "BOTH_CORRECT"
            if base and ir
            else "BASELINE_ONLY_CORRECT"
            if base
            else "WINDOW_IR_ONLY_CORRECT"
            if ir
            else "BOTH_INCORRECT"
        ] += 1
    return dict(counts)


def _phase_b_slot_metrics(ir_records: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "source_relation",
        "physical_outputs",
        "target",
        "partition_by",
        "order_by",
        "direction",
        "offset",
        "n",
        "aggregate",
        "function",
        "frame",
        "scale",
    )
    totals: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for record in ir_records:
        parts = record.get("semantic_parts") or {}
        if not parts:
            continue
        for field in fields:
            matching = [value for key, value in parts.items() if field in key]
            if matching:
                totals[field] += 1
                correct[field] += all(matching)
    return {
        field: {
            "correct": correct[field],
            "scored": totals[field],
            "accuracy": correct[field] / totals[field] if totals[field] else None,
        }
        for field in fields
    }


async def run_phase_b(run_dir: Path) -> dict[str, Any]:
    """Run the predeclared fresh 48-question paired capability rerun."""
    settings = get_settings().model_copy(
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
    compiler = WindowSqlCompiler(safety.catalog)
    adapter = RoleExplicitWindowProviderAdapter(safety.catalog)
    provider = OpenAICompatibleProvider(settings)
    rows = load_dev()
    if sha_bytes(DEV_PATH.read_bytes()) != SOURCE_SHA:
        raise RuntimeError("M2.12 development SHA mismatch before Phase B")
    from evaluation.run_m212r import _validate_gold

    gold = _validate_gold(rows, safety, compiler)
    schema = {
        row["id"]: serialize_schema_context(
            resolver.resolve(row["question"], SchemaContextMode.FULL_COMPACT)
        )
        for row in rows
    }
    baseline_service = TextToSqlService(
        resolver,
        provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
        schema_serializer=serialize_schema_context,
    )
    baseline: list[dict[str, Any]] = []
    ir_records: list[dict[str, Any]] = []
    io_records: list[dict[str, Any]] = []
    for row in rows:
        question_id = row["id"]
        baseline_result = await baseline_service.run(
            TextToSqlRequest(question=row["question"], correlation_id=question_id, execute=True)
        )
        captured = capture(provider)
        if captured is not None:
            io_records.append(captured)
        proposal = baseline_result.proposal
        baseline.append(
            {
                "question_id": question_id,
                "pattern": row["pattern"],
                "question": row["question"],
                "arm": "BASELINE_ONE_SHOT",
                "provider_calls_attempted": baseline_result.provider_calls_attempted,
                "provider_calls_succeeded": baseline_result.provider_calls_succeeded,
                "provider_calls_failed": baseline_result.provider_calls_failed,
                "input_tokens": proposal.prompt_tokens if proposal else None,
                "output_tokens": proposal.completion_tokens if proposal else None,
                "latency_ms": baseline_result.generation_latency_ms,
                "question_hash": sha_text(row["question"]),
                "schema_hash": sha_text(schema[question_id]),
                "parse_success": baseline_result.candidate is not None,
                "plan_accepted": isinstance(baseline_result.plan, QueryPlan),
                "execution_success": isinstance(baseline_result.execution, QueryExecution),
                "failure_stage": baseline_result.failure_stage.value
                if baseline_result.failure_stage
                else None,
                "policy_rejection": baseline_result.plan_failure.rejection.model_dump(mode="json")
                if baseline_result.plan_failure and baseline_result.plan_failure.rejection
                else None,
                "generated_sql": proposal.sql if proposal else None,
                "result_equivalent": _result_equivalent(
                    baseline_result.execution, gold["gold_results"][question_id]
                ),
            }
        )
        model = ROLE_PATTERN_DTOS[row["pattern"]]
        record: dict[str, Any] = {
            "question_id": question_id,
            "pattern": row["pattern"],
            "question": row["question"],
            "arm": "ROLE_EXPLICIT_WINDOW_IR",
            "provider_calls_attempted": 1,
            "provider_calls_succeeded": 0,
            "provider_calls_failed": 0,
            "json_parse_success": False,
            "dto_validation_success": False,
            "column_resolution_success": False,
            "adapter_success": False,
            "internal_ir_validation_success": False,
            "full_transport_success": False,
            "question_hash": sha_text(row["question"]),
            "schema_hash": sha_text(schema[question_id]),
        }
        try:
            response = await provider.propose_window_transport(
                row["question"],
                schema[question_id],
                role_prompt(family(row["pattern"]), model),
                response_format={"type": "json_object"},
                operation="m212r1_role_explicit",
            )
            record.update(
                {
                    "provider_calls_succeeded": 1,
                    "input_tokens": response.prompt_tokens,
                    "output_tokens": response.completion_tokens,
                    "generation_latency_ms": response.latency_ms,
                    "raw_json": response.content,
                }
            )
            payload = json.loads(response.content or "")
            if not isinstance(payload, dict):
                raise ValueError("provider JSON must be an object")
            record["json_parse_success"] = True
            dto = cast(RoleProviderDTO, model.model_validate(payload))
            record["dto_validation_success"] = True
            record["provider_dto"] = dto.model_dump(mode="json")
            ir = adapter.convert(dto)
            record.update(
                {
                    "column_resolution_success": True,
                    "adapter_success": True,
                    "internal_ir_validation_success": True,
                    "full_transport_success": True,
                    "model_ir": ir.model_dump(mode="json"),
                }
            )
            gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
            parts = _semantic_parts_for_rerun(ir, gold_ir)
            record["semantic_parts"] = parts
            record["model_ir_matches_gold"] = all(parts.values())
            compiled = compiler.compile(ir)
            record["compiled_sql"] = compiled
            planned = safety.plan(
                SqlCandidate(
                    sql=compiled, source=CandidateSource.WINDOW_COMPILER, correlation_id=question_id
                )
            )
            record["m1_plan_success"] = isinstance(planned, QueryPlan)
            if isinstance(planned, SqlPlanFailure):
                record["failure_taxonomy"] = "POLICY_REJECTION"
                record["policy_rejection"] = (
                    planned.rejection.model_dump(mode="json") if planned.rejection else None
                )
            else:
                executed = safety.execute(planned)
                record["execution_success"] = isinstance(executed, QueryExecution)
                record["result_equivalent"] = _result_equivalent(
                    executed if isinstance(executed, QueryExecution) else None,
                    gold["gold_results"][question_id],
                )
                if record["result_equivalent"] is not True:
                    record["failure_taxonomy"] = "RESULT_MISMATCH_AFTER_VALID_IR"
        except ValidationError as error:
            record.update({"failure_taxonomy": "DTO_VALIDATION_FAILED", "error": str(error)})
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            record.update(
                {"failure_taxonomy": "JSON_DECODE_OR_ADAPTER_FAILED", "error": str(error)}
            )
        except LLMProviderError as error:
            record.update(
                {
                    "provider_calls_failed": 1,
                    "failure_taxonomy": "PROVIDER_FAILURE",
                    "provider_error": error.detail.model_dump(mode="json")
                    if error.detail
                    else None,
                }
            )
        finally:
            captured = capture(provider)
            if captured is not None:
                record["model_io"] = captured
                io_records.append(captured)
        ir_records.append(record)
    pairwise = _phase_b_pairwise(baseline, ir_records)
    summary = {
        "milestone": "M2.12R.1",
        "phase": "B",
        "run_id": run_dir.name,
        "dataset_sha256": SOURCE_SHA,
        "questions": len(rows),
        "baseline_correct": sum(r.get("result_equivalent") is True for r in baseline),
        "ir_correct": sum(r.get("result_equivalent") is True for r in ir_records),
        "baseline_calls": sum(r["provider_calls_attempted"] for r in baseline),
        "ir_calls": sum(r["provider_calls_attempted"] for r in ir_records),
        "ir_transport": sum(bool(r.get("full_transport_success")) for r in ir_records),
        "pairwise": pairwise,
        "pattern_results": _phase_b_pattern_results(baseline, ir_records),
        "slot_metrics": _phase_b_slot_metrics(ir_records),
        "token_totals": {
            "baseline_input": sum(r.get("input_tokens") or 0 for r in baseline),
            "baseline_output": sum(r.get("output_tokens") or 0 for r in baseline),
            "ir_input": sum(r.get("input_tokens") or 0 for r in ir_records),
            "ir_output": sum(r.get("output_tokens") or 0 for r in ir_records),
        },
    }
    run_dir.mkdir(parents=True, exist_ok=False)
    write_json(
        run_dir / "metadata.json",
        {
            "milestone": "M2.12R.1",
            "phase": "B",
            "run_id": run_dir.name,
            "dataset_sha256": SOURCE_SHA,
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "schema_mode": "FULL_COMPACT",
            "holdout_used": False,
            "one_provider_call_per_question_per_arm": True,
            "code_sha": git_sha(),
        },
    )
    write_json(run_dir / "baseline.json", baseline)
    write_json(run_dir / "window_ir.json", ir_records)
    write_json(run_dir / "comparison.json", summary)
    write_json(
        run_dir / "transport_analysis.json",
        {
            "attempted": len(ir_records),
            "provider_success": sum(r["provider_calls_succeeded"] for r in ir_records),
            "json_parse_success": sum(bool(r.get("json_parse_success")) for r in ir_records),
            "dto_validation_success": sum(
                bool(r.get("dto_validation_success")) for r in ir_records
            ),
            "column_resolution_success": sum(
                bool(r.get("column_resolution_success")) for r in ir_records
            ),
            "adapter_success": sum(bool(r.get("adapter_success")) for r in ir_records),
            "internal_ir_validation_success": sum(
                bool(r.get("internal_ir_validation_success")) for r in ir_records
            ),
            "full_transport_success": summary["ir_transport"],
        },
    )
    write_json(run_dir / "slot_quality.json", summary["slot_metrics"])
    write_json(run_dir / "pattern_analysis.json", summary["pattern_results"])
    write_json(
        run_dir / "failure_analysis.json",
        dict(Counter(r.get("failure_taxonomy") for r in ir_records)),
    )
    (run_dir / "model_io.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in io_records)
    )
    return summary


async def run_phase_a(run_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings().model_copy(
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
    compiler = WindowSqlCompiler(safety.catalog)
    adapter = RoleExplicitWindowProviderAdapter(safety.catalog)
    provider = OpenAICompatibleProvider(settings)
    rows = {row["id"]: row for row in load_dev()}
    schema = {
        qid: serialize_schema_context(
            resolver.resolve(row["question"], SchemaContextMode.FULL_COMPACT)
        )
        for qid, row in rows.items()
    }
    records: list[dict[str, Any]] = []
    io_records: list[dict[str, Any]] = []
    for entry in manifest["entries"]:
        row = rows[entry["question_id"]]
        pattern = row["pattern"]
        model = ROLE_PATTERN_DTOS[pattern]
        rec: dict[str, Any] = {
            **entry,
            "question": row["question"],
            "arm": "ROLE_EXPLICIT_WINDOW_IR",
            "provider_calls_attempted": 1,
            "provider_calls_succeeded": 0,
            "provider_calls_failed": 0,
            "json_parse_success": False,
            "dto_validation_success": False,
            "column_resolution_success": False,
            "adapter_success": False,
            "internal_ir_validation_success": False,
            "full_transport_success": False,
            "model_ir": None,
            "computed_alias_as_physical_column": False,
            "question_hash": sha_text(row["question"]),
            "schema_hash": sha_text(schema[entry["question_id"]]),
        }
        try:
            response = await provider.propose_window_transport(
                row["question"],
                schema[entry["question_id"]],
                role_prompt(family(pattern), model),
                response_format={"type": "json_object"},
                operation="m212r1_role_explicit",
            )
            rec.update(
                {
                    "provider_calls_succeeded": 1,
                    "provider": response.provider,
                    "model": response.model,
                    "input_tokens": response.prompt_tokens,
                    "output_tokens": response.completion_tokens,
                    "generation_latency_ms": response.latency_ms,
                    "raw_json": response.content,
                }
            )
            try:
                payload = json.loads(response.content or "")
                if not isinstance(payload, dict):
                    raise ValueError("provider JSON must be an object")
                rec["json_parse_success"] = True
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                rec.update({"failure_taxonomy": "JSON_DECODE_FAILED", "error": str(error)})
                records.append(rec)
                continue
            try:
                dto = cast(RoleProviderDTO, model.model_validate(payload))
                rec["dto_validation_success"] = True
                rec["computed_alias_as_physical_column"] = alias_confusion(dto, safety.catalog)
                rec["provider_dto"] = dto.model_dump(mode="json")
            except ValidationError as error:
                rec.update({"failure_taxonomy": "DTO_VALIDATION_FAILED", "error": str(error)})
                records.append(rec)
                continue
            try:
                ir = adapter.convert(dto)
                rec.update(
                    {
                        "column_resolution_success": True,
                        "adapter_success": True,
                        "internal_ir_validation_success": True,
                        "full_transport_success": True,
                        "model_ir": ir.model_dump(mode="json"),
                    }
                )
            except ValueError as error:
                message = str(error)
                rec.update(
                    {
                        "failure_taxonomy": "COLUMN_RESOLUTION_FAILED"
                        if "column" in message or "relation" in message
                        else "ADAPTER_FAILED",
                        "error": message,
                    }
                )
                records.append(rec)
                continue
            gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
            rec["model_ir_matches_gold"] = compare_ir(ir, gold_ir)
            compiled = compiler.compile(ir)
            rec["compiled_sql"] = compiled
            planned = safety.plan(
                SqlCandidate(
                    sql=compiled, source=CandidateSource.WINDOW_COMPILER, correlation_id=row["id"]
                )
            )
            rec["m1_plan_success"] = isinstance(planned, QueryPlan)
            if isinstance(planned, SqlPlanFailure):
                rec["failure_taxonomy"] = "POLICY_REJECTION"
                rec["policy_rejection"] = (
                    planned.rejection.model_dump(mode="json") if planned.rejection else None
                )
            else:
                executed = safety.execute(planned)
                rec["execution_success"] = isinstance(executed, QueryExecution)
        except LLMProviderError as error:
            rec.update(
                {
                    "provider_calls_failed": 1,
                    "failure_taxonomy": "PROVIDER_FAILURE",
                    "provider_error_message": str(error),
                    "provider_error": error.detail.model_dump(mode="json")
                    if error.detail
                    else None,
                }
            )
        except Exception as error:
            rec.update(
                {
                    "failure_taxonomy": "INTERNAL_ERROR",
                    "error": type(error).__name__ + ": " + str(error),
                }
            )
        finally:
            io = capture(provider)
            if io is not None:
                rec["model_io"] = io
                io_records.append(io)
        records.append(rec)

    targeted = [r for r in records if r["role"] == "FAILURE_TARGET"]
    controls = [r for r in records if r["role"] == "CONTROL"]

    def count(key: str, values: list[dict[str, Any]]) -> int:
        return sum(bool(r.get(key)) for r in values)

    confusion_observed = any(r.get("dto_validation_success") for r in targeted)

    summary = {
        "milestone": "M2.12R.1",
        "phase": "A",
        "run_id": run_dir.name,
        "provider_calls_attempted": len(records),
        "provider_calls_succeeded": count("provider_calls_succeeded", records),
        "provider_calls_failed": count("provider_calls_failed", records),
        "json_parse_success": count("json_parse_success", records),
        "dto_validation_success": count("dto_validation_success", records),
        "column_resolution_success": count("column_resolution_success", records),
        "adapter_success": count("adapter_success", records),
        "internal_ir_validation_success": count("internal_ir_validation_success", records),
        "full_transport_success": count("full_transport_success", records),
        "targeted": {
            "count": len(targeted),
            "transport": count("full_transport_success", targeted),
            "computed_alias_confusion": count("computed_alias_as_physical_column", targeted),
        },
        "controls": {
            "count": len(controls),
            "transport": count("full_transport_success", controls),
        },
        "gate": {
            "overall_transport_passed": count("full_transport_success", records) / len(records)
            >= 0.90,
            "target_transport_passed": count("full_transport_success", targeted) >= 13,
            "control_transport_passed": count("full_transport_success", controls) >= 4,
            "computed_alias_confusion_estimable": confusion_observed,
            "computed_alias_confusion_passed": (
                confusion_observed and count("computed_alias_as_physical_column", targeted) <= 2
            ),
            "passed": (
                count("full_transport_success", records) / len(records) >= 0.90
                and count("full_transport_success", targeted) >= 13
                and count("full_transport_success", controls) >= 4
                and confusion_observed
                and count("computed_alias_as_physical_column", targeted) <= 2
            ),
        },
        "token_totals": {
            "input": sum(r.get("input_tokens") or 0 for r in records),
            "output": sum(r.get("output_tokens") or 0 for r in records),
        },
    }
    run_dir.mkdir(parents=True, exist_ok=False)
    target_summary = cast(dict[str, Any], summary["targeted"])
    write_json(
        run_dir / "metadata.json",
        {
            **manifest,
            "run_id": run_dir.name,
            "timestamp": datetime.now(UTC).isoformat(),
            "code_sha": git_sha(),
            "provider_calls": len(records),
        },
    )
    write_json(run_dir / "role_slot_field_inventory.json", role_inventory())
    write_json(
        run_dir / "provider_schema_hashes.json",
        {p: x["schema_sha256"] for p, x in role_inventory().items()},
    )
    write_json(run_dir / "transport_analysis.json", summary)
    write_json(
        run_dir / "computed_alias_confusion.json",
        {
            "original_m212r_target_confusion": "15/15",
            "m212r1_target_confusion": (
                f"{target_summary['computed_alias_confusion']}/{len(targeted)}"
            ),
            "records": [
                {
                    "question_id": r["question_id"],
                    "confusion": r.get("computed_alias_as_physical_column", False),
                }
                for r in targeted
            ],
        },
    )
    write_json(
        run_dir / "failure_analysis.json",
        {"counts": dict(Counter(r.get("failure_taxonomy") for r in records)), "records": records},
    )
    (run_dir / "responses.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in records)
    )
    (run_dir / "model_io.jsonl").write_text(
        "".join(json.dumps(r, default=str) + "\n" for r in io_records)
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--phase-a", action="store_true")
    parser.add_argument("--phase-b", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    if args.prepare:
        manifest = prepare_manifest()
        print(json.dumps({"manifest": str(MANIFEST_PATH), "questions": manifest["question_count"]}))
    if args.phase_a:
        manifest = cast(dict[str, Any], json.loads(MANIFEST_PATH.read_text()))
        stamp = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = ROOT / "evaluation/results/m212r1/diagnostic" / stamp
        result = asyncio.run(run_phase_a(run_dir, manifest))
        print(json.dumps(result, indent=2))
    if args.phase_b:
        stamp = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = ROOT / "evaluation/results/m212r1/dev" / stamp
        result = asyncio.run(run_phase_b(run_dir))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
