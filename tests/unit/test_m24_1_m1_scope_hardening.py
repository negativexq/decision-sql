import json
from pathlib import Path

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.models import PolicyCode
from app.sql.parser import SQLParser
from app.sql.policy import SQLPolicy

ROOT = Path(__file__).resolve().parents[2]


def test_m24_1_artifact_has_honest_historical_accounting() -> None:
    artifact = json.loads(
        (ROOT / "evaluation/fixtures/m24_1_m1_scope_hardening_result.json").read_text()
    )
    historical = artifact["historical_regression"]
    assert historical["status"] == "NOT_RUN"
    assert historical["measured_cases"] == 0
    assert "newly_accepted_from_old_rejection" not in historical
    assert "livesqlbench_replay" in artifact


def test_m24_1_ambiguous_resolution_is_not_first_match() -> None:
    policy = SQLPolicy(build_default_catalog(Base.metadata))
    parser = SQLParser()
    rejection = policy.validate(
        parser.parse("SELECT id FROM products p JOIN orders o ON o.id = p.id")
    )
    assert rejection is not None
    assert rejection.code is PolicyCode.UNKNOWN_COLUMN
    assert "Ambiguous unqualified column" in rejection.message
