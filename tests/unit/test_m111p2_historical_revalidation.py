"""Provider-free and DB-free tests for the M11.1P2 revalidation boundary."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from evaluation.m111p1_semantic_adaptability import assess_pair
from evaluation.m111p2_historical_revalidation import (
    P0_CHECKPOINT,
    P0_CONTRACT_HASH,
    STARTING_CHECKPOINT,
    _legacy_type,
    _typed_type,
    db_snapshot_identity,
    historical_pairs,
    revalidation_rulebook,
)


def test_predecessor_and_p0_metadata_constants_are_frozen() -> None:
    assert STARTING_CHECKPOINT == "8608063eec2a0dc883fa58b4578cd083f6bd5c7a"
    assert P0_CHECKPOINT == "c189f324edce0a00f1258444d252e0bf49daf250"
    assert P0_CONTRACT_HASH == "ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678"


def test_p0_report_metadata_mismatch_is_explicit_and_non_mutating() -> None:
    path = Path("evaluation/fixtures/m111p2_p0_metadata_audit.json")
    assert path.exists()
    text = path.read_text()
    assert "P1_CHECKPOINT_REPORT_METADATA_MISMATCH" in text
    assert '"historical_commit_modified": false' in text


def test_db_identity_gate_fails_closed_on_any_identity_difference() -> None:
    historical = json.loads(
        Path("evaluation/results/m10r/clean-rebaseline-20260904/db_state.json").read_text()
    )
    matching = db_snapshot_identity(historical)
    changed = db_snapshot_identity(dict(historical, table_row_counts={"orders": 999}))
    assert matching["classification"] == "DB_SNAPSHOT_IDENTITY_SUFFICIENT_FOR_REPLAY"
    assert changed["classification"] == "DB_SNAPSHOT_IDENTITY_UNPROVEN"
    assert changed["replay_authorized"] is False


def test_legacy_type_boundary_never_guesses_lost_types() -> None:
    assert _legacy_type("123.45") == "STRING"
    assert _legacy_type("00123") == "STRING"
    assert _typed_type(Decimal("123.45")) == "DECIMAL"
    assert _typed_type(date(2026, 9, 5)) == "DATE"


def test_p1_application_is_exhaustive_over_frozen_primary_targets_and_m4() -> None:
    rows, targets, m4 = historical_pairs()
    assert len(targets) == 105
    assert len(m4) == 50
    assert len(rows) == 5_250


def test_p1_pair_application_preserves_separate_semantic_and_adaptability_states() -> None:
    result = assess_pair(
        "SELECT amount FROM orders WHERE amount > 100",
        "SELECT amount FROM orders WHERE amount > 200",
    )
    assert result.semantic_relation.value == "PROVEN_MATERIAL_CONFLICT"
    assert result.adaptability.value == "PROVEN_ADAPTABLE"
    assert result.parameter_slots


def test_revalidation_rulebook_forbids_lost_type_reconstruction_and_causality() -> None:
    rules = revalidation_rulebook()
    assert rules["correctness"]["lost_type_recovery"] == "forbidden"
    assert rules["attribution"]["causal_effect"] == "not established without P3"


def test_p2_source_has_no_runtime_or_provider_integration() -> None:
    source = Path("evaluation/m111p2_historical_revalidation.py").read_text()
    assert "openai" not in source.lower()
    assert "memory_off" in source
    assert "retrieval_reruns" in source
