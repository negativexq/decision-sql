from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "benchmark" / "experiments" / "results" / "m45"


def _load(name: str):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def test_m45_analysis_has_three_applicable_cases_and_target_remains_wrong() -> None:
    data = _load("m45_additive_alignment_analysis.json")
    assert len(data["applicable_cases"]) == 3
    target = next(row for row in data["applicable_cases"] if row["case_id"] == "warehouse_08")
    assert target["m45_correct"] is False
    assert target["m45_status"] == "DIRECT_FANOUT_AGGREGATION"
    assert data["prompt_only_grain_repair_exhausted"] is True


def test_m45_paired_analysis_matches_frozen_case_set() -> None:
    data = _load("m45_paired_analysis.json")
    assert len(data["transitions"]) == 90
    assert data["applicable_family"]["m43_correct"] == 0
    assert data["applicable_family"]["m45_correct"] == 0
    assert data["json_target_fixes_preserved"] == 1


def test_m45_negative_control_did_not_regress() -> None:
    data = _load("m45_paired_analysis.json")
    risk = next(row for row in data["negative_controls"] if row["case_id"] == "risk_05")
    assert risk["m43_correct"] is True
    assert risk["m45_correct"] is True
    assert risk["regressed"] is False
