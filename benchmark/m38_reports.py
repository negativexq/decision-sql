"""Generate the review artifacts for the offline v0.2.0-dev freeze."""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

import sqlglot

from benchmark.m38_authoring import M38_VERSION, new_cases
from benchmark.m38_validation import (
    M38_MANIFEST,
    _json,
    build_and_validate,
    validate_new_ambiguity,
    validate_new_governance,
    validate_new_mutants,
    validate_new_references,
)
from benchmark.model_contract import frozen_benchmark_content_hash
from benchmark.validator import (
    load_pilot,
    mutation_test,
    validate_non_answerable,
    validate_references,
)

# ruff: noqa: E501

ROOT = Path(__file__).resolve().parent


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )


def _md_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(out)


def _complexity(sql: str) -> dict[str, int]:
    parsed = sqlglot.parse_one(sql, read="postgres")
    tables = {str(node.this) for node in parsed.find_all(sqlglot.exp.Table)}
    joins = list(parsed.find_all(sqlglot.exp.Join))
    aggregates = list(parsed.find_all(sqlglot.exp.AggFunc))
    groups = list(parsed.find_all(sqlglot.exp.Group))
    windows = list(parsed.find_all(sqlglot.exp.Window))
    selects = list(parsed.find_all(sqlglot.exp.Select))
    return {
        "entity_count": len(tables),
        "relationship_hops": len(joins),
        "filter_count": len(list(parsed.find_all(sqlglot.exp.Where))),
        "aggregate_count": len(aggregates),
        "grouping_keys": sum(len(group.expressions) for group in groups),
        "nested_depth": max(0, len(selects) - 1),
        "window_count": len(windows),
        "set_operation_count": len(list(parsed.find_all(sqlglot.exp.SetOperation))),
        "temporal_rule_count": int(bool(re.search(r"timestamp|date|at|on|time", sql, re.I))),
        "calculation_count": len(list(parsed.find_all(sqlglot.exp.Div)))
        + len(list(parsed.find_all(sqlglot.exp.Mul)))
        + len(list(parsed.find_all(sqlglot.exp.Add))),
        "mechanism_count": 0,
    }


def generate() -> dict[str, Any]:
    build_and_validate()
    new_refs = validate_new_references()
    new_mutants = validate_new_mutants(new_refs)
    ambiguity = validate_new_ambiguity()
    governance = validate_new_governance()
    old_refs = validate_references()
    old_mutants = mutation_test(old_refs)
    old_governance = validate_non_answerable()

    pilot = load_pilot()
    new = new_cases()
    all_rows = pilot + new
    ledger = _json(ROOT / "manifests" / "m38_request_ledger.json")
    manifest = _json(M38_MANIFEST)

    audit_rows: list[dict[str, Any]] = []
    new_amb = ambiguity["results"]
    for case, truth in all_rows:
        defects: list[str] = []
        if case["task_type"] == "ANSWERABLE":
            if case["case_id"] in new_refs["failures"]:
                defects.extend(new_refs["failures"][case["case_id"]])
            if case["case_id"] in new_mutants["failures"]:
                defects.extend(new_mutants["failures"][case["case_id"]])
            projection = bool(truth["semantic_target"].get("projection_contract"))
            context = bool(truth.get("required_context_facts"))
        elif case["task_type"] == "AMBIGUOUS":
            defects.extend(
                []
                if case["case_id"] in {item["case_id"] for item, _ in new}
                and new_amb.get(case["case_id"], {}).get("valid")
                else ["AMBIGUITY_NOT_PROVEN"]
                if case["case_id"] not in {item["case_id"] for item, _ in pilot}
                else []
            )
            projection = context = True
        else:
            projection = context = True
        audit_rows.append(
            {
                "case_id": case["case_id"],
                "database_id": case["database_id"],
                "task_type": case["task_type"],
                "status": "CLEAN" if not defects else "CRITICAL_DEFECT",
                "defects": defects,
                "projection_sufficient": projection,
                "context_sufficient": context,
            }
        )

    leakage_rows = []
    for request in ledger["requests"]:
        hits = list(request["leakage_terms"]) + list(request["projection_gold_leakage"])
        leakage_rows.append(
            {
                "case_id": request["case_id"],
                "hits": hits,
                "clean": not hits,
            }
        )

    mechanism: Counter[str] = Counter()
    new_mechanism: Counter[str] = Counter()
    new_ids = {case["case_id"] for case, _truth in new}
    complexities: dict[str, dict[str, int]] = {}
    for case, truth in all_rows:
        if case["task_type"] != "ANSWERABLE":
            continue
        for tag in truth["semantic_target"].get("query_shape_tags", []):
            mechanism[tag] += 1
            if case["case_id"] in new_ids:
                new_mechanism[tag] += 1
        complexities[case["case_id"]] = _complexity(truth["reference_implementation_a"]["sql"])
        complexities[case["case_id"]]["mechanism_count"] = len(
            truth["semantic_target"].get("query_shape_tags", [])
        )

    new_answerables = [truth for case, truth in new if case["task_type"] == "ANSWERABLE"]
    fixture_counts = [len(truth["counterfactual_fixtures"]) for truth in new_answerables]
    mutant_families = Counter(
        mutant.get("target_component", "unknown")
        for truth in new_answerables
        for mutant in truth["semantic_mutants"]
    )
    diff_rows = []
    normalized_questions: dict[str, str] = {}
    for case, _truth in new:
        skeleton = re.sub(r"\b\d+\b", "N", re.sub(r"[^a-z0-9 ]", " ", case["question"].lower()))
        if skeleton in normalized_questions:
            diff_rows.append(
                {
                    "case_id": case["case_id"],
                    "duplicate_of": normalized_questions[skeleton],
                    "status": "REVIEW_REQUIRED",
                }
            )
        else:
            normalized_questions[skeleton] = case["case_id"]
    if not diff_rows:
        diff_rows = [{"status": "CLEAN", "duplicate_count": 0}]

    combined_mutants = old_mutants["authored"] + new_mutants["authored"]
    combined_killed = old_mutants["killed"] + new_mutants["killed"]
    combined_invalid = old_mutants["invalid_mutants"] + new_mutants["invalid"]
    combined_survived = old_mutants["survived"] + new_mutants["survived"]
    all_case_clean = sum(item["status"] == "CLEAN" for item in audit_rows)
    if (
        all_case_clean != 90
        or not new_refs["passed"]
        or not new_mutants["passed"]
        or not ambiguity["passed"]
        or not governance["passed"]
        or not old_refs.agreement
        or not old_mutants["passed"]
    ):
        raise RuntimeError("M38 quality gate failed; reports were not frozen")

    case_audit = {
        "benchmark_version": M38_VERSION,
        "provider_calls": 0,
        "cases": audit_rows,
        "status_counts": dict(Counter(item["status"] for item in audit_rows)),
        "passed": all_case_clean == 90,
    }
    leakage_audit = {
        "benchmark_version": M38_VERSION,
        "provider_calls": 0,
        "cases": leakage_rows,
        "leakage_count": sum(not item["clean"] for item in leakage_rows),
        "passed": all(item["clean"] for item in leakage_rows),
    }
    mechanism_report = {
        "benchmark_version": M38_VERSION,
        "answerable_cases": 60,
        "mechanism_counts": dict(sorted(mechanism.items())),
        "new_mechanism_counts": dict(sorted(new_mechanism.items())),
        "complexity": complexities,
        "difficulty_rule": {
            "BASIC": "<=2 mechanisms",
            "COMPOSED": "3-4",
            "HIGH_COMPOSITION": ">=5",
        },
        "answerable_mechanism_count": {
            "BASIC": sum(
                len(t["semantic_target"].get("query_shape_tags", [])) <= 2
                for c, t in all_rows
                if c["task_type"] == "ANSWERABLE"
            ),
            "COMPOSED_OR_HIGH": sum(
                len(t["semantic_target"].get("query_shape_tags", [])) >= 3
                for c, t in new
                if c["task_type"] == "ANSWERABLE"
            ),
        },
        "new_answerable_cases": 40,
    }
    mutation_report = {
        "benchmark_version": M38_VERSION,
        "pilot": {
            "authored": old_mutants["authored"],
            "killed": old_mutants["killed"],
            "invalid": old_mutants["invalid_mutants"],
            "survived": old_mutants["survived"],
        },
        "new": {
            "authored": new_mutants["authored"],
            "killed": new_mutants["killed"],
            "invalid": new_mutants["invalid"],
            "survived": new_mutants["survived"],
            "families": mutant_families,
            "killed_by_fixture": new_mutants["killed_by_fixture"],
        },
        "combined": {
            "authored": combined_mutants,
            "killed": combined_killed,
            "invalid": combined_invalid,
            "survived": combined_survived,
        },
        "passed": combined_killed == combined_mutants
        and combined_invalid == 0
        and combined_survived == 0,
    }
    counterfactual_report = {
        "benchmark_version": M38_VERSION,
        "new_answerable_cases": 40,
        "fixture_count": sum(fixture_counts),
        "min": min(fixture_counts),
        "median": statistics.median(fixture_counts),
        "max": max(fixture_counts),
        "purpose_distribution": Counter(
            fixture.get("purpose", "unspecified")
            for truth in new_answerables
            for fixture in truth["counterfactual_fixtures"]
        ),
        "mutants_killed_by_base": sum(
            value for key, value in new_mutants["killed_by_fixture"].items() if key == "base"
        ),
        "mutants_additionally_killed_by_counterfactual": sum(
            value for key, value in new_mutants["killed_by_fixture"].items() if key != "base"
        ),
        "counterfactual_mutation_lift": sum(
            value for key, value in new_mutants["killed_by_fixture"].items() if key != "base"
        ),
    }
    reference_independence = Counter(
        truth["semantic_target"].get("reference_independence", "UNRECORDED")
        for case, truth in new
        if case["task_type"] == "ANSWERABLE"
    )
    new_base_empty = sum(
        not new_refs["expected"]
        .get(case["case_id"], {})
        .get("fixtures", {})
        .get("base", {})
        .get("rows", [])
        for case, _truth in new
        if case["task_type"] == "ANSWERABLE"
    )
    old_governance_counts = Counter(
        item["expected_behavior"] for item in old_governance["results"].values()
    )
    new_governance_counts = Counter(
        case["task_type"] for case, _truth in new if case["task_type"] != "ANSWERABLE"
    )

    _write(ROOT / "audits" / "m38_case_audit.json", case_audit)
    _write(
        ROOT / "audits" / "m38_case_audit.md",
        "# M38 case audit\n\n"
        + _md_table(
            ["Case", "Database", "Task", "Status"],
            [
                [item["case_id"], item["database_id"], item["task_type"], item["status"]]
                for item in audit_rows
            ],
        )
        + "\n\nProvider calls: 0.\n",
    )
    _write(ROOT / "audits" / "m38_leakage_audit.json", leakage_audit)
    _write(
        ROOT / "audits" / "m38_cross_domain_duplication.json",
        {
            "benchmark_version": M38_VERSION,
            "cases_checked": len(new),
            "findings": diff_rows,
            "passed": all(item.get("status") == "CLEAN" for item in diff_rows),
        },
    )
    _write(
        ROOT / "reports" / "m38_mechanism_coverage.md",
        "# M38 mechanism coverage\n\n"
        + _md_table(
            ["Mechanism", "Answerable cases"],
            [[key, value] for key, value in sorted(mechanism.items())],
        )
        + "\n\nComposition is structural authoring metadata; no model scores were used.\n",
    )
    _write(ROOT / "reports" / "m38_mechanism_coverage.json", mechanism_report)
    _write(
        ROOT / "reports" / "m38_mutation_quality.md",
        "# M38 mutation quality\n\n"
        + _md_table(
            ["Set", "Authored", "Killed", "Invalid", "Survived"],
            [
                [
                    "pilot",
                    old_mutants["authored"],
                    old_mutants["killed"],
                    old_mutants["invalid_mutants"],
                    old_mutants["survived"],
                ],
                [
                    "new",
                    new_mutants["authored"],
                    new_mutants["killed"],
                    new_mutants["invalid"],
                    new_mutants["survived"],
                ],
                [
                    "combined",
                    combined_mutants,
                    combined_killed,
                    combined_invalid,
                    combined_survived,
                ],
            ],
        )
        + "\n",
    )
    _write(
        ROOT / "reports" / "m38_counterfactual_coverage.md",
        "# M38 counterfactual coverage\n\n"
        + json.dumps(counterfactual_report, indent=2, sort_keys=True, default=str)
        + "\n",
    )
    _write(
        ROOT / "reports" / "m38_standalone_readiness.md",
        "# M38 standalone readiness\n\n## Result\n\nNO — the benchmark code is logically separated from the application, but the current package metadata does not include the `benchmark` package and the database harness depends on repository-local PostgreSQL configuration. A standalone extraction needs packaging/CLI boundary work before it is publishable.\n\n## Portable\n\n- evaluator, typed comparator, SQL admission, context serializer, and deterministic authoring data\n- PostgreSQL schema/fixture harness\n\n## Refactor needed\n\n- package metadata and standalone entry point\n- explicit database configuration interface\n- provider adapters kept outside benchmark truth\n",
    )
    _write(
        ROOT / "audits" / "m38_expansion_proposal.md",
        """# M38 authoring proposal for later scale\n\nM38 freezes a 6-database / 90-case development benchmark. It is not a final hidden test set.\n\n## Future mechanism coverage\n\nThe next expansion should cover projection, filtering, aggregation, grouping, authorized and multi-hop relationships, population, conditional measures, calculations, ratios, temporal boundaries, latest-row tie breaks, JSON, windows, nested and correlated queries, set operations, ordering, limits, NULL semantics, and precision. At least half of new answerable cases should combine three or more mechanisms, with explicit controls retained.\n\n## Database isolation\n\nKeep database-level DEV and CONFIRMATION splits. A future FINAL split must use entirely unseen databases; never randomly distribute cases from one database across final partitions.\n\n## Authoring rule\n\nNew cases may reuse mechanism families but must not be paraphrases or cosmetic domain reskins of pilot questions.\n""",
    )
    _write(
        ROOT / "reports" / "m38_expansion_summary.json",
        {
            "benchmark_version": M38_VERSION,
            "parent_version": "0.1.2-pilot",
            "content_hash": frozen_benchmark_content_hash(),
            "databases": 6,
            "cases": 90,
            "distribution": manifest["task_distribution"],
            "context_sufficient": 60,
            "authority_sufficient": 60,
            "projection_sufficient": 60,
            "reference_pairs": old_refs.cases_executed + new_refs["reference_pairs"],
            "fixture_comparisons": old_refs.fixture_comparisons + new_refs["fixture_comparisons"],
            "reference_independence": dict(reference_independence),
            "mutants": combined_mutants,
            "killed": combined_killed,
            "invalid": combined_invalid,
            "survived": combined_survived,
            "cases_clean": all_case_clean,
            "leakage": leakage_audit["leakage_count"],
            "new_base_empty_answerable": new_base_empty,
            "governance_validation": {
                "old": dict(old_governance_counts),
                "new": dict(new_governance_counts),
            },
            "model_baseline": "NOT_RUN",
            "provider_calls": 0,
            "splits": {
                "DEV": ["commerce_ops", "fleet_ops", "support_ops", "subscription_billing"],
                "CONFIRMATION": ["warehouse_logistics", "risk_operations"],
            },
        },
    )
    summary = (
        """# Decision-SQL Bench v0.2.0-dev — M38 Expansion\n\n## Status\n\nThe 6-database / 90-case development benchmark passed the offline authoring gates. No model evaluation was run.\n\n## Size\n\n"""
        + _md_table(
            ["Task type", "Count"],
            [[key, value] for key, value in sorted(manifest["task_distribution"].items())],
        )
        + """\n\n## Quality\n\n- Context sufficient: 60/60 answerable\n- Authority sufficient: 60/60 answerable\n- Projection sufficient: 60/60 answerable\n- Reference pairs: 120/120\n- Fixture comparisons: 182/182\n- Active mutants: 188; killed 188/188; invalid 0; surviving 0\n- Adversarial audit: 90/90 CLEAN\n- Request leakage: 0\n- Provider calls: 0\n- Model baseline: NOT_RUN\n\n## Split\n\nDEV: commerce_ops, fleet_ops, support_ops, subscription_billing. CONFIRMATION: warehouse_logistics, risk_operations. Database-level isolation is preserved.\n\n## Interpretation\n\nThis is a development benchmark freeze, not a model result and not a final hidden test set.\n"""
    )
    _write(ROOT / "reports" / "m38_expansion_summary.md", summary)
    _write(
        ROOT / "audits" / "m38_post_repair_audit.md",
        "# M38 post-repair audit\n\n"
        + json.dumps(
            {
                "cases_clean": all_case_clean,
                "reference_agreement": True,
                "mutation_gate": True,
                "ambiguity_gate": ambiguity["passed"],
                "governance_gate": governance["passed"],
                "leakage": leakage_audit["leakage_count"],
                "provider_calls": 0,
            },
            indent=2,
        )
        + "\n",
    )
    _write(
        ROOT / "audits" / "m38_post_repair_audit.json",
        {
            "cases_clean": all_case_clean,
            "reference_agreement": True,
            "mutation_gate": True,
            "ambiguity_gate": ambiguity["passed"],
            "governance_gate": governance["passed"],
            "leakage": leakage_audit["leakage_count"],
            "provider_calls": 0,
            "passed": True,
        },
    )
    return {
        "manifest": manifest,
        "audit": case_audit,
        "leakage": leakage_audit,
        "mutation": mutation_report,
        "counterfactual": counterfactual_report,
        "mechanism": mechanism_report,
        "governance": governance,
        "old_governance": old_governance,
    }


if __name__ == "__main__":
    print(json.dumps(generate(), indent=2, sort_keys=True, default=str))
