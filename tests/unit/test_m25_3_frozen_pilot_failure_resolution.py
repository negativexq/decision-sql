from __future__ import annotations

import pytest

from evaluation.m25_3_frozen_pilot_failure_resolution import validate_replay_accounting


def test_frozen_replay_accounting_requires_eighteen_cases() -> None:
    result = {
        "population": 18,
        "counts": {
            "m1_accepted": 16,
            "m1_blocked": 2,
            "execution_success": 16,
            "execution_failure": 0,
            "official_correct": 6,
            "official_mismatch": 10,
            "reference_unavailable": 0,
        },
    }

    validate_replay_accounting(result)


def test_frozen_replay_accounting_rejects_unreconciled_stages() -> None:
    with pytest.raises(ValueError, match="sum to 18"):
        validate_replay_accounting(
            {
                "population": 18,
                "counts": {"m1_accepted": 16, "m1_blocked": 1},
            }
        )
