"""Consistency checks for the M36.1 projection adjudication."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
M35R1 = ROOT / "benchmark" / "experiments" / "results" / "m35r1"
M36 = ROOT / "evaluation" / "forensics" / "m36"
M36_1 = ROOT / "evaluation" / "forensics" / "m36_1"
PROJECTION_CASES = {
    "commerce_05",
    "fleet_03",
    "fleet_05",
    "support_01",
    "support_02",
    "support_03",
    "support_05",
}
FROZEN_M35R1_HASHES = {
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
FROZEN_M36_HASHES = {
    "m36_case_forensics.json": "606ae62334094e4477dc4706af0c52348df611280226e01e0459ade9752dba14",
    "m36_case_forensics.md": "a1fd171ee6ee987e6d0c6f1ff5b0caf54f9b937ff5b3a548c198c655e211a433",
    "m36_complexity_comparison.json": (
        "e8c0d713254977d73ef71bc2c799e67910f860f58797a92368ae3c93fbafd653"
    ),
    "m36_mutant_overlap.json": "a5d5bb814593295cdea4dcea53cb0b2eec03a806b8bad085e1a582b79baf27f7",
    "m36_root_cause_summary.json": (
        "52cc23760da43f06c168496436ba2e5d7be1d15b9c5185ef81956e3dc0acb6a4"
    ),
}


def _json(name: str) -> Any:
    return json.loads((M36_1 / name).read_text(encoding="utf-8"))


def test_m36_1_has_exactly_seven_cases_and_excludes_fleet_06() -> None:
    cases = _json("m36_1_projection_adjudication.json")["cases"]
    assert {case["case_id"] for case in cases} == PROJECTION_CASES
    assert len(cases) == 7
    assert "fleet_06" not in {case["case_id"] for case in cases}
    assert _json("m36_1_projection_adjudication.json")["provider_calls"] == 0


def test_m36_1_counts_and_normalized_results_are_consistent() -> None:
    adjudication = _json("m36_1_projection_adjudication.json")
    counts = adjudication["counts"]
    assert (
        sum(
            counts[key]
            for key in ("EXPLICIT_PROJECTION", "IMPLIED_PROJECTION", "UNDERSPECIFIED_PROJECTION")
        )
        == 7
    )
    assert (
        sum(
            counts[key]
            for key in (
                "MODEL_ERROR",
                "CONTRACT_AMBIGUITY",
                "BENCHMARK_CONTRACT_DEFECT",
                "BENCHMARK_REVIEW_REQUIRED",
            )
        )
        == 7
    )
    normalized = _json("m36_1_projection_normalized_results.json")["projection_normalized_match"]
    assert sum(item["all_fixtures_match"] for item in normalized.values()) == 6
    assert normalized["support_02"]["all_fixtures_match"] is False


def test_m35r1_and_m36_artifacts_remain_unchanged() -> None:
    for name, expected in FROZEN_M35R1_HASHES.items():
        assert hashlib.sha256((M35R1 / name).read_bytes()).hexdigest() == expected
    for name, expected in FROZEN_M36_HASHES.items():
        assert hashlib.sha256((M36 / name).read_bytes()).hexdigest() == expected
