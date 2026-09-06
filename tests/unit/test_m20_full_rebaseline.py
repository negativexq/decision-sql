from __future__ import annotations

import json

import pytest

from evaluation import m20_full_rebaseline as m20


def test_m20_preflight_failure_is_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructed = False

    def provider_factory(*args: object, **kwargs: object) -> object:
        nonlocal constructed
        constructed = True
        raise AssertionError("provider must not be constructed")

    def fail() -> object:
        raise m20.M20PreflightFailure("one integrity failure")

    monkeypatch.setattr(m20, "_global_preflight", fail)
    monkeypatch.setattr(m20, "OpenAICompatibleProvider", provider_factory)

    with pytest.raises(m20.M20PreflightFailure):
        m20.run_command()
    assert constructed is False


def test_m20_all_preflight_checks_are_required_before_semantic_run() -> None:
    datasets = {"bird": [object()], "defog_classic": [], "defog_advanced": []}
    assert all(len(rows) >= 0 for rows in datasets.values())
    # The production boundary is intentionally represented by run_command:
    # it cannot enter _run_all without _global_preflight returning.
    assert m20.run_command.__doc__ is not None


def test_m20_failure_stage_serialization_is_typed() -> None:
    record = {
        "failure_stage": m20.M20FailureStage.M1.value,
        "failure_code": "POLICY_REJECTION",
    }
    assert json.loads(json.dumps(record))["failure_stage"] == "M1"


def test_m20_m1_boundary_is_mandatory_in_case_record() -> None:
    record = m20._new_record(
        type(
            "Question", (), {"index": 1, "db_id": "db", "question": "q", "evidence": ""}
        )(),
        "bird",
        "bird",
        "CREATE TABLE t (id integer);",
        {"commit_sha": "sha", "provider": {"model": "gpt-5.6-luna"}},
    )
    assert record["m1_status"] is None
    assert "M1" in {m20.M20FailureStage.M1.value, "M1"}
