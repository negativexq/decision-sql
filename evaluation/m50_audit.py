"""Provider-free M5.0 semantic-verifier feasibility audit.

The verifier receives only runtime-legitimate inputs. Gold SQL and correctness
labels are held by this evaluation module and are used after verification only
to score the diagnostic signals.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.memory.models import VerifiedQueryExample
from app.verification import (
    RULESET_HASH,
    VERIFIER_VERSION,
    DeterministicSemanticVerifier,
    VerificationSeverity,
)
from evaluation.m4_benchmark import build_benchmark, build_memory_corpus

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEV_DIR = ROOT / "evaluation/results/m4/dev/20260903T235000Z"
DEFAULT_HOLDOUT_DIR = ROOT / "evaluation/results/m4/holdout/20260904T000000Z"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the provider-free M5.0 verifier audit")
    parser.add_argument("--phase", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--accepted-rules", type=Path)
    args = parser.parse_args()

    source_dir = args.source_dir or (
        DEFAULT_DEV_DIR if args.phase == "dev" else DEFAULT_HOLDOUT_DIR
    )
    artifact_dir = args.artifact_dir or _new_artifact_dir(args.phase)
    artifact_dir.mkdir(parents=True, exist_ok=False)

    memory = build_memory_corpus()
    benchmark = {question.question_id: question for question in build_benchmark()}
    candidates = _load_candidates(source_dir, args.phase, benchmark, memory)
    verifier = DeterministicSemanticVerifier()
    reports = _verify_candidates(candidates, verifier, memory)
    all_rule_stats = _rule_stats(reports)
    accepted_rules = (
        _accepted_hard_rules(all_rule_stats)
        if args.phase == "dev"
        else _load_accepted_rules(args.accepted_rules)
    )
    summary = _summary(candidates, reports, all_rule_stats, accepted_rules, args.phase)

    _write_json(artifact_dir / "metadata.json", _metadata(args.phase, source_dir, memory))
    _write_json(artifact_dir / "accepted_rules.json", {"rules": accepted_rules})
    _write_json(
        artifact_dir / "ruleset_manifest.json",
        {
            "verifier_version": VERIFIER_VERSION,
            "ruleset_hash": RULESET_HASH,
            "candidate_rules": [code.value for code, _ in verifier_rules()],
            "accepted_hard_rules": accepted_rules,
        },
    )
    _write_jsonl(artifact_dir / f"{args.phase}_signal_results.jsonl", reports)
    _write_json(artifact_dir / f"{args.phase}_signal_summary.json", summary)
    _write_json(
        artifact_dir / "clarification_audit.json",
        {
            "sample_count": 0,
            "taxonomy": {},
            "candidate_precision": None,
            "classification": "INSUFFICIENT_CLARIFICATION_SAMPLE",
        },
    )
    _write_json(artifact_dir / "final_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def verifier_rules() -> tuple[tuple[Any, VerificationSeverity], ...]:
    from app.verification.verifier import _RULES

    return _RULES


def _load_candidates(
    source_dir: Path,
    split: str,
    benchmark: dict[str, Any],
    memory: tuple[VerifiedQueryExample, ...],
) -> list[dict[str, Any]]:
    memory_by_id = {example.example_id: example for example in memory}
    rows: list[dict[str, Any]] = []
    for arm_name in ("baseline", "memory"):
        path = source_dir / f"{split}_{arm_name}_results.jsonl"
        for line in path.read_text().splitlines():
            result = json.loads(line)
            question = benchmark[result["question_id"]]
            retrieved = tuple(
                memory_by_id[example_id]
                for example_id in result.get("retrieved_example_ids", ())
                if example_id in memory_by_id
            )
            rows.append(
                {
                    "candidate_id": f"{result['question_id']}:{result['arm']}",
                    "question_id": result["question_id"],
                    "split": split,
                    "arm": result["arm"],
                    "family": result["family"],
                    "question": question.question,
                    "generated_sql": result["generated_sql"],
                    "correct": bool(result["correct"]),
                    "plan_accepted": bool(result["plan_accepted"]),
                    "execution_success": bool(result["execution_success"]),
                    "retrieved_example_ids": result.get("retrieved_example_ids", []),
                    "memory_examples": retrieved,
                }
            )
    return rows


def _verify_candidates(
    candidates: list[dict[str, Any]],
    verifier: DeterministicSemanticVerifier,
    memory: tuple[VerifiedQueryExample, ...],
) -> list[dict[str, Any]]:
    del memory
    reports: list[dict[str, Any]] = []
    for candidate in candidates:
        started = perf_counter()
        report = verifier.verify(
            candidate["question"],
            candidate["generated_sql"],
            memory_examples=candidate["memory_examples"],
            generation_path=(
                "DIRECT_SQL_WITH_VERIFIED_MEMORY" if candidate["arm"] == "MEMORY" else "DIRECT_SQL"
            ),
        )
        reports.append(
            {
                "candidate_id": candidate["candidate_id"],
                "question_id": candidate["question_id"],
                "split": candidate["split"],
                "arm": candidate["arm"],
                "family": candidate["family"],
                "correct": candidate["correct"],
                "plan_accepted": candidate["plan_accepted"],
                "execution_success": candidate["execution_success"],
                "retrieved_example_ids": candidate["retrieved_example_ids"],
                "signals": [signal.model_dump(mode="json") for signal in report.signals],
                "recommendation": report.recommendation.value,
                "verifier_latency_ms": (perf_counter() - started) * 1000,
            }
        )
    return reports


def _rule_stats(reports: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    incorrect_total = sum(not report["correct"] for report in reports)
    stats: dict[str, dict[str, Any]] = {}
    for code, severity in verifier_rules():
        fired = [
            report
            for report in reports
            if any(signal["code"] == code.value for signal in report["signals"])
        ]
        true_positive = sum(not report["correct"] for report in fired)
        false_positive = sum(report["correct"] for report in fired)
        stats[code.value] = {
            "severity": severity.value,
            "support": len(fired),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "precision": true_positive / len(fired) if fired else 0.0,
            "recall_contribution": true_positive / incorrect_total if incorrect_total else 0.0,
        }
    return stats


def _accepted_hard_rules(stats: dict[str, dict[str, Any]]) -> list[str]:
    return sorted(
        code
        for code, values in stats.items()
        if values["severity"] == VerificationSeverity.HARD.value
        and int(values["support"]) >= 3
        and float(values["precision"]) >= 0.90
    )


def _load_accepted_rules(path: Path | None) -> list[str]:
    if path is None:
        raise SystemExit("--accepted-rules is required for holdout")
    payload = json.loads(path.read_text())
    return sorted(str(rule) for rule in payload["rules"])


def _summary(
    candidates: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    rule_stats: dict[str, dict[str, int | float]],
    accepted_rules: list[str],
    split: str,
) -> dict[str, Any]:
    accepted = set(accepted_rules)
    flagged = [
        report
        for report in reports
        if any(
            signal["code"] in accepted and signal["severity"] == "HARD"
            for signal in report["signals"]
        )
    ]
    true_positive = sum(not report["correct"] for report in flagged)
    per_arm = {}
    by_family = {}
    for arm in ("BASELINE", "MEMORY"):
        arm_candidates = [c for c in candidates if c["arm"] == arm]
        arm_reports = [r for r in reports if r["arm"] == arm]
        arm_flagged = [
            report
            for report in arm_reports
            if any(
                signal["code"] in accepted and signal["severity"] == "HARD"
                for signal in report["signals"]
            )
        ]
        arm_tp = sum(not report["correct"] for report in arm_flagged)
        per_arm[arm] = _detection_metrics(arm_tp, len(arm_flagged), arm_candidates)
        for family in sorted({c["family"] for c in arm_candidates}):
            family_candidates = [c for c in arm_candidates if c["family"] == family]
            family_ids = {c["candidate_id"] for c in family_candidates}
            family_flagged = [r for r in arm_flagged if r["candidate_id"] in family_ids]
            by_family[f"{arm}:{family}"] = {
                "total": len(family_candidates),
                "flagged": len(family_flagged),
                "incorrect": sum(not c["correct"] for c in family_candidates),
                "true_positive": sum(not r["correct"] for r in family_flagged),
            }
    return {
        "split": split,
        "verifier_version": VERIFIER_VERSION,
        "ruleset_hash": RULESET_HASH,
        "candidate_count": len(candidates),
        "candidate_count_by_arm": dict(Counter(candidate["arm"] for candidate in candidates)),
        "correctness_by_arm": {
            arm: {
                "correct": sum(c["correct"] for c in candidates if c["arm"] == arm),
                "total": sum(c["arm"] == arm for c in candidates),
            }
            for arm in ("BASELINE", "MEMORY")
        },
        "accepted_hard_rules": accepted_rules,
        "any_hard": _detection_metrics(true_positive, len(flagged), candidates),
        "rule_stats": rule_stats,
        "rule_stats_by_arm": {
            arm: _rule_stats([report for report in reports if report["arm"] == arm])
            for arm in ("BASELINE", "MEMORY")
        },
        "by_arm": per_arm,
        "by_family": by_family,
        "recommendation_counts": dict(Counter(report["recommendation"] for report in reports)),
        "signal_counts": dict(
            Counter(signal["code"] for report in reports for signal in report["signals"])
        ),
        "signal_counts_by_arm": {
            arm: dict(
                Counter(
                    signal["code"]
                    for report in reports
                    if report["arm"] == arm
                    for signal in report["signals"]
                )
            )
            for arm in ("BASELINE", "MEMORY")
        },
        "latency_ms": _latency_summary(reports),
        "insufficient_sample_rules": sorted(
            code for code, values in rule_stats.items() if int(values["support"]) < 3
        ),
    }


def _latency_summary(reports: list[dict[str, Any]]) -> dict[str, float]:
    values = sorted(float(report["verifier_latency_ms"]) for report in reports)
    if not values:
        return {"average": 0.0, "p50": 0.0, "p95": 0.0}
    return {
        "average": sum(values) / len(values),
        "p50": values[(len(values) - 1) // 2],
        "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
    }


def _detection_metrics(
    true_positive: int, flagged_count: int, candidates: list[dict[str, Any]]
) -> dict[str, int | float]:
    incorrect = sum(not candidate["correct"] for candidate in candidates)
    correct = len(candidates) - incorrect
    false_positive = flagged_count - true_positive
    not_flagged_incorrect = incorrect - true_positive
    not_flagged = len(candidates) - flagged_count
    return {
        "fired": flagged_count,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "precision": true_positive / flagged_count if flagged_count else 0.0,
        "incorrect_recall": true_positive / incorrect if incorrect else 0.0,
        "correct_query_false_positive_rate": false_positive / correct if correct else 0.0,
        "continue_wrong_rate": not_flagged_incorrect / not_flagged if not_flagged else 0.0,
    }


def _metadata(
    split: str, source_dir: Path, memory: tuple[VerifiedQueryExample, ...]
) -> dict[str, Any]:
    from evaluation.m4_benchmark import benchmark_hash, corpus_hash

    benchmark = build_benchmark()
    return {
        "phase": split,
        "source_dir": str(source_dir),
        "provider_calls": 0,
        "verifier_version": VERIFIER_VERSION,
        "ruleset_hash": RULESET_HASH,
        "memory_corpus_hash": corpus_hash(memory),
        "m4_benchmark_hash": benchmark_hash(benchmark),
        "m4_dev_hash": _split_hash(benchmark, "dev"),
        "m4_holdout_hash": _split_hash(benchmark, "holdout"),
    }


def _split_hash(benchmark: tuple[Any, ...], split: str) -> str:
    import hashlib

    value = [question.model_dump(mode="json") for question in benchmark if question.split == split]
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _new_artifact_dir(split: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return ROOT / "evaluation/results/m50" / split / stamp


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


if __name__ == "__main__":
    main()
