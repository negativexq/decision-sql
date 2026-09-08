"""Consistency checks for the frozen M35R1 forensic evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
M35R1 = ROOT / "benchmark" / "experiments" / "results" / "m35r1"
FORENSICS = ROOT / "evaluation" / "forensics" / "m36"
FAILED_CASES = {
    "commerce_05",
    "fleet_03",
    "fleet_05",
    "fleet_06",
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


def _json(name: str) -> Any:
    return json.loads((FORENSICS / name).read_text(encoding="utf-8"))


def test_m36_has_exactly_the_frozen_eight_failures() -> None:
    result_rows = json.loads((M35R1 / "m35r1_case_results.json").read_text(encoding="utf-8"))
    forensic_rows = _json("m36_case_forensics.json")["cases"]
    frozen_failed = {row["case_id"] for row in result_rows if not row["official_correct"]}
    forensic_failed = {row["case_id"] for row in forensic_rows}
    assert frozen_failed == FAILED_CASES
    assert forensic_failed == FAILED_CASES
    assert all(row["primary_failure"] for row in forensic_rows)


def test_m36_counts_and_baseline_recalculation_are_consistent() -> None:
    summary = _json("m36_root_cause_summary.json")
    assert sum(item["count"] for item in summary["root_cause_distribution"]) == 8
    assert sum(item["count"] for item in summary["stage_distribution"]) == 8
    assert summary["provider_calls"] == 0
    assert summary["baseline_recomputed"]["matches_frozen_summary"] is True


def test_m36_mutant_and_complexity_artifacts_cover_all_cases() -> None:
    overlap = _json("m36_mutant_overlap.json")
    complexity = _json("m36_complexity_comparison.json")
    assert len(overlap["cases"]) == 8
    assert overlap["exact_or_near_existing_mutant_count"] == 0
    assert overlap["novel_count"] == 8
    vectors = complexity["vectors"]
    assert len(vectors) == 20
    assert sum(int(vector["correct"]) for vector in vectors) == 12
    assert sum(not vector["correct"] for vector in vectors) == 8


def test_m35r1_frozen_artifacts_are_unchanged() -> None:
    for name, expected in FROZEN_M35R1_HASHES.items():
        actual = hashlib.sha256((M35R1 / name).read_bytes()).hexdigest()
        assert actual == expected, name
