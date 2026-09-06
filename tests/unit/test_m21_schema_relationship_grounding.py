import json
from pathlib import Path

MANIFEST = Path("evaluation/fixtures/m21_schema_relationship_grounding_manifest.json")
RESULT = Path("evaluation/fixtures/m21_schema_relationship_grounding.json")


def test_m21_frozen_sets_and_result_reconcile() -> None:
    manifest = json.loads(MANIFEST.read_text())
    result = json.loads(RESULT.read_text())

    sets = manifest["sets"]
    ids = [(case["benchmark"], str(case["case_id"])) for cases in sets.values() for case in cases]
    assert len(ids) == 86
    assert len(set(ids)) == 86
    assert manifest["planned_provider_calls"] == 86
    assert result["provider_calls"] == 86
    assert result["m21_manifest_hash"] == manifest["manifest_hash"]
    assert result["sets"]["target_holdout"]["paired"] == {
        "A": 0,
        "B": 0,
        "C": 2,
        "D": 34,
    }
    assert result["sets"]["control"]["paired"]["B"] == 3


def test_m21_experiment_has_no_spillover_or_runtime_mode() -> None:
    manifest = json.loads(MANIFEST.read_text())
    result = json.loads(RESULT.read_text())

    assert manifest["modes"] == {
        "memory": "OFF",
        "retrieval": "OFF",
        "queryplan": "DISABLED",
        "window_ir": "DISABLED",
        "governed": "DISABLED",
        "repair": 0,
        "judge": 0,
        "best_of_n": False,
    }
    assert result["secondary_spillover"] == "not run"
