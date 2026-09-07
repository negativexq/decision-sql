import json
from pathlib import Path

from sqlglot import exp

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.models import PolicyCode
from app.sql.parser import SQLParser
from app.sql.policy import SQLPolicy

ROOT = Path(__file__).resolve().parents[2]


def test_temporal_policy_matrix_is_stable_and_deny_first() -> None:
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    parser = SQLParser()
    accepted = (
        "SELECT CURRENT_DATE",
        "SELECT CURRENT_TIME",
        "SELECT CURRENT_TIMESTAMP",
        "SELECT LOCALTIME",
        "SELECT LOCALTIMESTAMP",
        "SELECT NOW()",
        "SELECT TRANSACTION_TIMESTAMP()",
        "SELECT STATEMENT_TIMESTAMP()",
    )
    rejected = (
        "SELECT RANDOM()",
        "SELECT CLOCK_TIMESTAMP()",
        "SELECT PG_SLEEP(1)",
        "SELECT SET_CONFIG('x', 'y', false)",
        "SELECT UNKNOWN_EXTENSION_FUNCTION(1)",
    )
    assert all(policy.validate(parser.parse(sql)) is None for sql in accepted)
    for sql in rejected:
        rejection = policy.validate(parser.parse(sql))
        assert rejection is not None
        assert rejection.code is PolicyCode.FORBIDDEN_FUNCTION


def test_sqlglot_temporal_aliases_canonicalize_for_policy() -> None:
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    parser = SQLParser()
    expected = {
        "SELECT NOW()": "CURRENT_TIMESTAMP",
        "SELECT TRANSACTION_TIMESTAMP()": "TRANSACTION_TIMESTAMP",
        "SELECT STATEMENT_TIMESTAMP()": "STATEMENT_TIMESTAMP",
        "SELECT RANDOM()": "RANDOM",
    }
    for sql, name in expected.items():
        functions = list(parser.parse(sql).expression.find_all(exp.Func))
        assert len(functions) == 1
        assert policy.function_name(functions[0]) == name


def test_m24_2_artifact_records_temporal_delta_without_evaluator_rewrite() -> None:
    artifact = json.loads(
        (ROOT / "evaluation/fixtures/m24_2_temporal_function_policy_result.json").read_text()
    )
    assert artifact["classification"] == "M24_2_TEMPORAL_POLICY_BLOCKED"
    assert artifact["provider_calls"] == 0
    assert artifact["livesqlbench_replay"]["before"]["accepted"] == 166
    assert artifact["livesqlbench_replay"]["after"]["accepted"] == 175
    assert artifact["livesqlbench_replay"]["delta"]["recovered_by_stable_temporal_policy"] == 9
    assert artifact["evaluator"]["pass"] == 170
    assert artifact["evaluator"]["fail"] == 5
    assert "gold_sql" not in artifact
