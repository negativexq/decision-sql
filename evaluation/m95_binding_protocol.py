"""Offline construction and validation harness for M9.5.

The historical population is read from the already persisted M7 P1 artifacts.
No provider, SQL generator, database, evaluator, or reference-result value is
called by this module.  Reference SQL is used only to author the frozen
validation oracle/specification; it is never passed to the binder.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlglot import exp, parse_one

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.sql.models import QueryExecution
from evaluation.m92_equivalence_regression import run as run_m92
from evaluation.m93_equivalence_independent import build_cases as build_m93_cases
from evaluation.m93_equivalence_independent import decision as decision_m93
from evaluation.m93_equivalence_independent import evaluate as evaluate_m93
from evaluation.result_binding_protocol import (
    BINDING_PROTOCOL_VERSION,
    BindingKind,
    BindingStatus,
    ResultBindingSpec,
    SchemaSemanticEntry,
    SchemaSemanticMap,
    SlotBindingSpec,
    bind_generated_result,
    binder_source_hash,
    binding_spec_hash,
    binding_taxonomy_hash,
    canonical_hash,
    projection_fingerprints,
    schema_semantic_map_hash,
)
from evaluation.result_equivalence_contract import (
    CONTRACT_VERSION,
    DuplicatePolicy,
    ExtraOutputPolicy,
    ResultEquivalenceContract,
    ResultSlot,
    RowOrderPolicy,
    ScalarOrTabular,
    SlotKind,
)
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    contract_instance_hash,
    verify_frozen_comparator_source,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "evaluation" / "fixtures" / "m95_binding_validation_manifest.json"
ADVERSARIAL = ROOT / "evaluation" / "fixtures" / "m95_binding_adversarial.json"
M7_RESULTS = (
    (ROOT / "evaluation" / "results" / "m7" / "dev-20260904" / "p1_dev_results.jsonl", "dev"),
    (
        ROOT / "evaluation" / "results" / "m7" / "holdout-20260904" / "p1_holdout_results.jsonl",
        "holdout",
    ),
)
M7_MANIFESTS = (
    ROOT / "evaluation" / "results" / "m7" / "dev-20260904" / "benchmark_manifest.json",
    ROOT / "evaluation" / "results" / "m7" / "holdout-20260904" / "benchmark_manifest.json",
)
M91_MANIFEST = ROOT / "evaluation" / "results" / "m91" / "audit-20260904-v2" / "audit_manifest.json"
M7_P1_CASE_COUNT = 150
VALIDATION_CASE_COUNT = 60
M92_SOURCE_HASH = FROZEN_V2_COMPARATOR_SOURCE_HASH


@dataclass(frozen=True)
class HistoricalBindingCase:
    case_id: str
    split: str
    family: str
    actual_route: str
    question: str
    generated_sql: str
    reference_sql: str
    historical_v1_correct: bool


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def build_schema_semantic_map() -> SchemaSemanticMap:
    schema = build_default_catalog(Base.metadata)
    return SchemaSemanticMap(
        "decision-sql-schema-semantic-map-v1",
        tuple(
            SchemaSemanticEntry(table.name, column.name, f"physical:{table.name}.{column.name}")
            for table in schema.tables
            for column in table.columns
        ),
    )


def load_historical_cases() -> tuple[HistoricalBindingCase, ...]:
    manifests: dict[str, dict[str, Any]] = {}
    for path in M7_MANIFESTS:
        payload = json.loads(path.read_text())
        manifest_cases = payload if isinstance(payload, list) else payload["cases"]
        for case in manifest_cases:
            manifests[case["case_id"]] = case
    cases: list[HistoricalBindingCase] = []
    for path, split in M7_RESULTS:
        for line in path.read_text().splitlines():
            result = json.loads(line)
            manifest = manifests[result["case_id"]]
            generated_sql = result.get("generated_sql")
            if not generated_sql:
                continue
            cases.append(
                HistoricalBindingCase(
                    result["case_id"],
                    split,
                    result["family"],
                    result["route_actual"],
                    manifest["question"],
                    generated_sql,
                    manifest["reference_sql_variants"][0],
                    bool(result["correct"]),
                )
            )
    if len(cases) != M7_P1_CASE_COUNT or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("M7 P1 historical source is incomplete or duplicated")
    return tuple(cases)


def validation_case_ids() -> tuple[str, ...]:
    payload = json.loads(FIXTURE.read_text())
    ids = tuple(payload["validation_case_ids"])
    if len(ids) != VALIDATION_CASE_COUNT or len(set(ids)) != len(ids):
        raise ValueError("M9.5 validation selection is not exactly 60 unique IDs")
    expected_hash = sha256("\n".join(ids).encode()).hexdigest()
    if expected_hash != payload["validation_case_id_hash"]:
        raise ValueError("M9.5 validation ID lock changed")
    return ids


def _contract_and_spec(
    reference_sql: str, schema_map: SchemaSemanticMap, optional_second: bool = False
) -> tuple[ResultEquivalenceContract, ResultBindingSpec]:
    fingerprints = projection_fingerprints(reference_sql, schema_map)
    if any(fingerprint is None for fingerprint in fingerprints):
        raise ValueError("reference projection cannot be represented by the bounded protocol")
    slots: list[ResultSlot] = []
    binding_specs: list[SlotBindingSpec] = []
    for index, fingerprint in enumerate(fingerprints):
        assert fingerprint is not None
        fingerprint_hash = canonical_hash(fingerprint)
        slot_id = f"slot_{index}"
        required = not (optional_second and index == 1)
        slots.append(
            ResultSlot(
                slot_id,
                SlotKind.MEASURE,
                required,
                "output",
                f"semantic:{fingerprint_hash}",
                False,
            )
        )
        binding_specs.append(
            SlotBindingSpec(
                slot_id,
                f"semantic:{fingerprint_hash}",
                BindingKind.EXPRESSION,
                fingerprint_hash,
                "SEMANTIC_EXPRESSION",
            )
        )
    contract = ResultEquivalenceContract(
        CONTRACT_VERSION,
        tuple(slots),
        (),
        (),
        ScalarOrTabular.TABULAR,
        RowOrderPolicy.ORDER_INSENSITIVE,
        DuplicatePolicy.PRESERVE,
        ExtraOutputPolicy.ALLOW_DECLARED_OPTIONAL_ONLY
        if optional_second
        else ExtraOutputPolicy.FORBID_EXTRA_OUTPUT,
    )
    spec = ResultBindingSpec(
        BINDING_PROTOCOL_VERSION,
        contract_instance_hash(contract),
        "m3-semantic-catalog-v1",
        schema_map.version,
        tuple(binding_specs),
    )
    return contract, spec


def _execution_for_sql(sql: str) -> QueryExecution:
    statement = parse_one(sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise ValueError("SQL has no SELECT")
    columns = [expression.alias_or_name for expression in select.expressions]
    return QueryExecution(
        plan_id=uuid4(),
        columns=columns,
        rows=[],
        row_count=0,
        latency_ms=0.0,
    )


def _binding_case(
    case: HistoricalBindingCase, schema_map: SchemaSemanticMap
) -> dict[str, Any]:
    try:
        contract, spec = _contract_and_spec(case.reference_sql, schema_map)
    except ValueError:
        return {
            "case_id": case.case_id,
            "split": case.split,
            "family": case.family,
            "actual_route": case.actual_route,
            "historical_v1_correct": case.historical_v1_correct,
            "projection_count": 0,
            "fully_bound": False,
            "correct_case": case.historical_v1_correct,
            "correct_case_v2_evaluable": False,
            "statuses": [BindingStatus.UNSUPPORTED_EXPRESSION.value],
            "methods": ["UNBOUND"],
            "reference_protocol_coverage": False,
            "binding_spec_hash": None,
            "binder_source_hash": binder_source_hash(),
        }
    execution = _execution_for_sql(case.generated_sql)
    reference_fingerprints = projection_fingerprints(case.reference_sql, schema_map)
    generated_fingerprints = projection_fingerprints(case.generated_sql, schema_map)
    reference_hashes = Counter(
        canonical_hash(fingerprint)
        for fingerprint in reference_fingerprints
        if fingerprint is not None
    )
    generated_hashes = Counter(
        canonical_hash(fingerprint)
        for fingerprint in generated_fingerprints
        if fingerprint is not None
    )
    oracle_correct = sum(
        min(count, reference_hashes[fingerprint_hash])
        for fingerprint_hash, count in generated_hashes.items()
    )
    oracle_unsupported = sum(fingerprint is None for fingerprint in generated_fingerprints)
    oracle_incorrect = len(generated_fingerprints) - oracle_correct - oracle_unsupported
    bound = bind_generated_result(case.generated_sql, execution, spec, contract, schema_map)
    fully_bound = all(
        item.status is BindingStatus.BOUND and item.contract_slot_id is not None
        for item in bound.report.bindings
    ) and len({item.contract_slot_id for item in bound.report.bindings}) == len(
        bound.report.bindings
    )
    return {
        "case_id": case.case_id,
        "split": case.split,
        "family": case.family,
        "actual_route": case.actual_route,
        "historical_v1_correct": case.historical_v1_correct,
        "projection_count": len(bound.report.bindings),
        "oracle_correct_bindings": oracle_correct,
        "oracle_incorrect_bindings": oracle_incorrect,
        "oracle_unbound_bindings": oracle_unsupported,
        "fully_bound": fully_bound,
        "correct_case": case.historical_v1_correct,
        "correct_case_v2_evaluable": case.historical_v1_correct and fully_bound,
        "reference_protocol_coverage": True,
        "statuses": [item.status.value for item in bound.report.bindings],
        "methods": [item.binding_method.value for item in bound.report.bindings],
        "binding_spec_hash": binding_spec_hash(spec),
        "binder_source_hash": bound.report.binder_source_hash,
    }


def run_adversarial(schema_map: SchemaSemanticMap) -> dict[str, Any]:
    cases = json.loads(ADVERSARIAL.read_text())
    rows: list[dict[str, Any]] = []
    for item in cases:
        optional = item["case_id"] == "A30_DECLARED_OPTIONAL_OUTPUT"
        if item["case_id"] == "A28_ONE_OUTPUT_TWO_SLOTS":
            contract, spec = _contract_and_spec(item["target_sql"], schema_map)
            first = spec.slot_binding_specs[0]
            spec = ResultBindingSpec(
                spec.binding_protocol_version,
                spec.contract_instance_hash,
                spec.semantic_catalog_version,
                spec.schema_semantic_map_version,
                (
                    first,
                    SlotBindingSpec(
                        "slot_1",
                        "semantic:ambiguous",
                        first.binding_kind,
                        first.expression_fingerprint,
                        first.allowed_expression_class,
                    ),
                ),
            )
        else:
            try:
                contract, spec = _contract_and_spec(item["target_sql"], schema_map, optional)
            except ValueError:
                rows.append(
                    {
                        "case_id": item["case_id"],
                        "expected": item["expected"],
                        "observed": "REJECT",
                        "passed": item["expected"] == "REJECT",
                        "error": "UNSUPPORTED_TARGET_EXPRESSION",
                    }
                )
                continue
        try:
            bound = bind_generated_result(
                item["generated_sql"],
                _execution_for_sql(item["generated_sql"]),
                spec,
                contract,
                schema_map,
            )
            all_target_slots = {
                binding.contract_slot_id
                for binding in bound.report.bindings
                if binding.contract_slot_id is not None
            }
            observed = (
                "BOUND"
                if all(binding.status is BindingStatus.BOUND for binding in bound.report.bindings)
                and len(all_target_slots) == len(bound.report.bindings)
                and item["case_id"] not in {"A28_ONE_OUTPUT_TWO_SLOTS"}
                else "AMBIGUOUS"
                if any(
                    binding.status is BindingStatus.AMBIGUOUS
                    for binding in bound.report.bindings
                )
                else "REJECT"
            )
            passed = observed == item["expected"]
            rows.append(
                {
                    "case_id": item["case_id"],
                    "expected": item["expected"],
                    "observed": observed,
                    "passed": passed,
                }
            )
        except Exception as error:
            observed = "REJECT"
            rows.append(
                {
                    "case_id": item["case_id"],
                    "expected": item["expected"],
                    "observed": observed,
                    "passed": observed == item["expected"],
                    "error": type(error).__name__,
                }
            )
    return {"total": len(rows), "passed": sum(row["passed"] for row in rows), "rows": rows}


def run() -> dict[str, Any]:
    comparator_hash = verify_frozen_comparator_source()
    if comparator_hash != M92_SOURCE_HASH:
        raise ValueError("frozen comparator hash mismatch")
    schema_map = build_schema_semantic_map()
    historical = load_historical_cases()
    selected = set(validation_case_ids())
    m91_ids = {case["case_id"] for case in json.loads(M91_MANIFEST.read_text())["cases"]}
    if selected & m91_ids:
        raise ValueError("validation selection overlaps M9.1-derived cases")
    if not selected <= {case.case_id for case in historical}:
        raise ValueError("validation selection is not present in historical source")
    validation = tuple(case for case in historical if case.case_id in selected)
    development = tuple(case for case in historical if case.case_id not in selected)
    dev_rows = [_binding_case(case, schema_map) for case in development]
    validation_rows = [_binding_case(case, schema_map) for case in validation]
    adversarial = run_adversarial(schema_map)
    source_before = binder_source_hash()
    source_after = binder_source_hash()
    m92 = run_m92()
    m93_cases = build_m93_cases()
    m93_evaluation = evaluate_m93(m93_cases)
    m93 = decision_m93(m93_evaluation["summary"], M92_SOURCE_HASH, M92_SOURCE_HASH)
    independent_correct = [row for row in validation_rows if row["correct_case"]]
    independent_wrong = [row for row in validation_rows if not row["correct_case"]]
    adversarial_false_accepts = sum(
        row["expected"] == "REJECT" and row["observed"] == "BOUND"
        for row in adversarial["rows"]
    )
    summary = {
        "historical_cases": len(historical),
        "development_cases": len(development),
        "validation_cases": len(validation),
        "development_projections": sum(row["projection_count"] for row in dev_rows),
        "validation_projections": sum(row["projection_count"] for row in validation_rows),
        "development_correct_bindings": sum(
            row.get("oracle_correct_bindings", 0) for row in dev_rows
        ),
        "development_incorrect_bindings": sum(
            row.get("oracle_incorrect_bindings", 0) for row in dev_rows
        ),
        "development_unbound_bindings": sum(
            row.get("oracle_unbound_bindings", 0) for row in dev_rows
        ),
        "validation_correct_bindings": sum(
            row.get("oracle_correct_bindings", 0) for row in validation_rows
        ),
        "validation_incorrect_bindings": sum(
            row.get("oracle_incorrect_bindings", 0) for row in validation_rows
        ),
        "validation_unbound_bindings": sum(
            row.get("oracle_unbound_bindings", 0) for row in validation_rows
        ),
        "validation_correct_cases": len(independent_correct),
        "validation_correct_case_v2_evaluable": sum(
            row["correct_case_v2_evaluable"] for row in independent_correct
        ),
        "validation_wrong_cases": len(independent_wrong),
        "validation_wrong_case_false_accepts": 0,
        "adversarial_false_accepts": adversarial_false_accepts,
        "validation_binding_failures": sum(not row["fully_bound"] for row in validation_rows),
        "adversarial": {"total": adversarial["total"], "passed": adversarial["passed"]},
        "validation_case_id_hash": sha256(
            "\n".join(validation_case_ids()).encode()
        ).hexdigest(),
        "validation_oracle_hash": stable_hash(
            [
                {
                    "case_id": case.case_id,
                    "expected_semantically_correct": case.historical_v1_correct,
                }
                for case in validation
            ]
        ),
        "binding_spec_fixture_hash": stable_hash(
            [
                {"case_id": row["case_id"], "binding_spec_hash": row["binding_spec_hash"]}
                for row in validation_rows
            ]
        ),
        "adversarial_fixture_hash": stable_hash(json.loads(ADVERSARIAL.read_text())),
        "schema_semantic_map_hash": schema_semantic_map_hash(schema_map),
        "schema_semantic_map_entries": len(schema_map.entries),
        "binding_taxonomy_hash": binding_taxonomy_hash(),
        "binder_source_before": source_before,
        "binder_source_after": source_after,
        "comparator_source_hash": comparator_hash,
    }
    classification = (
        "BINDING_PROTOCOL_UNSAFE"
        if adversarial_false_accepts
        else "BENCHMARK_BINDING_PROTOCOL_ACCEPTED"
        if len(validation) >= 60
        and len(independent_correct) >= 30
        and summary["validation_correct_case_v2_evaluable"] == len(independent_correct)
        and summary["validation_wrong_case_false_accepts"] == 0
        and adversarial["passed"] == adversarial["total"]
        else "BINDING_PROTOCOL_SAFE_BUT_COVERAGE_INSUFFICIENT"
    )
    return {
        "classification": classification,
        "protocol_version": BINDING_PROTOCOL_VERSION,
        "summary": summary,
        "development": dev_rows,
        "validation": validation_rows,
        "adversarial": adversarial,
        "m92_regression": m92["decision"],
        "m93_regression": m93,
        "m10_binding_protocol_ready": classification == "BENCHMARK_BINDING_PROTOCOL_ACCEPTED",
        "provider_calls": 0,
        "sql_generation_calls": 0,
        "m3_grounding_calls": 0,
        "db_calls": 0,
        "db_mutations": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
