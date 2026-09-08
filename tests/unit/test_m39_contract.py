from __future__ import annotations

import json

from benchmark.m39_runner import M38_LEDGER, M38_MANIFEST, _build_requests, _load_rows


def test_m39_frozen_request_contract_has_90_cases_and_no_leakage() -> None:
    ledger = json.loads(M38_LEDGER.read_text(encoding="utf-8"))
    rows = _load_rows(ledger)
    requests = _build_requests(ledger, rows)
    assert len(requests) == 90
    assert all(f"Case ID:\n{item['case_id']}\n" in item["request_text"] for item in requests)
    assert all(item["question"] in item["request_text"] for item in requests)
    assert (
        len(
            {
                item["serialized_context"]
                for item in requests
                if item["database_id"] == "risk_operations"
            }
        )
        == 1
    )
    assert all(
        item["request_sha256"] == ledger["requests"][index]["full_request_sha256"]
        for index, item in enumerate(requests)
    )


def test_m39_frozen_benchmark_manifest() -> None:
    manifest = json.loads(M38_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["benchmark_version"] == "0.2.0-dev"
    assert (
        manifest["benchmark_content_hash"]
        == "32fb1f941773f4ec7d7ccc1fa38525e2730509b00c5df1f5f7dbb72cc5c77226"
    )
    assert manifest["case_count"] == 90
    assert manifest["mutants"] == manifest["mutants_killed"] == 188
