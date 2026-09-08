import json

# ruff: noqa: E501
from benchmark.context import render_governed_context
from benchmark.models import ResultContract, Submission, compare_rows
from benchmark.mutations import supported_categories
from benchmark.validator import load_pilot, validate_structure


def test_pilot_distribution_and_model_truth_boundary() -> None:
    structure = validate_structure()
    assert structure["passed"]
    for case, _truth in load_pilot():
        assert "semantic_target" not in case
        assert "reference_implementation_a" not in case


def test_visible_context_contains_authority_but_not_ground_truth() -> None:
    context = render_governed_context("fleet_ops")
    assert context["context_profile"] == "GOVERNED_CONTEXT_V1"
    assert any(
        item["relationship_id"].endswith("trip_vehicle")
        for item in context["authorized_relationships"]
    )
    assert all("reference_implementation" not in json.dumps(context) for _ in [0])


def test_typed_comparator_preserves_duplicates_and_unordered_semantics() -> None:
    contract = ResultContract(column_count=1)
    assert compare_rows([(1,), (1,), (2,)], [(2,), (1,), (1,)], contract)[0]
    assert not compare_rows([(1,), (1,)], [(1,)], contract)[0]
    ordered = ResultContract(column_count=1, row_order=True)
    assert not compare_rows([(1,), (2,)], [(2,), (1,)], ordered)[0]
    tolerant = ResultContract(column_count=1, numeric_tolerance="0.01")
    assert compare_rows([(1.001,)], [(1.009,)], tolerant)[0]


def test_submission_contract_and_mutation_vocabulary() -> None:
    submission = Submission.from_dict(
        {
            "case_id": "commerce_10",
            "decision": "BLOCKED_POLICY",
            "sql": None,
            "reason_code": "READ_ONLY",
        }
    )
    assert submission.decision == "BLOCKED_POLICY"
    assert "where_filter_scope" in supported_categories()
    assert "inner_left_join" in supported_categories()
