import json
from pathlib import Path

from evaluation.m20_1r2_readonly_percent_transport_repair import validate_replay_artifact

ARTIFACT = Path("evaluation/fixtures/m20_1r2_readonly_percent_transport_repair.json")


def test_readonly_percent_replay_is_provider_free_and_complete() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    validate_replay_artifact(artifact)

    assert artifact["provider_calls"] == 0
    assert artifact["targeted_cases"] == {
        "total": 35,
        "old_percent_execution_failures": 35,
        "new_percent_execution_failures": 0,
        "m1_accepted": 35,
        "execution_succeeded": 35,
        "execution_failed": 0,
        "other_execution_failure": 0,
        "correct": 25,
        "mismatch": 10,
    }
    assert artifact["full_replay"]["m1_accepted"] == 510
    assert artifact["full_replay"]["m1_rejected"] == 264
    assert artifact["full_replay"]["m1_planning_errors"] == 0
    assert artifact["full_replay"]["execution_failure"] == 0
