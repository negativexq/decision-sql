import json
from pathlib import Path

import pytest

from benchmark.model_contract import (
    build_all_requests,
    context_hash,
    request_leakage,
    serialize_governed_context_v1,
)
from benchmark.models import ResultContract, Submission, compare_rows
from benchmark.validator import load_pilot


def test_model_requests_are_complete_and_do_not_expose_evaluator_labels():
    cases = {case["case_id"]: case for case, _truth in load_pilot()}
    requests = build_all_requests()

    assert len(requests) == 30
    assert {request.database_id for request in requests} == {
        "commerce_ops",
        "fleet_ops",
        "support_ops",
    }
    for request in requests:
        assert request.question == cases[request.case_id]["question"]
        assert request.instructions in request.request_text
        assert request.question in request.request_text
        assert request.serialized_context in request.request_text
        assert request_leakage(request, cases[request.case_id]) == []
        assert "semantic_target" not in request.request_text
        assert "reference_implementation" not in request.request_text
        assert "counterfactual_fixtures" not in request.request_text
        assert "semantic_mutants" not in request.request_text


def test_context_serialization_is_stable_and_database_scoped():
    requests = build_all_requests()
    for database_id in {request.database_id for request in requests}:
        serialized = {
            request.serialized_context
            for request in requests
            if request.database_id == database_id
        }
        assert len(serialized) == 1
        assert next(iter(serialized)).endswith("\n")
        assert context_hash(database_id)
    assert len({context_hash(request.database_id) for request in requests}) == 3
    assert "authorized_relationships" in serialize_governed_context_v1("support_ops")


def test_governance_leakage_audits_cover_hidden_evidence_without_hiding_public_rules():
    cases = {case["case_id"]: (case, truth) for case, truth in load_pilot()}
    requests = {request.case_id: request for request in build_all_requests()}

    for case_id, (case, truth) in cases.items():
        request = requests[case_id]
        if case["task_type"] == "AUTHORITY_BLOCKED":
            assert (
                truth["evidence"]["missing_authority"].lower()
                not in request.request_text.lower()
            )
            assert "BLOCKED_AUTHORITY" in request.request_text
        if case["task_type"] == "AMBIGUOUS":
            assert truth["evidence"]["interpretation_a"].lower() not in request.request_text.lower()
            assert truth["evidence"]["interpretation_b"].lower() not in request.request_text.lower()
        if case["task_type"] == "POLICY_BLOCKED":
            assert "read-only policy" in request.request_text.lower()


def test_support_08_authority_trap_is_fairly_visible_but_not_authorized():
    cases = {case["case_id"]: (case, truth) for case, truth in load_pilot()}
    request = next(item for item in build_all_requests() if item.case_id == "support_08")
    case, truth = cases["support_08"]

    assert request.question == case["question"]
    assert "numeric ticket ID equals the incident ID" in request.question
    assert truth["required_authority"][0] not in request.serialized_context
    assert "email" not in request.question.lower()


def test_result_contract_ordering_tolerance_and_duplicate_defaults_are_explicit():
    assert compare_rows([(2,), (1,)], [(1,), (2,)], ResultContract(column_count=1))[0]
    ordered = ResultContract(column_count=1, row_order=True)
    assert not compare_rows([(2,), (1,)], [(1,), (2,)], ordered)[0]
    tolerant = ResultContract(column_count=1, numeric_tolerance="0.01")
    assert compare_rows([(1.001,)], [(1.009,)], tolerant)[0]
    assert ResultContract(column_count=1).duplicates_significant is True


def test_submission_contract_is_strict_and_fail_closed():
    valid = {
        "case_id": "synthetic_01",
        "decision": "BLOCKED_AUTHORITY",
        "sql": None,
        "reason_code": "MISSING_AUTHORIZED_RELATIONSHIP",
    }
    assert Submission.from_dict(valid).decision == "BLOCKED_AUTHORITY"

    invalid_cases = [
        {**valid, "extra": True},
        {**valid, "decision": "BLOCKED_AUTHORITY", "reason_code": "NO_REASON"},
        {**valid, "decision": "ANSWER", "sql": "   ", "reason_code": None},
        {
            **valid,
            "decision": "NEEDS_CLARIFICATION",
            "reason_code": "AMBIGUOUS_SEMANTICS",
            "sql": "SELECT 1",
        },
    ]
    for value in invalid_cases:
        with pytest.raises(ValueError):
            Submission.from_dict(value)


def test_frozen_experiment_and_generated_artifacts_are_present():
    root = Path(__file__).parents[2]
    config = json.loads((root / "benchmark/experiments/m35_luna_none.json").read_text())
    manifest = json.loads((root / "benchmark/manifests/m34_3_model_contract.json").read_text())
    ledger = json.loads((root / "benchmark/manifests/m34_3_request_ledger.json").read_text())

    assert config["model"] == "gpt-5.6-luna"
    assert config["reasoning"] == "none"
    assert config["calls_per_case"] == 1
    assert config["repair"] is False
    assert manifest["provider_calls"] == 0
    assert manifest["case_count"] == 30
    assert ledger["provider_calls"] == 0
    assert len(ledger["requests"]) == 30
    assert all(set(row) == {
        "case_id", "database_id", "question_sha256", "context_sha256",
        "instruction_sha256", "submission_schema_sha256", "full_request_sha256", "request_bytes",
    } for row in ledger["requests"])
