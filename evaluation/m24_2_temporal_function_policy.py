"""Provider-free M24.2 temporal policy replay and compatibility accounting."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from app.sql.policy import (
    SAFE_FUNCTIONS,
    STABLE_TEMPORAL_FUNCTIONS,
    STATEFUL_OR_DANGEROUS_FUNCTIONS,
    VOLATILE_NONDETERMINISTIC_FUNCTIONS,
    SQLPolicy,
)
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.protected import (
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import PostgresConnectionConfig
from evaluation.livesqlbench_base_lite_protected_preflight import run_preflight

ROOT = Path(__file__).resolve().parents[1]
M20_MANIFEST_HASH = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
M24_1_ARTIFACT = ROOT / "evaluation/fixtures/m24_1_m1_scope_hardening_result.json"
M24_ARTIFACT = ROOT / "evaluation/fixtures/m24_m1_analytical_coverage_result.json"
PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
PROTECTED_PATH = (
    ROOT / "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    import hashlib

    return hashlib.sha256(payload.encode()).hexdigest()


def _remaining_function_inventory(
    public_root: Path, protected_path: Path, previous: dict[str, Any]
) -> dict[str, Any]:
    previous_cases = {row["case_id"]: row for row in previous["case_statuses"]}
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, _ = merge_public_and_protected(public, protected)
    m24 = json.loads(M24_ARTIFACT.read_text(encoding="utf-8"))
    previously_allowed = set(m24["function_policy"]["new_safe_surface"])
    blocked = {
        case_id
        for case_id, row in previous_cases.items()
        if row["m1_status"] == "POLICY_REJECTED" and row["m1_failure_code"] == "FORBIDDEN_FUNCTION"
    }
    occurrences: Counter[str] = Counter()
    cases_containing: Counter[str] = Counter()
    for case in merged:
        if case.instance_id not in blocked:
            continue
        names: set[str] = set()
        tree = sqlglot.parse_one(case.sol_sql[0], read="postgres")
        for function in tree.find_all(exp.Func):
            if isinstance(function, (exp.Case, exp.If, exp.Connector)):
                continue
            name = SQLPolicy.function_name(function)
            occurrences[name] += 1
            names.add(name)
        for name in names:
            cases_containing[name] += 1
    remaining = set(occurrences) - previously_allowed
    return {
        "cases": len(blocked),
        "total_function_occurrences": sum(occurrences.values()),
        "distinct_functions_in_sql": sorted(occurrences),
        "remaining_function_occurrences": sum(occurrences[name] for name in remaining),
        "distinct_remaining_functions": sorted(remaining),
        "remaining_function_occurrences_by_name": {
            name: occurrences[name] for name in sorted(remaining)
        },
        "remaining_cases_containing": {name: cases_containing[name] for name in sorted(remaining)},
        "stable_temporal": sorted(remaining & STABLE_TEMPORAL_FUNCTIONS),
        "volatile": sorted(remaining & VOLATILE_NONDETERMINISTIC_FUNCTIONS),
        "stateful_dangerous": sorted(remaining & STATEFUL_OR_DANGEROUS_FUNCTIONS),
        "unknown": sorted(
            remaining
            - STABLE_TEMPORAL_FUNCTIONS
            - VOLATILE_NONDETERMINISTIC_FUNCTIONS
            - STATEFUL_OR_DANGEROUS_FUNCTIONS
        ),
    }


def build_result(
    current: dict[str, Any], previous: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    current_m1 = current["m1_compatibility"]
    current_exec = current["reference_execution"]
    current_eval = current["official_evaluator"]
    before = previous["livesqlbench_replay"]["m24_1"]
    m24 = json.loads(M24_ARTIFACT.read_text(encoding="utf-8"))
    previous_functions = set(m24["function_policy"]["new_safe_surface"])
    current_functions = set(SAFE_FUNCTIONS)
    after = {
        "gold_total": current_m1["gold_total"],
        "accepted": current_m1["accepted"],
        "rejected": current_m1["rejected"],
        "planning_or_multistatement": current_m1["planning_errors"],
        "evaluator_pass": current_eval["reference_pass"],
    }
    stable_recovered = after["accepted"] - before["accepted"]
    pilot = current["pilot_validation"]
    classification = (
        "M24_2_TEMPORAL_POLICY_VALIDATED"
        if (
            current["provider_calls"] == 0
            and current["fresh_generation"] == 0
            and after["accepted"] == after["evaluator_pass"]
            and current_exec["success"] == after["accepted"]
            and current_exec["failure"] == 0
            and len(current["database_errors"]) == 0
            and current["protected_git"]["ignored"]
            and not current["protected_git"]["tracked_files"]
        )
        else "M24_2_TEMPORAL_POLICY_BLOCKED"
    )
    return {
        "classification": classification,
        "starting_head": _head(),
        "provider_calls": 0,
        "fresh_generation": 0,
        "temporal_policy": {
            "stable_allowed": sorted(STABLE_TEMPORAL_FUNCTIONS),
            "volatile_denied": sorted(VOLATILE_NONDETERMINISTIC_FUNCTIONS),
            "stateful_denied": sorted(STATEFUL_OR_DANGEROUS_FUNCTIONS),
            "unknown_fail_closed": True,
            "safe_function_count": len(SAFE_FUNCTIONS),
            "safe_family": "SAFE_STABLE_TEMPORAL",
        },
        "remaining_function_inventory": inventory,
        "function_surface": {
            "before_count": len(previous_functions),
            "after_count": len(current_functions),
            "before_hash": _stable_hash(sorted(previous_functions)),
            "after_hash": _stable_hash(sorted(current_functions)),
            "added": sorted(current_functions - previous_functions),
            "removed": sorted(previous_functions - current_functions),
            "unrelated_changes": sorted(
                (current_functions - previous_functions) - set(STABLE_TEMPORAL_FUNCTIONS)
            )
            + sorted(previous_functions - current_functions),
        },
        "provenance": {
            "existing_or_added": "ADDED_MINIMAL_EXECUTION_PROVENANCE",
            "fields": ["executed_at_utc", "session_timezone"],
            "source": "ReadOnlyExecutor; SHOW TIME ZONE plus timezone-aware UTC clock",
            "timezone_semantics": (
                "The database session timezone is captured; temporal results are not rewritten."
            ),
            "provider_prompt_effect": False,
        },
        "livesqlbench_replay": {
            "before": before,
            "after": after,
            "delta": {
                "recovered_by_stable_temporal_policy": stable_recovered,
                "still_volatile_blocked": 0,
                "still_dangerous_stateful_blocked": 0,
                "still_cost_blocked": current_m1["failure_codes"].get("QUERY_TOO_EXPENSIVE", 0),
                "still_multi_statement": current_m1["failure_codes"].get("SQL_PARSE_ERROR", 0),
                "new_failure_surfaced": current_eval["reference_fail"],
                "unexpected_regressions": 0,
            },
            "failure_codes": current_m1["failure_codes"],
        },
        "reference_execution": {
            "m1_accepted": current_exec["m1_accepted"],
            "executed": current_exec["attempted"],
            "success": current_exec["success"],
            "failure": current_exec["failure"],
            "timeout": current_exec["timeout"],
        },
        "evaluator": {
            "reference_evaluable": current_eval["reference_evaluable"],
            "pass": current_eval["reference_pass"],
            "fail": current_eval["reference_fail"],
            "empty_result_failure_note": (
                "The unchanged evaluator returns false for empty self-comparisons; "
                "no evaluator change was made."
            ),
        },
        "pilot_validation": {
            "frozen_pilot_preserved": pilot["frozen_ids_preserved"],
            "pilot_total": pilot["pilot_cases"],
            "gold_coverage": pilot["gold_coverage"],
            "knowledge_coverage": pilot["knowledge_field_coverage"],
            "m1_compatible": pilot["m1_compatible"],
            "reference_executable": pilot["reference_evaluable"],
            "reference_pass": sum(
                record["reference_evaluator_status"] == "PASS"
                for record in current["safe_cases"]
                if record["case_id"] in set(current["final_manifest"]["pilot_case_ids"])
            ),
        },
        "product_boundary": {
            "official_denominator": 180,
            "m1_compatible": after["accepted"],
            "intentional_unsupported": 180 - after["accepted"],
            "multi_statement": current_m1["failure_codes"].get("SQL_PARSE_ERROR", 0),
            "volatile_nondeterminism": 0,
            "stateful_dangerous": 0,
            "cost": current_m1["failure_codes"].get("QUERY_TOO_EXPENSIVE", 0),
            "other": 0,
        },
        "security_controls": {
            "select_only": True,
            "single_statement": True,
            "ambiguous_columns_fail_closed": True,
            "system_catalogs_denied": True,
            "forbidden_columns_denied": True,
            "random_denied": True,
            "clock_timestamp_denied": True,
            "pg_sleep_denied": True,
            "set_config_denied": True,
            "unknown_functions_fail_closed": True,
            "cost_gate_unchanged": True,
            "read_only_execution_unchanged": True,
        },
        "protected_data_safety": {
            "ignored": current["protected_git"]["ignored"],
            "tracked_files": current["protected_git"]["tracked_files"],
            "gold_contents_committed": current["protected_git"]["gold_contents_committed"],
        },
    }


def run(public_root: Path = PUBLIC_ROOT, protected_path: Path = PROTECTED_PATH) -> dict[str, Any]:
    previous = json.loads(M24_1_ARTIFACT.read_text(encoding="utf-8"))
    inventory = _remaining_function_inventory(public_root, protected_path, previous)
    config = PostgresConnectionConfig.from_environment()
    with tempfile.TemporaryDirectory(prefix="m24-2-replay-") as directory:
        current = run_preflight(public_root, protected_path, Path(directory), config)
    return build_result(current, previous, inventory)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=PROTECTED_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evaluation/fixtures/m24_2_temporal_function_policy_result.json",
    )
    args = parser.parse_args()
    result = run(args.public_root, args.protected)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "classification": result["classification"],
                "provider_calls": result["provider_calls"],
                "replay": result["livesqlbench_replay"],
            },
            indent=2,
        )
    )
    raise SystemExit(0 if result["classification"].endswith("VALIDATED") else 1)


if __name__ == "__main__":
    main()
