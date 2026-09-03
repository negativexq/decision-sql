import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.models import BaselineReport, CaseEvaluation


def persist_m2_run(
    output_dir: Path,
    metadata: dict[str, Any],
    reports: dict[str, BaselineReport],
    report_path: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "metadata.json", metadata)
    for mode, report in reports.items():
        _write_json(output_dir / f"{mode}_schema.json", report.model_dump(mode="json"))
    _write_json(output_dir / "comparison.json", _comparison_payload(metadata, reports))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_markdown(metadata, reports), encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _comparison_payload(
    metadata: dict[str, Any], reports: dict[str, BaselineReport]
) -> dict[str, Any]:
    full = reports["full"]
    retrieved = reports["retrieved"]
    rows = {
        "total_questions": _comparison_row(full.total_cases, retrieved.total_cases),
        "result_equivalence_rate": _comparison_row(
            full.result_equivalence_rate, retrieved.result_equivalence_rate
        ),
        "parse_success_rate": _comparison_row(
            full.parse_success_rate, retrieved.parse_success_rate
        ),
        "plan_acceptance_rate": _comparison_row(
            full.plan_acceptance_rate, retrieved.plan_acceptance_rate
        ),
        "execution_success_rate": _comparison_row(
            full.execution_success_rate, retrieved.execution_success_rate
        ),
        "retrieval_miss_count": _comparison_row(
            full.retrieval_miss_count, retrieved.retrieval_miss_count
        ),
        "average_context_tables": _comparison_row(
            full.average_context_tables, retrieved.average_context_tables
        ),
        "average_context_columns": _comparison_row(
            full.average_context_columns, retrieved.average_context_columns
        ),
        "average_prompt_tokens": _comparison_row(
            full.average_prompt_tokens, retrieved.average_prompt_tokens
        ),
        "average_generation_latency_ms": _comparison_row(
            full.average_generation_latency_ms, retrieved.average_generation_latency_ms
        ),
    }
    return {
        "run_id": metadata.get("run_id"),
        "full_schema": full.model_dump(mode="json"),
        "retrieved_context": retrieved.model_dump(mode="json"),
        "comparison": rows,
        "category_breakdown": {
            "full": _category_breakdown(full.cases),
            "retrieved": _category_breakdown(retrieved.cases),
        },
    }


def _comparison_row(full: Any, retrieved: Any) -> dict[str, Any]:
    delta = None
    if isinstance(full, (int, float)) and isinstance(retrieved, (int, float)):
        delta = retrieved - full
    return {"full_schema": full, "retrieved_context": retrieved, "delta": delta}


def _category_breakdown(cases: list[CaseEvaluation]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[CaseEvaluation]] = defaultdict(list)
    for case in cases:
        grouped[case.category].append(case)
    return {
        category: {
            "total": len(items),
            "result_equivalence_count": sum(item.result_equivalent is True for item in items),
            "result_equivalence_rate": _rate(
                sum(item.result_equivalent is True for item in items), len(items)
            ),
            "parse_success_count": sum(item.parse_success is True for item in items),
            "execution_success_count": sum(item.execution_success for item in items),
        }
        for category, items in sorted(grouped.items())
    }


def render_markdown(metadata: dict[str, Any], reports: dict[str, BaselineReport]) -> str:
    full = reports["full"]
    retrieved = reports["retrieved"]
    lines = [
        "# M2 Baseline Evidence",
        "",
        f"- Run ID: `{metadata['run_id']}`",
        f"- Evaluation timestamp: `{metadata['evaluation_timestamp']}`",
        f"- Provider: `{metadata['provider']}`",
        f"- Model: `{metadata['model']}`",
        f"- Dataset: `{metadata['dataset_path']}`",
        f"- Dataset SHA-256: `{metadata['dataset_sha256']}`",
        "",
        "## Methodology",
        "",
        (
            "The same 48-question deterministic dataset, model, prompt contract, "
            "generation settings, M1 policy, and PostgreSQL reader were used in both runs. "
            "The only intended difference was full queryable schema context versus "
            "retrieved/bounded context. Primary correctness is result equivalence against "
            "the gold query result; SQL text equality is not used."
        ),
        "",
        (
            "Generation settings: `temperature=0`, structured JSON response format, "
            "PostgreSQL chat-completions contract."
        ),
        "",
        "## Primary and pipeline results",
        "",
        "| Metric | Full Schema | Retrieved Context | Delta (retrieved - full) |",
        "|---|---:|---:|---:|",
    ]
    rows = [
        ("Result equivalence", full.result_equivalence_rate, retrieved.result_equivalence_rate),
        ("Parse success", full.parse_success_rate, retrieved.parse_success_rate),
        ("Plan acceptance", full.plan_acceptance_rate, retrieved.plan_acceptance_rate),
        ("Execution success", full.execution_success_rate, retrieved.execution_success_rate),
        (
            "Average context tables",
            full.average_context_tables,
            retrieved.average_context_tables,
        ),
        (
            "Average context columns",
            full.average_context_columns,
            retrieved.average_context_columns,
        ),
        ("Average prompt tokens", full.average_prompt_tokens, retrieved.average_prompt_tokens),
        (
            "Average generation latency (ms)",
            full.average_generation_latency_ms,
            retrieved.average_generation_latency_ms,
        ),
    ]
    for label, full_value, retrieved_value in rows:
        lines.append(_metric_row(label, full_value, retrieved_value))
    lines.extend(
        [
            "",
            "## Counts and failure attribution",
            "",
            "| Metric | Full Schema | Retrieved Context |",
            "|---|---:|---:|",
            f"| Total questions | {full.total_cases} | {retrieved.total_cases} |",
            _count_row(
                "Result-equivalent",
                full.result_equivalence_count,
                retrieved.result_equivalence_count,
            ),
            _count_row(
                "Parse success", full.parse_success_count, retrieved.parse_success_count
            ),
            _count_row(
                "Plan accepted", full.plan_acceptance_count, retrieved.plan_acceptance_count
            ),
            _count_row(
                "Execution success", full.execution_success_count, retrieved.execution_success_count
            ),
            _count_row(
                "Query cost rejection",
                full.query_cost_rejection_count,
                retrieved.query_cost_rejection_count,
            ),
            _count_row(
                "Policy rejection", full.policy_rejection_count, retrieved.policy_rejection_count
            ),
            _count_row(
                "Generation failure",
                full.generation_failure_count,
                retrieved.generation_failure_count,
            ),
            _count_row(
                "Retrieval miss (diagnostic)",
                full.retrieval_miss_count,
                retrieved.retrieval_miss_count,
            ),
            "",
            (
                "Failure attribution is assigned to the earliest observed pipeline stage. "
                "An executed but non-equivalent result is `RESULT_INCORRECT`; retrieval hit "
                "is reported separately and is not automatically treated as the cause of "
                "an incorrect result."
            ),
            "",
            "### Failure modes",
            "",
            "| Attribution | Full Schema | Retrieved Context |",
            "|---|---:|---:|",
        ]
    )
    failure_counts = sorted(
        {
            case.failure_attribution
            for report in reports.values()
            for case in report.cases
            if case.failure_attribution
        }
    )
    for attribution in failure_counts:
        lines.append(
            _count_row(
                attribution,
                _failure_count(full.cases, attribution),
                _failure_count(retrieved.cases, attribution),
            )
        )
    lines.extend(
        [
            "",
            "## Retrieval and context bounds",
            "",
            (
                f"Retrieved-context hit rate: **{_format_value(retrieved.retrieval_hit_rate)}**. "
                "The configured retrieved-mode bounds were recorded in metadata; selected "
                "context sizes are reported above. Full mode includes all queryable catalog "
                "objects and is the ablation control."
            ),
            "",
            "## Token and latency observations",
            "",
            (
                f"Full schema tokens: prompt total={_format_value(full.total_prompt_tokens)}, "
                f"completion total={_format_value(full.total_completion_tokens)}, "
                f"p50 latency={_format_value(full.p50_generation_latency_ms)} ms, "
                f"p95 latency={_format_value(full.p95_generation_latency_ms)} ms."
            ),
            (
                "Retrieved context tokens: "
                f"prompt total={_format_value(retrieved.total_prompt_tokens)}, "
                f"completion total={_format_value(retrieved.total_completion_tokens)}, "
                f"p50 latency={_format_value(retrieved.p50_generation_latency_ms)} ms, "
                f"p95 latency={_format_value(retrieved.p95_generation_latency_ms)} ms."
            ),
            "",
            "## Category breakdown",
            "",
            "| Category | Full Schema | Retrieved Context |",
            "|---|---:|---:|",
        ]
    )
    full_categories = _category_breakdown(full.cases)
    retrieved_categories = _category_breakdown(retrieved.cases)
    for category in sorted(full_categories):
        lines.append(
            _category_row(category, full_categories[category], retrieved_categories[category])
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            (
                "This is a 48-question M2 baseline over the synthetic commerce database, "
                "not the future M8 benchmark. No statistical significance is claimed. No "
                "retrieval or prompt tuning was performed after observing these results. No "
                "semantic metrics, value profiling, repair, clarification, answer synthesis, "
                "tenant/RLS, or UI behavior is measured here."
            ),
            "",
            (
                "M1 remains the sole SQL planning and execution authority; LLM output is "
                "always an untrusted `SqlCandidate`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _failure_count(cases: list[CaseEvaluation], attribution: str) -> int:
    return sum(case.failure_attribution == attribution for case in cases)


def _metric_row(label: str, full: Any, retrieved: Any) -> str:
    return (
        f"| {label} | {_format_value(full)} | {_format_value(retrieved)} | "
        f"{_format_value(_delta(full, retrieved))} |"
    )


def _count_row(label: str, full: int, retrieved: int) -> str:
    return f"| {label} | {full} | {retrieved} |"


def _category_row(category: str, full: dict[str, Any], retrieved: dict[str, Any]) -> str:
    full_rate = _format_value(full["result_equivalence_rate"])
    retrieved_rate = _format_value(retrieved["result_equivalence_rate"])
    full_count = f"{full['result_equivalence_count']}/{full['total']}"
    retrieved_count = f"{retrieved['result_equivalence_count']}/{retrieved['total']}"
    return f"| {category} | {full_rate} ({full_count}) | {retrieved_rate} ({retrieved_count}) |"


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _delta(full: Any, retrieved: Any) -> float | None:
    if isinstance(full, (int, float)) and isinstance(retrieved, (int, float)):
        return retrieved - full
    return None


def _format_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
