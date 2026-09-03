import hashlib
import json
from pathlib import Path

from evaluation.runner import load_baseline


def test_m25_holdout_is_frozen_and_balanced() -> None:
    dataset_path = Path("evaluation/datasets/m25_holdout.json")
    manifest = json.loads(Path("evaluation/datasets/m25_holdout_manifest.json").read_text())
    cases = load_baseline(dataset_path)

    assert len(cases) == manifest["question_count"] == 54
    assert len({case.id for case in cases}) == 54
    assert hashlib.sha256(dataset_path.read_bytes()).hexdigest() == manifest["dataset_sha256"]
    assert manifest["frozen_before_m25_generation_changes"] is True
    assert {case.category for case in cases} == set(manifest["category_distribution"])
