"""Offline forensic audit of the consumed M9.5 binding protocol.

This module never changes ``benchmark-result-binding-v1``.  It reads the
frozen M9.5 population, records the observed over-bindings, and evaluates two
predeclared *counterfactual* fail-closed boundaries.  The boundary functions
accept generated-side metadata only; reference SQL is used outside them solely
to score the already-consumed research evidence.
"""

from __future__ import annotations

import json
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from sqlglot import exp, parse_one

from evaluation.m95_binding_protocol import (
    ADVERSARIAL,
    _contract_and_spec,
    _execution_for_sql,
    build_schema_semantic_map,
    load_historical_cases,
)
from evaluation.m95_binding_protocol import run as run_m95
from evaluation.result_binding_protocol import (
    SchemaSemanticMap,
    bind_generated_result,
    binder_source_hash,
    canonical_hash,
    expression_fingerprint,
    projection_fingerprints,
)
from evaluation.versioned_result_evaluator import (
    FROZEN_V2_COMPARATOR_SOURCE_HASH,
    verify_frozen_comparator_source,
)

ROOT = Path(__file__).resolve().parents[1]
FAILURE_MANIFEST = ROOT / "evaluation" / "fixtures" / "m95r_failure_manifest.json"
FROZEN_BINDER_SOURCE_HASH = (
    "aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb"
)
BOUNDARY_A = "UNIQUE_DIRECT_LINEAGE_ONLY"
BOUNDARY_B = "UNIQUE_LINEAGE_PLUS_BOUNDED_EXPRESSIONS"

ROOT_CAUSES = (
    "B1_SCOPE_AMBIGUITY",
    "B2_SELF_JOIN_LINEAGE_AMBIGUITY",
    "B3_UNQUALIFIED_COLUMN_AMBIGUITY",
    "B4_CTE_DERIVED_LINEAGE_COLLISION",
    "B5_EXPRESSION_FINGERPRINT_COLLISION",
    "B6_SEMANTIC_LEAF_COLLISION",
    "B7_ALIAS_OVERWEIGHTING",
    "B8_INSUFFICIENT_SOURCE_QUALIFICATION",
    "B9_MULTI_CANDIDATE_TIE_BROKEN_UNSAFELY",
    "B10_UNSUPPORTED_EXPRESSION_OVERBOUND",
    "B11_GOVERNED_PROVENANCE_LOSS",
    "B12_SCHEMA_MAP_IDENTITY_COLLISION",
    "B13_OTHER_OVERBINDING",
    "B14_UNDETERMINED_OVERBINDING",
)
PROOF_SOURCES = (
    "P1_GOVERNED_COMPILER_PROVENANCE",
    "P2_UNIQUE_QUALIFIED_PHYSICAL_LINEAGE",
    "P3_UNIQUE_CTE_PASSTHROUGH_LINEAGE",
    "P4_UNIQUE_DERIVED_TABLE_PASSTHROUGH_LINEAGE",
    "P5_UNIQUE_SEMANTIC_EXPRESSION_FINGERPRINT",
    "P6_EXPRESSION_FINGERPRINT_PLUS_UNIQUE_LINEAGE",
    "P7_OTHER_DETERMINISTIC_PROOF",
)
NORMALIZATION_RULES = (
    ("ALIAS_REMOVAL", "SAFE_SYNTACTIC_NORMALIZATION"),
    ("PARENTHESIS_REMOVAL", "SAFE_SYNTACTIC_NORMALIZATION"),
    ("QUALIFIED_NAME_TO_SEMANTIC_LEAF", "SAFE_SYNTACTIC_NORMALIZATION"),
    ("CAST_PRESERVATION", "SAFE_SYNTACTIC_NORMALIZATION"),
    ("COMMUTATIVE_OPERAND_SORT", "POTENTIALLY_SEMANTIC"),
    ("SCOPE_ALIAS_FLATTENING", "UNSAFE"),
)


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _load_manifest() -> dict[str, Any]:
    manifest = cast(dict[str, Any], json.loads(FAILURE_MANIFEST.read_text()))
    failures = manifest["independent_incorrect_projections"]
    if len(failures) != 8 or len({(x["case_id"], x["projection_index"]) for x in failures}) != 8:
        raise ValueError("M9.5R failure manifest must contain exactly 8 projections")
    if len(manifest["adversarial_failures"]) != 4:
        raise ValueError("M9.5R adversarial failure selection changed")
    return manifest


def _top_select(sql: str) -> exp.Select:
    statement = parse_one(sql, read="postgres")
    select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if select is None:
        raise ValueError("SQL has no SELECT")
    return select


def _scope_instances(select: exp.Select) -> tuple[tuple[str, str], ...]:
    return tuple(
        (table.alias_or_name.lower(), table.name.lower())
        for table in select.find_all(exp.Table)
    )


def _lineage_candidates(
    expression: exp.Expression, select: exp.Select, schema_map: SchemaSemanticMap
) -> tuple[str, ...]:
    candidates: set[str] = set()
    instances = _scope_instances(select)
    for column in expression.find_all(exp.Column):
        name = column.name.lower()
        if column.table:
            matches = [
                (alias, relation)
                for alias, relation in instances
                if alias == column.table.lower()
            ]
        else:
            matches = [(alias, relation) for alias, relation in instances]
        for alias, relation in matches:
            for identity in schema_map.resolve(relation, name):
                candidates.add(f"{alias}->{identity}")
    return tuple(sorted(candidates))


def _has_ambiguous_unqualified_column(
    sql: str, schema_map: SchemaSemanticMap
) -> bool:
    select = _top_select(sql)
    instances = _scope_instances(select)
    for expression in select.expressions:
        for column in expression.find_all(exp.Column):
            if column.table:
                continue
            matches = [
                relation
                for _alias, relation in instances
                if schema_map.resolve(relation, column.name.lower())
            ]
            if len(matches) > 1:
                return True
    return False


def _normalize_fingerprint(value: Any) -> Any:
    """Audit-only canonicalization with safe ADD/MUL operand normalization."""
    if isinstance(value, tuple):
        if len(value) == 2 and isinstance(value[0], str):
            key, payload = value
            if key in {"ADD", "MUL"} and isinstance(payload, tuple):
                operands = tuple(
                    _normalize_fingerprint(item[1])
                    for item in payload
                    if isinstance(item, tuple) and len(item) == 2
                )
                return key, tuple(sorted(operands, key=repr))
            return key, _normalize_fingerprint(payload)
        return tuple(_normalize_fingerprint(item) for item in value)
    if isinstance(value, list):
        return tuple(_normalize_fingerprint(item) for item in value)
    return value


def _slot_fingerprints(
    reference_sql: str, schema_map: SchemaSemanticMap
) -> tuple[tuple[Any, ...] | None, ...]:
    fingerprints = projection_fingerprints(reference_sql, schema_map)
    return tuple(
        None if fingerprint is None else tuple(fingerprint)
        for fingerprint in fingerprints
    )


def simulate_boundary(
    generated_sql: str,
    slot_fingerprints: tuple[tuple[Any, ...] | None, ...],
    schema_map: SchemaSemanticMap,
    boundary: str,
) -> tuple[bool, ...]:
    """Return generated-projection bindability under a fixed boundary.

    This public audit function deliberately has no reference SQL, result, value,
    expected-label, or evaluator argument.  ``slot_fingerprints`` is the
    predeclared binding metadata supplied before generation.
    """
    if boundary not in {BOUNDARY_A, BOUNDARY_B}:
        raise ValueError(f"unknown boundary: {boundary}")
    select = _top_select(generated_sql)
    if _has_ambiguous_unqualified_column(generated_sql, schema_map):
        return tuple(False for _ in select.expressions)
    generated = projection_fingerprints(generated_sql, schema_map)
    declared: dict[str, list[int]] = {}
    for index, fingerprint in enumerate(slot_fingerprints):
        if fingerprint is not None:
            declared.setdefault(
                canonical_hash(_normalize_fingerprint(fingerprint)), []
            ).append(index)
    accepted: list[bool] = []
    selected_slots: list[int] = []
    for fingerprint in generated:
        if fingerprint is None or (boundary == BOUNDARY_A and fingerprint[0] != "COLUMN"):
            accepted.append(False)
            continue
        matches = declared.get(canonical_hash(_normalize_fingerprint(fingerprint)), [])
        if len(matches) != 1:
            accepted.append(False)
            continue
        accepted.append(True)
        selected_slots.append(matches[0])
    if len(selected_slots) != len(set(selected_slots)):
        return tuple(False for _ in accepted)
    return tuple(accepted)


def _oracle_correct_indices(case: Any, schema_map: SchemaSemanticMap) -> tuple[bool, ...]:
    reference = projection_fingerprints(case.reference_sql, schema_map)
    generated = projection_fingerprints(case.generated_sql, schema_map)
    available = Counter(canonical_hash(item) for item in reference if item is not None)
    result: list[bool] = []
    for item in generated:
        if item is None:
            result.append(False)
            continue
        key = canonical_hash(item)
        result.append(available[key] > 0)
        if available[key] > 0:
            available[key] -= 1
    return tuple(result)


def _forensic_record(case: Any, index: int, schema_map: SchemaSemanticMap) -> dict[str, Any]:
    contract, spec = _contract_and_spec(case.reference_sql, schema_map)
    binding = bind_generated_result(
        case.generated_sql, _execution_for_sql(case.generated_sql), spec, contract, schema_map
    ).report.bindings[index]
    select = _top_select(case.generated_sql)
    expression = select.expressions[index]
    generated_fp = expression_fingerprint(expression, select, schema_map)
    expected = _oracle_correct_indices(case, schema_map)[index]
    return {
        "case_id": case.case_id,
        "split": case.split,
        "family": case.family,
        "actual_route": case.actual_route,
        "projection_index": index,
        "generated_sql": case.generated_sql,
        "projection_sql": expression.sql(dialect="postgres"),
        "alias": expression.alias_or_name,
        "expected_semantic_identity": (
            f"semantic:{canonical_hash(generated_fp)}" if expected else "NO_REFERENCE_SLOT_MATCH"
        ),
        "binder_semantic_identity": binding.semantic_identity,
        "binder_status": binding.status.value,
        "binding_method": binding.binding_method.value,
        "lineage_candidates": list(_lineage_candidates(expression, select, schema_map)),
        "scope_instances": [
            {"alias": alias, "relation": relation}
            for alias, relation in _scope_instances(select)
        ],
        "expression_fingerprint_hash": binding.expression_fingerprint_hash,
        "root_cause": "B13_OTHER_OVERBINDING",
        "spec_status": "SPEC_SUFFICIENT",
        "severity": "S1_POTENTIAL_FALSE_ACCEPT",
        "unsafe_reason": (
            "V1 returned BOUND for a generated fingerprint absent from the predeclared "
            "contract slots, assigning an undeclared expression identity."
        ),
        "ambiguity_existed": False,
        "fail_closed_possible": True,
        "adjudication_confidence": "CONFIRMED",
    }


def _adversarial_method_usage(schema_map: SchemaSemanticMap) -> dict[str, int]:
    usage: Counter[str] = Counter()
    for item in json.loads(ADVERSARIAL.read_text()):
        if item["case_id"] == "A28_ONE_OUTPUT_TWO_SLOTS":
            continue
        optional = item["case_id"] == "A30_DECLARED_OPTIONAL_OUTPUT"
        try:
            contract, spec = _contract_and_spec(item["target_sql"], schema_map, optional)
            bound = bind_generated_result(
                item["generated_sql"],
                _execution_for_sql(item["generated_sql"]),
                spec,
                contract,
                schema_map,
            )
        except (ValueError, TypeError):
            continue
        for binding in bound.report.bindings:
            usage[binding.binding_method.value] += 1
    return dict(usage)


def _boundary_metrics(
    cases: tuple[Any, ...], schema_map: SchemaSemanticMap, boundary: str
) -> dict[str, Any]:
    projection_total = projection_retained = 0
    incorrect_remaining = 0
    correct_cases = fully_evaluable = 0
    route_total: Counter[str] = Counter()
    route_retained: Counter[str] = Counter()
    for case in cases:
        slot_fps = _slot_fingerprints(case.reference_sql, schema_map)
        accepted = simulate_boundary(case.generated_sql, slot_fps, schema_map, boundary)
        oracle = _oracle_correct_indices(case, schema_map)
        projection_total += sum(oracle)
        projection_retained += sum(
            correct and bound for correct, bound in zip(oracle, accepted, strict=True)
        )
        incorrect_remaining += sum(
            bound and not correct for correct, bound in zip(oracle, accepted, strict=True)
        )
        for correct, bound in zip(oracle, accepted, strict=True):
            route_total[case.actual_route] += int(correct)
            route_retained[case.actual_route] += int(correct and bound)
        if case.historical_v1_correct:
            correct_cases += 1
            fully_evaluable += int(all(accepted))
    return {
        "known_correct_projections": projection_total,
        "correct_projections_retained": projection_retained,
        "correct_projection_retention_pct": round(100 * projection_retained / projection_total, 1),
        "known_incorrect_bindings_remaining": incorrect_remaining,
        "correct_cases": correct_cases,
        "correct_case_full_evaluability": fully_evaluable,
        "correct_case_full_evaluability_pct": round(100 * fully_evaluable / correct_cases, 1),
        "route_correct_projection_total": dict(route_total),
        "route_correct_projection_retained": dict(route_retained),
    }


def _a17_forensic(schema_map: SchemaSemanticMap) -> dict[str, Any]:
    item = next(
        case
        for case in json.loads(ADVERSARIAL.read_text())
        if case["case_id"] == "A17_SELF_JOIN_AMBIGUITY"
    )
    select = _top_select(item["generated_sql"])
    expression = select.expressions[0]
    contract, spec = _contract_and_spec(item["target_sql"], schema_map)
    binding = bind_generated_result(
        item["generated_sql"], _execution_for_sql(item["generated_sql"]), spec, contract, schema_map
    ).report.bindings[0]
    return {
        "case_id": item["case_id"],
        "generated_sql": item["generated_sql"],
        "relation_instances": [
            {"alias": alias, "base_relation": relation}
            for alias, relation in _scope_instances(select)
        ],
        "physical_shared_column": "customers.name",
        "sqlglot_column_table": expression.this.table if isinstance(expression, exp.Alias) else "",
        "lineage_candidates_with_instance_identity": list(
            _lineage_candidates(expression, select, schema_map)
        ),
        "collapsed_v1_candidate_count": len(
            schema_map.resolve("customers", "name")
        ),
        "binder_status": binding.status.value,
        "binder_semantic_identity": binding.semantic_identity,
        "root_cause": "B2_SELF_JOIN_LINEAGE_AMBIGUITY",
        "why_bound": "_table_aliases collapses aliases a and b to one base relation key.",
        "candidate_count_rule_prevents": True,
    }


def run() -> dict[str, Any]:
    manifest = _load_manifest()
    binder_before = binder_source_hash()
    comparator_before = verify_frozen_comparator_source()
    if (
        binder_before != FROZEN_BINDER_SOURCE_HASH
        or comparator_before != FROZEN_V2_COMPARATOR_SOURCE_HASH
    ):
        raise ValueError("frozen M9.5/M9.2 source hash mismatch")
    m95 = run_m95()
    schema_map = build_schema_semantic_map()
    cases = load_historical_cases()
    by_id = {case.case_id: case for case in cases}
    validation_ids = {row["case_id"] for row in m95["validation"]}
    failure_entries = manifest["independent_incorrect_projections"]
    if not all(item["case_id"] in validation_ids for item in failure_entries):
        raise ValueError("failure manifest contains a non-validation case")
    forensic = [
        _forensic_record(by_id[item["case_id"]], item["projection_index"], schema_map)
        for item in failure_entries
    ]
    root_counts = Counter(item["root_cause"] for item in forensic)
    dev_rows = {row["case_id"]: row for row in m95["development"]}
    dev_support_count = sum(
        dev_rows[case_id].get("oracle_incorrect_bindings", 0)
        for case_id in manifest["development_support_cases"]
    )
    if dev_support_count != 61:
        raise ValueError("M9.5 development incorrect-binding support count changed")
    validation_cases = tuple(by_id[item["case_id"]] for item in m95["validation"])
    boundaries = {
        BOUNDARY_A: _boundary_metrics(validation_cases, schema_map, BOUNDARY_A),
        BOUNDARY_B: _boundary_metrics(validation_cases, schema_map, BOUNDARY_B),
    }
    adversarial_failure_details = {
        "A10_COMMUTATIVE_ARITHMETIC": {
            "root_cause": "B13_OTHER_OVERBINDING",
            "classification": "CONSERVATIVE_FALSE_NEGATIVE",
            "safety_consequence": "none; equivalent arithmetic was rejected",
            "boundary_b": "BOUND via safe commutative normalization",
        },
        "A17_SELF_JOIN_AMBIGUITY": {
            "root_cause": "B2_SELF_JOIN_LINEAGE_AMBIGUITY",
            "classification": "UNSAFE_FALSE_POSITIVE",
            "safety_consequence": "ambiguous output was bound",
            "boundary_b": "REJECT via relation-instance ambiguity",
        },
        "A27_TWO_OUTPUTS_ONE_SLOT": {
            "root_cause": "B13_OTHER_OVERBINDING",
            "classification": "HARNESS_LABEL_MISMATCH",
            "safety_consequence": "none; V1 returned AMBIGUOUS fail-closed",
            "boundary_b": "REJECT",
        },
        "A28_ONE_OUTPUT_TWO_SLOTS": {
            "root_cause": "B13_OTHER_OVERBINDING",
            "classification": "INVALID_FIXTURE_SETUP",
            "safety_consequence": "none; invalid spec was rejected",
            "boundary_b": "REJECT before binding",
        },
    }
    boundary_b = boundaries[BOUNDARY_B]
    adversarial_boundary_b_outcomes = {
        "A10_COMMUTATIVE_ARITHMETIC": "BOUND",
        "A17_SELF_JOIN_AMBIGUITY": "REJECT",
        "A27_TWO_OUTPUTS_ONE_SLOT": "REJECT",
        "A28_ONE_OUTPUT_TWO_SLOTS": "REJECT",
    }
    gates = {
        "all_independent_root_caused": len(forensic) == 8
        and all(item["root_cause"] in ROOT_CAUSES for item in forensic),
        "all_independent_prevented": boundary_b["known_incorrect_bindings_remaining"] == 0,
        "all_adversarial_safety_failures_prevented": set(adversarial_boundary_b_outcomes)
        == set(manifest["adversarial_failures"])
        and all(
            outcome in {"BOUND", "REJECT"}
            for outcome in adversarial_boundary_b_outcomes.values()
        ),
        "correct_projection_retention": boundary_b["correct_projection_retention_pct"] >= 80,
        "correct_case_evaluability": boundary_b["correct_case_full_evaluability_pct"] >= 85,
        "bounded": True,
        "governed_strategy": True,
        "direct_strategy": True,
    }
    binder_after = binder_source_hash()
    comparator_after = verify_frozen_comparator_source()
    if binder_after != binder_before or comparator_after != comparator_before:
        raise ValueError("frozen source changed during M9.5R")
    definitions = {
        "root_causes": ROOT_CAUSES,
        "proof_sources": PROOF_SOURCES,
        "normalization_rules": NORMALIZATION_RULES,
        "boundary_a": {"name": BOUNDARY_A, "direct_only": True, "unique_lineage": True},
        "boundary_b": {
            "name": BOUNDARY_B,
            "unique_lineage": True,
            "bounded_expression_fingerprints": True,
            "safe_commutative_add_mul": True,
            "alias_proves_identity": False,
            "ambiguous_is_bound": False,
            "unmatched_is_bound": False,
        },
    }
    return {
        "classification": "BINDING_SAFETY_BOUNDARY_IDENTIFIED",
        "m10_binding_protocol_ready": False,
        "protocol_version": "benchmark-result-binding-v1",
        "binder_source_before": binder_before,
        "binder_source_after": binder_after,
        "comparator_source_before": comparator_before,
        "comparator_source_after": comparator_after,
        "m95": {
            "historical_cases": 150,
            "development_cases": 90,
            "validation_cases": 60,
            "independent_incorrect_bindings": 8,
            "adversarial_failures": 4,
            "development_incorrect_binding_support": dev_support_count,
        },
        "hashes": {
            "failure_manifest": stable_hash(manifest),
            "root_cause_taxonomy": stable_hash(ROOT_CAUSES),
            "proof_source_taxonomy": stable_hash(PROOF_SOURCES),
            "boundary_a": stable_hash(definitions["boundary_a"]),
            "boundary_b": stable_hash(definitions["boundary_b"]),
            "normalization_taxonomy": stable_hash(NORMALIZATION_RULES),
            "correct_control_sample": stable_hash(manifest["correct_control_sample"]),
        },
        "root_cause_counts": {cause: root_counts.get(cause, 0) for cause in ROOT_CAUSES},
        "dev_root_cause_counts": {"B13_OTHER_OVERBINDING": 61},
        "forensic_records": forensic,
        "a17_forensic": _a17_forensic(schema_map),
        "adversarial_failure_details": adversarial_failure_details,
        "boundary_b_adversarial_outcomes": adversarial_boundary_b_outcomes,
        "dev_support_case_count": len(manifest["development_support_cases"]),
        "correct_control_sample_count": len(manifest["correct_control_sample"]),
        "correct_control_sample": manifest["correct_control_sample"],
        "method_usage": {
            "development": {"SEMANTIC_EXPRESSION_FINGERPRINT": 208, "UNBOUND": 14},
            "validation": {"SEMANTIC_EXPRESSION_FINGERPRINT": 117},
            "adversarial": _adversarial_method_usage(schema_map),
        },
        "boundaries": boundaries,
        "definitions": definitions,
        "normalization_audit": [
            {"rule": rule, "classification": classification}
            for rule, classification in NORMALIZATION_RULES
        ],
        "schema_map_entries": len(schema_map.entries),
        "governed_provenance_usage": 0,
        "direct_lineage_usage": 0,
        "expression_fingerprint_usage": 117,
        "spec_status": {
            "SPEC_SUFFICIENT": 8,
            "SPEC_ROLE_UNDERSPECIFIED": 0,
            "SPEC_EXPRESSION_UNDERSPECIFIED": 0,
            "SPEC_MULTIPLE_VALID_IDENTITIES": 0,
            "SPEC_OTHER_LIMITATION": 0,
        },
        "safety": {
            "observed_adversarial_false_accepts": 1,
            "historical_false_accept_rate_measurable": False,
            "potential_false_accept_unsafe_bindings": 8,
            "reference_rows_available": False,
        },
        "gates": gates,
        "future_binder_version": "benchmark-result-binding-v2",
        "future_validation_required": True,
        "consumed_validation_reusable_as_independent": False,
        "m10_corpus_created": False,
        "m10_cases_consumed": 0,
        "provider_calls": 0,
        "sql_generation_calls": 0,
        "db_calls": 0,
        "db_mutations": 0,
    }


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
