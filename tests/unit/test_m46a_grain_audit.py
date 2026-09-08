from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.semantics.grain import GrainDiagnosticCode

ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_m46a_replay_contains_required_structural_diagnostics() -> None:
    replay = _load("benchmark/audits/m46a/m46a_historical_replay.json")
    targets = {
        f"{row['experiment']}:{row['case_id']}": row["diagnostic"]["code"]
        for row in replay["rows"]
        if row["case_id"] in {"warehouse_08", "risk_05"}
    }
    assert targets["M43:warehouse_08"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    assert targets["M45:warehouse_08"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT
    assert targets["M43:risk_05"] == GrainDiagnosticCode.NOT_APPLICABLE
    assert targets["M45:risk_05"] == GrainDiagnosticCode.NOT_APPLICABLE

    summary = _load("benchmark/reports/m46a_structured_grain_summary.json")
    assert summary["historical_replay"]["targets"]["M44:warehouse_08"]["code"] == "NO_SQL"


def test_m46a_reference_replay_surfaces_review_candidates_without_hiding_them() -> None:
    replay = _load("benchmark/audits/m46a/m46a_reference_replay.json")
    assert replay["summary"]["PARENT_MEASURE_FANOUT"] == 2

    analysis = _load("benchmark/audits/m46a/m46a_false_positive_analysis.json")
    assert analysis["reference_parent_measure_fanout"] == 2
    assert analysis["historically_correct_model_parent_measure_fanout"] == 0
    assert {row["case_id"] for row in analysis["reference_structural_fanout_cases"]} == {
        "subscription_04",
        "subscription_10",
    }


def test_m46a_historical_artifact_hashes_are_unchanged() -> None:
    manifest = _load("benchmark/manifests/m46a_architecture_manifest.json")
    for relative, expected_hash in manifest["historical_artifact_hashes"].items():
        actual_hash = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual_hash == expected_hash, relative


def test_m46a_is_offline_and_benchmark_is_frozen() -> None:
    summary = _load("benchmark/reports/m46a_structured_grain_summary.json")
    assert summary["provider_calls"] == 0
    assert summary["model_calls"] == 0
    assert summary["benchmark_changed"] is False
    assert summary["prompt_changed"] is False
    assert summary["benchmark_version"] == "0.2.1-dev"
    assert (
        summary["benchmark_hash"]
        == "aeea34b3b71d90806ee18a6bd3d3dd29ab0f06b5e8e7d8fd47119bb5031d5281"
    )


def test_grain_implementation_has_no_benchmark_case_logic() -> None:
    source = (ROOT / "app/semantics/grain.py").read_text(encoding="utf-8").lower()
    for case_id in ("warehouse_08", "subscription_04", "subscription_10", "risk_05"):
        assert case_id not in source
