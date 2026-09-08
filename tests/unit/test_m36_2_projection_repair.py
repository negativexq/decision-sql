"""Offline consistency gates for the M36.2 repaired pilot contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from benchmark import BENCHMARK_VERSION
from benchmark.model_contract import (
    build_all_requests,
    frozen_benchmark_content_hash,
    request_leakage,
)
from benchmark.validator import load_pilot

ROOT = Path(__file__).resolve().parents[2]
M35R1 = ROOT / "benchmark" / "experiments" / "results" / "m35r1"
PROJECTION_AUDIT = ROOT / "benchmark" / "audits" / "m36_2_projection_audit.json"
POST_AUDIT = ROOT / "benchmark" / "audits" / "m36_2_post_repair_audit.json"
LEDGER = ROOT / "benchmark" / "manifests" / "m36_2_request_ledger.json"
FROZEN_M35R1 = {
    "m35r1_manifest.json": "48be7c7a1eab5f7888fec01be50fe3a33a26088e0314e1f88f14bae35eefb6db",
    "m35r1_request_ledger.json": "e8d8e3db1e910195d2299f09ea3df22b3beba78e0368341db43c0e723100a836",
    "m35r1_raw_responses.jsonl": "cda7782eb497d2f454f3d34b1304c8e57cf81a21c7d0fe830125f06710bb81ac",
    "m35r1_parsed_submissions.jsonl": (
        "b935349c05b28fb22a594e369dcf5de8ed2f50a2b53aa500cf6851e5bb932a0f"
    ),
    "m35r1_case_results.json": "a815c03e5310a07cb45660144e3f2876b1e8cde3697e03162683d20b0f96b123",
    "m35r1_summary.json": "eba12f8ea6f184faa33bf4046caff01037719949d76f37d89c0004736adbdcae",
    "m35r1_summary.md": "714083d0321ffdb6e37666fa6dfda566c7dbc7adbb34b9eb04d952cbd7946c94",
}


def _json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_m36_2_projection_contracts_are_visible_and_exact() -> None:
    audit = _json(PROJECTION_AUDIT)
    assert audit["benchmark_version"] == BENCHMARK_VERSION == "0.1.2-pilot"
    assert audit["answerable_total"] == 20
    assert audit["projection_contracts"] == 20
    assert audit["visible_projection_provenance"] == 20
    assert audit["hidden_exact_projection_requirements"] == 0
    assert audit["unresolved_ambiguities"] == 0
    assert audit["passed"] is True
    assert all(item["label"] == "EXPLICIT_SUFFICIENT" for item in audit["cases"])


def test_m36_2_requests_have_no_evaluator_projection_leakage() -> None:
    ledger = _json(LEDGER)
    requests = build_all_requests()
    assert len(requests) == 30
    assert ledger["provider_calls"] == 0
    assert ledger["counts"] == {
        "database_context": 30,
        "exact_case_id": 30,
        "exact_question": 30,
        "governance_instructions": 30,
        "leakage_cases": 0,
        "projection_gold_leakage": 0,
        "requests": 30,
    }
    for request in requests:
        assert not request_leakage(request)
        assert "projection_contract" not in request.request_text
        assert "reference_result_summaries" not in request.request_text


def test_m36_2_post_repair_quality_gate_is_clean() -> None:
    post = _json(POST_AUDIT)
    assert post["provider_calls"] == 0
    assert post["status_counts"] == {"CLEAN": 30, "REVIEW_REQUIRED": 0}
    assert post["context_sufficiency"] == {"answerable": 20, "passed": 20}
    assert post["authority_completeness"]["answerable"] == 20
    assert post["authority_completeness"]["passed"] is True
    assert post["governance_validation"] == {
        "non_answerable_cases": 10,
        "passed": True,
        "policy_visibility": True,
    }
    assert post["model_contract_visibility"] == {
        "strict_projection_public": True,
        "case_specific_gold_outputs_present": False,
    }
    assert post["reference_validation"] == {
        "agreement": True,
        "cases": 40,
        "fixture_comparisons": 62,
    }
    assert post["mutation_validation"]["invalid_mutants"] == 0
    assert post["mutation_validation"]["survived"] == 0
    assert post["mutation_validation"]["killed"] == post["mutation_validation"]["executed"]
    assert post["passed"] is True


def test_m36_2_has_exactly_30_cases_and_frozen_m35r1_is_untouched() -> None:
    rows = load_pilot()
    assert len(rows) == 30
    assert sum(case["task_type"] == "ANSWERABLE" for case, _truth in rows) == 20
    assert sum(case["task_type"] == "AUTHORITY_BLOCKED" for case, _truth in rows) == 5
    assert sum(case["task_type"] == "AMBIGUOUS" for case, _truth in rows) == 3
    assert sum(case["task_type"] == "POLICY_BLOCKED" for case, _truth in rows) == 2
    for name, expected in FROZEN_M35R1.items():
        assert hashlib.sha256((M35R1 / name).read_bytes()).hexdigest() == expected
    assert (
        frozen_benchmark_content_hash()
        == _json(ROOT / "benchmark" / "version.json")["content_hash"]
    )
