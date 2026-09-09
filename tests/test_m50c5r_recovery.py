import base64
import hashlib
import json
from pathlib import Path

from benchmark.m50c5_runner import _counter_records

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "benchmark" / "audits" / "m50c5"


def test_mixed_optional_categories_preserve_null_and_are_stable() -> None:
    values = [None] * 6 + ["RELATIONSHIP"] * 10 + ["SCHEMA_OBJECT"] * 5
    expected = [
        {"key": None, "count": 6},
        {"key": "RELATIONSHIP", "count": 10},
        {"key": "SCHEMA_OBJECT", "count": 5},
    ]
    assert _counter_records(values) == expected
    assert _counter_records(list(reversed(values))) == expected


def test_frozen_response_files_and_invalid_count_are_unchanged() -> None:
    expected = {
        "m50c5_control_responses.jsonl": (
            "520c2d0c3690011723c21ab7a34f8c99d48f6a3fa64e9616906172c568b2e8d5"
        ),
        "m50c5_treatment_responses.jsonl": (
            "dcf4f336fc6a0474402e47dfcd81dbeed1725b5c5885344b3f31bb8f2253947e"
        ),
    }
    for name, digest in expected.items():
        assert hashlib.sha256((AUDIT / name).read_bytes()).hexdigest() == digest
    rows = [
        json.loads(line)
        for line in (AUDIT / "m50c5_treatment_responses.jsonl").read_text().splitlines()
    ]
    invalid = [row for row in rows if row["parse_status"] != "PASS"]
    assert len(invalid) == 6
    declared = [
        json.loads(
            json.loads(base64.b64decode(row["raw_response_bytes_base64"]))["choices"][0]["message"][
                "content"
            ]
        )
        for row in invalid
    ]
    assert {
        (item["blocking_claim"]["family"], item["blocking_claim"]["assertion"]) for item in declared
    } == {("POLICY", "UNAUTHORIZED")}
