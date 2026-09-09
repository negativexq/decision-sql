from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from benchmark.m51a_authoring import DOMAINS, new_cases


def test_m51a_expansion_has_frozen_shape_without_model_calls() -> None:
    rows = new_cases()
    assert len(rows) == 90
    assert Counter(case["task_type"] for case, _ in rows) == {
        "ANSWERABLE": 60,
        "AUTHORITY_BLOCKED": 15,
        "AMBIGUOUS": 9,
        "POLICY_BLOCKED": 6,
    }
    assert len(DOMAINS) == 6
    assert all(
        sum(case["database_id"] == domain.domain_id for case, _ in rows) == 15 for domain in DOMAINS
    )


def test_m51a_case_numbering_does_not_encode_task_type() -> None:
    rows = new_cases()
    for domain in DOMAINS:
        cases = [case for case, _ in rows if case["database_id"] == domain.domain_id]
        assert len({case["case_id"].rsplit("_", 1)[-1] for case in cases}) == 15
        assert len({case["task_type"] for case in cases[:5]}) > 1


def test_m51a_model_visible_case_boundary_has_no_truth_fields() -> None:
    rows = new_cases()
    allowed = {"case_id", "database_id", "question", "task_type", "context_profile", "provenance"}
    forbidden = {
        "semantic_target",
        "reference_implementation_a",
        "reference_implementation_b",
        "counterfactual_fixtures",
        "semantic_mutants",
    }
    for case, _ in rows:
        assert set(case) == allowed
        assert not forbidden.intersection(case)


def test_m51a_generated_manifests_are_zero_call_and_unscored() -> None:
    manifest = json.loads(Path("benchmark/manifests/m51a_180_case_manifest.json").read_text())
    assert manifest["provider_calls"] == 0
    assert manifest["model_calls"] == 0
    assert manifest["model_evaluation"] == "NOT_RUN"
