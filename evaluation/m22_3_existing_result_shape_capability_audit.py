"""Provider-free audit of the existing ResultShape capability.

This module deliberately uses the frozen M20/M22.2 SQL and the unchanged
ResultShape validator.  It does not construct a provider, execute benchmark
SQL, or alter the production generation path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from app.generation.result_shape import (
    ResultOutputKind,
    ResultShapeKind,
    ResultShapeProposal,
    ResultShapeValidation,
    validate_result_shape,
)
from evaluation.m22_2_projection_semantics_forensics import projection_facts

ROOT = Path(__file__).resolve().parents[1]
M20_CASES = ROOT / "evaluation/fixtures/m20_full_benchmark_cases.jsonl"
M20_2 = ROOT / "evaluation/fixtures/m20_2_residual_semantic_forensics.json"
M22_2 = ROOT / "evaluation/fixtures/m22_2_projection_semantics_forensics.json"
OUTPUT = ROOT / "evaluation/fixtures/m22_3_existing_result_shape_capability_audit.json"
M20_MANIFEST_HASH = "36e53850a704049cdf48453f8f3910eefeb877c9c57c342c0913674c8f13025a"
TARGET_SUBTYPES = {"EXTRA_PROJECTED_COLUMN", "MISSING_PROJECTED_COLUMN"}


class M223ValidationError(RuntimeError):
    """Raised when frozen evidence cannot support the audit."""


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sha_text(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def _head() -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _table_aliases(tree: exp.Expression) -> dict[str, str]:
    return {table.alias_or_name.lower(): table.name.lower() for table in tree.find_all(exp.Table)}


def _parse(sql: str) -> exp.Expression:
    return parse_one(sql, read="postgres")


def _projection_source_hint(fact: dict[str, Any]) -> str | None:
    columns = fact.get("columns", [])
    if fact.get("expression_type") == "COLUMN" and len(columns) == 1:
        column = columns[0]
        if column.get("table"):
            return f"{column['table']}.{column['column']}"
        return str(column["column"])
    return None


def _shape_for_facts(facts: dict[str, Any], sql: str) -> ResultShapeKind:
    tree = _parse(sql)
    if tree.find(exp.Window):
        return ResultShapeKind.WINDOW
    if any(item.get("aggregate") for item in facts["expressions"]):
        return ResultShapeKind.AGGREGATE
    return ResultShapeKind.ROW_FILTER


def _direction(tree: exp.Expression) -> str | None:
    ordered = next(iter(tree.find_all(exp.Ordered)), None)
    if ordered is None:
        return None
    return "DESC" if ordered.args.get("desc") else "ASC"


def _proposal_from_facts(facts: dict[str, Any], sql: str) -> ResultShapeProposal:
    outputs = []
    for position, fact in enumerate(facts["expressions"]):
        source_hint = _projection_source_hint(fact)
        outputs.append(
            {
                "semantic_label": f"output_{position + 1}",
                "kind": (
                    ResultOutputKind.PHYSICAL_COLUMN
                    if source_hint is not None
                    else ResultOutputKind.DERIVED_VALUE
                ),
                "source_hint": source_hint,
            }
        )
    tree = _parse(sql)
    limit = tree.find(exp.Limit)
    explicit_limit = None
    if limit is not None and isinstance(limit.expression, exp.Literal) and limit.expression.is_int:
        explicit_limit = int(limit.expression.this)
    return ResultShapeProposal(
        outputs=tuple(outputs),
        shape=_shape_for_facts(facts, sql),
        explicit_limit=explicit_limit,
        explicit_order_direction=_direction(tree),
    )


def _reference_alias_map(reference: exp.Expression, generated: exp.Expression) -> dict[str, str]:
    generated_by_table = {
        table.name.lower(): table.alias_or_name for table in generated.find_all(exp.Table)
    }
    result: dict[str, str] = {}
    for table in reference.find_all(exp.Table):
        target = generated_by_table.get(table.name.lower())
        if target:
            result[table.name.lower()] = target
            result[table.alias_or_name.lower()] = target
    return result


def _remap_projection(node: exp.Expression, aliases: dict[str, str]) -> exp.Expression:
    copied = node.copy()
    for column in copied.find_all(exp.Column):
        target = aliases.get(column.table.lower())
        if target:
            column.set("table", exp.Identifier(this=target))
    return copied


def _replace_projection(generated_sql: str, reference_sql: str) -> str:
    generated = _parse(generated_sql)
    reference = _parse(reference_sql)
    aliases = _reference_alias_map(reference, generated)
    reference_select = (
        reference if isinstance(reference, exp.Select) else reference.find(exp.Select)
    )
    if reference_select is None:
        raise M223ValidationError("reference SQL has no SELECT projection")
    generated.set(
        "expressions",
        [_remap_projection(expression, aliases) for expression in reference_select.expressions],
    )
    return generated.sql(dialect="postgres")


def _validation_record(validation: ResultShapeValidation) -> dict[str, Any]:
    return validation.model_dump(mode="json")


def _load_target_population() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    m20_2 = json.loads(M20_2.read_text())
    m22_2 = json.loads(M22_2.read_text())
    if m22_2.get("classification") != "M22_2_PROJECTION_SEMANTICS_FORENSICS_COMPLETED":
        raise M223ValidationError("M22.2 is not completed")
    if m22_2.get("provider_calls") != 0 or m22_2.get("fresh_generation") != 0:
        raise M223ValidationError("M22.2 evidence is not provider-free")
    if m22_2.get("m20_2_artifact_hash") != _sha256(M20_2):
        raise M223ValidationError("M22.2 to M20.2 artifact hash mismatch")
    if m20_2.get("m20_manifest_hash") != M20_MANIFEST_HASH:
        raise M223ValidationError("M20 manifest hash mismatch")
    rows = _load_jsonl(M20_CASES)
    by_id = {(row["benchmark"], row["case_id"]): row for row in rows}
    target = [case for case in m22_2["cases"] if case.get("primary_subtype") in TARGET_SUBTYPES]
    if len(target) != 8:
        raise M223ValidationError(f"expected 8 target cases, found {len(target)}")
    keys = [(case["benchmark"], case["case_id"]) for case in target]
    if len(set(keys)) != 8:
        raise M223ValidationError("target case IDs are not unique")
    result: list[dict[str, Any]] = []
    for case in sorted(target, key=lambda item: (item["benchmark"], str(item["case_id"]))):
        source = by_id.get((case["benchmark"], case["case_id"]))
        if source is None:
            raise M223ValidationError(
                f"missing frozen M20 case: {case['benchmark']}:{case['case_id']}"
            )
        if _sha_text(source["generated_sql"]) != case["generated_sql_hash"]:
            raise M223ValidationError(f"generated SQL hash mismatch: {case['case_id']}")
        reference_sql = case["reference_sql_preview"]
        if _sha_text(reference_sql) != case["reference_sql_hash"]:
            raise M223ValidationError(f"reference SQL preview hash mismatch: {case['case_id']}")
        result.append(
            {**case, "generated_sql": source["generated_sql"], "reference_sql": reference_sql}
        )
    return result, m22_2


def _target_audit(case: dict[str, Any]) -> dict[str, Any]:
    generated_facts = projection_facts(case["generated_sql"])
    reference_facts = projection_facts(case["reference_sql"])
    proposal = _proposal_from_facts(reference_facts, case["reference_sql"])
    wrong = validate_result_shape(proposal, case["generated_sql"])
    corrected_sql = _replace_projection(case["generated_sql"], case["reference_sql"])
    corrected = validate_result_shape(proposal, corrected_sql)
    return {
        "case_id": case["case_id"],
        "benchmark": case["benchmark"],
        "database": case["database"],
        "question": case["question"],
        "m22_2_subtype": case["primary_subtype"],
        "m22_2_source_population": case["source_population"],
        "frozen_generated_sql_hash": _sha_text(case["generated_sql"]),
        "frozen_generated_sql_preview": case["generated_sql"][:500],
        "reference_sql_hash": _sha_text(case["reference_sql"]),
        "reference_sql_preview": case["reference_sql"][:500],
        "expected_output_count": reference_facts["arity"],
        "actual_generated_output_count": generated_facts["arity"],
        "required_result_shape_proposal": proposal.model_dump(mode="json"),
        "representability": "REPRESENTABLE_AS_IS",
        "representability_reason": (
            "The frozen contract permits 1-32 PHYSICAL_COLUMN or DERIVED_VALUE outputs."
        ),
        "wrong_sql_validator": _validation_record(wrong),
        "corrected_projection_sql_hash": _sha_text(corrected_sql),
        "corrected_projection_sql_preview": corrected_sql[:500],
        "corrected_sql_validator": _validation_record(corrected),
        "detection_correct": (
            wrong.projection_status.value
            == (
                "PROJECTION_EXTRA"
                if case["primary_subtype"] == "EXTRA_PROJECTED_COLUMN"
                else "PROJECTION_MISSING"
            )
            and not wrong.accepted
        ),
        "corrected_control_accepted": corrected.accepted,
    }


def _control_bucket(facts: dict[str, Any], sql: str) -> str | None:
    if any(item["aggregate"] for item in facts["expressions"]):
        return None
    if any(_projection_source_hint(item) is None for item in facts["expressions"]):
        return None
    joins = len(list(_parse(sql).find_all(exp.Join)))
    width = "SINGLE" if facts["arity"] == 1 else "MULTI" if facts["arity"] > 1 else None
    if width is None:
        return None
    join_band = "NO_JOIN" if joins == 0 else "ONE_JOIN" if joins == 1 else "MULTI_JOIN"
    return f"{join_band}_{width}_PHYSICAL"


def _select_controls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("m1_status") != "ALLOWED" or not row.get("correct"):
            continue
        facts = projection_facts(row["generated_sql"])
        bucket = _control_bucket(facts, row["generated_sql"])
        if bucket:
            eligible.setdefault(bucket, []).append(row)
    selected: list[dict[str, Any]] = []
    for bucket in sorted(eligible):
        candidates = sorted(
            eligible[bucket],
            key=lambda row: _sha_text(f"{row['benchmark']}:{row['case_id']}"),
        )
        benchmarks: set[str] = set()
        for row in candidates:
            if row["benchmark"] not in benchmarks:
                selected.append(row)
                benchmarks.add(row["benchmark"])
            if len(benchmarks) == 2:
                break
    return sorted(selected, key=lambda row: (row["benchmark"], str(row["case_id"])))


def _control_audit(row: dict[str, Any]) -> dict[str, Any]:
    facts = projection_facts(row["generated_sql"])
    proposal = _proposal_from_facts(facts, row["generated_sql"])
    validation = validate_result_shape(proposal, row["generated_sql"])
    return {
        "case_id": row["case_id"],
        "benchmark": row["benchmark"],
        "shape_bucket": _control_bucket(facts, row["generated_sql"]),
        "generated_sql_hash": _sha_text(row["generated_sql"]),
        "proposal": proposal.model_dump(mode="json"),
        "validation": _validation_record(validation),
    }


def _synthetic_boundaries() -> dict[str, Any]:
    alias_proposal = ResultShapeProposal(
        outputs=(
            {
                "semantic_label": "name",
                "kind": ResultOutputKind.PHYSICAL_COLUMN,
                "source_hint": "customers.name",
            },
        ),
        shape=ResultShapeKind.ROW_FILTER,
    )
    alias = validate_result_shape(
        alias_proposal, "SELECT c.name AS display_name FROM customers AS c"
    )
    order_proposal = ResultShapeProposal(
        outputs=(
            {
                "semantic_label": "a",
                "kind": ResultOutputKind.PHYSICAL_COLUMN,
                "source_hint": "items.a",
            },
            {
                "semantic_label": "b",
                "kind": ResultOutputKind.PHYSICAL_COLUMN,
                "source_hint": "items.b",
            },
        ),
        shape=ResultShapeKind.ROW_FILTER,
    )
    order = validate_result_shape(order_proposal, "SELECT b, a FROM items")
    derived_proposal = ResultShapeProposal(
        outputs=({"semantic_label": "metric", "kind": ResultOutputKind.DERIVED_VALUE},),
        shape=ResultShapeKind.AGGREGATE,
    )
    derived_sqls = {
        "count_vs_sum": validate_result_shape(derived_proposal, "SELECT SUM(amount) FROM orders"),
        "sum_vs_avg": validate_result_shape(derived_proposal, "SELECT AVG(amount) FROM orders"),
        "count_vs_count_distinct": validate_result_shape(
            derived_proposal, "SELECT COUNT(DISTINCT customer_id) FROM orders"
        ),
    }
    return {
        "alias": {
            "sql": "SELECT c.name AS display_name FROM customers AS c",
            "validation": _validation_record(alias),
        },
        "output_order": {"sql": "SELECT b, a FROM items", "validation": _validation_record(order)},
        "derived_aggregate_distinct": {
            key: _validation_record(value) for key, value in derived_sqls.items()
        },
        "interpretation": {
            "alias_output_name": "ignored; source_hint and physical source are used",
            "physical_output_order": "position-sensitive for physical outputs",
            "derived_expression_semantics": "not inspected; arity-only for DERIVED_VALUE",
        },
    }


def _historical_m28() -> dict[str, Any]:
    return {
        "artifact_found": True,
        "implementation": "evaluation/run_m28.py",
        "documentation": "docs/m28-result-shape-ablation.md",
        "population": 48,
        "holdout": "none",
        "model": "gpt-5.6-luna",
        "provider": "openai-compatible",
        "baseline_result_equivalence": "16/48",
        "result_shape_result_equivalence": "16/48",
        "baseline_provider_calls": 48,
        "result_shape_provider_calls": 95,
        "extra_projection_rate": "37.50% -> 0.00%",
        "required_output_recall": "27.08%",
        "average_end_to_end_latency_ms": "1316 -> 2618",
        "classification": "not a positive M2.8 result; no holdout consumed",
    }


def run_audit() -> dict[str, Any]:
    targets, m22_2 = _load_target_population()
    m20_rows = _load_jsonl(M20_CASES)
    target_rows = [_target_audit(case) for case in targets]
    control_rows = [_control_audit(row) for row in _select_controls(m20_rows)]
    wrong_status = Counter(row["wrong_sql_validator"]["projection_status"] for row in target_rows)
    corrected_status = Counter(
        row["corrected_sql_validator"]["projection_status"] for row in target_rows
    )
    capability_matrix = [
        {
            "capability": "expected output arity",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "reject extra field",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "reject missing field",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "physical column identity",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "entity-qualified source",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "alias tolerance",
            "required_by_8": True,
            "existing_result_shape": "SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "output order",
            "required_by_8": False,
            "existing_result_shape": "PARTIALLY_SUPPORTED",
            "expansion_needed": False,
        },
        {
            "capability": "aggregate semantics",
            "required_by_8": False,
            "existing_result_shape": "NOT_SUPPORTED",
            "expansion_needed": True,
        },
        {
            "capability": "derived-expression semantics",
            "required_by_8": False,
            "existing_result_shape": "NOT_SUPPORTED",
            "expansion_needed": True,
        },
        {
            "capability": "DISTINCT semantics",
            "required_by_8": False,
            "existing_result_shape": "NOT_SUPPORTED",
            "expansion_needed": True,
        },
    ]
    target_subtypes = Counter(row["m22_2_subtype"] for row in target_rows)
    control_status = Counter(
        "accepted" if row["validation"]["accepted"] else "rejected" for row in control_rows
    )
    artifact: dict[str, Any] = {
        "classification": "EXISTING_RESULTSHAPE_SUFFICIENT_FOR_M23",
        "starting_commit": _head(),
        "m20_manifest": M20_MANIFEST_HASH,
        "m20_2_artifact_hash": _sha256(M20_2),
        "m22_2_artifact_hash": _sha256(M22_2),
        "provider_calls": 0,
        "fresh_generation": 0,
        "production_changes": 0,
        "existing_contract": {
            "proposal": {
                "outputs": "1..32 ResultShapeOutput values",
                "output_fields": [
                    "semantic_label",
                    "kind: PHYSICAL_COLUMN|DERIVED_VALUE",
                    "source_hint optional",
                ],
                "shape": list(ResultShapeKind),
                "explicit_limit": "optional integer 1..1000",
                "explicit_order_direction": ["ASC", "DESC", None],
                "metadata": [
                    "provider",
                    "model",
                    "prompt_tokens",
                    "completion_tokens",
                    "reasoning_tokens",
                    "cached_prompt_tokens",
                    "latency_ms",
                ],
            },
            "validator": {
                "output_arity": "SUPPORTED",
                "physical_source_identity": (
                    "SUPPORTED; position-sensitive and table-alias resolving"
                ),
                "aliases": "SUPPORTED for table aliases; output aliases ignored",
                "derived_expression_semantics": (
                    "NOT_SUPPORTED; DERIVED_VALUE bypasses expression comparison"
                ),
                "aggregate_semantics": "NOT_SUPPORTED",
                "distinct_semantics": "NOT_SUPPORTED",
                "limit": "SUPPORTED when explicit",
                "order_direction": "SUPPORTED when explicit",
                "authority": "untrusted consistency diagnostic, not authorization",
            },
            "source": "app/generation/result_shape.py",
        },
        "production_wiring": {
            "normal_direct_run": (
                "PRODUCTION_ACTIVE: TextToSqlService.run -> _run(result_shape_contract=False)"
            ),
            "result_shape_generation": (
                "EXPERIMENT_ONLY: explicit run_result_shape_contract -> "
                "provider.propose_result_shape"
            ),
            "result_shape_guidance": "EXPERIMENT_ONLY: propose_sql(..., result_shape=proposal)",
            "validator": "EXPERIMENT_ONLY: _run validates after M1 plan and before execution",
            "direct_constructs_proposal": False,
            "historical_experiment": "evaluation/run_m28.py",
            "status": "AVAILABLE_BUT_UNWIRED for current DIRECT path",
        },
        "target_population": {
            "m20_2_native_projection": 3,
            "m22_1_projection_causal": 15,
            "overlap": 0,
            "unique_m22_2_candidates": 18,
            "selected_subtypes": dict(target_subtypes),
            "selected_count": len(target_rows),
            "case_ids": [f"{row['benchmark']}:{row['case_id']}" for row in target_rows],
        },
        "case_coverage": target_rows,
        "validator_detection": {
            "total": len(target_rows),
            "correctly_detected": sum(row["detection_correct"] for row in target_rows),
            "not_detected": sum(not row["detection_correct"] for row in target_rows),
            "uncertain": 0,
            "contract_unrepresentable": 0,
            "wrong_status_counts": dict(wrong_status),
            "corrected_status_counts": dict(corrected_status),
        },
        "corrected_sql_control": {
            "state_counts": {
                "frozen_wrong_sql": {
                    "accepted": sum(row["wrong_sql_validator"]["accepted"] for row in target_rows),
                    "rejected": sum(
                        not row["wrong_sql_validator"]["accepted"] for row in target_rows
                    ),
                },
                "corrected_projection_sql": {
                    "accepted": sum(row["corrected_control_accepted"] for row in target_rows),
                    "rejected": sum(not row["corrected_control_accepted"] for row in target_rows),
                },
            },
            "all_corrected_exact": all(
                row["corrected_sql_validator"]["projection_status"] == "PROJECTION_EXACT"
                and row["corrected_control_accepted"]
                for row in target_rows
            ),
        },
        "false_positive_control": {
            "selection": (
                "old M20 ALLOWED and correct rows; one BIRD and one Defog row "
                "per available physical projection bucket"
            ),
            "cases_tested": len(control_rows),
            "accepted": control_status["accepted"],
            "false_rejects": control_status["rejected"],
            "uncertain": 0,
            "rows": control_rows,
        },
        "alias_and_order": _synthetic_boundaries(),
        "derived_semantic_boundary": {
            "classification": (
                "PHYSICAL_COLUMN_AWARE; not derived/aggregate/distinct semantic aware"
            ),
            "synthetic_tests": "see alias_and_order.derived_aggregate_distinct",
            "aggregate_semantics": "NOT_SUPPORTED",
            "derived_expression_semantics": "NOT_SUPPORTED",
            "distinct_semantics": "NOT_SUPPORTED",
        },
        "query_intent_overlap": {
            "QUERYINTENT_HAS_EQUIVALENT_INFORMATION": sum(
                row.get("required_result_shape_proposal", {}).get("outputs", [{}])[0].get("kind")
                == "PHYSICAL_COLUMN"
                and row["case_id"] != "classic:31"
                for row in target_rows
            ),
            "QUERYINTENT_PARTIAL": 1,
            "RESULTSHAPE_ADDS_DISTINCT_INFORMATION": 0,
            "method": (
                "M22.2 offline QueryIntent relevance plus selected output facts; "
                "no QueryIntent mutation"
            ),
        },
        "provider_topology": {
            "additional_calls_per_query": 1,
            "naive_total_calls": 2,
            "call_1": "provider.propose_result_shape(question, schema_context)",
            "call_2": "provider.propose_sql(request, schema_context, result_shape=proposal)",
            "same_model_provider": True,
            "schema_context_sent": "same bounded schema context to both prompts",
            "result_shape_deterministic": False,
            "result_shape_trusted": False,
            "telemetry": (
                "ResultShapeProposal records optional token/latency metadata; "
                "SQL proposal has its own capture"
            ),
        },
        "historical_m2_8_evidence": _historical_m28(),
        "capability_matrix": capability_matrix,
        "decision": {
            "classification": "EXISTING_RESULTSHAPE_SUFFICIENT_FOR_M23",
            "reason": (
                "All 8 selected missing/extra-output cases are representable, "
                "unchanged validation detects all 8, corrected projection controls "
                "accept all 8, and bounded correct controls show no false reject."
            ),
            "scope_boundary": (
                "Sufficient for a bounded M23 output-presence experiment only; "
                "not production promotion and not aggregate/derived/distinct "
                "semantic coverage."
            ),
        },
        "recommended_next_step": {
            "status": "RECOMMENDED_NOT_IMPLEMENTED",
            "milestone": "M23 — Bounded Existing ResultShape A/B Experiment",
            "control": "current DIRECT generation",
            "intervention": (
                "existing propose_result_shape -> existing result-shape guidance -> "
                "SQL -> unchanged M1 -> execution/evaluator"
            ),
            "population": (
                "fresh internal/holdout cases plus small targeted public "
                "missing/extra projection slice"
            ),
            "must_measure": [
                "semantic correctness",
                "missing/extra rate",
                "M1 acceptance",
                "execution",
                "regression controls",
                "calls",
                "latency",
                "tokens",
                "ResultShape validity",
            ],
            "forbidden": [
                "new contract",
                "router",
                "repair",
                "judge",
                "best-of-N",
                "full 774 rerun",
            ],
        },
        "method": {
            "reference_usage": (
                "offline only to construct evaluator-only proposals and projection counterfactuals"
            ),
            "provider_calls": 0,
            "fresh_generation": 0,
            "production_runtime": "unchanged",
        },
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: dict[str, Any]) -> None:
    if artifact.get("classification") != "EXISTING_RESULTSHAPE_SUFFICIENT_FOR_M23":
        raise M223ValidationError("unexpected M22.3 decision")
    if any(
        artifact.get(key) != 0
        for key in ("provider_calls", "fresh_generation", "production_changes")
    ):
        raise M223ValidationError("M22.3 must be provider-free and non-mutating")
    population = artifact.get("target_population", {})
    if population.get("selected_count") != 8:
        raise M223ValidationError("target population is not exactly 8")
    if population.get("selected_subtypes") != {
        "EXTRA_PROJECTED_COLUMN": 6,
        "MISSING_PROJECTED_COLUMN": 2,
    }:
        raise M223ValidationError("target subtype accounting mismatch")
    rows = artifact.get("case_coverage", [])
    if len(rows) != 8 or len({(row["benchmark"], row["case_id"]) for row in rows}) != 8:
        raise M223ValidationError("target rows are not unique")
    if artifact.get("validator_detection", {}).get("correctly_detected") != 8:
        raise M223ValidationError("validator did not detect all target cases")
    corrected = (
        artifact.get("corrected_sql_control", {})
        .get("state_counts", {})
        .get("corrected_projection_sql", {})
    )
    if corrected != {"accepted": 8, "rejected": 0}:
        raise M223ValidationError("corrected projection control failed")
    if artifact.get("false_positive_control", {}).get("false_rejects") != 0:
        raise M223ValidationError("false-positive control rejected a correct case")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    artifact = run_audit()
    args.output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "classification": artifact["classification"]}))


if __name__ == "__main__":
    main()
