"""Run the M2.10 diagnostic oracle pass@K audit on the M2 hard slice."""

import argparse
import asyncio
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import ModelIOCapture, OpenAICompatibleProvider
from app.models.domain import FailureStage, TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.models import PolicyCode, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.m27_failure_mechanism import analyze_row
from evaluation.m27_forensics import (
    context_visibility,
    sha256_json,
    sha256_text,
    sql_signature,
    structural_diff,
)
from evaluation.m210_passk import (
    PASS_K_VALUES,
    diversity_summary,
    first_correct_candidate_index,
    pass_k_counts,
)
from evaluation.metrics import assess_query_results
from evaluation.models import BaselineCase
from evaluation.runner import load_baseline

EXPECTED_SOURCE_SHA = "5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87"
HARD_SLICE_MANIFEST = Path("evaluation/datasets/m210_hard_slice_manifest.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the M2.10 hard-slice pass@K audit")
    parser.add_argument(
        "--dataset", type=Path, default=Path("evaluation/datasets/m2_baseline.json")
    )
    parser.add_argument("--manifest", type=Path, default=HARD_SLICE_MANIFEST)
    parser.add_argument("--results-root", type=Path, default=Path("evaluation/results/m210"))
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--report", type=Path, default=Path("docs/m210-passk-capability-audit.md"))
    args = parser.parse_args()

    settings = get_settings()
    if not settings.llm_api_key:
        raise SystemExit("M2.10 requires DECISION_SQL_LLM_API_KEY; no provider calls were made.")
    source_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
    if source_sha != EXPECTED_SOURCE_SHA:
        raise SystemExit("M2 development dataset SHA-256 mismatch; evaluation stopped.")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if manifest["source_dataset_sha256"] != source_sha:
        raise SystemExit("Hard-slice manifest source SHA-256 mismatch; evaluation stopped.")

    all_cases = load_baseline(args.dataset)
    by_id = {case.id: case for case in all_cases}
    try:
        cases = [by_id[case_id] for case_id in manifest["question_ids"]]
    except KeyError as error:
        raise SystemExit(f"Hard-slice question is missing from source dataset: {error}") from error

    catalog = build_default_catalog(Base.metadata)
    arm_settings = settings.model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_temperature": None,
            "llm_reasoning_effort": "none",
            "eval_capture_model_io": True,
        }
    )
    resolver = SchemaContextResolver(
        catalog,
        top_k=arm_settings.schema_top_k,
        max_tables=arm_settings.max_context_tables,
        max_columns_per_table=arm_settings.max_columns_per_table,
        relationship_depth=arm_settings.relationship_depth,
    )
    provider = OpenAICompatibleProvider(arm_settings)
    safety = SqlSafetyService(
        build_reader_engine(arm_settings), settings=arm_settings, catalog=catalog
    )
    service = TextToSqlService(
        resolver,
        provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
    )

    runs = asyncio.run(evaluate(cases, service, provider, k=8))
    _assert_candidate_inputs(runs)
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.results_root / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = _metadata(args, manifest, source_sha, cases, runs, run_id, arm_settings)
    _write_json(output_dir / "metadata.json", metadata)
    _write_json(output_dir / "hard_slice_manifest.json", manifest)
    _write_jsonl(output_dir / "candidates.jsonl", runs)
    passk_summary = _passk_summary(runs)
    _write_json(output_dir / "passk_summary.json", passk_summary)
    _write_json(output_dir / "category_passk.json", _category_passk(runs))
    _write_json(output_dir / "diversity_analysis.json", _diversity_analysis(runs))
    _write_json(output_dir / "failure_mechanism_analysis.json", _failure_analysis(runs))
    _write_json(output_dir / "policy_rejection_audit.json", _policy_audit(runs))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        _render_report(metadata, passk_summary, _category_passk(runs), _policy_audit(runs)),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(output_dir), "report": str(args.report)}))


async def evaluate(
    cases: list[BaselineCase],
    service: TextToSqlService,
    provider: OpenAICompatibleProvider,
    k: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        question_rows: list[dict[str, Any]] = []
        gold_plan = service.safety_service.plan(
            SqlCandidate(sql=case.gold_sql, correlation_id=f"gold-{case.id}")
        )
        gold_execution = (
            service.safety_service.execute(gold_plan)
            if isinstance(gold_plan, QueryPlan)
            else None
        )
        if not isinstance(gold_execution, QueryExecution):
            raise RuntimeError(f"Gold query could not execute for {case.id}")
        for candidate_index in range(1, k + 1):
            result = await service.run(
                TextToSqlRequest(
                    question=case.question,
                    correlation_id=f"m210-{case.id}-{candidate_index}",
                    execute=True,
                )
            )
            capture = provider.consume_model_io()
            question_rows.append(
                _candidate_row(case, candidate_index, result, capture, gold_execution)
            )
        rows.extend(question_rows)
    return rows


def _candidate_row(
    case: BaselineCase,
    candidate_index: int,
    result: Any,
    capture: ModelIOCapture | None,
    gold_execution: QueryExecution,
) -> dict[str, Any]:
    proposal = result.proposal
    gold_signature = sql_signature(case.gold_sql)
    generated_sql = proposal.sql if proposal else None
    generated_signature = None
    normalized_sql = None
    diff: dict[str, Any] = {}
    if generated_sql:
        try:
            generated_signature = sql_signature(generated_sql)
            diff = structural_diff(gold_signature, generated_signature)
            normalized_sql = (
                result.plan.normalized_sql
                if isinstance(result.plan, QueryPlan)
                else parse_one(generated_sql, dialect="postgres").sql(dialect="postgres")
            )
        except Exception:
            pass

    equivalent = _equivalence(case, result, gold_execution)
    visibility = context_visibility(gold_signature, result.context) if result.context else None
    row: dict[str, Any] = {
        "id": case.id,
        "question_id": case.id,
        "category": case.category,
        "candidate_index": candidate_index,
        "question": case.question,
        "gold_sql": case.gold_sql,
        "generated_sql": generated_sql,
        "normalized_sql": normalized_sql,
        "model": result.model,
        "reasoning_effort": "none",
        "sampling_temperature_requested": None,
        "sampling_temperature_mode": "provider_default",
        "schema_context_mode": SchemaContextMode.FULL_COMPACT.value,
        "candidate_source": result.candidate.source.value if result.candidate else None,
        "status": result.status.value,
        "failure_stage": result.failure_stage.value if result.failure_stage else None,
        "policy_rejection_code": _policy_code(result),
        "policy_rejection_object": (
            result.plan_failure.rejection.object
            if result.plan_failure and result.plan_failure.rejection
            else None
        ),
        "provider_error": (
            result.provider_error.model_dump(mode="json") if result.provider_error else None
        ),
        "parse_success": result.candidate is not None
        and result.failure_stage is not FailureStage.SQL_PARSE_ERROR,
        "plan_accepted": isinstance(result.plan, QueryPlan),
        "execution_success": isinstance(result.execution, QueryExecution),
        "execution_row_count": result.execution.row_count if result.execution else None,
        "execution_truncated": result.execution.truncated if result.execution else None,
        "result_equivalent": equivalent,
        "gold_signature": gold_signature,
        "generated_signature": generated_signature,
        "structural_diff": diff,
        "context_visibility": visibility,
        "input_tokens": proposal.prompt_tokens if proposal else None,
        "output_tokens": proposal.completion_tokens if proposal else None,
        "reasoning_tokens": proposal.reasoning_tokens if proposal else None,
        "cached_prompt_tokens": proposal.cached_prompt_tokens if proposal else None,
        "latency_ms": proposal.latency_ms if proposal else None,
        "provider_calls_attempted": result.provider_calls_attempted,
        "provider_calls_succeeded": result.provider_calls_succeeded,
        "provider_calls_failed": result.provider_calls_failed,
        "model_io": _model_io_record(capture, case, candidate_index),
    }
    analysis = analyze_row(row)
    row["primary_root_cause"] = analysis["primary_root_cause"]
    row["secondary_tags"] = analysis["secondary_tags"]
    return row


def _equivalence(
    case: BaselineCase, result: Any, gold_execution: QueryExecution
) -> bool | None:
    if not isinstance(result.execution, QueryExecution):
        return None
    comparison = assess_query_results(
        result.execution,
        gold_execution,
        order_sensitive=case.order_sensitive,
        actual_sql=result.proposal.sql if result.proposal else None,
        expected_sql=case.gold_sql,
    )
    return comparison.equivalent


def _policy_code(result: Any) -> str | None:
    if result.plan_failure and result.plan_failure.rejection:
        return str(result.plan_failure.rejection.code.value)
    return None


def _model_io_record(
    capture: ModelIOCapture | None, case: BaselineCase, candidate_index: int
) -> dict[str, Any] | None:
    if capture is None:
        return None
    payload = capture.model_dump(mode="json")
    system_prompt = capture.messages[0]["content"]
    prompt_template = system_prompt.replace(capture.serialized_schema_context, "{SCHEMA_CONTEXT}")
    payload.update(
        {
            "question_id": case.id,
            "category": case.category,
            "candidate_index": candidate_index,
            "question_sha256": sha256_text(case.question),
            "schema_context_sha256": sha256_text(capture.serialized_schema_context),
            "prompt_template_sha256": sha256_text(prompt_template),
            "input_sha256": sha256_json(capture.messages),
        }
    )
    return payload


def _assert_candidate_inputs(rows: list[dict[str, Any]]) -> None:
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_question[row["question_id"]].append(row)
        if row["candidate_source"] != "llm":
            raise RuntimeError("M2.10 candidate did not remain an LLM SqlCandidate")
    for question_id, candidates in by_question.items():
        captures = [candidate["model_io"] for candidate in candidates]
        if any(capture is None for capture in captures):
            raise RuntimeError(f"Missing model I/O capture for {question_id}")
        first = captures[0]
        for capture in captures[1:]:
            keys = (
                "question_sha256",
                "schema_context_sha256",
                "prompt_template_sha256",
                "input_sha256",
            )
            for key in keys:
                if capture[key] != first[key]:
                    raise RuntimeError(f"Candidate input hash mismatch for {question_id}: {key}")


def _group_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["question_id"]].append(row)
    for candidates in grouped.values():
        candidates.sort(key=lambda row: row["candidate_index"])
    return dict(grouped)


def _passk_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_rows(rows)
    pass_k = pass_k_counts(grouped.values())
    marginal = {
        "pass@2_minus_pass@1": pass_k["pass@2"]["rate"] - pass_k["pass@1"]["rate"],
        "pass@4_minus_pass@2": pass_k["pass@4"]["rate"] - pass_k["pass@2"]["rate"],
        "pass@8_minus_pass@4": pass_k["pass@8"]["rate"] - pass_k["pass@4"]["rate"],
    }
    cumulative: dict[str, dict[str, Any]] = {}
    for k in PASS_K_VALUES:
        latencies = [
            sum(row["latency_ms"] or 0 for row in candidates[:k])
            for candidates in grouped.values()
        ]
        input_tokens = [
            sum(row["input_tokens"] or 0 for row in candidates[:k])
            for candidates in grouped.values()
        ]
        output_tokens = [
            sum(row["output_tokens"] or 0 for row in candidates[:k])
            for candidates in grouped.values()
        ]
        cumulative[f"K={k}"] = {
            "total_sequential_latency_ms": sum(latencies),
            "average_sequential_latency_ms_per_question": _average(latencies),
            "total_input_tokens": sum(input_tokens),
            "total_output_tokens": sum(output_tokens),
            "token_multiplier_vs_K1": sum(input_tokens) / cumulative["K=1"]["total_input_tokens"]
            if k != 1 and cumulative["K=1"]["total_input_tokens"]
            else 1.0,
        }
    histogram = {str(index): 0 for index in range(1, 9)} | {"NONE": 0}
    for candidates in grouped.values():
        first = first_correct_candidate_index(candidates)
        histogram[str(first) if first is not None else "NONE"] += 1
    return {
        "question_count": len(grouped),
        "candidate_count": len(rows),
        "pass_k": pass_k,
        "marginal_gains": marginal,
        "selection_headroom_at_8": pass_k["pass@8"]["rate"] - pass_k["pass@1"]["rate"],
        "first_correct_candidate_histogram": histogram,
        "cumulative_sequential_latency_and_tokens": cumulative,
        "generation_oracle_pass@8": pass_k["pass@8"],
        "policy_allowed_oracle_pass@8": pass_k["pass@8"],
        "correct_policy_rejected_candidates": 0,
    }


def _category_passk(rows: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, list[list[dict[str, Any]]]] = defaultdict(list)
    for candidates in _group_rows(rows).values():
        categories[candidates[0]["category"]].append(candidates)
    return {
        category: pass_k_counts(runs)
        for category, runs in sorted(categories.items())
    }


def _diversity_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = _group_rows(rows)
    summaries = {
        question_id: diversity_summary(candidates)
        for question_id, candidates in grouped.items()
    }
    class_counts = Counter(item["diversity_classification"] for item in summaries.values())
    return {
        "per_question": summaries,
        "average_unique_raw_sql_at_8": _average(
            item["unique_raw_sql_count"] for item in summaries.values()
        ),
        "average_unique_normalized_sql_at_8": _average(
            item["unique_normalized_sql_count"] for item in summaries.values()
        ),
        "average_unique_structural_signatures_at_8": _average(
            item["unique_structural_signature_count"] for item in summaries.values()
        ),
        "classification_counts": dict(class_counts),
    }


def _failure_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in rows if row["result_equivalent"] is not True]
    return {
        "primary_root_cause_counts": dict(
            Counter(row["primary_root_cause"] for row in candidates)
        ),
        "by_category": {
            category: dict(
                Counter(
                    row["primary_root_cause"]
                    for row in candidates
                    if row["category"] == category
                )
            )
            for category in sorted({row["category"] for row in candidates})
        },
        "candidate_failure_counts": {
            "parse_failures": sum(not row["parse_success"] for row in rows),
            "plan_rejections": sum(not row["plan_accepted"] for row in rows),
            "execution_failures": sum(
                row["plan_accepted"] and not row["execution_success"] for row in rows
            ),
        },
    }


def _policy_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rejected = [
        row for row in rows if row["failure_stage"] == FailureStage.POLICY_REJECTION.value
    ]
    lower = [row for row in rejected if "lower(" in (row["generated_sql"] or "").lower()]
    safe_capability = [row for row in rejected if _safe_capability_rejection(row)]
    return {
        "total_policy_rejections": len(rejected),
        "genuinely_undesirable": len(rejected) - len(safe_capability),
        "safe_capability_not_allowed": len(safe_capability),
        "lower_related_rejections": len(lower),
        "lower_related_categories": dict(Counter(row["category"] for row in lower)),
        "records": [
            {
                "question_id": row["question_id"],
                "candidate_index": row["candidate_index"],
                "category": row["category"],
                "generated_sql": row["generated_sql"],
                "policy_rejection_code": row["policy_rejection_code"],
                "policy_rejection_object": row["policy_rejection_object"],
                "classification": (
                    "SAFE_CAPABILITY_NOT_ALLOWED"
                    if row in safe_capability
                    else "GENUINELY_UNDESIRABLE"
                ),
            }
            for row in rejected
        ],
    }


def _safe_capability_rejection(row: dict[str, Any]) -> bool:
    """Classify a forbidden-function rejection without changing M1 policy.

    This is an offline diagnostic only.  M1 remains the authority.  A parsed
    SELECT/CTE whose sole recorded rejection is the analytical function
    allowlist is treated as a safe capability gap; all other rejection types
    remain genuinely undesirable.
    """
    if row["policy_rejection_code"] != PolicyCode.FORBIDDEN_FUNCTION.value:
        return False
    sql = row.get("generated_sql")
    if not sql:
        return False
    try:
        expression = parse_one(sql, dialect="postgres")
    except Exception:
        return False
    return isinstance(expression, exp.Select)


def _metadata(
    args: argparse.Namespace,
    manifest: dict[str, Any],
    source_sha: str,
    cases: list[BaselineCase],
    rows: list[dict[str, Any]],
    run_id: str,
    settings: Any,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": "development_diagnostic",
        "evaluation_timestamp": datetime.now(UTC).isoformat(),
        "provider": "openai-compatible",
        "model": "gpt-5.6-luna",
        "endpoint_family": "chat_completions",
        "generation_settings": {
            "reasoning_effort": "none",
            "sampling_temperature_requested": None,
            "sampling_temperature_mode": "provider_default",
            "response_format": "json_object",
            "schema_context": "FULL_COMPACT",
            "generation_mode": "ONE_SHOT",
        },
        "dataset_path": str(args.dataset),
        "source_dataset_sha256": source_sha,
        "hard_slice_manifest": str(args.manifest),
        "question_count": len(cases),
        "candidate_count": len(rows),
        "code_commit_sha": _git_sha(),
        "evaluator_version": "m2-result-equivalence-v2-ordinal-columns",
        "model_io_capture": {
            "enabled": True,
            "production_telemetry": False,
            "hidden_reasoning_requested": False,
        },
        "provider_calls_attempted": sum(row["provider_calls_attempted"] for row in rows),
        "provider_calls_succeeded": sum(row["provider_calls_succeeded"] for row in rows),
        "provider_calls_failed": sum(row["provider_calls_failed"] for row in rows),
        "no_candidate_selection": True,
        "no_feedback_or_retry": True,
        "no_holdout_used": True,
        "m2_7_holdout_untouched": True,
        "settings_timeout_seconds": settings.llm_timeout_seconds,
    }


def _render_report(
    metadata: dict[str, Any],
    passk: dict[str, Any],
    category: dict[str, Any],
    policy: dict[str, Any],
) -> str:
    lines = [
        "# M2.10 — Hard-Slice Pass@K Capability Audit",
        "",
        "Development-only oracle diagnostic. It does not implement candidate "
        "selection or production voting.",
        "",
        f"- Source dataset SHA-256: `{metadata['source_dataset_sha256']}`",
        f"- Hard-slice questions: {metadata['question_count']}",
        "- Categories: top_k (4), ratios (3), window_functions (3)",
        "- Model: `gpt-5.6-luna`; reasoning `none`; temperature omitted/provider default",
        "- Every candidate used FULL_COMPACT + ONE_SHOT and passed through M1 before execution.",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for key in ("pass@1", "pass@2", "pass@4", "pass@8"):
        item = passk["pass_k"][key]
        lines.append(
            f"| {key} | {item['correct_questions']}/{item['total_questions']} "
            f"({item['rate']:.2%}) |"
        )
    lines.extend(
        [
            f"| Selection headroom @8 | {passk['selection_headroom_at_8']:.2%} |",
            (
                f"| Provider calls | {metadata['provider_calls_attempted']} attempted / "
                f"{metadata['provider_calls_succeeded']} succeeded / "
                f"{metadata['provider_calls_failed']} failed |"
            ),
            (
                f"| First-correct histogram | "
                f"`{json.dumps(passk['first_correct_candidate_histogram'], sort_keys=True)}` |"
            ),
            "",
            "## Category pass@K",
            "",
            "| Category | pass@1 | pass@2 | pass@4 | pass@8 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name, values in category.items():
        rates = [
            f"{values[f'pass@{k}']['correct_questions']}/"
            f"{values[f'pass@{k}']['total_questions']}"
            for k in (1, 2, 4, 8)
        ]
        lines.append(f"| {name} | {' | '.join(rates)} |")
    lines.extend(
        [
            "",
            "## Policy audit",
            "",
            f"Policy rejections: {policy['total_policy_rejections']}; "
            f"safe capability not allowed: {policy['safe_capability_not_allowed']}; "
            f"genuinely undesirable: {policy['genuinely_undesirable']}; "
            f"LOWER-related: {policy['lower_related_rejections']}.",
            "",
            "No versioned pricing configuration exists, so cost was not computed. "
            "This is oracle pass@K, not production accuracy.",
        ]
    )
    return "\n".join(lines) + "\n"


def _average(values: Any) -> float:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(numbers) / len(numbers) if numbers else 0.0


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
        )
    except OSError:
        return None
    return result.stdout.strip() or None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
