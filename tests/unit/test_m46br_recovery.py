from __future__ import annotations

import pytest

from benchmark import m39_runner as m39
from benchmark.m46br_recovery import (
    ExpectedEvaluationBundle,
    InvalidExpectedResultBundle,
    validate_expected_bundle,
)
from benchmark.models import Submission


def test_raw_fixture_map_is_rejected_before_evaluation() -> None:
    with pytest.raises(InvalidExpectedResultBundle, match="INVALID_EXPECTED_RESULT_BUNDLE"):
        validate_expected_bundle({"base": {"rows": []}})


def test_nested_expected_bundle_is_typed_and_accepted() -> None:
    contract = {"column_count": 1, "row_order": False}
    bundle = ExpectedEvaluationBundle(
        contract=contract,
        fixtures={"base": {"columns": ["value"], "rows": [(1,)]}},
    )
    validated = validate_expected_bundle(bundle.as_dict())
    assert validated.contract == contract
    assert validated.fixtures["base"]["rows"] == [(1,)]


def test_frozen_evaluator_consumes_counterfactual_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    case = {
        "case_id": "synthetic_case",
        "database_id": "commerce_ops",
        "task_type": "ANSWERABLE",
    }
    contract = {"column_count": 1, "row_order": False}
    truth = {
        "semantic_target": {
            "result_comparison_contract": contract,
        },
        "counterfactual_fixtures": [{"fixture_id": "cf1", "patch_sql": ["fixture"]}],
    }
    submission = Submission(case_id="synthetic_case", decision="ANSWER", sql="SELECT 1")
    expected = {
        "synthetic_case": ExpectedEvaluationBundle(
            contract=contract,
            fixtures={
                "base": {"columns": ["?column?"], "rows": [(1,)]},
                "cf1": {"columns": ["?column?"], "rows": [(2,)]},
            },
        ).as_dict()
    }

    monkeypatch.setattr(m39, "_seed", lambda _database_id: None)
    monkeypatch.setattr(
        m39,
        "execute_query",
        lambda _connection, _schema, _sql, patch_sql: (
            ["?column?"],
            [(2,)] if patch_sql else [(1,)],
        ),
    )
    result = m39._evaluate(case, truth, submission, expected)
    assert result["official_correct"] is True
    assert result["all_fixtures_passed"] is True


def test_counterfactual_failure_is_not_base_only_success(monkeypatch: pytest.MonkeyPatch) -> None:
    case = {
        "case_id": "synthetic_case",
        "database_id": "commerce_ops",
        "task_type": "ANSWERABLE",
    }
    contract = {"column_count": 1}
    truth = {
        "semantic_target": {"result_comparison_contract": contract},
        "counterfactual_fixtures": [{"fixture_id": "cf1", "patch_sql": ["fixture"]}],
    }
    submission = Submission(case_id="synthetic_case", decision="ANSWER", sql="SELECT 1")
    expected = {
        "synthetic_case": ExpectedEvaluationBundle(
            contract=contract,
            fixtures={
                "base": {"columns": ["?column?"], "rows": [(1,)]},
                "cf1": {"columns": ["?column?"], "rows": [(2,)]},
            },
        ).as_dict()
    }
    monkeypatch.setattr(m39, "_seed", lambda _database_id: None)
    monkeypatch.setattr(m39, "execute_query", lambda *_args, **_kwargs: (["?column?"], [(1,)]))
    result = m39._evaluate(case, truth, submission, expected)
    assert result["base_passed"] is True
    assert result["all_fixtures_passed"] is False
    assert result["official_correct"] is False
