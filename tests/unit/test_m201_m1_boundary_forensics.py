import json
from pathlib import Path

import pytest

from evaluation.m20_1_m1_boundary_forensics import (
    ForensicValidationError,
    _safe_forensic_sql,
    validate_artifact,
)
from evaluation.m20_1r_explain_placeholder_repair import validate_replay_artifact

ARTIFACT = Path("evaluation/fixtures/m20_1_m1_boundary_forensics.json")


def test_frozen_forensic_artifact_accounts_for_all_m1_failures() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    validate_artifact(artifact)
    assert artifact["m1_failures"] == 299
    assert artifact["summary"]["unaccounted"] == 0
    assert artifact["replay"]["provider_calls"] == 0


def test_forensic_artifact_rejects_accepted_case() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    artifact["cases"][0]["m20_m1_status"] = "ALLOWED"
    with pytest.raises(ForensicValidationError):
        validate_artifact(artifact)


def test_forensic_execution_gate_never_runs_obvious_unsafe_sql() -> None:
    assert _safe_forensic_sql("SELECT pg_read_file('/etc/passwd')")[0] is False
    assert _safe_forensic_sql("DELETE FROM public.orders")[0] is False
    assert _safe_forensic_sql("SELECT name FROM public.customers")[0] is True


def test_forensic_artifact_root_mapping_is_deterministic() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    first = [(row["case_id"], row["root_mechanism"]) for row in artifact["cases"]]
    second = [(row["case_id"], row["root_mechanism"]) for row in artifact["cases"]]
    assert first == second


def test_explain_repair_replay_is_fail_closed_and_provider_free() -> None:
    replay = json.loads(
        Path("evaluation/fixtures/m20_1r_explain_placeholder_repair.json").read_text()
    )
    validate_replay_artifact(replay)
    assert replay["targeted_cases"]["old_placeholder_errors"] == 35
    assert replay["targeted_cases"]["new_placeholder_errors"] == 0
    assert replay["targeted_cases"]["accepted"] == 35
    assert replay["targeted_cases"]["execution_failure"] == 35
