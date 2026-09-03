"""Pure offline helpers for the M2.10 oracle pass@K diagnostic."""

import json
from collections import Counter
from collections.abc import Iterable
from typing import Any

PASS_K_VALUES = (1, 2, 4, 8)


def pass_at_k(candidates: list[dict[str, Any]], k: int) -> bool:
    """Return whether any of the first k candidates is result-equivalent."""
    if k not in PASS_K_VALUES:
        raise ValueError(f"Unsupported pass@K value: {k}")
    if len(candidates) < k:
        raise ValueError("Candidate list is shorter than requested K")
    return any(candidate.get("result_equivalent") is True for candidate in candidates[:k])


def first_correct_candidate_index(candidates: list[dict[str, Any]]) -> int | None:
    """Return the one-based first correct candidate index, or None."""
    for candidate in candidates:
        if candidate.get("result_equivalent") is True:
            return int(candidate["candidate_index"])
    return None


def pass_k_counts(question_runs: Iterable[list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    runs = list(question_runs)
    total = len(runs)
    output: dict[str, dict[str, Any]] = {}
    for k in PASS_K_VALUES:
        count = sum(pass_at_k(candidates, k) for candidates in runs)
        output[f"pass@{k}"] = {
            "correct_questions": count,
            "total_questions": total,
            "rate": count / total if total else 0.0,
        }
    return output


def first_correct_histogram(question_runs: Iterable[list[dict[str, Any]]]) -> dict[str, int]:
    histogram = Counter(
        str(first_correct_candidate_index(candidates) or "NONE") for candidates in question_runs
    )
    return {key: histogram[key] for key in [*(str(index) for index in range(1, 9)), "NONE"]}


def diversity_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = {
        candidate["normalized_sql"] for candidate in candidates if candidate.get("normalized_sql")
    }
    signatures = {
        json.dumps(
            candidate.get("structural_signature") or candidate.get("generated_signature"),
            sort_keys=True,
        )
        for candidate in candidates
        if candidate.get("structural_signature") is not None
        or candidate.get("generated_signature") is not None
    }
    correct = any(candidate.get("result_equivalent") is True for candidate in candidates)
    unique_signature_count = len(signatures)
    if correct:
        classification = (
            "HIGH_DIVERSITY_WITH_CORRECT"
            if unique_signature_count > 1
            else "LOW_DIVERSITY_WITH_CORRECT"
        )
    elif unique_signature_count <= 1:
        classification = "LOW_DIVERSITY_FAILURE"
    else:
        classification = "HIGH_DIVERSITY_NO_CORRECT"
    return {
        "unique_raw_sql_count": len({candidate.get("generated_sql") for candidate in candidates}),
        "unique_normalized_sql_count": len(normalized),
        "unique_structural_signature_count": unique_signature_count,
        "diversity_classification": classification,
    }
