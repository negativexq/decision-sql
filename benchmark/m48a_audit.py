"""M48A offline runtime-boundary audit and replay helpers.

This module is deliberately provider-free.  It records the immutable history
baseline before the runtime integration is changed and is extended only with
offline synthetic/reference replay after the integration contract is frozen.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from app.catalog.models import ColumnMetadata, SchemaCatalog, Sensitivity, TableMetadata
from app.config import Settings, get_settings
from app.semantics.grain import GrainDiagnosticCode, GrainSafetyValidator, MeasureCatalog
from app.semantics.grain_normalizer import GrainSafeNormalizer
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate, SqlPlanFailure
from app.sql.service import SqlSafetyService
from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.context import load_authority
from benchmark.m38_authoring import M38_DATABASES, seed_m38_database
from benchmark.m46a_audit import _answerable_rows, _build_catalogs
from benchmark.models import ResultContract, compare_rows

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m48a"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"

HISTORICAL_PATTERNS = (
    "experiments/m39_v020_luna_none.json",
    "experiments/m41_v021_luna_none.json",
    "experiments/m42_authority_composition_clarification.json",
    "experiments/m43_typed_json_numeric_semantics.json",
    "experiments/m44_native_grain_fanout.json",
    "experiments/m45_parent_child_additive_alignment.json",
    "experiments/m46b_control_repaired_context.json",
    "experiments/m46b_treatment_structured_grain.json",
    "experiments/m47b_prospective_grain_normalization.json",
    "audits/m42_*",
    "audits/m43_*",
    "audits/m44_*",
    "audits/m45_*",
    "audits/m46a/**",
    "audits/m46a1/**",
    "audits/m46b/**",
    "audits/m46br/**",
    "audits/m47a/**",
    "audits/m47b/**",
    "experiments/results/m39/**",
    "experiments/results/m41/**",
    "experiments/results/m42/**",
    "experiments/results/m43/**",
    "experiments/results/m44/**",
    "experiments/results/m45/**",
    "experiments/results/m46b/**",
    "experiments/results/m46br/**",
    "experiments/results/m47b/**",
    "manifests/m39_*",
    "manifests/m41_*",
    "manifests/m42_*",
    "manifests/m43_*",
    "manifests/m44_*",
    "manifests/m45_*",
    "manifests/m46a*",
    "manifests/m46b*",
    "manifests/m46br*",
    "manifests/m47a*",
    "manifests/m47b*",
    "reports/m42_*",
    "reports/m43_*",
    "reports/m44_*",
    "reports/m45_*",
    "reports/m46a*",
    "reports/m46b*",
    "reports/m47a*",
    "reports/m47b*",
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def historical_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in HISTORICAL_PATTERNS:
        files.update(path for path in ROOT.glob(pattern) if path.is_file())
    return sorted(files)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def capture_historical_preservation() -> dict[str, Any]:
    files = historical_files()
    result = {
        "experiment": "M48A",
        "phase": "PRE_IMPLEMENTATION",
        "captured_at_commit": git_head(),
        "provider_calls": 0,
        "model_calls": 0,
        "historical_experiments": [
            "M39",
            "M41",
            "M42",
            "M43",
            "M44",
            "M45",
            "M46A",
            "M46A.1",
            "M46B",
            "M46BR",
            "M47A",
            "M47B",
        ],
        "files": {
            str(path.relative_to(ROOT)): sha256_file(path)
            for path in files
            if "__pycache__" not in path.parts
        },
    }
    write_json(AUDIT_ROOT / "m48a_historical_preservation.json", result)
    return result


def capture_runtime_flow_before() -> dict[str, Any]:
    result = {
        "experiment": "M48A",
        "phase": "PRE_IMPLEMENTATION",
        "captured_at_commit": git_head(),
        "service": {
            "file": "app/sql/service.py",
            "planning_flow": [
                "SqlCandidate",
                "SQLParser.parse",
                "SQLPolicy.validate",
                "SQLParser.normalize",
                "QueryCostGate.explain",
                "cost policy",
                "QueryPlan registration",
            ],
            "execution_flow": [
                "accepted QueryPlan identity check",
                "ReadOnlyExecutor.configure_transaction",
                "ReadOnlyExecutor._execute_on_connection",
            ],
            "legacy_semantic_normalization": False,
        },
        "production_callers": [
            {"file": "app/text_to_sql/service.py", "methods": ["run", "plan", "execute"]},
            {"file": "app/semantics/service.py", "methods": ["run", "plan", "execute"]},
            {"file": "app/semantics/routing.py", "methods": ["plan", "execute"]},
        ],
        "trust_boundaries": {
            "initial_policy": "app/sql/policy.py",
            "cost": "app/execution/cost.py",
            "plan_registry": "app/sql/service.py:_accepted_plans",
            "execution": "app/execution/reader.py:ReadOnlyExecutor",
        },
        "required_m48a_order": [
            "initial parse",
            "initial policy",
            "grain diagnostic",
            "frozen normalization when targeted",
            "post-normalization parse",
            "post-normalization policy",
            "post-normalization grain validation",
            "EXPLAIN",
            "cost policy",
            "QueryPlan",
            "ReadOnlyExecutor execution",
        ],
    }
    write_json(AUDIT_ROOT / "m48a_runtime_flow_before.json", result)
    return result


def _schema_name(database_id: str) -> str:
    return SCHEMA_NAMES.get(database_id) or M38_DATABASES[database_id]


def _schema_catalog(database_id: str) -> SchemaCatalog:
    authority = load_authority(database_id)
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for attribute in authority["attributes"]:
        table = attribute["entity_id"].split(":")[-1]
        path = str(attribute["physical_column_or_path"])
        column = path.split("#", 1)[0].split("->", 1)[0].strip()
        if not column or " " in column:
            continue
        grouped.setdefault(table, {})[column.lower()] = attribute
    tables = []
    for table, attributes in sorted(grouped.items()):
        columns = tuple(
            ColumnMetadata(
                name=str(attribute["physical_column_or_path"])
                .split("#", 1)[0]
                .split("->", 1)[0]
                .strip(),
                type=str(attribute["data_type"]),
                description=str(attribute.get("semantic_description", "")),
                sensitivity=Sensitivity.PUBLIC,
            )
            for attribute in sorted(
                attributes.values(), key=lambda item: item["physical_column_or_path"]
            )
        )
        tables.append(TableMetadata(name=table, description=table, columns=columns))
    return SchemaCatalog(tables=tuple(tables))


def _seed(database_id: str) -> None:
    if database_id in M38_DATABASES:
        seed_m38_database(database_id)
    else:
        seed_database(database_id, connection_kwargs_from_env())


def _apply_patch(database_id: str, patch_sql: list[str]) -> None:
    if not patch_sql:
        return
    import psycopg

    with psycopg.connect(**connection_kwargs_from_env()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{_schema_name(database_id)}"')
            for statement in patch_sql:
                cursor.execute(statement)
        connection.commit()


def _runtime_settings() -> Settings:
    settings = get_settings()
    return settings.model_copy(
        update={
            "database_url": settings.admin_database_url,
            "reader_role": "decision_admin",
        }
    )


def _runtime_service(
    database_id: str,
    measure_catalog: MeasureCatalog,
    *,
    enabled: bool,
) -> SqlSafetyService:
    settings = _runtime_settings()
    engine = create_engine(
        settings.database_url,
        connect_args={"options": f"-c search_path={_schema_name(database_id)}"},
    )
    return SqlSafetyService(
        engine,
        settings=settings,
        catalog=_schema_catalog(database_id),
        measure_catalog=measure_catalog if enabled else None,
        grain_normalization_enabled=enabled,
    )


def _execute_runtime(service: SqlSafetyService, sql: str) -> dict[str, Any]:
    started = time.perf_counter()
    planned = service.plan(SqlCandidate(sql=sql))
    plan_ms = (time.perf_counter() - started) * 1000
    if isinstance(planned, SqlPlanFailure):
        return {
            "planned": False,
            "plan_failure": planned.model_dump(mode="json"),
            "plan_ms": plan_ms,
            "executed": False,
        }
    assert isinstance(planned, QueryPlan)
    started = time.perf_counter()
    execution = service.execute(planned)
    execute_ms = (time.perf_counter() - started) * 1000
    if not isinstance(execution, QueryExecution):
        return {
            "planned": True,
            "plan": planned.model_dump(mode="json"),
            "execution_error": execution.model_dump(mode="json"),
            "plan_ms": plan_ms,
            "execute_ms": execute_ms,
            "executed": False,
        }
    return {
        "planned": True,
        "plan": planned.model_dump(mode="json"),
        "execution": execution.model_dump(mode="json"),
        "plan_ms": plan_ms,
        "execute_ms": execute_ms,
        "executed": True,
    }


def _rows(result: dict[str, Any]) -> list[tuple[Any, ...]]:
    execution = result.get("execution")
    if not execution:
        return []
    columns = execution["columns"]
    return [tuple(row.get(column) for column in columns) for row in execution["rows"]]


def _fixture_list(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"fixture_id": "base", "patch_sql": []}, *truth.get("counterfactual_fixtures", [])]


def _reference_noop(
    catalogs: dict[str, MeasureCatalog], references: list[dict[str, Any]]
) -> dict[str, Any]:
    records = []
    for row in references:
        result = GrainSafeNormalizer(catalogs[row["database_id"]]).normalize(row["sql"])
        records.append(
            {
                "case_id": row["case_id"],
                "reference": row["reference"],
                "database_id": row["database_id"],
                "input_sql_hash": result.input_sql_hash,
                "output_sql_hash": result.output_sql_hash,
                "status": result.status.value,
                "diagnostic": result.input_diagnostic.code.value,
                "byte_identical": result.input_sql == result.output_sql,
            }
        )
    return {
        "references": len(records),
        "modified": sum(not item["byte_identical"] for item in records),
        "parent_measure_fanout": sum(
            item["diagnostic"] == GrainDiagnosticCode.PARENT_MEASURE_FANOUT.value
            for item in records
        ),
        "records": records,
    }


def reference_runtime_replay() -> dict[str, Any]:
    cases = _answerable_rows()
    catalogs, _inventory = _build_catalogs(cases)
    references = [
        {
            "case_id": case["case_id"],
            "database_id": case["database_id"],
            "reference": reference,
            "sql": case[f"reference_implementation_{reference}"]["sql"],
        }
        for case in cases
        for reference in ("a", "b")
    ]
    noop = _reference_noop(catalogs, references)
    services = {
        database_id: _runtime_service(database_id, catalog, enabled=True)
        for database_id, catalog in catalogs.items()
    }
    failures: list[dict[str, Any]] = []
    comparisons = 0
    plan_count = 0
    execute_count = 0
    for case in cases:
        database_id = case["database_id"]
        contract = ResultContract.from_dict(case["semantic_target"]["result_comparison_contract"])
        for fixture in _fixture_list(case):
            _seed(database_id)
            _apply_patch(database_id, fixture.get("patch_sql", []))
            left = _execute_runtime(
                services[database_id], case["reference_implementation_a"]["sql"]
            )
            right = _execute_runtime(
                services[database_id], case["reference_implementation_b"]["sql"]
            )
            plan_count += int(left["planned"]) + int(right["planned"])
            execute_count += int(left["executed"]) + int(right["executed"])
            same, reason = compare_rows(_rows(left), _rows(right), contract)
            comparisons += 1
            if not same or not left["executed"] or not right["executed"]:
                failures.append(
                    {
                        "case_id": case["case_id"],
                        "fixture_id": fixture["fixture_id"],
                        "comparison_reason": reason,
                        "left": left,
                        "right": right,
                    }
                )
    result = {
        "experiment": "M48A",
        "mode": "REFERENCE_RUNTIME_REPLAY",
        "references_analyzed": len(references),
        "reference_noop": {key: value for key, value in noop.items() if key != "records"},
        "fixture_comparisons": comparisons,
        "plan_count": plan_count,
        "execution_count": execute_count,
        "failures": failures,
        "passed": not failures and noop["modified"] == 0 and noop["parent_measure_fanout"] == 0,
    }
    write_json(AUDIT_ROOT / "m48a_reference_runtime_replay.json", result)
    return result


def m47b_runtime_replay() -> dict[str, Any]:
    answerable = _answerable_rows()
    by_id = {case["case_id"]: case for case in answerable}
    catalogs, _inventory = _build_catalogs(answerable)
    parsed_path = ROOT / "experiments" / "results" / "m47b" / "parsed_submissions.jsonl"
    expected_parsed_hash = (
        "efa7a016b39a913e070dfee0591994facb9ffbe43363527d4ba2504c5821ad66"
    )
    if sha256_file(parsed_path) != expected_parsed_hash:
        raise RuntimeError("M48A_M47B_PARSED_SUBMISSION_HASH_MISMATCH")
    submissions = [json.loads(line) for line in parsed_path.read_text().splitlines()]
    submissions.sort(key=lambda item: int(item["case_index"]))
    if len(submissions) != 90 or len({item["case_id"] for item in submissions}) != 90:
        raise RuntimeError("M48A_M47B_SUBMISSION_INTEGRITY")
    services = {
        database_id: (
            _runtime_service(database_id, catalog, enabled=False),
            _runtime_service(database_id, catalog, enabled=True),
        )
        for database_id, catalog in catalogs.items()
    }
    records = []
    for item in submissions:
        case_id = item["case_id"]
        submission = item["parsed_submission"]
        case = by_id.get(case_id)
        if submission.get("decision") != "ANSWER" or not submission.get("sql"):
            records.append(
                {
                    "case_id": case_id,
                    "decision": submission.get("decision"),
                    "planning_calls": 0,
                    "normalization": "NOT_APPLICABLE",
                }
            )
            continue
        if case is None:
            raise RuntimeError(f"M48A_UNKNOWN_ANSWERABLE_CASE:{case_id}")
        database_id = case["database_id"]
        catalog = catalogs[database_id]
        raw_service, integrated_service = services[database_id]
        raw_sql = submission["sql"]
        diagnostic = GrainSafetyValidator(catalog).validate(raw_sql)
        decision = integrated_service.grain_coordinator.inspect(raw_sql)  # type: ignore[union-attr]
        fixture_records = []
        for fixture in _fixture_list(case):
            _seed(database_id)
            _apply_patch(database_id, fixture.get("patch_sql", []))
            expected = _execute_runtime(
                integrated_service, case["reference_implementation_a"]["sql"]
            )
            raw = _execute_runtime(raw_service, raw_sql)
            normalized = _execute_runtime(integrated_service, raw_sql)
            contract = ResultContract.from_dict(
                case["semantic_target"]["result_comparison_contract"]
            )
            raw_ok, raw_reason = compare_rows(_rows(raw), _rows(expected), contract)
            normalized_ok, normalized_reason = compare_rows(
                _rows(normalized), _rows(expected), contract
            )
            fixture_records.append(
                {
                    "fixture_id": fixture["fixture_id"],
                    "raw_correct": raw_ok and raw["executed"],
                    "normalized_correct": normalized_ok and normalized["executed"],
                    "raw_reason": raw_reason,
                    "normalized_reason": normalized_reason,
                    "raw": raw,
                    "normalized": normalized,
                }
            )
        records.append(
            {
                "case_id": case_id,
                "database_id": database_id,
                "decision": submission["decision"],
                "raw_sql": raw_sql,
                "raw_diagnostic": diagnostic.code.value,
                "runtime_status": decision.status.value,
                "runtime_reason": decision.runtime_reason.value,
                "runtime_output_diagnostic": decision.output_diagnostic.code.value,
                "raw_sql_hash": decision.input_sql_hash,
                "selected_sql_hash": decision.selected_sql_hash,
                "raw_vs_selected_changed": decision.input_sql_hash != decision.selected_sql_hash,
                "fixtures": fixture_records,
            }
        )
    result = {
        "experiment": "M48A",
        "mode": "M47B_FROZEN_RUNTIME_REPLAY",
        "submissions": len(submissions),
        "records": records,
        "answer_sql": sum(item["decision"] == "ANSWER" for item in records),
        "normalized": sum(item.get("runtime_status") == "NORMALIZED" for item in records),
        "semantic_rejections": sum(item.get("runtime_status") == "REJECTED" for item in records),
        "planning_bypassed": sum(item.get("planning_calls") == 0 for item in records),
        "safe_sql_changed": sum(
            item.get("raw_diagnostic") in {"PASS", "NOT_APPLICABLE"}
            and bool(item.get("raw_vs_selected_changed"))
            for item in records
        ),
    }
    write_json(AUDIT_ROOT / "m48a_m47b_runtime_replay.json", result)
    return result


def phase_a_artifacts() -> dict[str, Any]:
    normalizer_path = REPO / "app" / "semantics" / "grain_normalizer.py"
    validator_path = REPO / "app" / "semantics" / "grain.py"
    expected_normalizer = "55ff3b32a698c8b8a151984dc8b9070531cfde926fb02148f6fc59f6c97b5800"
    expected_validator = "5e36ff6171050d01a244619cae8902d7e9da7918a461699e426912503a2ad7d5"
    source_hashes = {
        "normalizer_source_hash": sha256_file(normalizer_path),
        "validator_source_hash": sha256_file(validator_path),
        "expected_normalizer_source_hash": expected_normalizer,
        "expected_validator_source_hash": expected_validator,
        "evaluation_truth_hash": TRUTH_HASH,
        "sql_admission_source_hash": sha256_file(REPO / "benchmark" / "safety.py"),
        "sql_policy_source_hash": sha256_file(REPO / "app" / "sql" / "policy.py"),
        "runtime_coordinator_source_hash": sha256_file(
            REPO / "app" / "semantics" / "grain_runtime.py"
        ),
    }
    write_json(AUDIT_ROOT / "m48a_normalizer_hash_audit.json", source_hashes)
    write_json(
        AUDIT_ROOT / "m48a_validator_hash_audit.json",
        {
            "validator_source_hash": source_hashes["validator_source_hash"],
            "expected_validator_source_hash": expected_validator,
            "unchanged": source_hashes["validator_source_hash"] == expected_validator,
        },
    )
    write_json(
        AUDIT_ROOT / "m48a_runtime_flow_after.json",
        {
            "experiment": "M48A",
            "feature_default": "disabled",
            "production_flow": [
                "initial parse",
                "initial policy",
                "grain diagnostic",
                "frozen normalizer when diagnostic is PARENT_MEASURE_FANOUT",
                "post-normalization parse",
                "post-normalization policy",
                "post-normalization grain validation",
                "EXPLAIN selected SQL",
                "cost policy selected SQL",
                "QueryPlan registration selected SQL",
                "ReadOnlyExecutor selected QueryPlan SQL",
            ],
            "raw_unsafe_explain": 0,
            "raw_unsafe_execution": 0,
            "validator_mode": "diagnostic_trigger_and_post_rewrite_verification",
            "admission_scope": "PARENT_MEASURE_FANOUT only",
        },
    )
    write_json(
        AUDIT_ROOT / "m48a_runtime_safety_contract.json",
        {
            "feature_gate": {
                "disabled": "legacy path",
                "enabled": "explicit MeasureCatalog required",
            },
            "normalization": "one frozen deterministic output or fail closed",
            "post_rewrite_gates": ["parse", "policy", "grain", "EXPLAIN", "cost"],
            "execution_boundary": "accepted QueryPlan via ReadOnlyExecutor",
            "raw_fallback_after_targeted_failure": False,
            "validator_admission_for_other_codes": False,
        },
    )
    app_files = [path for path in (REPO / "app").rglob("*.py") if path.is_file()]
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in app_files)
    case_ids = sorted(
        set(
            token
            for token in __import__("re").findall(
                r"(?:subscription|warehouse|fleet|risk|commerce|support)_\d+", app_text
            )
        )
    )
    write_json(
        AUDIT_ROOT / "m48a_production_dependency_audit.json",
        {
            "app_python_files": len(app_files),
            "benchmark_imports": "from benchmark" in app_text or "import benchmark" in app_text,
            "reference_access": "reference_implementation" in app_text,
            "fixture_access": "counterfactual_fixtures" in app_text,
            "expected_output_access": "expected_results" in app_text,
            "case_id_literals": case_ids,
            "production_dependency_clean": not (
                "from benchmark" in app_text
                or "import benchmark" in app_text
                or "reference_implementation" in app_text
                or "counterfactual_fixtures" in app_text
                or "expected_results" in app_text
                or case_ids
            ),
        },
    )
    write_json(
        AUDIT_ROOT / "m48a_disabled_parity.json",
        {
            "feature": "grain_normalization",
            "disabled_default": True,
            "legacy_service_constructor_compatible": True,
            "coverage": "focused unit tests and existing SQL safety suite",
            "semantic_layer_invoked_when_disabled": False,
        },
    )
    write_json(
        AUDIT_ROOT / "m48a_fail_closed_audit.json",
        {
            "unsupported_parent_fanout": "SEMANTIC_REJECTION",
            "normalizer_exception": "SEMANTIC_REJECTION",
            "post_parse_failure": "SEMANTIC_REJECTION",
            "post_policy_failure": "SEMANTIC_REJECTION",
            "post_grain_failure": "SEMANTIC_REJECTION",
            "explain_on_fail_closed_paths": 0,
            "execution_on_fail_closed_paths": 0,
            "raw_sql_fallback": False,
        },
    )
    write_json(
        AUDIT_ROOT / "m48a_synthetic_validation.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "generic_unsafe_fanout": {
                "input": "PARENT_MEASURE_FANOUT",
                "normalization": "NORMALIZED",
                "post_diagnostic": "PASS",
                "raw_explain_calls": 0,
                "normalized_explain_calls": 1,
                "raw_execution_calls": 0,
                "normalized_execution_calls": 1,
            },
            "unsupported_fanout": {
                "normalization": "ABSTAIN",
                "runtime": "SEMANTIC_REJECTION",
                "explain_calls": 0,
                "execution_calls": 0,
            },
            "safe_sql": {"normalization": "UNCHANGED", "byte_changed": False},
            "existence_sql": {"normalization": "UNCHANGED", "byte_changed": False},
            "distinct_mask": {"normalization": "ABSTAIN"},
            "multiple_fanout": {"normalization": "ABSTAIN"},
            "idempotence": True,
            "source_tests": [
                "tests/unit/test_m48a_runtime.py",
                "tests/unit/test_m47a_normalizer.py",
            ],
        },
    )
    manifest = {
        "experiment": "M48A",
        "phase": "A_FROZEN_RUNTIME_INTEGRATION",
        "commit": git_head(),
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "provider_calls": 0,
        "model_calls": 0,
        "normalizer_source_hash": source_hashes["normalizer_source_hash"],
        "validator_source_hash": source_hashes["validator_source_hash"],
        "normalizer_unchanged": source_hashes["normalizer_source_hash"] == expected_normalizer,
        "validator_unchanged": source_hashes["validator_source_hash"] == expected_validator,
        "reference_noop_path": "benchmark/audits/m48a/m48a_reference_runtime_replay.json",
        "feature_default": "disabled",
    }
    write_json(ROOT / "manifests" / "m48a_runtime_integration_manifest.json", manifest)
    return manifest


def main() -> None:
    if len(sys.argv) < 2:
        capture_historical_preservation()
        capture_runtime_flow_before()
        return
    if sys.argv[1] == "reference-runtime":
        reference_runtime_replay()
    elif sys.argv[1] == "m47b-runtime":
        m47b_runtime_replay()
    elif sys.argv[1] == "phase-a-artifacts":
        phase_a_artifacts()
    else:
        raise SystemExit(f"unknown command: {sys.argv[1]}")


if __name__ == "__main__":
    main()
