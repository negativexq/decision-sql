# ruff: noqa: E501

"""Offline M10.1 residual audit over immutable M10R artifacts.

This module deliberately does not import the application runtime, database
session, provider adapter, retriever, or evaluator execution path.  It reads
the consumed M10R ledger and benchmark fixtures, performs bounded sqlglot AST
comparisons, and writes only ignored M10.1 result artifacts.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
M10R = ROOT / "evaluation" / "results" / "m10r" / "clean-rebaseline-20260904"
OUT = ROOT / "evaluation" / "results" / "m101" / "residual-audit-20260904"

EXPECTED_INPUT_HASHES = {
    "case_ledger.jsonl": "0eb4d4d640f3759f24da40ad0e242950eedc1a39fb6e039968e9e9c94e346f49",
    "primary_results.json": "e327a6bd3b9272549d5e5cea7fa4edcc836a6841f7627c87b27d96a78c67640d",
    "v2_shadow_results.json": "536a1d77883d754e4161d4a0a0bb648013333b7f4f37a85d33f6eafb1dfb9497",
    "residual_results.json": "38d914526c0debf68da291c9a1a7070c5b4ad3d906ea8bf16f25831b30900e04",
    "m10r_cases_v2.json": "17f6b2791006ddfd4247180e41f32cde766262838c1d5e6e2670f7ed9de2fbbb",
    "m10r_references_v2.json": "32e51f71f2af395f83caa064b29434be5ec43fe883e88f768fb3c4f33300f92f",
    "m10r_result_contracts_v2.json": "41b1b9873bd23a32e6e474e8ddebbed3492e0fe09596aa1c696b51f7551d4af9",
    "m10r_reference_bindings_v2.json": "cb6b0c24e994bbee4f7aed35c81c529da699c5d1d4594cfa495b8879f5d66caf",
    "m10r_freshness_manifest_v2.json": "619c9312d8133bb6d383cbc6fc4b6e2ed7049c7a59b055598c0f3d5bec7d02e5",
}


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_hash(path: Path) -> str:
    digest = sha256(path.read_bytes()).hexdigest()
    return str(digest)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def verify_inputs() -> dict[str, str]:
    actual: dict[str, str] = {}
    for name, expected in EXPECTED_INPUT_HASHES.items():
        path = FIXTURES / name if name.startswith("m10r_") else M10R / name
        actual[name] = file_hash(path)
        if actual[name] != expected:
            raise RuntimeError(f"frozen M10R input hash mismatch: {name}")
    return actual


def _norm_sql(node: Any) -> str:
    if node is None:
        return ""
    return str(node.sql(dialect="postgres", pretty=False).strip().lower())


def _names(nodes: Any) -> list[str]:
    return sorted({_norm_sql(node) for node in nodes})


def _tables(tree: exp.Expression) -> list[str]:
    return sorted({f"{table.db + '.' if table.db else ''}{table.name}".lower() for table in tree.find_all(exp.Table)})


def _joins(tree: exp.Expression) -> list[str]:
    values = []
    for join in tree.find_all(exp.Join):
        target = join.this
        on = join.args.get("on")
        values.append(f"{_norm_sql(target)}|{_norm_sql(on)}")
    return sorted(values)


def _select(tree: exp.Expression) -> exp.Select | None:
    if isinstance(tree, exp.Select):
        return tree
    return tree.find(exp.Select)


def _where_columns(tree: exp.Expression) -> list[str]:
    result = []
    for column in tree.find_all(exp.Column):
        result.append(f"{column.table.lower() + '.' if column.table else ''}{column.name.lower()}")
    return sorted(set(result))


def _where_ops(tree: exp.Expression) -> list[str]:
    predicate_names = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Like,
        exp.ILike,
        exp.In,
        exp.Is,
        exp.Between,
    )
    return sorted(type(node).__name__.upper() for node in tree.find_all(*predicate_names))


def _aggregates(tree: exp.Expression) -> list[str]:
    return sorted(_norm_sql(node) for node in tree.find_all(exp.AggFunc))


def _arithmetic(tree: exp.Expression) -> list[str]:
    kinds = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.Neg)
    return sorted(_norm_sql(node) for node in tree.find_all(*kinds))


def _temporal(tree: exp.Expression) -> list[str]:
    values = []
    for node in tree.walk():
        name = type(node).__name__.upper()
        sql = _norm_sql(node)
        if any(token in name for token in ("DATE", "TIME", "TIMESTAMP", "INTERVAL", "EXTRACT")):
            values.append(sql)
        elif isinstance(node, exp.Literal) and any(token in sql for token in ("-", ":")):
            values.append(sql)
    return sorted(set(values))


def _windows(tree: exp.Expression) -> list[str]:
    return sorted(_norm_sql(node) for node in tree.find_all(exp.Window))


def _set_operations(tree: exp.Expression) -> list[str]:
    return sorted(_norm_sql(node) for node in tree.find_all(exp.Union, exp.Intersect, exp.Except))


def ast_signature(sql: str) -> dict[str, Any]:
    tree = parse_one(sql, read="postgres")
    select = _select(tree)
    projections = [] if select is None else [_norm_sql(item) for item in select.expressions]
    group_node = None if select is None else select.args.get("group")
    order_node = None if select is None else select.args.get("order")
    group = [] if group_node is None else [_norm_sql(item) for item in group_node.expressions]
    order = [] if order_node is None else [_norm_sql(item) for item in order_node.expressions]
    limit = "" if select is None else _norm_sql(select.args.get("limit"))
    offset = "" if select is None else _norm_sql(select.args.get("offset"))
    where = tree.args.get("where") or tree.find(exp.Where)
    distinct = ""
    if select is not None and select.args.get("distinct") is not None:
        distinct = _norm_sql(select.args["distinct"])
    return {
        "tables": _tables(tree),
        "join_graph": _joins(tree),
        "projections": projections,
        "projection_count": len(projections),
        "where": _norm_sql(where),
        "where_columns_operators": {"columns": _where_columns(where or tree), "operators": _where_ops(where or tree)},
        "group_by": sorted(group),
        "aggregates": _aggregates(tree),
        "distinct": distinct,
        "arithmetic": _arithmetic(tree),
        "temporal": _temporal(tree),
        "windows": _windows(tree),
        "order_by": sorted(order),
        "limit": limit,
        "offset": offset,
        "set_operations": _set_operations(tree),
    }


SIGNATURE_FIELDS = (
    "tables", "join_graph", "projections", "projection_count", "where",
    "where_columns_operators", "group_by", "aggregates", "distinct", "arithmetic",
    "temporal", "windows", "order_by", "limit", "offset", "set_operations",
)


def signature_diff(candidate: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    return [field for field in SIGNATURE_FIELDS if candidate.get(field) != reference.get(field)]


def primary_reference(case: dict[str, Any]) -> str:
    variants = case.get("reference_sql_variants", [])
    return variants[0] if variants else ""


def capture_metric(row: dict[str, Any]) -> tuple[str | None, list[str], bool | None]:
    for capture in row.get("provider_captures", []):
        if capture.get("operation") == "metric_grounding":
            parsed = capture.get("parsed_metric_grounding") or {}
            return parsed.get("metric_name"), list(parsed.get("dimensions") or []), parsed.get("applicable")
    return None, [], None


def memory_provenance_present(row: dict[str, Any]) -> bool:
    keys = {"retrieved_example_ids", "retrieval_scores", "memory_sql", "verified_memory_provenance"}
    if keys.intersection(row):
        return True
    return any(keys.intersection(capture) for capture in row.get("provider_captures", []))


def classify_ast(case: dict[str, Any], row: dict[str, Any], candidate_sig: dict[str, Any], reference_sig: dict[str, Any], diff: list[str]) -> tuple[str, str, str, list[str]]:
    if row.get("v2_shadow", {}).get("status") == "V2_EVALUATED" and row.get("v2_shadow", {}).get("equivalent") is True:
        return "UNCLASSIFIED", "E5_UNCLASSIFIED", "V2 shadow recovery is diagnostic only; no generation mechanism is asserted.", ["EVALUATOR_SHADOW_RECOVERY_DIAGNOSTIC"]
    if not diff:
        return "UNCLASSIFIED", "E5_UNCLASSIFIED", "Known result mismatch but no difference in the bounded AST signature.", []
    family = str(case.get("family") or "")
    family_field = {
        "FILTER_PROJECTION": "where",
        "MULTI_TABLE_JOIN": "join_graph",
        "AGGREGATE_GROUP": "aggregates",
        "ORDER_TOPN": "order_by",
        "FORMULA_RATIO": "arithmetic",
        "TEMPORAL": "temporal",
        "WINDOW": "windows",
        "DISTINCT_OR_SET": "distinct",
    }.get(family)
    direct_map = {
        "tables": ("DIRECT_SCHEMA_OR_COLUMN_GROUNDING", "candidate/reference relation or source-column sets differ"),
        "where": ("DIRECT_FILTER_OR_VALUE_CONDITION", "predicate structure differs"),
        "where_columns_operators": ("DIRECT_FILTER_OR_VALUE_CONDITION", "predicate columns/operators differ"),
        "join_graph": ("DIRECT_JOIN_OR_RELATIONAL_COMPOSITION", "join relation or edge differs"),
        "aggregates": ("DIRECT_AGGREGATION_OR_GROUPING", "aggregate signature differs"),
        "group_by": ("DIRECT_AGGREGATION_OR_GROUPING", "grouping grain differs"),
        "arithmetic": ("DIRECT_FORMULA_OR_METRIC_SEMANTICS", "arithmetic/formula tree differs"),
        "temporal": ("DIRECT_TEMPORAL_LOGIC", "temporal signature differs"),
        "windows": ("DIRECT_WINDOW_LOGIC", "window signature differs"),
        "order_by": ("DIRECT_ORDER_OR_LIMIT", "ordering signature differs"),
        "limit": ("DIRECT_ORDER_OR_LIMIT", "limit differs"),
        "offset": ("DIRECT_ORDER_OR_LIMIT", "offset differs"),
        "distinct": ("DIRECT_DISTINCT_OR_SET_SEMANTICS", "distinct/set signature differs"),
        "set_operations": ("DIRECT_DISTINCT_OR_SET_SEMANTICS", "set-operation signature differs"),
        "projections": ("DIRECT_PROJECTION_OR_RESULT_SHAPE", "projection expressions differ"),
        "projection_count": ("DIRECT_PROJECTION_OR_RESULT_SHAPE", "projection count differs"),
    }
    relevant = [field for field in diff if field == family_field or field in {"tables", "where_columns_operators", "projections", "projection_count"}]
    if len(relevant) == 1 and relevant[0] in direct_map:
        mechanism, summary = direct_map[relevant[0]]
        return mechanism, "E2_STRUCTURAL_CORROBORATION", summary, []
    return "COMPOUND_SEMANTIC_FAILURE", "E2_STRUCTURAL_CORROBORATION", f"multiple semantically relevant AST signature fields differ: {','.join(diff)}", []


def run() -> dict[str, Any]:
    input_hashes = verify_inputs()
    taxonomy = load_json(FIXTURES / "m101_residual_taxonomy.json")
    evidence_manifest = load_json(FIXTURES / "m101_evidence_manifest.json")
    audit_protocol = load_json(FIXTURES / "m101_audit_protocol.json")
    taxonomy_hash = stable_hash(taxonomy)
    evidence_manifest_hash = stable_hash(evidence_manifest)
    audit_protocol_hash = stable_hash(audit_protocol)

    cases = {case["case_id"]: case for case in load_json(FIXTURES / "m10r_cases_v2.json")["cases"]}
    references = {case["case_id"]: case for case in load_json(FIXTURES / "m10r_references_v2.json")["cases"]}
    rows = [json.loads(line) for line in (M10R / "case_ledger.jsonl").read_text().splitlines() if line.strip()]
    frozen_residuals = load_json(M10R / "residual_results.json").get("residuals", [])
    frozen_m1_reason = {
        item["case_id"]: item.get("mechanism", "") if item.get("mechanism") == "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL" else item["basis"].split(": ", 1)[-1]
        for item in frozen_residuals
        if item.get("primary_status") == "M1_REJECTED"
    }
    if len(rows) != 200 or set(cases) != {row["case_id"] for row in rows}:
        raise RuntimeError("M10R ledger/corpus population is not exactly 200 cases")

    row_by_id = {row["case_id"]: row for row in rows}
    failures = [row for row in rows if not row.get("v1_correct")]
    controls = [row for row in rows if row.get("v1_correct")]
    if len(failures) != 172 or len(controls) != 28:
        raise RuntimeError("frozen M10R primary population changed")

    governed_audit: list[dict[str, Any]] = []
    confusion: Counter[tuple[str | None, str | None]] = Counter()
    metric_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        case = cases[row["case_id"]]
        if row.get("actual_path") != "GOVERNED_METRIC":
            continue
        selected_metric, selected_dimensions, applicable = capture_metric(row)
        expected_metric = case.get("governed_metric")
        metric_match = expected_metric is not None and selected_metric == expected_metric
        confusion[(expected_metric, selected_metric)] += 1
        metric_rows[str(expected_metric)].append({"case_id": row["case_id"], "correct": bool(row.get("v1_correct")), "selected_metric": selected_metric})
        governed_audit.append({
            "case_id": row["case_id"], "split": row["split"], "expected_route": case["expected_route"],
            "actual_route": row["actual_path"], "family": case["family"], "expected_metric": expected_metric,
            "selected_metric": selected_metric, "expected_dimensions": case.get("dimensions", []),
            "selected_dimensions": selected_dimensions, "applicable": applicable, "metric_match": metric_match,
            "v1_correct": bool(row.get("v1_correct")), "route_metric": row.get("route_metric"),
        })

    memory_rows: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("memory_used"):
            continue
        memory_rows.append({
            "case_id": row["case_id"], "split": row["split"], "family": row["family"],
            "v1_correct": bool(row.get("v1_correct")), "actual_path": row.get("actual_path"),
            "retrieval_provenance_complete": memory_provenance_present(row),
            "transfer_level": "M0_NO_TRANSFER_EVIDENCE",
            "primary_memory_mechanism": None,
            "evidence_grade": "E5_UNCLASSIFIED",
        })
    memory_complete = all(item["retrieval_provenance_complete"] for item in memory_rows)

    audit_rows: list[dict[str, Any]] = []
    ast_rows: list[dict[str, Any]] = []
    mechanism_counts: Counter[str] = Counter()
    grade_counts: Counter[str] = Counter()
    contributor_counts: Counter[str] = Counter()
    m1_counts: Counter[str] = Counter()
    split_counts: dict[str, Counter[str]] = {"dev": Counter(), "holdout": Counter()}
    family_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in failures:
        case = cases[row["case_id"]]
        mechanism = "UNCLASSIFIED"
        grade = "E5_UNCLASSIFIED"
        evidence_summary = ""
        contributors: list[str] = []
        stage = row.get("primary_status") or "UNKNOWN"
        if stage == "M1_REJECTED":
            reason = frozen_m1_reason.get(row["case_id"], "M1_INCOMPATIBLE_GENERATED_STRUCTURE")
            reason = {
                "FORBIDDEN_FUNCTION": "M1_FORBIDDEN_FUNCTION",
                "UNKNOWN_COLUMN": "M1_UNKNOWN_COLUMN",
                "UNKNOWN_TABLE": "M1_UNKNOWN_TABLE",
                "TRANSPORT_PLACEHOLDER_COLLISION": "M1_TRANSPORT_PLACEHOLDER_COLLISION",
                "M1_TRANSPORT_INCOMPATIBLE_GENERATED_SQL": "M1_TRANSPORT_PLACEHOLDER_COLLISION",
            }.get(reason, "M1_INCOMPATIBLE_GENERATED_STRUCTURE")
            mechanism = reason
            grade = "E1_DIRECT_PROVENANCE"
            evidence_summary = "Frozen ledger records M1 rejection; the original M10R residual artifact preserves the stage boundary."
            m1_counts[reason] += 1
        elif case["expected_route"] == "DIRECT" and row.get("actual_path") == "GOVERNED_METRIC":
            mechanism = "ROUTING_OVERREACH"
            grade = "E1_DIRECT_PROVENANCE"
            evidence_summary = "Frozen benchmark metadata is expected-direct while the frozen runtime path is governed; no direct counterfactual is claimed."
            contributors.append("ROUTE_MISMATCH_PRESENT")
        elif row.get("actual_path") == "GOVERNED_METRIC":
            selected_metric, selected_dimensions, _ = capture_metric(row)
            expected_metric = case.get("governed_metric")
            if expected_metric != selected_metric:
                mechanism = "GOVERNED_METRIC_GROUNDING_FAILURE"
                grade = "E1_DIRECT_PROVENANCE"
                evidence_summary = f"Frozen metric grounding selected {selected_metric!r}; expected {expected_metric!r}."
            else:
                mechanism = "GOVERNED_UNCLASSIFIED"
                grade = "E5_UNCLASSIFIED"
                evidence_summary = "Metric identity matches, but frozen M10R artifacts do not retain enough grounding-slot/compiler provenance for a stronger governed cause."
                if case.get("dimensions") != selected_dimensions:
                    contributors.append("GOVERNED_SLOT_DIFFERENCE_RECORDED_BUT_NOT_CAUSALLY_COMPLETE")
        else:
            candidate_sql = row.get("candidate_sql") or ""
            reference_sql = primary_reference(references[row["case_id"]])
            try:
                candidate_sig = ast_signature(candidate_sql)
                reference_sig = ast_signature(reference_sql)
                diff = signature_diff(candidate_sig, reference_sig)
                mechanism, grade, evidence_summary, ast_contributors = classify_ast(case, row, candidate_sig, reference_sig, diff)
                contributors.extend(ast_contributors)
                ast_rows.append({"case_id": row["case_id"], "family": case["family"], "memory_used": bool(row.get("memory_used")), "candidate_signature": candidate_sig, "reference_signature": reference_sig, "different_fields": diff, "mechanism": mechanism, "evidence_grade": grade})
            except Exception as error:
                mechanism = "UNCLASSIFIED"
                grade = "E5_UNCLASSIFIED"
                evidence_summary = f"Bounded AST analysis unavailable: {type(error).__name__}."
        if row.get("memory_used"):
            contributors.append("MEMORY_USED_PROVENANCE_INCOMPLETE")
        if row.get("actual_path") != ("GOVERNED_METRIC" if case["expected_route"] == "GOVERNED" else "DIRECT_SQL"):
            contributors.append("ROUTE_MISMATCH_PRESENT")
        if row.get("v2_shadow", {}).get("status") == "V2_EVALUATED" and row.get("v2_shadow", {}).get("equivalent") is True:
            contributors.append("EVALUATOR_SHADOW_RECOVERY_DIAGNOSTIC")
            contributor_counts["V2_SHADOW_RECOVERY"] += 1
        mechanism_counts[mechanism] += 1
        grade_counts[grade] += 1
        split_counts[row["split"]][mechanism] += 1
        family_counts[case["family"]][mechanism] += 1
        for contributor in set(contributors):
            contributor_counts[contributor] += 1
        audit_rows.append({
            "case_id": row["case_id"], "split": row["split"], "expected_route": case["expected_route"],
            "actual_route": row.get("actual_path"), "family": case["family"], "v1_correct": False,
            "v2_shadow": row.get("v2_shadow"), "primary_stage": stage, "primary_mechanism": mechanism,
            "evidence_grade": grade, "evidence_summary": evidence_summary, "contributors": sorted(set(contributors)),
            "memory_provenance_complete": memory_provenance_present(row),
        })

    controls_ledger = []
    for row in controls:
        case = cases[row["case_id"]]
        selected_metric, selected_dimensions, applicable = capture_metric(row)
        controls_ledger.append({"case_id": row["case_id"], "split": row["split"], "family": case["family"], "expected_route": case["expected_route"], "actual_route": row.get("actual_path"), "memory_used": bool(row.get("memory_used")), "v1_correct": True, "selected_metric": selected_metric, "selected_dimensions": selected_dimensions, "applicable": applicable, "v2_shadow": row.get("v2_shadow")})

    def write(name: str, value: Any) -> str:
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return file_hash(path)

    governed_payload = {"cases": sorted(governed_audit, key=lambda item: item["case_id"]), "metric_selection_accuracy_expected_governed": sum(item["metric_match"] for item in governed_audit if item["expected_route"] == "GOVERNED") / 50}
    governed_hash = write("governed_case_audit.json", governed_payload)
    confusion_payload = [{"expected_metric": expected, "selected_metric": selected, "count": count, "correct": sum(item["case_id"] in row_by_id and row_by_id[item["case_id"]].get("v1_correct") for item in metric_rows.get(str(expected), []) if item["selected_metric"] == selected)} for (expected, selected), count in sorted(confusion.items(), key=str)]
    confusion_hash = write("governed_confusion_matrix.json", confusion_payload)
    memory_hash = write("memory_case_audit.json", {"provenance_complete": memory_complete, "cases": sorted(memory_rows, key=lambda item: item["case_id"])})
    memory_summary = {"memory_use_cases": len(memory_rows), "correct_controls": sum(item["v1_correct"] for item in memory_rows), "incorrect_cases": sum(not item["v1_correct"] for item in memory_rows), "retrieval_provenance_complete": memory_complete, "retrieval_selectivity_failure": 0, "overtransfer": 0, "wrong_motif_carryover": 0, "near_exact_wrong_sql_carryover": 0, "structural_similarity_only": 0, "lexical_only": 0, "no_transfer_evidence": len(memory_rows), "score_diagnostics": "unavailable: frozen M10R artifacts omit retrieval scores"}
    memory_summary_hash = write("memory_transfer_summary.json", memory_summary)
    ast_hash = write("direct_ast_diagnostics.json", sorted(ast_rows, key=lambda item: item["case_id"]))
    failure_hash = write("automated_failure_ledger.json", sorted(audit_rows, key=lambda item: item["case_id"]))
    control_hash = write("control_ledger.json", sorted(controls_ledger, key=lambda item: item["case_id"]))
    mechanism_summary = [{"mechanism": mechanism, "count": count, "share_of_failures": count / 172, "e1": sum(item["evidence_grade"] == "E1_DIRECT_PROVENANCE" for item in audit_rows if item["primary_mechanism"] == mechanism), "e2": sum(item["evidence_grade"] == "E2_STRUCTURAL_CORROBORATION" for item in audit_rows if item["primary_mechanism"] == mechanism), "e3": 0, "e4": 0, "dev": split_counts["dev"][mechanism], "holdout": split_counts["holdout"][mechanism], "expected_governed": sum(item["primary_mechanism"] == mechanism and item["expected_route"] == "GOVERNED" for item in audit_rows), "expected_direct": sum(item["primary_mechanism"] == mechanism and item["expected_route"] == "DIRECT" for item in audit_rows)} for mechanism, count in sorted(mechanism_counts.items(), key=lambda item: (-item[1], item[0]))]
    mechanism_hash = write("mechanism_summary.json", mechanism_summary)
    split_summary = {split: dict(sorted(values.items())) for split, values in split_counts.items()}
    split_hash = write("split_summary.json", split_summary)
    automated_audit_payload = {"taxonomy_hash": taxonomy_hash, "audit_protocol_hash": audit_protocol_hash, "governed_hash": governed_hash, "confusion_hash": confusion_hash, "memory_hash": memory_hash, "memory_summary_hash": memory_summary_hash, "ast_hash": ast_hash, "failure_hash": failure_hash, "control_hash": control_hash, "mechanism_hash": mechanism_hash, "split_hash": split_hash}
    automated_audit_hash = stable_hash(automated_audit_payload)
    eligible = [item for item in mechanism_summary if item["mechanism"] not in {"UNCLASSIFIED", "GOVERNED_UNCLASSIFIED", "COMPOUND_SEMANTIC_FAILURE", "M1_FORBIDDEN_FUNCTION", "M1_UNKNOWN_COLUMN", "M1_UNKNOWN_TABLE", "M1_TRANSPORT_PLACEHOLDER_COLLISION"} and item["e1"] + item["e2"] >= 43]
    final = {"classification": "M101_AUDIT_BLOCKED" if not memory_complete else ("M101_DOMINANT_RESIDUAL_MECHANISM_IDENTIFIED" if len(eligible) == 1 else "M101_MULTIPLE_DOMINANT_MECHANISMS_IDENTIFIED" if len(eligible) > 1 else "M101_FRAGMENTED_RESIDUALS_CONFIRMED"), "input_hashes": input_hashes, "taxonomy_hash": taxonomy_hash, "evidence_manifest_hash": evidence_manifest_hash, "audit_protocol_hash": audit_protocol_hash, "automated_audit_hash": automated_audit_hash, "failure_count": len(failures), "control_count": len(controls), "grade_counts": dict(sorted(grade_counts.items())), "mechanism_counts": dict(mechanism_counts), "contributor_counts": dict(contributor_counts), "m1_counts": dict(m1_counts), "governed_cases_audited": len(governed_audit), "expected_governed_cases_audited": sum(item["expected_route"] == "GOVERNED" for item in governed_audit), "governed_correct": sum(item["v1_correct"] for item in governed_audit), "governed_failures": sum(not item["v1_correct"] for item in governed_audit), "metric_selection_accuracy_expected_governed": governed_payload["metric_selection_accuracy_expected_governed"], "memory_summary": memory_summary, "ast_cases_audited": len(ast_rows), "dominance_threshold": 43, "dominant_candidates": eligible, "no_score_change": True, "provider_calls": 0, "db_calls": 0, "sql_generation_calls": 0, "embedding_calls": 0, "reranker_calls": 0, "judge_calls": 0, "m10r_artifacts_modified": False}
    final_basis = dict(final)
    final["final_summary_hash"] = stable_hash(final_basis)
    write("final_decision.json", final)
    return final


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
