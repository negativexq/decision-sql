from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.m24_3_livesqlbench_temporal_integrity import (
    _frozen_proxy_sql,
    classify_empty_reference,
)


def _result(*rows: tuple[object, ...]) -> LiveSqlBenchResult:
    return LiveSqlBenchResult(("value",), tuple(rows))


def test_official_empty_result_semantics_reject_empty_self_comparison() -> None:
    assert not soft_ex_match(_result(), _result(), ordered=False)
    assert not soft_ex_match(_result(), _result((1,)), ordered=False)
    assert soft_ex_match(_result((1,)), _result((1,)), ordered=False)


def test_temporal_drift_classifier_requires_temporal_evidence() -> None:
    assert (
        classify_empty_reference(
            temporal_dependency=True, proxy_became_non_empty=True, max_age_days=565
        )
        == "TEMPORAL_DRIFT_CONFIRMED"
    )
    assert (
        classify_empty_reference(
            temporal_dependency=True, proxy_became_non_empty=False, max_age_days=565
        )
        == "TEMPORAL_DRIFT_PLAUSIBLE"
    )
    assert (
        classify_empty_reference(
            temporal_dependency=False, proxy_became_non_empty=True, max_age_days=565
        )
        == "NON_TEMPORAL_REFERENCE_EMPTY"
    )


def test_frozen_date_diagnostic_is_ast_aware_and_evaluation_only() -> None:
    original = "SELECT * FROM events WHERE event_date >= CURRENT_DATE - INTERVAL '90 days'"
    rewritten = _frozen_proxy_sql(original, date(2025, 2, 19))

    assert "CURRENT_DATE" in original
    assert "CURRENT_DATE" not in rewritten
    assert "CAST('2025-02-19' AS DATE)" in rewritten


def test_committed_artifact_contains_no_local_case_diagnostics() -> None:
    artifact_path = Path("evaluation/fixtures/m24_3_livesqlbench_temporal_integrity_result.json")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert artifact["provider_calls"] == 0
    assert artifact["fresh_generation"] == 0
    assert "local_case_diagnostics" not in artifact

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not keys(artifact) & {"gold_sql", "question", "external_knowledge", "test_cases"}
