"""Provider-free replay and accounting for the frozen M25.2 SQL outputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import soft_ex_match
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import safety_for_database
from evaluation.external.livesqlbench.protected import (
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import (
    PostgresConnectionConfig,
    introspect_database,
)
from evaluation.m25_2_livesqlbench_direct_semantic_context import _result_from_execution

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
DEFAULT_PROTECTED = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
DEFAULT_FROZEN_CASES = (
    ROOT
    / "evaluation/external/livesqlbench/protected/results/m25_2_direct_semantic_context_cases.jsonl"
)


def validate_replay_accounting(result: dict[str, Any]) -> None:
    """Fail closed if the frozen-output stage funnel cannot reconcile."""
    counts = Counter(result.get("counts", {}))
    if result.get("population") != 18:
        raise ValueError("frozen replay population must be exactly 18")
    if counts["m1_accepted"] + counts["m1_blocked"] != 18:
        raise ValueError("M1 replay stages do not sum to 18")
    if counts["execution_success"] + counts["execution_failure"] + counts["m1_blocked"] != 18:
        raise ValueError("execution replay stages do not sum to 18")
    if (
        counts["official_correct"]
        + counts["official_mismatch"]
        + counts["reference_unavailable"]
        + counts["execution_failure"]
        + counts["m1_blocked"]
        != 18
    ):
        raise ValueError("official replay stages do not sum to 18")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def replay_frozen_outputs(
    public_root: Path = DEFAULT_PUBLIC_ROOT,
    protected_path: Path = DEFAULT_PROTECTED,
    frozen_cases_path: Path = DEFAULT_FROZEN_CASES,
) -> dict[str, Any]:
    """Replay persisted SQL only; this function never constructs a provider."""
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    if merge["exact_matches"] != 270 or merge["unmatched_public"] or merge["unmatched_protected"]:
        raise ValueError("public/protected merge is not exact")
    frozen = _read_jsonl(frozen_cases_path)
    if len(frozen) != 18 or len({row.get("case_id") for row in frozen}) != 18:
        raise ValueError("frozen M25.2 output population is not exactly 18 unique cases")
    cases = {case.instance_id: case for case in merged}
    if any(row.get("case_id") not in cases for row in frozen):
        raise ValueError("frozen output case is absent from protected merge")

    config = PostgresConnectionConfig.from_environment()
    services: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    counts: Counter[str] = Counter()
    failure_codes: Counter[str] = Counter()
    safe_cases: list[dict[str, Any]] = []
    try:
        for row in frozen:
            case_id = str(row["case_id"])
            case = cases[case_id]
            if case.database not in services:
                database = introspect_database(case.database, config)
                services[case.database] = safety_for_database(database, config)
            _, safety, _ = services[case.database]
            plan = safety.plan(SqlCandidate(sql=str(row["generated_sql"])))
            record: dict[str, Any] = {
                "case_id": case_id,
                "database": case.database,
                "original_m1_status": row["m25_2"].get("m1_status"),
                "original_official_status": row["m25_2"].get("official_evaluator_status"),
                "replay_m1_status": "REJECTED",
                "replay_m1_failure_code": None,
                "replay_execution_status": "NOT_ATTEMPTED_M1",
                "replay_official_status": "NOT_EVALUATED",
                "replay_correct": False,
            }
            if not isinstance(plan, QueryPlan):
                counts["m1_blocked"] += 1
                code = plan.rejection.code.value if plan.rejection else plan.status.value
                failure_codes[code] += 1
                record["replay_m1_failure_code"] = code
                safe_cases.append(record)
                continue
            counts["m1_accepted"] += 1
            record["replay_m1_status"] = "ACCEPTED"
            execution = safety.execute(plan)
            if not isinstance(execution, QueryExecution):
                counts["execution_failure"] += 1
                record["replay_execution_status"] = "FAILURE"
                safe_cases.append(record)
                continue
            counts["execution_success"] += 1
            record["replay_execution_status"] = "SUCCESS"
            reference_plan = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
            if not isinstance(reference_plan, QueryPlan):
                counts["reference_unavailable"] += 1
                record["replay_official_status"] = "NOT_EVALUATED"
                safe_cases.append(record)
                continue
            reference_execution = safety.execute(reference_plan)
            if not isinstance(reference_execution, QueryExecution):
                counts["reference_unavailable"] += 1
                safe_cases.append(record)
                continue
            correct = soft_ex_match(
                _result_from_execution(execution),
                _result_from_execution(reference_execution),
                ordered=bool(case.public.conditions.get("order", False)),
            )
            record["replay_official_status"] = "PASS" if correct else "FAIL"
            record["replay_correct"] = correct
            counts["official_correct" if correct else "official_mismatch"] += 1
            safe_cases.append(record)
    finally:
        for engine, _, _ in services.values():
            engine.dispose()

    result = {
        "population": len(frozen),
        "provider_calls": 0,
        "counts": dict(counts),
        "failure_codes": dict(sorted(failure_codes.items())),
        "safe_cases": safe_cases,
    }
    validate_replay_accounting(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=DEFAULT_PROTECTED)
    parser.add_argument("--frozen-cases", type=Path, default=DEFAULT_FROZEN_CASES)
    args = parser.parse_args()
    print(
        json.dumps(
            replay_frozen_outputs(args.public_root, args.protected, args.frozen_cases),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
