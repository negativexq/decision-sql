# ruff: noqa: E501

"""Deterministic, provider-free M10.4S causal attribution.

The audit consumes only the frozen M10.4S corpus/gold, the case ledger, and
runtime provenance.  It never calls a provider, database, evaluator judge, or
embedding service.  Structural SQL comparisons are bounded diagnostics; V1
execution/evaluator status remains authoritative.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from sqlglot import exp, parse_one

from evaluation.m4_benchmark import build_memory_corpus
from evaluation.m104s_corpus import FIXTURES
from evaluation.m104s_protocol import RESULT_ROOT, file_hash

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = FIXTURES / "m104s_corpus.json"
RESULTS_PATH = RESULT_ROOT / "case_ledger.jsonl"
PROVENANCE_PATH = RESULT_ROOT / "runtime_provenance.jsonl"
MECHANISMS = (
    "EVALUATOR_ARTIFACT",
    "EXECUTION_FAILURE",
    "TRANSPORT_FAILURE",
    "M1_REJECTION",
    "ROUTING_OVERREACH",
    "ROUTING_FALSE_DIRECT",
    "GOVERNED_GROUNDING_FAILURE",
    "GOVERNED_COMPILER_FAILURE",
    "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE",
    "MEMORY_OVERTRANSFER_EVIDENCE",
    "DOWNSTREAM_GENERATION_FAILURE",
    "UNRESOLVED",
)
GRADES = (
    "E1_DIRECT_DECISION_EVIDENCE",
    "E2_DETERMINISTIC_CORROBORATION",
    "E3_TRACE_ASSOCIATION",
    "E4_MANUAL_DIAGNOSTIC",
    "E5_UNRESOLVED",
)
FAMILY_PHENOTYPE = {
    "SCHEMA_SOURCE_GROUNDING": "SCHEMA_SOURCE",
    "JOIN_GRAIN": "JOIN_GRAIN",
    "AGGREGATION_GROUPING": "AGGREGATION_GROUPING",
    "FORMULA_RATIO": "FORMULA_RATIO",
    "FILTER_COLUMN": "FILTER_COLUMN",
    "FILTER_OPERATOR_BOOLEAN": "FILTER_BOOLEAN_LOGIC",
    "FILTER_LITERAL": "FILTER_LITERAL",
    "TEMPORAL": "TEMPORAL",
    "WINDOW": "WINDOW",
    "PROJECTION_RESULT_SHAPE": "PROJECTION_RESULT_SHAPE",
    "ORDER_TOPN": "ORDER_TOPN",
    "DISTINCT_SET": "DISTINCT_SET",
    "GOVERNED_SEMANTIC": "COMPOUND_SEMANTIC",
}


def _load_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _normal_expr(node: exp.Expression | None, aliases: dict[str, str]) -> str:
    if node is None:
        return ""
    copy = node.copy()
    for table in copy.find_all(exp.Table):
        table.set("alias", None)
    for column in copy.find_all(exp.Column):
        if column.table:
            column.set("table", aliases.get(column.table, column.table))
    for alias in copy.find_all(exp.Alias):
        alias.set("alias", None)
    return str(copy.sql(dialect="postgres", pretty=False))


def _sql_features(sql: str | None) -> dict[str, Any] | None:
    if not sql:
        return None
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return None
    aliases: dict[str, str] = {}
    occurrences: Counter[str] = Counter()
    table_tokens: dict[int, str] = {}
    for table in tree.find_all(exp.Table):
        name = table.name
        token = f"{name}#{occurrences[name]}"
        occurrences[name] += 1
        table_tokens[id(table)] = token
        if table.alias:
            aliases[table.alias] = token
    # An unaliased reference is unambiguous only when the statement contains
    # one instance of that physical table.  Self-joins retain occurrence
    # identity instead of being broadly alias-normalized.
    for table_name, count in occurrences.items():
        if count == 1:
            aliases[table_name] = f"{table_name}#0"

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return None
    joins = []
    for join in tree.find_all(exp.Join):
        target = join.this
        target_name = target.name if isinstance(target, exp.Table) else _normal_expr(target, aliases)
        joins.append((target_name, _normal_expr(join.args.get("on"), aliases)))
    order = []
    order_arg = select.args.get("order")
    if order_arg is not None:
        for item in order_arg.expressions:
            order.append(_normal_expr(item, aliases))
    windows = sorted(_normal_expr(item, aliases) for item in tree.find_all(exp.Window))
    aggregates = sorted(type(item).__name__.upper() for item in tree.find_all(exp.AggFunc))
    set_operators = sorted(
        type(item).__name__.upper()
        for item in tree.find_all((exp.Union, exp.Intersect, exp.Except))
    )
    return {
        "sources": tuple(table_tokens.values()),
        "joins": tuple(joins),
        "projection": tuple(_normal_expr(item, aliases) for item in select.expressions),
        "where": _normal_expr(select.args.get("where"), aliases),
        "group": tuple(_normal_expr(item, aliases) for item in (select.args.get("group").expressions if select.args.get("group") else ())),
        "order": tuple(order),
        "limit": _normal_expr(select.args.get("limit"), aliases),
        "distinct": bool(select.args.get("distinct")),
        "aggregates": tuple(aggregates),
        "windows": tuple(windows),
        "set_operators": tuple(set_operators),
    }


def _material_differences(left: dict[str, Any] | None, right: dict[str, Any] | None) -> list[str]:
    if left is None or right is None:
        return ["sql_identity_unavailable"]
    return [key for key in left if left[key] != right[key]]


def _stable_id(event: dict[str, Any]) -> str:
    return f"{event['run_id']}:{event['sequence']}:{event['stage']}"


def _event_map(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {event["stage"]: event for event in events}


def _same_material_motif(candidate: dict[str, Any], memory: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    return [
        key
        for key in candidate
        if candidate[key] == memory[key] and candidate[key] != reference[key]
    ]


def audit() -> dict[str, Any]:
    cases = {case["case_id"]: case for case in json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]}
    rows = {row["case_id"]: row for row in _load_lines(RESULTS_PATH)}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _load_lines(PROVENANCE_PATH):
        events[event["case_id"]].append(event)
    memory_sql = {example.example_id: example.sql for example in build_memory_corpus()}
    memory_features = {key: _sql_features(value) for key, value in memory_sql.items()}

    records: list[dict[str, Any]] = []
    primary_counts: Counter[str] = Counter()
    grade_counts: Counter[str] = Counter()
    partition_grades: dict[str, Counter[str]] = {"DIAG_A": Counter(), "DIAG_B": Counter()}
    phenotype_counts: Counter[str] = Counter()
    memory_strength: Counter[str] = Counter()
    compatible_memory_failures = 0
    selected_compatible = selected_incompatible = 0
    correct_memory = incorrect_memory = 0
    route_counts: Counter[str] = Counter(cast(str, row.get("actual_path")) for row in rows.values())
    governed_owned: Counter[str] = Counter()
    governed_metric_results: dict[str, Counter[str]] = defaultdict(Counter)
    v2: Counter[str] = Counter()

    for case_id, row in sorted(rows.items()):
        case = cases[case_id]
        case_events = events.get(case_id, [])
        by_stage = _event_map(case_events)
        candidate_features = _sql_features(row.get("candidate_sql"))
        reference_features = _sql_features(case.get("reference_sql"))
        failure = not bool(row.get("v1_correct"))
        mechanism: str | None = None
        grade: str | None = None
        reason = "authoritative V1 correct control"
        strength = "MT0_NO_TRANSFER_EVIDENCE"
        transfer_motifs: list[str] = []
        selected_ids = list((by_stage.get("MEMORY_SELECTION") or {}).get("payload", {}).get("selected_memory_ids", []))
        selected_features = [
            cast(dict[str, Any], memory_features[item])
            for item in selected_ids
            if item in memory_features and memory_features[item] is not None
        ]

        if row.get("memory_used"):
            if failure:
                incorrect_memory += 1
            else:
                correct_memory += 1
        if row.get("v2_shadow", {}).get("status") == "V2_EVALUATED":
            v2["bindable"] += 1
            if bool(row["v2_shadow"].get("equivalent")) == bool(row.get("v1_correct")):
                v2["agreements"] += 1
            else:
                v2["disagreements"] += 1
                if not row.get("v1_correct") and row["v2_shadow"].get("equivalent"):
                    v2["v1_wrong_v2_correct"] += 1
                if row.get("v1_correct") and not row["v2_shadow"].get("equivalent"):
                    v2["v1_correct_v2_wrong"] += 1
        else:
            v2["unavailable"] += 1

        if not failure:
            pass
        elif row.get("primary_status") == "TRANSPORT_FAILURE":
            mechanism, grade, reason = "TRANSPORT_FAILURE", "E1_DIRECT_DECISION_EVIDENCE", "provider transport failed after frozen retry behavior"
        elif row.get("primary_status") == "M1_REJECTED":
            mechanism, grade, reason = "M1_REJECTION", "E1_DIRECT_DECISION_EVIDENCE", "M1 rejected the generated candidate"
        elif case["expected_route"] == "DIRECT" and row.get("actual_path") == "GOVERNED_METRIC":
            mechanism, grade, reason = "ROUTING_OVERREACH", "E1_DIRECT_DECISION_EVIDENCE", "direct gold case took the governed route"
        elif case["expected_route"] == "GOVERNED" and row.get("actual_path") != "GOVERNED_METRIC":
            mechanism, grade, reason = "ROUTING_FALSE_DIRECT", "E1_DIRECT_DECISION_EVIDENCE", "governed gold case did not take the governed route"
        elif row.get("actual_path") == "GOVERNED_METRIC":
            grounding = (by_stage.get("GOVERNED_GROUNDING_RESULT") or {}).get("payload", {}).get("grounding_output") or {}
            metric_ok = grounding.get("metric_name") == case.get("governed_metric")
            dimension_ok = tuple(grounding.get("dimensions", ())) == tuple(case.get("dimensions", ()))
            governed_owned["applicable_correct"] += int(bool(grounding.get("applicable")) == (case["expected_route"] == "GOVERNED"))
            governed_owned["metric_correct"] += int(metric_ok)
            governed_owned["dimensions_correct"] += int(dimension_ok)
            governed_metric_results[case["governed_metric"]]["cases"] += 1
            governed_metric_results[case["governed_metric"]]["correct"] += int(bool(row.get("v1_correct")))
            if not metric_ok or not dimension_ok:
                mechanism, grade, reason = "GOVERNED_GROUNDING_FAILURE", "E1_DIRECT_DECISION_EVIDENCE", "grounding DTO differs from frozen metric/dimension semantics"
            elif _material_differences(candidate_features, reference_features):
                mechanism, grade, reason = "GOVERNED_COMPILER_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "grounding-owned DTO is compatible but deterministic candidate SQL structure differs from the frozen governed reference"
            else:
                mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "V1 result mismatch remains after compatible grounding and structurally compatible compiler output"
        elif candidate_features is None:
            mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "candidate SQL identity is unavailable for bounded structural attribution"
        else:
            incompatible = [item for item in selected_features if _material_differences(item, reference_features)]
            if selected_ids:
                if incompatible:
                    selected_incompatible += 1
                else:
                    selected_compatible += 1
            if incompatible:
                transfer_motifs = sorted(
                    {
                        motif
                        for item in incompatible
                        for motif in _same_material_motif(
                            candidate_features,
                            item,
                            cast(dict[str, Any], reference_features),
                        )
                    }
                )
                if transfer_motifs:
                    mechanism, grade, reason = "MEMORY_OVERTRANSFER_EVIDENCE", "E3_TRACE_ASSOCIATION", "candidate carries a material structural motif present in rendered selected memory and absent from the frozen gold structure"
                    strength = "MT3_MATERIAL_WRONG_MOTIF_TRANSFER"
                    memory_strength[strength] += 1
                else:
                    mechanism, grade, reason = "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "a selected memory entry is materially incompatible with frozen gold structure and no earlier mechanism applies"
                    strength = "MT2_STRUCTURAL_MOTIF_MATCH"
                    memory_strength[strength] += 1
            elif _material_differences(candidate_features, reference_features):
                mechanism, grade, reason = "DOWNSTREAM_GENERATION_FAILURE", "E2_DETERMINISTIC_CORROBORATION", "no incompatible selected memory was established, but generated SQL structure diverges from frozen gold"
                if selected_ids:
                    compatible_memory_failures += 1
            else:
                mechanism, grade, reason = "UNRESOLVED", "E5_UNRESOLVED", "V1 result mismatch is not explained by the bounded structural comparison"
                if selected_ids:
                    strength = "MT1_CONTEXT_ASSOCIATION_ONLY"
                    memory_strength[strength] += 1

        if mechanism is not None and failure:
            if grade is None:
                raise RuntimeError(f"missing evidence grade for {case_id}")
            phenotype = case.get("secondary_phenotypes") or [FAMILY_PHENOTYPE[case["primary_diagnostic_family"]]]
            event_ids = [_stable_id(event) for event in case_events]
            record = {
                "case_id": case_id,
                "partition": case["partition"],
                "expected_route": case["expected_route"],
                "actual_route": row.get("actual_path"),
                "primary_causal_mechanism": mechanism,
                "evidence_grade": grade,
                "secondary_phenotypes": phenotype,
                "relevant_provenance_event_ids": event_ids,
                "memory_transfer_strength": strength,
                "selected_memory_ids": selected_ids,
                "transfer_motifs": transfer_motifs,
                "evidence_reason": reason,
            }
            records.append(record)
            primary_counts[mechanism] += 1
            grade_counts[grade] += 1
            partition_grades[case["partition"]][grade] += 1
            for item in phenotype:
                phenotype_counts[item] += 1

    failures = len(records)
    qualifying: dict[str, dict[str, Any]] = {}
    for mechanism in MECHANISMS:
        e1 = sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] == GRADES[0] for item in records)
        e2 = sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] == GRADES[1] for item in records)
        diag_a = sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] in GRADES[:2] and item["partition"] == "DIAG_A" for item in records)
        diag_b = sum(item["primary_causal_mechanism"] == mechanism and item["evidence_grade"] in GRADES[:2] and item["partition"] == "DIAG_B" for item in records)
        e1e2 = e1 + e2
        coherent = mechanism != "UNRESOLVED"
        qualifying[mechanism] = {
            "e1": e1,
            "e2": e2,
            "e1_e2": e1e2,
            "share_of_failures": e1e2 / failures if failures else 0.0,
            "diag_a": diag_a,
            "diag_b": diag_b,
            "gate_at_least_12": e1e2 >= 12,
            "gate_at_least_20_percent": e1e2 >= 12 and e1e2 / failures >= 0.20 if failures else False,
            "gate_both_partitions": diag_a > 0 and diag_b > 0,
            "gate_at_least_5_each_partition": diag_a >= 5 and diag_b >= 5,
            "coherent_boundary_gate": coherent,
            "eligible": e1e2 >= 12 and e1e2 / failures >= 0.20 if failures else False and diag_a >= 5 and diag_b >= 5 and coherent,
        }
        qualifying[mechanism]["eligible"] = (
            qualifying[mechanism]["gate_at_least_12"]
            and qualifying[mechanism]["gate_at_least_20_percent"]
            and qualifying[mechanism]["gate_both_partitions"]
            and qualifying[mechanism]["gate_at_least_5_each_partition"]
            and coherent
        )
    eligible = [key for key, value in qualifying.items() if value["eligible"]]
    ranked = sorted(eligible, key=lambda key: (qualifying[key]["e1_e2"], qualifying[key]["e1"], qualifying[key]["diag_a"] + qualifying[key]["diag_b"]), reverse=True)
    selected = None
    if len(eligible) == 1:
        classification = "M104S_HIGH_LEVERAGE_MECHANISM_IDENTIFIED"
        selected = eligible[0]
        next_milestone = f"M11 mapped to {selected}"
    elif len(eligible) > 1:
        classification = "M104S_MULTIPLE_HIGH_LEVERAGE_MECHANISMS_IDENTIFIED"
        top = qualifying[ranked[0]]["e1_e2"]
        second = qualifying[ranked[1]]["e1_e2"]
        if second and (top - second) / top <= 0.10:
            next_milestone = "M10.5 — High-Leverage Mechanism Disambiguation Audit"
        else:
            selected = ranked[0]
            next_milestone = f"M11 mapped to {selected}"
    else:
        classification = "M104S_FRAGMENTED_CAUSAL_RESIDUALS"
        next_milestone = "M10.5 — High-Value Residual Slice Audit"

    v2_values = {
        "bindable": v2["bindable"],
        "unavailable": v2["unavailable"],
        "protocol_failures": 0,
        "agreements": v2["agreements"],
        "disagreements": v2["disagreements"],
        "v1_wrong_v2_correct": v2["v1_wrong_v2_correct"],
        "v1_correct_v2_wrong": v2["v1_correct_v2_wrong"],
        "score_overwrite": False,
        "denominator_removal": False,
    }
    artifact = {
        "classification": classification,
        "corpus_version": "decisionsql-m104s-provenance-diagnostic-v1",
        "total_cases": len(rows),
        "v1_correct": sum(bool(row.get("v1_correct")) for row in rows.values()),
        "failures": failures,
        "primary_counts": {key: primary_counts.get(key, 0) for key in MECHANISMS},
        "evidence_counts": {key: grade_counts.get(key, 0) for key in GRADES},
        "partition_evidence_counts": {key: {grade: value.get(grade, 0) for grade in GRADES} for key, value in partition_grades.items()},
        "qualifying": qualifying,
        "eligible_mechanisms": eligible,
        "selected_mechanism": selected,
        "next_milestone": next_milestone,
        "route_counts": dict(route_counts),
        "phenotype_counts": dict(phenotype_counts),
        "governed_owned_field_correctness": dict(governed_owned),
        "governed_metric_results": {key: dict(value) for key, value in governed_metric_results.items()},
        "memory": {
            "retrieval_attempts": sum("MEMORY_RETRIEVAL_RESULT" in {event["stage"] for event in events.get(key, [])} for key in rows),
            "memory_use_cases": sum(bool(row.get("memory_used")) for row in rows.values()),
            "no_memory_cases": sum(not bool(row.get("memory_used")) for row in rows.values()),
            "correct_memory_use_cases": correct_memory,
            "incorrect_memory_use_cases": incorrect_memory,
            "selected_compatible_cases": selected_compatible,
            "selected_incompatible_cases": selected_incompatible,
            "compatible_memory_downstream_failures": compatible_memory_failures,
            "transfer_strength_counts": dict(memory_strength),
            "top_k": 3,
            "threshold": None,
            "score_semantics": "existing weighted final similarity",
            "counterfactual_run": False,
        },
        "pipeline": {
            "provider_failures": sum(row.get("primary_status") == "TRANSPORT_FAILURE" for row in rows.values()),
            "parse_failures": sum(row.get("primary_status") == "PARSE_FAILURE" for row in rows.values()),
            "m1_rejections": sum(row.get("primary_status") == "M1_REJECTED" for row in rows.values()),
            "transport_failures": sum(row.get("primary_status") == "TRANSPORT_FAILURE" for row in rows.values()),
            "execution_failures": sum(row.get("primary_status") == "EXECUTION_FAILURE" for row in rows.values()),
            "evaluator_failures": sum(row.get("primary_status") == "EVALUATOR_FAILURE" for row in rows.values()),
        },
        "v2_shadow": v2_values,
        "manual_review": {"performed": False, "records": 0},
        "frozen_taxonomy_used": True,
        "no_gold_runtime": True,
        "no_llm_judge": True,
        "no_memory_counterfactual": True,
    }
    attribution_path = RESULT_ROOT / "automated_causal_attribution.json"
    attribution_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    artifact["attribution_hash"] = file_hash(attribution_path)
    summary_path = RESULT_ROOT / "mechanism_summary.json"
    summary_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    decision_path = RESULT_ROOT / "selection_decision.json"
    decision_path.write_text(json.dumps({"classification": classification, "selected_mechanism": selected, "next_milestone": next_milestone, "gate": qualifying, "m11_selected": selected is not None}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return artifact


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2, sort_keys=True))
