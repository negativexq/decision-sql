from __future__ import annotations

import json
from pathlib import Path

from evaluation.m101_residual_audit import ast_signature, run, stable_hash


def test_m101_transport_and_taxonomy_inputs_are_frozen() -> None:
    result = run()
    assert result["failure_count"] == 172
    assert result["control_count"] == 28
    assert result["provider_calls"] == 0
    assert result["db_calls"] == 0
    assert result["no_score_change"] is True


def test_ast_signature_exposes_bounded_semantic_fields() -> None:
    signature = ast_signature(
        "SELECT customer_id, COUNT(*) FROM orders GROUP BY customer_id "
        "ORDER BY customer_id LIMIT 3"
    )
    assert signature["tables"] == ["orders"]
    assert signature["projection_count"] == 2
    assert signature["aggregates"]
    assert signature["group_by"] == ["customer_id"]
    assert signature["limit"]


def test_final_artifact_is_ignored_and_keeps_v2_score_out_of_audit() -> None:
    path = Path("evaluation/results/m101/residual-audit-20260904/final_decision.json")
    payload = json.loads(path.read_text())
    assert payload["m10r_artifacts_modified"] is False
    assert payload["provider_calls"] == 0
    assert payload["classification"] == "M101_AUDIT_BLOCKED"
    assert (
        stable_hash({key: value for key, value in payload.items() if key != "final_summary_hash"})
        == payload["final_summary_hash"]
    )
