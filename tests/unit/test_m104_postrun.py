from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

ROOT = Path("evaluation/results/m104/fresh-provenance-20260904")


def _read(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / name).read_text()))


def test_m104_provenance_is_complete() -> None:
    report = _read("provenance_completeness.json")
    assert report["cases"] == 160
    assert report["complete_cases"] == 160
    assert report["completeness"] == 1.0
    assert report["ledger_parse_failures"] == 0
    assert report["ordering_violations"] == 0


def test_m104_causal_counts_cover_all_failures() -> None:
    summary = _read("mechanism_summary.json")
    assert summary["failures"] == 127
    assert sum(summary["primary_counts"].values()) == summary["failures"]
    assert summary["eligible_mechanisms"] == ["MEMORY_RETRIEVAL_SELECTIVITY_FAILURE"]


def test_m104_selection_gate_is_precommitted_and_memory_is_observational() -> None:
    summary = _read("final_summary.json")
    assert summary["memory"]["counterfactual_run"] is False
    assert summary["classification"] == "M104_RUN_INVALID"
    assert summary["m11"]["selected"] is None
