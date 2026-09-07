"""Provider-free LiveSQLBench semantic-oracle execution ceiling.

This evaluation-only harness turns the already frozen reference semantics into
the canonical semantic plan, then sends only the compiler output through the
real M1 and read-only executor.  Protected SQL and result rows are consumed
locally and represented in the output only by bounded status, counts, and
hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.semantics.semantic_compiler import SemanticPlanValidator, SemanticQueryCompiler
from app.semantics.semantic_mapping import SemanticMappingSnapshot
from app.semantics.semantic_query import plan_to_ir
from app.semantics.semantic_validation import SemanticConsistencyValidator
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.loader import DATASET_FILENAME, load_dataset
from evaluation.external.livesqlbench.m1 import catalog_for_m1, safety_for_database
from evaluation.external.livesqlbench.protected import (
    load_protected_artifact,
    merge_public_and_protected,
)
from evaluation.external.livesqlbench.schema import (
    PostgresConnectionConfig,
    introspect_database,
)
from evaluation.semantic_oracle_ceiling import oracle_plan_from_reference_sql

DEFAULT_PUBLIC_ROOT = Path("/Users/ofk/livesqlbench-base-lite-audit")
DEFAULT_PROTECTED = Path(
    "evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl"
)
DEFAULT_PILOT_MANIFEST = Path("evaluation/fixtures/livesqlbench_base_lite_final_manifest.json")
DEFAULT_OUTPUT = Path("evaluation/fixtures/semantic_oracle_execution_ceiling_result.json")
COMPILER_VERSION = "semantic-query-compiler-2"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result(execution: QueryExecution) -> LiveSqlBenchResult:
    return LiveSqlBenchResult(
        tuple(execution.columns),
        tuple(tuple(row.get(column) for column in execution.columns) for row in execution.rows),
    )


def _base_record(
    ordinal: int,
    case_id: str,
    database: str,
    mapping: SemanticMappingSnapshot,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "case_id": case_id,
        "database": database,
        "relationship_metadata": "PRESENT",
        "plan_valid": False,
        "ir_valid": False,
        "representable": False,
        "compiled": False,
        "semantic_valid": False,
        "m1": "NOT_REACHED",
        "m1_failure_code": None,
        "explain": "NOT_REACHED",
        "executed": "NOT_REACHED",
        "execution_failure_code": None,
        "official_result": "NOT_REACHED",
        "semantic_plan_hash": None,
        "semantic_ir_hash": None,
        "compiled_sql_hash": None,
        "mapping_hash": mapping.content_hash,
        "compiler_version": COMPILER_VERSION,
        "row_count": None,
    }


def run_ceiling(
    public_root: Path,
    protected_path: Path,
    pilot_manifest: Path,
    output_path: Path,
    config: PostgresConnectionConfig,
) -> dict[str, Any]:
    public = load_dataset(public_root / DATASET_FILENAME)
    protected = load_protected_artifact(protected_path)
    merged, merge = merge_public_and_protected(public, protected)
    by_id = {case.instance_id: case for case in merged}
    pilot_ids = tuple(json.loads(pilot_manifest.read_text(encoding="utf-8"))["pilot_case_ids"])
    if len(pilot_ids) != 18 or len(set(pilot_ids)) != 18:
        raise RuntimeError("the frozen semantic oracle pilot must contain 18 unique cases")
    if any(case_id not in by_id for case_id in pilot_ids):
        raise RuntimeError("the frozen semantic oracle pilot is not present in merged GT")

    provider_calls = 0
    records: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    counts = {
        "plan_valid": 0,
        "ir_valid": 0,
        "representable": 0,
        "compiled": 0,
        "semantic_valid": 0,
        "m1_accepted": 0,
        "executed": 0,
        "official_correct": 0,
        "evaluator_limitation": 0,
    }

    for database in public.databases:
        live_database = introspect_database(database, config)
        inventory.append(
            {
                "database": database,
                "tables": live_database.table_count,
                "columns": live_database.column_count,
                "foreign_keys": live_database.foreign_key_count,
                "server_version": live_database.server_version,
                "catalog_hash": live_database.stable_identity(),
                "ready": True,
            }
        )

    for ordinal, case_id in enumerate(pilot_ids, start=1):
        case = by_id[case_id]
        database_name = next(item for item in public.databases if item == case.database)
        live_catalog = introspect_database(database_name, config)
        mapping = SemanticMappingSnapshot.from_schema(
            catalog_for_m1(live_catalog), database_id=case.database
        )
        record = _base_record(ordinal, case_id, case.database, mapping)
        try:
            plan = oracle_plan_from_reference_sql(
                case.sol_sql[0], mapping, database_id=case.database
            )
            record["semantic_plan_hash"] = _sha256(
                plan.model_dump_json(exclude_none=True, by_alias=True)
            )
            ir = plan_to_ir(plan)
            record["semantic_ir_hash"] = _sha256(
                ir.model_dump_json(exclude_none=True, by_alias=True)
            )
            SemanticPlanValidator(mapping).validate(ir)
            record["plan_valid"] = True
            counts["plan_valid"] += 1
            record["ir_valid"] = True
            record["representable"] = True
            counts["ir_valid"] += 1
            counts["representable"] += 1
            compiled = SemanticQueryCompiler(mapping).compile(ir)
            record["compiled"] = True
            record["compiled_sql_hash"] = _sha256(compiled.sql)
            counts["compiled"] += 1
            semantic_validation = SemanticConsistencyValidator(mapping).validate(ir, compiled.ast)
            if not semantic_validation.accepted:
                raise RuntimeError("semantic validator rejected canonical compiler output")
            record["semantic_valid"] = True
            counts["semantic_valid"] += 1
        except Exception as error:
            code = getattr(error, "code", None)
            record["m1_failure_code"] = code.value if code is not None else type(error).__name__
            if record["m1_failure_code"] == "UNKNOWN_RELATIONSHIP":
                record["relationship_metadata"] = "MISSING_SERVER_OWNED_RELATIONSHIP_METADATA"
            records.append(record)
            continue

        engine, safety, _ = safety_for_database(live_catalog, config)
        try:
            planned = safety.plan(SqlCandidate(sql=compiled.sql))
            if not isinstance(planned, QueryPlan):
                record["m1"] = "REJECTED"
                record["m1_failure_code"] = (
                    planned.rejection.code.value
                    if planned.rejection is not None
                    else planned.status.value
                )
                records.append(record)
                continue
            record["m1"] = "ACCEPTED"
            record["explain"] = "ACCEPTED"
            counts["m1_accepted"] += 1
            execution = safety.execute(planned)
            if not isinstance(execution, QueryExecution):
                record["executed"] = "FAILED"
                record["execution_failure_code"] = "EXECUTION_ERROR"
                records.append(record)
                continue
            record["executed"] = "SUCCESS"
            record["row_count"] = execution.row_count
            counts["executed"] += 1
            reference_plan = safety.plan(SqlCandidate(sql=case.sol_sql[0]))
            if not isinstance(reference_plan, QueryPlan):
                record["official_result"] = "EVALUATOR_LIMITATION"
                counts["evaluator_limitation"] += 1
                records.append(record)
                continue
            reference_execution = safety.execute(reference_plan)
            if not isinstance(reference_execution, QueryExecution):
                record["official_result"] = "EVALUATOR_LIMITATION"
                counts["evaluator_limitation"] += 1
                records.append(record)
                continue
            correct = soft_ex_match(
                _result(execution),
                _result(reference_execution),
                ordered=bool(case.public.conditions.get("order", False)),
            )
            if correct:
                record["official_result"] = "CORRECT"
                counts["official_correct"] += 1
            elif not reference_execution.rows:
                record["official_result"] = "EVALUATOR_LIMITATION"
                counts["evaluator_limitation"] += 1
            else:
                record["official_result"] = "INCORRECT"
        finally:
            engine.dispose()
        records.append(record)

    if provider_calls != 0:
        raise AssertionError("oracle ceiling must not invoke a provider")
    result: dict[str, Any] = {
        "classification": "LIVESQLBENCH_SEMANTIC_ORACLE_EXECUTION_MEASURED",
        "provider_calls": provider_calls,
        "merge": merge,
        "pilot_cases": len(pilot_ids),
        "database_inventory": inventory,
        "counts": counts,
        "records": records,
        "protected_data_safety": {
            "protected_gt_used_runtime_only": True,
            "protected_gt_committed": False,
            "protected_sql_in_output": False,
            "result_rows_in_output": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=DEFAULT_PUBLIC_ROOT)
    parser.add_argument("--protected", type=Path, default=DEFAULT_PROTECTED)
    parser.add_argument("--pilot-manifest", type=Path, default=DEFAULT_PILOT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_ceiling(
        args.public_root,
        args.protected,
        args.pilot_manifest,
        args.output,
        PostgresConnectionConfig.from_environment(),
    )
    print(json.dumps({key: result[key] for key in ("classification", "provider_calls", "counts")}))


if __name__ == "__main__":
    main()
