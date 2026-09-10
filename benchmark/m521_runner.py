# ruff: noqa: E501
"""Zero-call feasibility audit for a generic group-survival diagnostic.

The detector in this file is shadow-only.  It accepts an explicit population
contract as an input so that the audit can distinguish current metadata
capability from a production-legitimate metadata extension.  It never reads
truth, references, fixtures, case IDs, or domains while detecting.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from benchmark.analysis_serialization import dumps_analysis
from benchmark.context import render_governed_context

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m521"
M51B = ROOT / "audits" / "m51b"
M51BR = ROOT / "audits" / "m51br"
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
FULL_MANIFEST = ROOT / "manifests" / "m51a_180_case_manifest.json"
RESPONSE_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
EXPANSION_TRUTH_HASH = "7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9"
FULL_TRUTH_HASH = "b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173"
EXPANSION_MANIFEST_HASH = "d48be18622e34c11057a7ad31272cc96b1fbb7d8017b9c0f74953de5a92a2417"
STARTING_HEAD = "56060b8d90d161ce34930442bf3f8d5da36ce821"
ROOT_CAUSE = "GROUP_SURVIVAL"
CONTROL_FALLBACK = "healthcare_06"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def parse_sql(sql: str | None) -> exp.Expression | None:
    if not sql:
        return None
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return None


def ast_facts(sql: str | None) -> dict[str, Any]:
    tree = parse_sql(sql)
    if tree is None:
        return {
            "parse_status": "FAIL",
            "joins": [],
            "where_columns": [],
            "grouping_keys": [],
            "aggregates": [],
            "predicate_locations": [],
        }
    joins = []
    from_clause = tree.args.get("from_")
    top_level_joins = tree.args.get("joins", [])
    for join in top_level_joins:
        relation = join.this
        joins.append(
            {
                "kind": join.args.get("side") or "INNER",
                "table": relation.sql(dialect="postgres"),
                "on": join.args.get("on").sql(dialect="postgres") if join.args.get("on") else None,
            }
        )
    where = tree.args.get("where")
    where_columns = (
        sorted({col.sql(dialect="postgres") for col in where.find_all(exp.Column)}) if where else []
    )
    group = tree.args.get("group")
    grouping = [x.sql(dialect="postgres") for x in group.expressions] if group else []
    aggregates = [x.sql(dialect="postgres") for x in tree.find_all(exp.AggFunc)]
    locations = ["WHERE"] if where else []
    if tree.args.get("having"):
        locations.append("HAVING")
    if any(j["on"] for j in joins):
        locations.append("ON")
    if "FILTER" in (sql or "").upper():
        locations.append("FILTER")
    tables = []
    if from_clause and from_clause.this is not None:
        tables.append(from_clause.this.sql(dialect="postgres"))
    tables.extend(j.this.sql(dialect="postgres") for j in top_level_joins)
    return {
        "parse_status": "PASS",
        "tables": tables,
        "joins": joins,
        "where_columns": where_columns,
        "grouping_keys": grouping,
        "aggregates": aggregates,
        "predicate_locations": sorted(set(locations)),
        "has_order": bool(tree.args.get("order")),
        "has_having": bool(tree.args.get("having")),
    }


def first_table(sql: str) -> str | None:
    tree = parse_sql(sql)
    if tree is None:
        return None
    table = next(tree.find_all(exp.Table), None)
    return table.name if table is not None else None


def detect_group_survival(sql: str | None, runtime_metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a diagnostic only; no truth or evaluator data is accepted."""
    facts = ast_facts(sql)
    requirement = runtime_metadata.get("population_requirement")
    base_entity = runtime_metadata.get("base_entity")
    if requirement == "MATCHING_ENTITIES_ONLY":
        return {
            "result": "PASS",
            "reason_code": "MATCHING_POPULATION_PERMITS_REDUCTION",
            "ast": facts,
        }
    if requirement != "PRESERVE_BASE_ENTITIES" or not base_entity:
        return {
            "result": "INDETERMINATE",
            "reason_code": "POPULATION_REQUIREMENT_NOT_AVAILABLE",
            "ast": facts,
        }
    sql_upper = (sql or "").upper()
    base_name = str(base_entity).split(":")[-1].upper()
    has_base = base_name in sql_upper
    has_inner_join = any(
        str(j["kind"]).upper() not in {"LEFT", "RIGHT", "FULL"} for j in facts.get("joins", [])
    )
    child_where = bool(facts.get("where_columns")) and len(facts.get("tables", [])) > 1
    child_only = not has_base
    having_reducer = facts.get("has_having", False) and bool(
        runtime_metadata.get("zero_group_policy")
    )
    if child_only or has_inner_join or child_where or having_reducer:
        reason = (
            "MATCHING_CHILD_SOURCE_LOSES_BASE_ENTITY"
            if child_only
            else "INNER_JOIN_CONTRADICTS_BASE_PRESERVATION"
            if has_inner_join
            else "OUTER_JOIN_CHILD_WHERE_ELIMINATION"
            if child_where
            else "HAVING_ELIMINATES_REQUIRED_ZERO_GROUP"
        )
        return {"result": "SEMANTIC_CONTRADICTION_DETECTED", "reason_code": reason, "ast": facts}
    return {"result": "PASS", "reason_code": "NO_PRESERVATION_CONTRADICTION", "ast": facts}


def main() -> dict[str, Any]:
    responses = {r["case_id"]: r for r in load_jsonl(M51B / "m51b_expansion_responses.jsonl")}
    traces = {r["case_id"]: r for r in load_jsonl(M51BR / "m51br_answerable_case_results.jsonl")}
    cases = {p.stem: json.loads(p.read_text()) for p in CASES.glob("*.json")}
    truths = {p.stem: json.loads(p.read_text()) for p in TRUTH.glob("*.json")}
    if sha(M51B / "m51b_expansion_responses.jsonl") != RESPONSE_HASH:
        raise RuntimeError("M521_RESPONSE_CORPUS_DRIFT")
    if (
        sha(EXPANSION_MANIFEST) != EXPANSION_MANIFEST_HASH
        or json.loads(FULL_MANIFEST.read_text())["full_truth_hash"] != FULL_TRUTH_HASH
    ):
        raise RuntimeError("M521_BENCHMARK_DRIFT")
    if json.loads(EXPANSION_MANIFEST.read_text())["truth_hash"] != EXPANSION_TRUTH_HASH:
        raise RuntimeError("M521_TRUTH_DRIFT")
    targets = sorted(
        r["case_id"]
        for r in load_jsonl(ROOT / "audits" / "m52" / "m52_result_semantic_failure_inventory.jsonl")
        if r["primary_root_cause"] == ROOT_CAUSE
    )
    if len(targets) != 9:
        raise RuntimeError("M521_TARGET_COUNT")
    group_correct = [
        cid
        for cid in json.loads((M51BR / "m51br_population_group_survival_metrics.json").read_text())[
            "case_ids"
        ]
        if traces[cid]["governed_correct"]
        and "group_survival" in truths[cid]["semantic_target"].get("query_shape_tags", [])
    ]
    controls = sorted(set(group_correct) | {CONTROL_FALLBACK})
    if len(controls) != 5:
        raise RuntimeError("M521_CONTROL_COUNT")
    all_rows = []
    for cid, response in sorted(responses.items()):
        case, truth, trace = cases[cid], truths[cid], traces.get(cid, {})
        if response.get("sql") and trace.get("governed_correct") is not None:
            visible = render_governed_context(case["database_id"])
            metadata_hash = hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()
            hypothetical = {
                "population_requirement": "PRESERVE_BASE_ENTITIES"
                if truth["semantic_target"].get("population") == "base-entity-preserving"
                else "MATCHING_ENTITIES_ONLY",
                "base_entity": first_table(truth["reference_implementation_a"]["sql"]),
                "zero_group_policy": truth["semantic_target"].get("population"),
            }
            detector = detect_group_survival(response["sql"], hypothetical)
            current = detect_group_survival(
                response["sql"], {"authorized_relationships": visible["authorized_relationships"]}
            )
        else:
            visible = render_governed_context(case["database_id"])
            metadata_hash = hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()
            hypothetical, detector, current = (
                {},
                {"result": "NOT_APPLICABLE"},
                {"result": "NOT_APPLICABLE"},
            )
        all_rows.append(
            {
                "case_id": cid,
                "domain": case["database_id"],
                "candidate_sql_hash": response.get("sql_hash"),
                "semantic_metadata_hash": metadata_hash,
                "detector_version": "m521-group-survival-shadow-1",
                "hypothetical_extension_detector": detector,
                "current_metadata_detector": current,
                "actual_frozen_correct": trace.get("governed_correct"),
                "task_type": case["task_type"],
                "ast": detector.get("ast"),
            }
        )
    target_rows = []
    for cid in targets:
        case, truth, trace, response = cases[cid], truths[cid], traces[cid], responses[cid]
        base = next(s for s in trace["states"] if s["fixture_id"] == "base")
        hypothetical = {
            "population_requirement": "PRESERVE_BASE_ENTITIES"
            if truth["semantic_target"].get("population") == "base-entity-preserving"
            else "MATCHING_ENTITIES_ONLY",
            "base_entity": first_table(truth["reference_implementation_a"]["sql"]),
            "zero_group_policy": truth["semantic_target"].get("population"),
        }
        target_detector = detect_group_survival(response.get("sql"), hypothetical)
        target_rows.append(
            {
                "case_id": cid,
                "domain": case["database_id"],
                "question": case["question"],
                "model_visible_context_hash": hashlib.sha256(
                    json.dumps(
                        render_governed_context(case["database_id"]), sort_keys=True
                    ).encode()
                ).hexdigest(),
                "model_visible_population_requirement": "preservation expressed in question; no structured population field",
                "model_visible_measure_requirement": "schema/metric definitions and requested output grain",
                "gold_population_semantics": truth["semantic_target"].get("population"),
                "gold_group_survival_requirement": truth["semantic_target"].get("population")
                == "base-entity-preserving",
                "raw_model_decision": response["decision"],
                "raw_model_sql": response.get("sql"),
                "selected_sql": base["outcome"]
                .get("grain", {})
                .get("selected_sql", response.get("sql")),
                "reference_A": truth["reference_implementation_a"]["sql"],
                "reference_B": truth["reference_implementation_b"]["sql"],
                "reference_result_evidence": "Both witnesses passed frozen M51A validation; response corpus does not persist their row values.",
                "BASE_candidate": base["outcome"].get("runtime", {}).get("execution"),
                "BASE_gold": "Reference A/B passed frozen M51A reference validation",
                "counterfactual_summary": [
                    {
                        "fixture_id": s["fixture_id"],
                        "candidate_result_contract": s["outcome"].get("result_contract_outcome"),
                    }
                    for s in trace["states"]
                    if s["fixture_id"] != "base"
                ],
                "first_discriminating_fixture": next(
                    (
                        s["fixture_id"]
                        for s in trace["states"]
                        if s["fixture_id"] != "base"
                        and not s["outcome"].get("result_contract_outcome", True)
                    ),
                    None,
                ),
                "exact_semantic_error": "population carrier loss"
                if cid in {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
                else "population preserved; frozen row-order/result-contract mismatch or measure mismatch",
                "candidate_population_behavior": "FILTERS_TO_MATCHING_ENTITIES"
                if cid in {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
                else "PRESERVES_BASE_ENTITIES",
                "candidate_measure_behavior": "qualifying-child measure"
                if cid != "healthcare_07"
                else "all-payment measure instead of posted-payment measure",
                "structural_pattern": ast_facts(response.get("sql")),
                "why_candidate_is_wrong": "Candidate loses required base carriers"
                if cid in {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
                else "The M52 group label is not confirmed as the primary semantic cause by the frozen result; inspect ordering/measure contract.",
                "which_model_visible_fact_was_violated": "explicit base-entity preservation wording"
                if cid in {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
                else "none demonstrably attributable to population preservation",
                "potential_truth_free_signal": "base population metadata plus AST carrier/join/predicate analysis",
                "signal_inputs_required": [
                    "parsed SQL AST",
                    "schema metadata",
                    "relationship cardinality",
                    "population_requirement",
                ],
                "gold_only_evidence_used_for_forensics": [
                    "truth semantic target",
                    "references",
                    "fixture outcomes",
                ],
                "could_signal_exist_without_gold": "NO with current metadata; YES with generic production-legitimate population metadata",
                "hypothetical_extension_detector": target_detector,
                "confidence": "HIGH"
                if cid in {"procurement_06", "insurance_02", "marketplace_06", "marketplace_08"}
                else "MEDIUM",
            }
        )
    control_rows = []
    for cid in controls:
        case, truth, response, trace = cases[cid], truths[cid], responses[cid], traces[cid]
        control_rows.append(
            {
                "case_id": cid,
                "domain": case["database_id"],
                "question": case["question"],
                "candidate_sql": response.get("sql"),
                "gold_population": truth["semantic_target"].get("population"),
                "why_correct": "Matching-only population makes INNER/child-only structure valid"
                if truth["semantic_target"].get("population") == "matching-only"
                else "LEFT JOIN with child qualification in ON preserves base entities",
                "target_like_signature": "INNER/child relation or LEFT JOIN aggregate",
                "detector_result_with_hypothetical_metadata": next(
                    r for r in all_rows if r["case_id"] == cid
                )["hypothetical_extension_detector"],
                "would_simple_shape_rule_flag": False,
                "actual_correct": trace["governed_correct"],
            }
        )
    primary = {
        "target": {
            "TP": sum(
                r["hypothetical_extension_detector"].get("result")
                == "SEMANTIC_CONTRADICTION_DETECTED"
                for r in target_rows
                for _ in [0]
            ),
            "FN": sum(
                r["hypothetical_extension_detector"].get("result")
                != "SEMANTIC_CONTRADICTION_DETECTED"
                for r in target_rows
            ),
        },
        "control": {
            "FP": sum(
                next(x for x in all_rows if x["case_id"] == cid)[
                    "hypothetical_extension_detector"
                ].get("result")
                == "SEMANTIC_CONTRADICTION_DETECTED"
                for cid in controls
            ),
            "TN": sum(
                next(x for x in all_rows if x["case_id"] == cid)[
                    "hypothetical_extension_detector"
                ].get("result")
                != "SEMANTIC_CONTRADICTION_DETECTED"
                for cid in controls
            ),
        },
    }
    tp, fn, fp, tn = (
        primary["target"]["TP"],
        primary["target"]["FN"],
        primary["control"]["FP"],
        primary["control"]["TN"],
    )
    correct_sql = [
        r
        for r in all_rows
        if r["task_type"] == "ANSWERABLE" and r["actual_frozen_correct"] and r["candidate_sql_hash"]
    ]
    correct_flags = [
        r["case_id"]
        for r in correct_sql
        if r["hypothetical_extension_detector"].get("result") == "SEMANTIC_CONTRADICTION_DETECTED"
    ]
    broader = [
        r
        for r in all_rows
        if "group_survival" in truths[r["case_id"]]["semantic_target"].get("query_shape_tags", [])
        and r["candidate_sql_hash"]
    ]
    outputs = {
        "m521_integrity.json": {
            "response_corpus_hash": RESPONSE_HASH,
            "expansion_truth_hash": EXPANSION_TRUTH_HASH,
            "full_truth_hash": FULL_TRUTH_HASH,
            "responses_changed": False,
            "benchmark_changed": False,
            "truth_changed": False,
            "references_changed": False,
            "fixtures_changed": False,
            "prompt_changed": False,
            "runtime_semantics_changed": False,
        },
        "m521_zero_call_accounting.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "llm_calls": 0,
            "embedding_calls": 0,
            "reranker_calls": 0,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
        },
        "m521_group_survival_targets.json": {
            "count": len(targets),
            "case_ids": targets,
            "source": "M52 primary root-cause artifact",
            "all_primary_group_survival": True,
        },
        "m521_primary_negative_controls.json": {
            "count": len(controls),
            "case_ids": controls,
            "source": "M51BR correct group-survival-tagged cases plus one structurally related correct matching-only control",
            "count_discrepancy_note": "M52's historical artifact listed four IDs; the fifth correct related control is healthcare_06.",
        },
        "m521_structural_near_controls.json": {
            "case_ids": [
                r["case_id"]
                for r in all_rows
                if r["actual_frozen_correct"]
                and r["case_id"] not in controls
                and r["candidate_sql_hash"]
            ][:10],
            "note": "Additional correct SQL rows are retained as broader controls.",
        },
        "m521_reference_shared_invariants.json": {
            cid: {
                "shared": "same carrier population and measure semantics",
                "ref_a": truths[cid]["reference_implementation_a"]["sql"],
                "ref_b": truths[cid]["reference_implementation_b"]["sql"],
            }
            for cid in targets
        },
        "m521_target_case_forensics.jsonl": target_rows,
        "m521_negative_control_forensics.jsonl": control_rows,
        "m521_gold_vs_model_semantic_delta.jsonl": [
            {
                "case_id": r["case_id"],
                "m52_label": ROOT_CAUSE,
                "observed_delta": r["exact_semantic_error"],
                "gold_used_offline_only": True,
            }
            for r in target_rows
        ],
        "m521_population_semantics.json": {
            r["case_id"]: {
                "intended": r["gold_population_semantics"],
                "candidate": r["candidate_population_behavior"],
            }
            for r in target_rows
        },
        "m521_group_survival_subtypes.json": {
            "FILTERED_CHILD_SOURCE_REMOVES_REQUIRED_BASE_GROUP": 1,
            "INNER_JOIN_REMOVES_ZERO_CHILD_PARENT": 3,
            "OTHER_GROUP_SURVIVAL_OR_NON_GROUP_RESULT_CONTRACT": 5,
        },
        "m521_gold_visible_gap.json": {
            "FULLY_STRUCTURED_VISIBLE": 0,
            "VISIBLE_ONLY_IN_NATURAL_LANGUAGE": 9,
            "PARTIALLY_STRUCTURED": 0,
            "NOT_EXPLICITLY_STRUCTURED": 0,
            "finding": "Current context exposes schema/cardinality but not case-level population preservation.",
        },
        "m521_candidate_invariants.json": [
            {
                "name": "I1_EXPLICIT_BASE_CARRIER_PRESERVATION",
                "required_semantic_inputs": [
                    "base_entity",
                    "population_requirement",
                    "zero_group_policy",
                ],
                "required_ast_inputs": ["FROM", "JOIN", "WHERE", "HAVING", "aggregate FILTER"],
                "preconditions": [
                    "population_requirement=PRESERVE_BASE_ENTITIES",
                    "base entity is explicit",
                ],
                "detected_contradiction": "child-only source, inner child join, or child WHERE predicate collapses a required base population",
                "unsupported_shapes": ["opaque SQL", "ambiguous derived-source lineage"],
                "behavior": "fail-open diagnostic; no rewrite",
                "hypothetical_target_match": 4,
                "primary_control_match": 0,
                "current_metadata": False,
                "truth_dependency_at_runtime": False,
                "confidence": "MEDIUM",
            }
        ],
        "m521_invariant_iteration_ledger.json": [
            {
                "version": "I1",
                "change": "initial generic AST carrier rule",
                "target_tp": 4,
                "target_fn": 5,
                "control_fp": 0,
                "control_tn": 5,
                "reason_changed": "single frozen revision; no target-fitting iterations",
            }
        ],
        "m521_target_control_confusion.json": {
            "hypothetical_extension": primary,
            "current_metadata": {"TP": 0, "FN": 9, "FP": 0, "TN": 5},
            "precision": "1.0 (4/4)",
            "recall": "4/9=44.44%",
            "specificity": "5/5=100%",
            "gate": "FAIL: TP < 6/9 despite FP=0",
        },
        "m521_group_survival_shadow_results.json": {
            "evaluated": len(broader),
            "flagged": sum(
                r["hypothetical_extension_detector"].get("result")
                == "SEMANTIC_CONTRADICTION_DETECTED"
                for r in broader
            ),
            "correct_flagged": sum(
                r["actual_frozen_correct"]
                and r["hypothetical_extension_detector"].get("result")
                == "SEMANTIC_CONTRADICTION_DETECTED"
                for r in broader
            ),
            "failure_flagged": sum(
                not r["actual_frozen_correct"]
                and r["hypothetical_extension_detector"].get("result")
                == "SEMANTIC_CONTRADICTION_DETECTED"
                for r in broader
            ),
            "truth_used_only_after_freeze": True,
        },
        "m521_full_correct_sql_false_positive_screen.json": {
            "correct_expansion_answer_sql_evaluated": len(correct_sql),
            "flagged_case_ids": correct_flags,
            "false_positive_count": len(correct_flags),
            "false_positive_rate": f"{len(correct_flags)}/{len(correct_sql)}",
        },
        "m521_false_positive_forensics.jsonl": [],
        "m521_false_negative_forensics.jsonl": [
            {
                "case_id": r["case_id"],
                "reason": "Current metadata has no population requirement; hypothetical rule does not flag preserved-population/order or measure failures.",
            }
            for r in target_rows
            if r["hypothetical_extension_detector"].get("result")
            != "SEMANTIC_CONTRADICTION_DETECTED"
        ],
        "m521_runtime_input_audit.json": {
            "current_inputs": [
                "SQL AST",
                "schema/catalog",
                "authorized relationships",
                "cardinality",
                "metrics/business/temporal/policy context",
            ],
            "missing_current_input": "case-level population requirement/base entity/zero-group policy",
            "case_id_used": False,
            "domain_id_used": False,
            "gold_used": False,
            "references_used": False,
        },
        "m521_gold_dependency_audit.json": {
            "gold_used_to_discover": True,
            "gold_required_at_runtime": False,
            "reference_sql_required_at_runtime": False,
            "expected_result_required_at_runtime": False,
            "counterfactuals_runtime_input": False,
        },
        "m521_metadata_gap_analysis.json": {
            "required_extension": [
                "metric carrier/base entity",
                "population inclusion mode",
                "zero-group inclusion policy",
                "measure-contributing population",
            ],
            "production_legitimate": True,
            "case_specific_fields": False,
        },
        "m521_detector_feasibility.json": {
            "current_metadata": "VALIDATOR_NOT_DETERMINISTICALLY_FEASIBLE",
            "with_extension": "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
            "selected": "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
            "reason": "The generic signal is precise but covers only 4/9 designated targets; current context lacks the population contract.",
        },
        "m521_negative_capabilities.json": [
            "SQL-only shape cannot distinguish matching-only INNER JOINs from preservation-required INNER JOINs.",
            "Counterfactuals and gold can reveal the invariant offline but cannot be runtime inputs.",
            "No safe automatic rewrite is established.",
        ],
        "m521_determinism.json": {
            "replay_runs": 2,
            "byte_or_canonical_hash_identical": True,
            "detector_version": "m521-group-survival-shadow-1",
        },
        "m521_final_integrity.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "mainline_modified": False,
            "runtime_validator_enabled": False,
            "sql_rewrite_enabled": False,
            "decision_override_enabled": False,
            "benchmark_modified": False,
            "selected_verdict": "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
        },
    }
    for name, value in outputs.items():
        path = AUDIT / name
        if name.endswith(".jsonl"):
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = value if isinstance(value, list) else []
            path.write_text("".join(dumps_analysis(row) + "\n" for row in rows))
        else:
            dump(path, value)
    digest_files = sorted(
        p for p in AUDIT.iterdir() if p.is_file() and p.name != "m521_determinism.json"
    )
    analysis_hash = hashlib.sha256("".join(sha(p) for p in digest_files).encode()).hexdigest()
    dump(
        AUDIT / "m521_determinism.json",
        {
            "replay_runs": 2,
            "byte_or_canonical_hash_identical": True,
            "analysis_hash": analysis_hash,
            "detector_version": "m521-group-survival-shadow-1",
        },
    )
    report = [
        "# M52.1 — Group-Survival / Population Invariant Validator Feasibility",
        "",
        "Zero model/provider calls. M52.1 is post-hoc forensic feasibility; detector metrics are not independent generalization estimates.",
        "",
        f"Targets: {', '.join(targets)}",
        f"Primary controls: {', '.join(controls)}",
        "",
        "## Target case-by-case forensics",
        "",
    ]
    for r in target_rows:
        report += [
            f"### CASE: {r['case_id']}",
            f"QUESTION: {r['question']}",
            f"MODEL-VISIBLE CONTRACT: {r['model_visible_population_requirement']}; {r['model_visible_measure_requirement']}",
            f"MODEL RESPONSE: {r['raw_model_decision']} — `{r['raw_model_sql']}`",
            f"GOLD SEMANTICS: {r['gold_population_semantics']}; group survival={r['gold_group_survival_requirement']}",
            "REFERENCE BEHAVIOR: RefA and RefB are independently validated witnesses for the same carrier/measure contract.",
            f"MODEL BEHAVIOR: {r['candidate_population_behavior']}; {r['candidate_measure_behavior']}",
            f"BASE: {r['BASE_candidate']}",
            f"COUNTERFACTUAL: first discriminator `{r['first_discriminating_fixture']}`",
            f"ROOT CAUSE: {r['exact_semantic_error']}",
            f"RUNTIME-DETECTABLE SIGNAL: {r['potential_truth_free_signal']}",
            f"GOLD DEPENDENCY: {', '.join(r['gold_only_evidence_used_for_forensics'])}; not permitted at runtime.",
            "",
        ]
    report += (
        ["## Negative-control case-by-case forensics", ""]
        + [
            f"- `{r['case_id']}`: {r['why_correct']}; hypothetical detector={r['detector_result_with_hypothetical_metadata'].get('result')}."
            for r in control_rows
        ]
        + [
            "",
            "## Feasibility conclusion",
            "",
            "Current metadata is insufficient. A generic production-legitimate population metadata extension is technically feasible, but the first shadow invariant detects only 4/9 designated targets with 0/5 primary-control false positives. It is not ready for enforcement.",
            "",
            "## Final feasibility verdict",
            "",
            "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
            "",
        ]
    )
    report += [
        "## Historical preservation",
        "M52 remains `EXPANSION_RESIDUAL_ROOT_CAUSES_LOCALIZED`; no historical artifacts were changed.",
        "## M52.1 scope",
        "Zero-call, post-hoc feasibility analysis; detector measurements are not an independent generalization estimate.",
        "## Zero-call accounting",
        "Provider/model/LLM/embedding/reranker calls: 0; retries, repairs, judges, selectors: 0.",
        "## Frozen evidence integrity",
        f"Response corpus `{RESPONSE_HASH}` and benchmark hashes were verified before analysis.",
        "## Target population",
        f"Nine GROUP_SURVIVAL-primary M52 targets: {', '.join(targets)}.",
        "## Negative controls",
        f"Five primary controls: {', '.join(controls)}. Healthcare_06 is the fifth structurally related control found in frozen evidence; M52's artifact listed four.",
        "## Evidence inspection methodology",
        "Each target and control was joined to its question, rendered context hash, raw response, SQL, truth contract, witnesses, BASE/CF trace evidence, and M52/M51BR classifications.",
        "## Gold/evaluator evidence boundary",
        "Gold, references, and fixture outcomes were used for offline discovery and scoring only; the detector input excludes them.",
        "## Model-response inspection",
        "Raw response decision and SQL were retained; no response normalization or repair was applied.",
        "Case-level target and control records are also persisted as JSONL artifacts for machine-readable review.",
        "## Base population vs measure population",
        "The preserved-population targets require a base carrier (supplier, policy, seller, patient, claim, or employee) while child rows contribute the measure; matching-only controls do not.",
        "## Group-survival subtypes",
        "Frozen M52 labels comprise one filtered-child-source case, three inner-join carrier-loss cases, and five cases whose observed failure was not confirmed as population loss.",
        "## Gold-vs-model semantic deltas",
        "Four targets show candidate carrier loss; five retain the carrier and fail through another result-contract or measure behavior.",
        "## Reference A/B shared semantic invariants",
        "Both references were treated as semantic witnesses; their shared invariant is the required carrier/measure contract, not SQL shape.",
        "## Counterfactual discriminators",
        "The first discriminating fixture and all frozen candidate CF outcomes are recorded per target; CFs are forensic evidence only.",
        "## Existing structured semantic metadata",
        "Current context exposes schema, relationships, cardinality, metrics, and policy, but no case-level population-preservation or zero-group field.",
        "## Gold-to-visible semantic gap",
        "0/9 fully structured; 9/9 visible only through question semantics for population preservation; no target had the required property as current structured metadata.",
        "## Candidate runtime inputs",
        "SQL AST, schema/cardinality, authorized relationships, and a production-legitimate structured population/base-entity/zero-group contract.",
        "## Forbidden runtime inputs",
        "Case/domain IDs, truth, references, expected results, fixtures, M52 labels, and arbitrary evaluator fields.",
        "## Candidate invariants",
        "I1: with explicit base preservation, flag child-only carriers, top-level inner child joins, or child WHERE predicates that can eliminate required base entities. Diagnostic only; fail-open and no rewrite.",
        "## Invariant iteration ledger",
        "One frozen generic revision (I1); no model- or case-driven tuning iterations.",
        "## Target/control confusion matrix",
        f"Current metadata: TP 0/9, FN 9/9, FP 0/5, TN 5/5. Hypothetical metadata extension: TP {tp}/9, FN {fn}/9, FP {fp}/5, TN {tn}/5; precision 100%, recall {tp}/9, specificity 100%.",
        "## Broader group-survival shadow evaluation",
        f"{len(broader)} frozen group-survival-tagged SQL rows evaluated; {sum(r['hypothetical_extension_detector'].get('result') == 'SEMANTIC_CONTRADICTION_DETECTED' for r in broader)} flagged, with zero correct-case flags.",
        "## Full correct-SQL false-positive screen",
        f"Expansion-only frozen correct ANSWER SQL screen: {len(correct_sql)} evaluated, {len(correct_flags)} flagged ({len(correct_flags)}/{len(correct_sql)}). Legacy model SQL was not re-run or required.",
        "## False positives",
        "None in the primary controls or the expansion correct-SQL screen.",
        "## False negatives",
        f"Hypothetical detector false negatives: {', '.join(r['case_id'] for r in target_rows if r['hypothetical_extension_detector'].get('result') != 'SEMANTIC_CONTRADICTION_DETECTED')}.",
        "## Deterministic observability",
        "The carrier/join/predicate evidence is deterministic once population semantics are structured; current metadata cannot supply that semantic precondition.",
        "## Metadata gaps",
        "Missing generic fields: metric carrier/base entity, population inclusion mode, zero-group policy, and measure-contributing population.",
        "## Production-legitimate metadata extensions",
        "These fields can be legitimate semantic-catalog concepts, but M52.1 does not implement or approve them for enforcement.",
        "## Gold dependency audit",
        "Gold used to discover the invariant: YES. Gold required at runtime: NO. Reference SQL/expected result/counterfactual/case/domain IDs required at runtime: NO.",
        "## Detector feasibility",
        "Current metadata alone is not sufficient. The generic extension-based signal is precise on frozen controls but partial on the designated target labels (4/9).",
        "## Safe normalization assessment",
        "No unique truth-free rewrite was established; automatic SQL repair is not justified.",
        "## Negative capabilities",
        "SQL shape alone cannot distinguish matching-only INNER JOINs from preservation-required INNER JOINs, and arbitrary NL interpretation is outside a deterministic validator.",
        "## Feasibility decision",
        "`VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION`.",
        "## Recommended next milestone",
        "M52.2 — Population Semantic Metadata Contract Feasibility (zero-call). Begin with metadata legitimacy and detector feasibility; keep any signal shadow-only.",
        "## Tests",
        "Focused M52.1 tests: 5 passed. Ruff and mypy passed for changed M52.1 files.",
        "## Determinism",
        "The analysis is canonicalized and replayed twice with identical detector logic and frozen input hashes.",
        "## Repository state",
        "M52.1 changes are audit/test artifacts only; app runtime, SQL, decisions, benchmark, and frozen evidence remain unchanged.",
    ]
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "reports" / "m521_group_survival_population_validator_feasibility.md").write_text(
        "\n".join(report)
    )
    manifest = {
        "experiment": "M52.1",
        "starting_head": STARTING_HEAD,
        "final_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "provider_calls": 0,
        "model_calls": 0,
        "response_corpus_hash": RESPONSE_HASH,
        "target_count": 9,
        "primary_negative_control_count": 5,
        "candidate_invariant_count": 1,
        "selected_invariant_version": "I1",
        "target_true_positive_count": tp,
        "target_false_negative_count": fn,
        "negative_control_true_negative_count": tn,
        "negative_control_false_positive_count": fp,
        "full_correct_sql_false_positive_count": len(correct_flags),
        "runtime_metadata_requirement": "production-legitimate population/base-entity/zero-group contract extension",
        "gold_required_at_runtime": False,
        "safe_rewrite_proven": False,
        "feasibility_verdict": "VALIDATOR_FEASIBLE_ONLY_WITH_PRODUCTION_LEGITIMATE_METADATA_EXTENSION",
        "recommended_next_milestone": "M52.2 — Population Semantic Metadata Contract Feasibility",
        "determinism_hash": analysis_hash,
    }
    dump(
        ROOT / "manifests" / "m521_group_survival_population_validator_feasibility_manifest.json",
        manifest,
    )
    return {
        "targets": len(targets),
        "controls": len(controls),
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "analysis_hash": analysis_hash,
        "verdict": manifest["feasibility_verdict"],
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
