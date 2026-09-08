import json
from pathlib import Path

from benchmark import m41_runner


def test_m41_contract_is_frozen_offline() -> None:
    contract = json.loads(m41_runner.CONTRACT_PATH.read_text(encoding="utf-8"))
    ledger = json.loads(m41_runner.LEDGER_PATH.read_text(encoding="utf-8"))

    assert contract["benchmark_version"] == "0.2.1-dev"
    assert contract["benchmark_content_hash"] == m41_runner.EXPECTED_HASH
    assert contract["provider_calls"] == 0
    assert ledger["provider_calls"] == 0
    assert len(ledger["requests"]) == 90
    assert len(set(ledger["case_order"])) == 90
    assert all(entry["full_request_sha256"] for entry in ledger["requests"])


def test_m41_provider_schema_remains_flat() -> None:
    schema = m41_runner.submission_schema()
    encoded = json.dumps(schema, sort_keys=True)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    for keyword in ("allOf", "oneOf", "if", "then", "else", "dependentSchemas"):
        assert keyword not in encoded


def test_m39_historical_artifacts_are_not_m41_outputs() -> None:
    historical = Path("benchmark/experiments/results/m39")
    assert historical.exists()
    assert not (historical / "m41_case_results.json").exists()
