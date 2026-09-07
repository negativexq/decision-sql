import os
from pathlib import Path

import pytest

from evaluation.external.livesqlbench.schema import PostgresConnectionConfig
from evaluation.livesqlbench_base_lite_protected_preflight import run_preflight


@pytest.mark.skipif(
    os.getenv("RUN_LIVESQLBENCH_PROTECTED_INTEGRATION") != "1",
    reason="set RUN_LIVESQLBENCH_PROTECTED_INTEGRATION=1 for local protected GT audit",
)
def test_protected_preflight_reconciles_and_fails_closed_on_m1_boundary(tmp_path: Path) -> None:
    root = Path(os.environ["LIVESQLBENCH_BASE_LITE_ROOT"])
    protected = Path(
        "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
    )
    result = run_preflight(
        root,
        protected,
        tmp_path,
        PostgresConnectionConfig.from_environment(),
    )

    assert result["provider_calls"] == 0
    assert result["merge"]["exact_matches"] == 270
    assert result["select_population"]["select_cases"] == 180
    assert result["m1_compatibility"]["gold_total"] == 180
    assert result["m1_compatibility"]["accepted"] == 175
    assert result["official_evaluator"]["reference_pass"] == 170
    assert result["protected_git"]["ignored"] is True
    assert result["protected_git"]["tracked_files"] == []
    assert result["classification"] == "LIVESQLBENCH_BASE_LITE_FINAL_PREFLIGHT_BLOCKED"
