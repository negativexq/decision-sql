from evaluation.m210_passk import (
    diversity_summary,
    first_correct_candidate_index,
    pass_at_k,
    pass_k_counts,
)


def _candidates(values: list[bool]) -> list[dict[str, object]]:
    return [
        {
            "candidate_index": index,
            "result_equivalent": value,
            "generated_sql": f"SELECT {index};",
            "normalized_sql": f"SELECT {index};",
            "structural_signature": {"tables": (f"t{index}",)},
        }
        for index, value in enumerate(values, start=1)
    ]


def test_pass_at_k_uses_cumulative_candidate_order() -> None:
    candidates = _candidates([False, False, True, False, False, False, False, False])

    assert pass_at_k(candidates, 1) is False
    assert pass_at_k(candidates, 2) is False
    assert pass_at_k(candidates, 4) is True
    assert pass_at_k(candidates, 8) is True
    assert first_correct_candidate_index(candidates) == 3


def test_pass_k_counts_and_histogram_inputs_are_offline_only() -> None:
    runs = [
        _candidates([True, False, False, False, False, False, False, False]),
        _candidates([False] * 8),
    ]

    summary = pass_k_counts(runs)

    assert summary["pass@1"] == {"correct_questions": 1, "total_questions": 2, "rate": 0.5}
    assert summary["pass@8"]["correct_questions"] == 1


def test_diversity_classifies_correct_and_low_diversity_failure() -> None:
    correct = _candidates([False, True, False, False, False, False, False, False])
    failed = _candidates([False] * 8)
    for candidate in failed:
        candidate["structural_signature"] = {"tables": ("same",)}

    assert diversity_summary(correct)["diversity_classification"] == "HIGH_DIVERSITY_WITH_CORRECT"
    assert diversity_summary(failed)["diversity_classification"] == "LOW_DIVERSITY_FAILURE"


def test_diversity_accepts_persisted_generated_signature_field() -> None:
    candidates = _candidates([False] * 8)
    for candidate in candidates:
        candidate.pop("structural_signature")
        candidate["generated_signature"] = {"tables": ("same",)}

    assert diversity_summary(candidates)["unique_structural_signature_count"] == 1
