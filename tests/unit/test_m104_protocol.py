from __future__ import annotations

import json
from pathlib import Path

from evaluation.m104_corpus import build_cases, freshness_report
from evaluation.m104_protocol import (
    CAUSAL_MECHANISMS,
    EVIDENCE_GRADES,
    PHENOTYPES,
    validate_protocol,
)


def test_m104_fresh_corpus_quotas_and_freshness() -> None:
    cases = build_cases()
    assert len(cases) == 160
    assert sum(case["expected_route"] == "GOVERNED" for case in cases) == 40
    assert sum(case["expected_route"] == "DIRECT" for case in cases) == 120
    assert freshness_report(cases)["status"] == "PASS"


def test_m104_partition_and_metric_quotas() -> None:
    result = validate_protocol()
    assert result["status"] == "PASS"
    assert result["partitions"] == {"DIAG_A": 80, "DIAG_B": 80}
    assert all(value == 4 for value in result["metrics"].values())
    assert all(value == 10 for value in result["families"].values())


def test_m104_taxonomy_is_closed_and_exclusive() -> None:
    assert len(CAUSAL_MECHANISMS) == len(set(CAUSAL_MECHANISMS)) == 12
    assert len(EVIDENCE_GRADES) == len(set(EVIDENCE_GRADES)) == 5
    assert len(PHENOTYPES) == len(set(PHENOTYPES)) == 16
    taxonomy = json.loads(Path("evaluation/fixtures/m104_causal_taxonomy.json").read_text())
    assert tuple(taxonomy["primary_mechanisms"]) == CAUSAL_MECHANISMS


def test_m104_runtime_input_has_no_gold_fields() -> None:
    forbidden = {
        "reference_sql", "reference_result", "expected_route", "correctness", "residual_label"
    }
    for case in build_cases():
        runtime_request = {"question": case["question"], "case_id": case["case_id"]}
        assert not forbidden.intersection(runtime_request)
