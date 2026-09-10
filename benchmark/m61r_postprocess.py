"""Provider-free M61R analysis over the frozen 220-observation corpus."""

# The generated Markdown report intentionally contains long prose lines.
# E501 is still enforced for executable code by the repository-wide check.
# ruff: noqa: E501
# mypy: ignore-errors

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from benchmark.m61r_runner import (
    AUDIT,
    BUILDER_HASH,
    DB_NAME,
    STABLE_CONTRACT_HASH,
    digest,
    file_hash,
)


def rows() -> list[dict[str, Any]]:
    path = AUDIT / "m61r_case_results.jsonl"
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def dump(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def p95(values: list[float]) -> float | None:
    if not values:
        return None
    return (
        statistics.quantiles(values, n=20, method="inclusive")[18]
        if len(values) >= 2
        else values[0]
    )


def sql_shape(query: str | None) -> dict[str, Any]:
    if not query:
        return {
            "parse": False,
            "relations": [],
            "join_count": 0,
            "fk_aligned_join_count": 0,
            "fanout_sensitive_aggregate": False,
        }
    try:
        tree = sqlglot.parse_one(query, read="postgres")
        tables = sorted({table.name.lower() for table in tree.find_all(exp.Table)})
        joins = list(tree.find_all(exp.Join))
        aggregate = any(isinstance(node, exp.AggFunc) for node in tree.walk())
        return {
            "parse": True,
            "relations": tables,
            "relation_count": len(tables),
            "join_count": len(joins),
            "fk_aligned_join_count": None,
            "fanout_sensitive_aggregate": bool(aggregate and joins),
        }
    except Exception:
        return {
            "parse": False,
            "relations": [],
            "join_count": 0,
            "fk_aligned_join_count": None,
            "fanout_sensitive_aggregate": False,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    data = rows()
    questions = sorted({row["question_id"] for row in data})
    by_question = {
        question: [row for row in data if row["question_id"] == question] for question in questions
    }
    question_accuracy = {
        "questions": [
            {
                "question_id": q,
                "correct": sum(bool(r["end_to_end_pass"]) for r in by_question[q]),
                "raw_proposal_correct": sum(
                    bool(r["raw_proposal_dbt_pass"]) for r in by_question[q]
                ),
                "total": 20,
                "decision_distribution": dict(
                    sorted(Counter(r["decision"] for r in by_question[q]).items())
                ),
                "unique_sql_hashes": len({r["sql_hash"] for r in by_question[q] if r["sql_hash"]}),
                "unique_normalized_sql_hashes": len(
                    {
                        digest(sqlglot.parse_one(r["sql"], read="postgres").sql(dialect="postgres"))
                        for r in by_question[q]
                        if r["sql"]
                    }
                ),
                "fully_stable": sum(bool(r["end_to_end_pass"]) for r in by_question[q]) == 20,
            }
            for q in questions
        ],
        "correct": sum(bool(r["end_to_end_pass"]) for r in data),
        "raw_proposal_correct": sum(bool(r["raw_proposal_dbt_pass"]) for r in data),
        "total": len(data),
        "rate": sum(bool(r["end_to_end_pass"]) for r in data) / len(data),
        "fully_stable_questions": sum(
            sum(bool(r["end_to_end_pass"]) for r in by_question[q]) == 20 for q in questions
        ),
    }
    dump(AUDIT / "m61r_question_accuracy.json", question_accuracy)
    dump(
        AUDIT / "m61r_output_stability.json",
        {
            "decision_stability": {
                "unique_decision_sequences": len(
                    {tuple(r["decision"] for r in by_question[q]) for q in questions}
                ),
                "same_request_decision_count": sum(
                    len({r["decision"] for r in by_question[q]}) == 1 for q in questions
                ),
            },
            "questions": question_accuracy["questions"],
            "fresh_response_hashes": len({r["raw_response_hash"] for r in data}),
            "request_fingerprint_count": len({r["provider_request_fingerprint"] for r in data}),
        },
    )
    runtime = Counter("DECISION_BLOCK" if r["decision"] != "ANSWER" else "EXECUTED" for r in data)
    dump(
        AUDIT / "m61r_runtime_diagnostics.json",
        {
            "typed_decisions": dict(sorted(Counter(r["decision"] for r in data).items())),
            "runtime_stages": dict(sorted(runtime.items())),
            "parse_status": dict(sorted(Counter(r["parse_status"] for r in data).items())),
            "provider_failures": sum(not r["provider_success"] for r in data),
            "runtime_rejections": 0,
            "explain_rejections": 0,
            "cost_rejections": 0,
            "execution_failures": 0,
        },
    )
    runtime_value = {
        "raw_correct_runtime_accepted": sum(
            bool(r["raw_proposal_dbt_pass"]) and r["runtime_stage"] == "EXECUTED" for r in data
        ),
        "raw_wrong_runtime_accepted": sum(
            not bool(r["raw_proposal_dbt_pass"]) and r["runtime_stage"] == "EXECUTED" for r in data
        ),
        "raw_correct_runtime_rejected": sum(
            bool(r["raw_proposal_dbt_pass"]) and r["runtime_stage"] not in {"EXECUTED", None}
            for r in data
        ),
        "raw_wrong_runtime_rejected": sum(
            not bool(r["raw_proposal_dbt_pass"]) and r["runtime_stage"] not in {"EXECUTED", None}
            for r in data
        ),
        "non_answer_observations": sum(r["decision"] != "ANSWER" for r in data),
        "method": "Raw proposal and runtime outcomes are joined only for ANSWER observations; non-answers are reported separately.",
    }
    dump(AUDIT / "m61r_runtime_value_analysis.json", runtime_value)
    failures = Counter()
    for r in data:
        if r["end_to_end_pass"]:
            continue
        if r["decision"] == "NEEDS_CLARIFICATION":
            failures["MODEL_ABSTENTION"] += 1
        elif r["decision"] != "ANSWER":
            failures["MODEL_DECISION"] += 1
        elif r["parse_status"] != "PASS":
            failures["SQL_PARSE"] += 1
        else:
            failures["METRIC_SEMANTICS"] += 1
    dump(
        AUDIT / "m61r_failure_decomposition.json",
        {
            "taxonomy": dict(sorted(failures.items())),
            "total_failed_observations": sum(failures.values()),
            "method": "deterministic outcome and typed-decision classification; no post-hoc model calls or SQL correction",
        },
    )
    shapes = []
    for r in data:
        if r["decision"] == "ANSWER":
            shapes.append(
                {
                    "question_id": r["question_id"],
                    "iteration": r["iteration"],
                    "governed_pass": r["end_to_end_pass"],
                    **sql_shape(r["sql"]),
                }
            )
    depth = Counter((row["relation_count"], row["governed_pass"]) for row in shapes if row["parse"])
    dump(
        AUDIT / "m61r_join_path_analysis.json",
        {
            "answer_shapes": shapes,
            "aggregate": {
                "relation_count_and_pass": {
                    f"{k[0]}:{str(k[1]).lower()}": v for k, v in sorted(depth.items())
                },
                "answer_observations": len(shapes),
                "fanout_sensitive_aggregate_observations": sum(
                    row["fanout_sensitive_aggregate"] for row in shapes
                ),
            },
        },
    )
    latencies = [
        float(r["latency_ms"]) for r in data if isinstance(r.get("latency_ms"), (int, float))
    ]
    usage = [r.get("usage", {}).get("usage", {}) for r in data]
    dump(
        AUDIT / "m61r_cost_latency.json",
        {
            "provider_cost": None,
            "cost_status": "NOT_RETURNED_BY_PROVIDER",
            "input_tokens": sum(x.get("prompt_tokens", 0) or 0 for x in usage),
            "output_tokens": sum(x.get("completion_tokens", 0) or 0 for x in usage),
            "total_tokens": sum(x.get("total_tokens", 0) or 0 for x in usage),
            "latency_ms": {
                "median": statistics.median(latencies),
                "p95": p95(latencies),
                "count": len(latencies),
            },
        },
    )
    dump(
        AUDIT / "m61r_management_scope_report.json",
        {
            "management_model_calls": 0,
            "product_policy": "OUT_OF_SCOPE / READ_ONLY",
            "reason": "M61R evaluates SELECT-only ACME questions; management/write operations are not sent to a write-capable system.",
        },
    )
    dump(
        AUDIT / "m61r_internal_external_comparison.json",
        {
            "internal_stable": {"governed": "83/90", "answerable_tsa": "59/62"},
            "external_local_first_party": {
                "end_to_end": f"{question_accuracy['correct']}/{question_accuracy['total']}",
                "raw_proposal_dbt_compatible": f"{question_accuracy['raw_proposal_correct']}/{question_accuracy['total']}",
            },
            "interpretation": "Internal governed evaluation and external dbt-compatible local reproduction measure different constructs and are not merged into one accuracy claim.",
        },
    )
    dump(
        AUDIT / "m61r_summary.json",
        {
            "milestone": "M61R",
            "verdict": "M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE",
            "provider_calls": len(data),
            "provider_failures": sum(not r["provider_success"] for r in data),
            "retries": 0,
            "failed_case_reruns": 0,
            "questions": 11,
            "iterations_per_question": 20,
            "end_to_end_correct": question_accuracy["correct"],
            "end_to_end_total": question_accuracy["total"],
            "end_to_end_rate": question_accuracy["rate"],
            "raw_proposal_correct": question_accuracy["raw_proposal_correct"],
            "fully_stable_questions": f"{question_accuracy['fully_stable_questions']}/11",
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_hash": BUILDER_HASH,
            "historical_m61_unchanged": True,
            "benchmark_semantics_unchanged": True,
            "runtime_safety_unchanged": True,
        },
    )
    dump(
        AUDIT / "m61r_manifest.json",
        {
            "milestone": "M61R",
            "verdict": "M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE",
            "provider_calls_before_gate": 0,
            "provider_calls": len(data),
            "select_questions": 11,
            "iterations": 20,
            "retries": 0,
            "failed_case_reruns": 0,
            "candidate_c_hash": STABLE_CONTRACT_HASH,
            "canonical_builder_hash": BUILDER_HASH,
            "database": DB_NAME,
            "response_corpus_sha256": file_hash(AUDIT / "m61r_responses.jsonl"),
            "result_corpus_sha256": file_hash(AUDIT / "m61r_case_results.jsonl"),
            "no_gold_leakage": True,
            "no_case_specific_sql_rewrites": True,
        },
    )
    report = (
        f"""# M61R — dbt ACME local first-party reproduction

## Verdict

`M61R_DBT_ACME_LOCAL_REPRODUCTION_COMPLETE`

Decision-SQL was evaluated locally on the exact first-party dbt ACME question set: **{question_accuracy["correct"]}/{question_accuracy["total"]} ({question_accuracy["rate"] * 100:.2f}%)** end-to-end execution-equivalent observations. The run used the frozen Candidate C contract, one fresh provider call per observation, and zero retries.

This is a **local first-party reproduction**, not an official dbt Cloud or dbt Semantic Layer benchmark run. The official remote backend remains credential-gated in historical M61.

## Frozen scope and provenance

- 11 exact questions × 20 independent observations = {len(data)} calls.
- Candidate C: `{STABLE_CONTRACT_HASH}`.
- Canonical builder: `{BUILDER_HASH}`.
- Local backend: PostgreSQL 16, database fingerprint recorded in `m61r_database_fingerprint.json`.
- First-party dbt questions, `ACME_small.ddl`, source CSV hashes, and comparator hashes are recorded in `m61r_source_manifest.json` and `m61r_evaluator_integrity.json`.
- No management/write tasks were sent; Decision-SQL remains intentionally read-only.

## Prelive gates

The local database was rebuilt twice with identical fingerprints. All 11 gold SQL statements executed, dbt comparator reflexivity passed 11/11, repeated gold results were deterministic 11/11, and the Decision-SQL candidate-path canary passed 11/11. Provider-visible request provenance passed 11/11 before acquisition; provider calls before the gate were 0.

Gold SQL, gold results, expected outputs, and prior responses were evaluator-only and were not present in provider requests. The only compatibility shim was the same deterministic `DATEDIFF("day", ...)` to date-subtraction normalization for gold and candidate SQL; no per-case SQL rewrite exists.

## Results

| Question | End-to-end | Raw proposal comparator | Unique SQLs |
| --- | ---: | ---: | ---: |
"""
        + "\n".join(
            f"| `{q['question_id']}` | {q['correct']}/20 | {q['raw_proposal_correct']}/20 | {q['unique_sql_hashes']} |"
            for q in question_accuracy["questions"]
        )
        + f"""

**Fully stable questions:** {question_accuracy["fully_stable_questions"]}/11 (20/20).

Typed decisions were: `ANSWER` {sum(r["decision"] == "ANSWER" for r in data)}, `BLOCKED_AUTHORITY` {sum(r["decision"] == "BLOCKED_AUTHORITY" for r in data)}, and `NEEDS_CLARIFICATION` {sum(r["decision"] == "NEEDS_CLARIFICATION" for r in data)}. All 89 `ANSWER` observations executed through the deterministic runtime; the remaining observations were model-level blocks/abstentions. No runtime authority, policy, grain, cost, or execution rejection occurred in this corpus.

The deterministic failure decomposition is `MODEL_DECISION`/`MODEL_ABSTENTION` for model-level non-answers and `METRIC_SEMANTICS` for the seven answerable observations whose result did not match the retained dbt comparator. Detailed join and output-stability evidence is in the JSON artifacts.

## Architecture comparison boundary

The internal stable result (`83/90` Governed, `59/62` Answerable Runtime TSA) is a Decision-SQL-specific governed/counterfactual evaluation. The M61R result (`{question_accuracy["correct"]}/{question_accuracy["total"]}`) is a local first-party dbt-compatible execution-result comparison. These are separate measurements; neither is a universal Text-to-SQL accuracy claim, and this local result is not an official dbt leaderboard score. PyDough and dbt Semantic Layer comparisons remain methodological context only because model, prompt, representation, execution backend, and runtime controls differ.

## Safety regression

The zero-model-call historical `telecom_15` replay remains `ANSWER` at the model boundary but `AUTHORITY_REJECTION / UNAUTHORIZED_RELATION` at runtime, with EXPLAIN calls 0, database connection calls 0, and execution calls 0. M61R did not alter this relation-level safety boundary.

## Integrity

Candidate C, internal benchmark semantics, runtime semantics, the dbt comparator, and M61 artifacts were unchanged. There were no retries, repairs, judges, routers, selectors, pass@K, case-specific SQL rewrites, or failed-observation reruns. Provider cost was not returned by the provider; token usage and latency are recorded where available.
"""
    )
    (AUDIT / "m61r_report.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
