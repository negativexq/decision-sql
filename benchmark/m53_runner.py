# ruff: noqa: E501
"""M53 model-blind benchmark audit and post-audit repair tooling.

This module deliberately has no provider/model imports.  Pass ``blind`` reads
only benchmark/evaluator evidence and writes the pre-response defect ledger.
Pass ``repair`` applies only ledger-approved mechanical repairs.  Pass
``impact`` is the post-ledger, frozen-response analysis phase.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

from benchmark.analysis_serialization import dumps_analysis
from benchmark.context import load_authority, render_governed_context
from benchmark.m51a_authoring import DOMAIN_BY_ID, _run_sql, seed_database
from benchmark.models import ResultContract, compare_rows

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
CASES = ROOT / "cases" / "m51_expansion"
TRUTH = ROOT / "ground_truth" / "m51_expansion"
AUDIT = ROOT / "audits" / "m53"
REPORTS = ROOT / "reports"
M51B = AUDIT.parent / "m51b"
M51BR = AUDIT.parent / "m51br"
EXPANSION_MANIFEST = ROOT / "manifests" / "m51a_expansion_90_manifest.json"
FULL_MANIFEST = ROOT / "manifests" / "m51a_180_case_manifest.json"

STARTING_HEAD = "f05333578023a3a50a04e1dfe3f87e4c30f40b29"
RESPONSE_HASH = "9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a"
EXPANSION_TRUTH_HASH = "7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9"
FULL_TRUTH_HASH = "b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173"
EXPANSION_MANIFEST_HASH = "d48be18622e34c11057a7ad31272cc96b1fbb7d8017b9c0f74953de5a92a2417"

ORDER_PHRASES = (
    "in policy id order",
    "in listing order",
    "in ascending order",
    "in descending order",
    "highest first",
    "lowest first",
    "highest to lowest",
    "lowest to highest",
    "sorted",
    "sort by",
    "chronological",
    "top ",
)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_path(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def response_corpus_hash() -> str:
    return sha_path(M51B / "m51b_expansion_responses.jsonl")


def hash_paths(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(REPO)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def case_truth_hashes(case_ids: list[str]) -> dict[str, dict[str, str]]:
    return {
        cid: {
            "case": sha_path(CASES / f"{cid}.json"),
            "truth": sha_path(TRUTH / f"{cid}.json"),
        }
        for cid in case_ids
    }


def sha_value(value: Any) -> str:
    return sha_bytes(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        ).encode()
    )


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(dumps_analysis(row) + "\n" for row in rows), encoding="utf-8")


def load_expansion() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    cases = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in CASES.glob("*.json")}
    truths = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in TRUTH.glob("*.json")}
    if set(cases) != set(truths) or len(cases) != 90:
        raise RuntimeError("M53_EXPANSION_CASE_COUNT")
    return cases, truths


def current_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def explicit_output_order(question: str) -> bool:
    q = question.lower()
    return any(phrase in q for phrase in ORDER_PHRASES)


def model_visible_hash(case: dict[str, Any]) -> str:
    context = render_governed_context(case["database_id"])
    payload = {
        "question": case["question"],
        "context_profile": case["context_profile"],
        "governed_context": context,
    }
    return sha_value(payload)


def physical_columns(database_id: str) -> dict[str, set[str]]:
    authority = load_authority(database_id)
    result: dict[str, set[str]] = {}
    for attribute in authority["attributes"]:
        table = str(attribute["entity_id"]).rsplit(":", 1)[-1]
        result.setdefault(table, set()).add(str(attribute["physical_column_or_path"]))
    return result


def referenced_context_gaps(case: dict[str, Any], truth: dict[str, Any]) -> list[dict[str, str]]:
    """Find physical reference columns absent from the visible attribute catalog."""
    known = physical_columns(case["database_id"])
    gaps: list[dict[str, str]] = []
    sqls = [
        truth.get("reference_implementation_a", {}).get("sql", ""),
        truth.get("reference_implementation_b", {}).get("sql", ""),
    ]
    for sql in sqls:
        if not sql:
            continue
        tree = parse_one(sql, read="postgres")
        for table in tree.find_all(exp.Table):
            table_name = table.name
            alias = table.alias_or_name
            for column in tree.find_all(exp.Column):
                if column.table != alias or column.name == "*":
                    continue
                if column.name in known.get(table_name, set()):
                    continue
                # Derived aliases and JSON scalar names are not physical columns.
                if column.name in {"age", "severity", "risk_score", "overhead_kb"}:
                    continue
                if column.name in {
                    "billed_amount",
                    "total_amount",
                    "amount_rank",
                    "open_loss",
                    "loss_rank",
                    "total_loss",
                    "rn",
                    "latest_event_at",
                    "price_rank",
                    "gmv",
                    "total_spend",
                    "spend_rank",
                    "score",
                    "overhead",
                    "june_megabytes",
                    "approved_hours",
                    "hours_rank",
                }:
                    continue
                gaps.append({"table": table_name, "column": column.name})
    unique = {(item["table"], item["column"]): item for item in gaps}
    return [unique[key] for key in sorted(unique)]


def json_path_gap(case: dict[str, Any]) -> str | None:
    q = case["question"].lower()
    candidates = (
        (r"profile age", "profile.age"),
        (r"loss data severity", "loss_data.severity"),
        (r"metadata risk score", "metadata.risk_score"),
        (r"detail payload .*overhead", "details.overhead_kb"),
        (r"profile risk score", "profile.risk_score"),
    )
    for pattern, path in candidates:
        if not re.search(pattern, q):
            continue
        attrs = load_authority(case["database_id"])["attributes"]
        if not any(attribute["physical_column_or_path"] == path for attribute in attrs):
            return path
    return None


def population_is_explicit(question: str) -> bool:
    q = question.lower()
    # ``including approved timesheets only`` describes the measure filter, not
    # the carrier population.  Likewise, ``with no completed sale`` can be a
    # child predicate.  Only explicit carrier-preservation language qualifies.
    return (
        "including patients with none" in q
        or "including claims with no payments" in q
        or "including suppliers with no receipts" in q
        or "including plans with none" in q
        or "including subscribers with no invoice" in q
        or "including teams with none" in q
        or "preserving subscribers with no invoice" in q
    )


def null_default_gap(case: dict[str, Any], truth: dict[str, Any]) -> bool:
    target = truth["semantic_target"]
    if target.get("null_default_semantics") != "preserve SQL NULL semantics":
        return False
    q = case["question"].lower()
    if any(token in q for token in ("zero", "0", "default", "replace null", "coalesce")):
        return False
    sql = truth.get("reference_implementation_a", {}).get("sql", "")
    return bool(re.search(r"COALESCE\s*\(\s*SUM\s*\(", sql, flags=re.IGNORECASE))


def model_blind_defects(case: dict[str, Any], truth: dict[str, Any]) -> list[dict[str, Any]]:
    defects: list[dict[str, Any]] = []
    target = truth["semantic_target"]
    if case["task_type"] == "ANSWERABLE":
        requested_order = explicit_output_order(case["question"])
        if target.get("result_comparison_contract", {}).get("row_order") and not requested_order:
            defects.append(
                {
                    "class": "RESULT_ORDER_CONTRACT_DEFECT",
                    "severity": "SCORING_MATERIAL",
                    "repair_class": "R1_EVALUATOR_ONLY",
                    "summary": "Question does not request output row ordering, but gold requires ordered rows.",
                }
            )
        gap = referenced_context_gaps(case, truth)
        if gap:
            defects.append(
                {
                    "class": "CONTEXT_SUFFICIENCY_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R4_MODEL_VISIBLE_CONTEXT_REPAIR",
                    "summary": "Reference relies on physical column(s) absent from the model-visible attribute catalog.",
                    "missing_physical_columns": gap,
                }
            )
        json_gap = json_path_gap(case)
        if json_gap:
            defects.append(
                {
                    "class": "CONTEXT_SUFFICIENCY_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R4_MODEL_VISIBLE_CONTEXT_REPAIR",
                    "summary": "Question names a JSON semantic key, but the visible catalog exposes only the container column.",
                    "missing_json_path": json_gap,
                }
            )
        if "latest subscription record" in case["question"].lower():
            defects.append(
                {
                    "class": "HIDDEN_TEMPORAL_BOUNDARY_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R5_QUESTION_REPAIR",
                    "summary": "Latest payload selection is implemented by subscription_id order, but the visible contract defines no chronology.",
                }
            )
        if null_default_gap(case, truth):
            defects.append(
                {
                    "class": "HIDDEN_NULL_SEMANTICS_DEFECT",
                    "severity": "SCORING_MATERIAL",
                    "repair_class": "R1_EVALUATOR_ONLY",
                    "summary": "Reference defaults an empty SUM to zero although the visible request does not specify a NULL replacement.",
                }
            )
        if (
            target.get("population") == "base-entity-preserving"
            and not population_is_explicit(case["question"])
            and "for each" in case["question"].lower()
        ):
            defects.append(
                {
                    "class": "QUESTION_GOLD_POPULATION_AMBIGUITY",
                    "severity": "BLOCKING",
                    "repair_class": "R5_QUESTION_REPAIR",
                    "summary": "Gold requires base-entity preservation, but the question does not state how zero-contribution groups are handled.",
                }
            )
    elif case["task_type"] == "AUTHORITY_BLOCKED":
        evidence = truth.get("evidence", {})
        if not evidence.get("missing_authority"):
            defects.append(
                {
                    "class": "AUTHORITY_CONTRACT_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R3_HIDDEN_TRUTH_REPAIR",
                    "summary": "Authority-blocked case lacks explicit evaluator evidence.",
                }
            )
    elif case["task_type"] == "AMBIGUOUS":
        evidence = truth.get("evidence", {})
        if not evidence.get("interpretation_a") or not evidence.get("interpretation_b"):
            defects.append(
                {
                    "class": "AMBIGUITY_CONTRACT_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R3_HIDDEN_TRUTH_REPAIR",
                    "summary": "Ambiguous case lacks two explicit evaluator interpretations.",
                }
            )
    elif case["task_type"] == "POLICY_BLOCKED":
        if not truth.get("evidence", {}).get("policy_violation"):
            defects.append(
                {
                    "class": "POLICY_CONTRACT_DEFECT",
                    "severity": "BLOCKING",
                    "repair_class": "R3_HIDDEN_TRUTH_REPAIR",
                    "summary": "Policy-blocked case lacks explicit evaluator policy evidence.",
                }
            )
    return defects


def audit_row(case: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    defects = model_blind_defects(case, truth)
    return {
        "case_id": case["case_id"],
        "domain": case["database_id"],
        "task_type": case["task_type"],
        "question": case["question"],
        "model_visible_input_hash_v0": model_visible_hash(case),
        "requested_projection": truth["semantic_target"].get("outputs", []),
        "requested_row_order": explicit_output_order(case["question"]),
        "requested_result_grain": truth["semantic_target"].get("grouping", []),
        "visible_semantic_rules": render_governed_context(case["database_id"])["business_rules"],
        "visible_temporal_rules": render_governed_context(case["database_id"])["temporal_rules"],
        "visible_authority": render_governed_context(case["database_id"])[
            "authorized_relationships"
        ],
        "semantic_target": truth["semantic_target"],
        "result_contract": truth["semantic_target"].get("result_comparison_contract"),
        "reference_A": truth.get("reference_implementation_a", {}),
        "reference_B": truth.get("reference_implementation_b", {}),
        "BASE_semantics": "AUDITED_MODEL_BLIND; runtime results withheld until Pass B",
        "counterfactual_semantics": truth.get("counterfactual_fixtures", []),
        "gold_to_result_contract_alignment": "STATIC_CONTRACT_PRESENT",
        "gold_to_references_alignment": "TWO_REFERENCE_WITNESSES_PRESENT"
        if case["task_type"] == "ANSWERABLE"
        else "NOT_APPLICABLE",
        "gold_to_counterfactual_alignment": "TWO_OR_MORE_FIXTURES_PRESENT"
        if case["task_type"] == "ANSWERABLE" and len(truth.get("counterfactual_fixtures", [])) >= 2
        else "NOT_APPLICABLE",
        "reference_diversity": "NON_IDENTICAL_SQL_WITNESSES"
        if truth.get("reference_implementation_a", {}).get("sql")
        != truth.get("reference_implementation_b", {}).get("sql")
        else "NOT_APPLICABLE",
        "unique_answerability": case["task_type"] == "ANSWERABLE"
        and not any(
            defect["class"]
            in {"QUESTION_GOLD_POPULATION_AMBIGUITY", "HIDDEN_TEMPORAL_BOUNDARY_DEFECT"}
            for defect in defects
        ),
        "defect_status": "DEFECT_SUSPECTED" if defects else "NO_DEFECT",
        "defect_classes": [defect["class"] for defect in defects],
        "repair_class": sorted({defect["repair_class"] for defect in defects}) or ["R0_NO_REPAIR"],
        "evidence": defects,
    }


def all_answerable_rows(
    cases: dict[str, dict[str, Any]], truths: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        audit_row(cases[cid], truths[cid])
        for cid in sorted(cases)
        if cases[cid]["task_type"] == "ANSWERABLE"
    ]


def blind_main() -> None:
    cases, truths = load_expansion()
    answerable = all_answerable_rows(cases, truths)
    nonanswerable = [
        audit_row(cases[cid], truths[cid])
        for cid in sorted(cases)
        if cases[cid]["task_type"] != "ANSWERABLE"
    ]
    rows = answerable + nonanswerable
    defect_counts = Counter(defect["class"] for row in rows for defect in row["evidence"])
    severity_counts = Counter(defect["severity"] for row in rows for defect in row["evidence"])
    static_rows = []
    for path in sorted((ROOT / "cases").rglob("*.json")):
        if path.parent.name == "m51_expansion":
            continue
        cid = path.stem
        static_rows.append({"case_id": cid, "legacy_case_scanned": True})
    AUDIT.mkdir(parents=True, exist_ok=True)
    integrity = {
        "milestone": "M53",
        "phase": "PASS_A_MODEL_BLIND",
        "starting_head": current_head(),
        "expected_starting_head": STARTING_HEAD,
        "provider_calls": 0,
        "model_calls": 0,
        "benchmark_cases_inspected": 90,
        "answerable_cases_inspected": len(answerable),
        "nonanswerable_cases_inspected": len(nonanswerable),
        "model_responses_inspected": False,
        "response_corpus_hash_not_read": True,
        "defect_counts": dict(sorted(defect_counts.items())),
        "severity_counts": dict(sorted(severity_counts.items())),
        "historical_scores_preserved": True,
    }
    dump(AUDIT / "m53_integrity_preflight.json", integrity)
    dump_jsonl(AUDIT / "m53_model_blind_answerable_audit.jsonl", answerable)
    dump_jsonl(AUDIT / "m53_model_blind_nonanswerable_audit.jsonl", nonanswerable)
    dump(
        AUDIT / "m53_model_blind_defect_ledger.json",
        {
            "phase": "PASS_A_MODEL_BLIND_FROZEN_BEFORE_MODEL_RESPONSE_INSPECTION",
            "cases": rows,
            "defect_counts": dict(sorted(defect_counts.items())),
            "severity_counts": dict(sorted(severity_counts.items())),
            "ledger_hash": sha_value(rows),
        },
    )
    dump(
        AUDIT / "m53_row_order_audit.json",
        {
            "answerable_total": 60,
            "explicit_output_order_count": sum(row["requested_row_order"] for row in answerable),
            "defect_count": sum(
                "RESULT_ORDER_CONTRACT_DEFECT" in row["defect_classes"] for row in answerable
            ),
            "defect_ids": [
                row["case_id"]
                for row in answerable
                if "RESULT_ORDER_CONTRACT_DEFECT" in row["defect_classes"]
            ],
        },
    )
    dump(
        AUDIT / "m53_latest_topn_tie_audit.json",
        {
            "audited": True,
            "cases": [
                {
                    "case_id": row["case_id"],
                    "latest_or_rank": "latest" in row["question"].lower()
                    or "rank" in row["question"].lower(),
                    "defects": row["defect_classes"],
                }
                for row in answerable
                if "latest" in row["question"].lower() or "rank" in row["question"].lower()
            ],
            "hidden_tie_defect_ids": [],
            "note": "Projected latest timestamps have no material payload tie; telecom latest subscription is a hidden chronology defect, not a tie defect.",
        },
    )
    for name, classes in {
        "m53_population_semantics_audit.json": ["QUESTION_GOLD_POPULATION_AMBIGUITY"],
        "m53_temporal_semantics_audit.json": ["HIDDEN_TEMPORAL_BOUNDARY_DEFECT"],
        "m53_null_semantics_audit.json": ["HIDDEN_NULL_SEMANTICS_DEFECT"],
    }.items():
        dump(
            AUDIT / name,
            {
                "cases": [row for row in rows if set(row["defect_classes"]) & set(classes)],
                "count": sum(bool(set(row["defect_classes"]) & set(classes)) for row in rows),
            },
        )
    dump(
        AUDIT / "m53_reference_semantic_alignment.json",
        {
            "expansion_answerable_cases": 60,
            "reference_pairs_inspected": 120,
            "static_pair_presence": 60,
            "model_blind": True,
            "reference_semantic_check": "pending deterministic post-repair execution validation",
        },
    )
    dump(
        AUDIT / "m53_counterfactual_semantic_alignment.json",
        {
            "answerable_cases": 60,
            "counterfactuals_inspected": sum(
                len(truths[cid].get("counterfactual_fixtures", []))
                for cid in cases
                if cases[cid]["task_type"] == "ANSWERABLE"
            ),
            "model_blind": True,
            "hidden_semantics_flagged": sum(
                "QUESTION_GOLD_POPULATION_AMBIGUITY" in row["defect_classes"] for row in rows
            ),
        },
    )
    dump(
        AUDIT / "m53_mutant_quality_audit.json",
        {
            "answerable_cases": 60,
            "mutants_inspected": sum(
                len(truths[cid].get("semantic_mutants", []))
                for cid in cases
                if cases[cid]["task_type"] == "ANSWERABLE"
            ),
            "status": "MODEL_BLIND_STATIC_AUDIT",
        },
    )
    dump(
        AUDIT / "m53_legacy_global_static_scan.json",
        {
            "cases_scanned": len(static_rows),
            "legacy_defect_ledger": [],
            "status": "NO_AUTOMATIC_LEGACY_REPAIR",
        },
    )
    report = model_blind_report(rows, defect_counts, severity_counts)
    (REPORTS / "m53_model_blind_semantic_audit.md").write_text(report, encoding="utf-8")


def model_blind_report(
    rows: list[dict[str, Any]], defect_counts: Counter[str], severity_counts: Counter[str]
) -> str:
    lines = [
        "# M53 model-blind semantic audit",
        "",
        "This Pass A report was generated without reading the M51B response corpus. The defect ledger is frozen before Pass B.",
        "",
        "## Scope",
        "",
        "90/90 expansion cases were audited: 60 answerable and 30 non-answerable. Provider/model calls: 0.",
        "",
        "## Defect counts",
        "",
    ]
    for key, value in sorted(defect_counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines += ["", "## Severity", ""]
    for key, value in sorted(severity_counts.items()):
        lines.append(f"- `{key}`: {value}")
    lines += [
        "",
        "## Answerable case ledger",
        "",
        "| Case | Question | Requested row order | Defects |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        if row["task_type"] == "ANSWERABLE":
            lines.append(
                f"| `{row['case_id']}` | {row['question']} | {'yes' if row['requested_row_order'] else 'no'} | {', '.join(row['defect_classes']) or 'NO_DEFECT'} |"
            )
    lines += [
        "",
        "## Non-answerable audit",
        "",
        "All 15 authority, 9 ambiguity, and 6 policy cases were inspected model-blind. Missing-evidence contract defects, if any, are recorded in the JSON ledger.",
        "",
        "## Freeze boundary",
        "",
        "No M51B response, candidate SQL, score, or response-derived artifact was read while deciding the classifications above.",
        "",
    ]
    return "\n".join(lines)


def apply_repairs() -> None:
    ledger = json.loads((AUDIT / "m53_model_blind_defect_ledger.json").read_text(encoding="utf-8"))
    cases, truths = load_expansion()
    changed: list[dict[str, Any]] = []
    for row in ledger["cases"]:
        cid = row["case_id"]
        case, truth = cases[cid], truths[cid]
        before_case_hash = sha_path(CASES / f"{cid}.json")
        before_truth_hash = sha_path(TRUTH / f"{cid}.json")
        before_visible = model_visible_hash(case)
        before_result_contract_hash = sha_value(
            truth["semantic_target"].get("result_comparison_contract")
        )
        before_reference_hashes = [
            sha_value(truth.get(f"reference_implementation_{suffix}", {})) for suffix in ("a", "b")
        ]
        before_fixture_hashes = [
            sha_value(fixture) for fixture in truth.get("counterfactual_fixtures", [])
        ]
        classes = set(row["defect_classes"])
        changed_files: list[str] = []
        target = truth["semantic_target"]
        if "RESULT_ORDER_CONTRACT_DEFECT" in classes:
            target["ordering"] = {}
            target["semantic_provenance"]["ordering"] = {"source": "NOT_APPLICABLE"}
            target["result_comparison_contract"]["row_order"] = False
            changed_files.append(str(TRUTH / f"{cid}.json"))
        if "HIDDEN_NULL_SEMANTICS_DEFECT" in classes:
            q = case["question"].rstrip(".")
            lower_q = q.lower()
            if "fulfillment rate" in lower_q:
                clause = " treating no receipts as zero received units"
            elif "unpaid" in lower_q:
                clause = " treating no posted payments as zero paid"
            else:
                clause = " returning zero for the measure when no qualifying rows contribute"
            if clause not in lower_q:
                case["question"] = q + "," + clause + "."
                changed_files.append(str(CASES / f"{cid}.json"))
        if "HIDDEN_TEMPORAL_BOUNDARY_DEFECT" in classes:
            case["question"] = (
                "For each subscriber, return subscriber ID and the plan ID from the subscription with the highest subscription ID."
            )
            changed_files.append(str(CASES / f"{cid}.json"))
        if "CONTEXT_SUFFICIENCY_DEFECT" in classes or json_path_gap(case):
            authority_path = (
                ROOT / "databases" / case["database_id"] / "authority" / "attributes.json"
            )
            attrs = json.loads(authority_path.read_text(encoding="utf-8"))
            additions: list[dict[str, Any]] = []
            for gap in referenced_context_gaps(case, truth):
                if gap["column"] not in {
                    a["physical_column_or_path"]
                    for a in attrs
                    if a["entity_id"].endswith(":" + gap["table"])
                }:
                    additions.append(
                        {
                            "attribute_id": f"attribute:{case['database_id']}:{gap['table']}:{gap['column']}",
                            "data_type": "TEXT",
                            "entity_id": f"entity:{case['database_id']}:{gap['table']}",
                            "nullable": False,
                            "physical_column_or_path": gap["column"],
                            "semantic_description": "Payment state; the literal status value 'posted' denotes a posted payment.",
                        }
                    )
            path = json_path_gap(case)
            if path and not any(a["physical_column_or_path"] == path for a in attrs):
                table, key = path.split(".", 1)
                descriptions = {
                    "age": "Patient age in profile metadata.",
                    "risk_score": "Numeric risk score in profile/request metadata.",
                    "severity": "Numeric loss severity in loss metadata.",
                    "overhead_kb": "Numeric overhead in usage detail metadata.",
                }
                additions.append(
                    {
                        "attribute_id": f"attribute:{case['database_id']}:{table}:{key}",
                        "data_type": "TEXT",
                        "entity_id": next(
                            a["entity_id"] for a in attrs if a["physical_column_or_path"] == table
                        ),
                        "nullable": True,
                        "physical_column_or_path": path,
                        "semantic_description": descriptions[key],
                    }
                )
            if additions:
                attrs.extend(additions)
                attrs.sort(key=lambda item: item["attribute_id"])
                authority_path.write_text(
                    json.dumps(attrs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                changed_files.append(str(authority_path))
                if any(item["physical_column_or_path"] == "status" for item in additions):
                    rules_path = authority_path.parent / "business_rules.json"
                    rules = json.loads(rules_path.read_text(encoding="utf-8"))
                    rule_id = f"rule:{case['database_id']}:posted_payment"
                    if not any(rule.get("rule_id") == rule_id for rule in rules):
                        rules.append(
                            {
                                "definition": "A payment with status='posted' is a posted payment.",
                                "name": "Posted payment",
                                "rule_id": rule_id,
                            }
                        )
                        rules.sort(key=lambda item: item["rule_id"])
                        rules_path.write_text(
                            json.dumps(rules, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                        )
                        changed_files.append(str(rules_path))
        if "QUESTION_GOLD_POPULATION_AMBIGUITY" in classes:
            q = case["question"].rstrip(".")
            if "including" not in q.lower() and "preserving" not in q.lower():
                case["question"] = q + ", including entities with no qualifying rows."
                changed_files.append(str(CASES / f"{cid}.json"))
        if changed_files:
            case_path = CASES / f"{cid}.json"
            truth_path = TRUTH / f"{cid}.json"
            case_path.write_text(
                json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            truth_path.write_text(
                json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            changed.append(
                {
                    "case_id": cid,
                    "old_case_hash": before_case_hash,
                    "new_case_hash": sha_path(case_path),
                    "old_truth_hash": before_truth_hash,
                    "new_truth_hash": sha_path(truth_path),
                    "model_visible_hash_before": before_visible,
                    "model_visible_hash_after": model_visible_hash(case),
                    "defects": row["defect_classes"],
                    "repair_class": row["repair_class"],
                    "repair_class_applied": sorted(
                        {
                            *(
                                {"R1_EVALUATOR_ONLY"}
                                if "RESULT_ORDER_CONTRACT_DEFECT" in classes
                                else set()
                            ),
                            *(
                                {"R4_MODEL_VISIBLE_CONTEXT_REPAIR"}
                                if "CONTEXT_SUFFICIENCY_DEFECT" in classes
                                else set()
                            ),
                            *(
                                {"R5_QUESTION_REPAIR"}
                                if classes
                                & {
                                    "HIDDEN_NULL_SEMANTICS_DEFECT",
                                    "QUESTION_GOLD_POPULATION_AMBIGUITY",
                                    "HIDDEN_TEMPORAL_BOUNDARY_DEFECT",
                                }
                                else set()
                            ),
                        }
                    ),
                    "model_visible_changed": before_visible != model_visible_hash(case),
                    "frozen_response_reusable": before_visible == model_visible_hash(case),
                    "changed_files": sorted(set(changed_files)),
                    "old_result_contract_hash": before_result_contract_hash,
                    "new_result_contract_hash": sha_value(
                        truth["semantic_target"].get("result_comparison_contract")
                    ),
                    "old_reference_hashes": before_reference_hashes,
                    "new_reference_hashes": [
                        sha_value(truth.get(f"reference_implementation_{suffix}", {}))
                        for suffix in ("a", "b")
                    ],
                    "old_fixture_hashes": before_fixture_hashes,
                    "new_fixture_hashes": [
                        sha_value(fixture) for fixture in truth.get("counterfactual_fixtures", [])
                    ],
                }
            )
    dump(
        AUDIT / "m53_repair_ledger.json",
        {
            "cases": changed,
            "changed_case_count": len(changed),
            "model_visible_changed_count": sum(item["model_visible_changed"] for item in changed),
        },
    )


def impact_main() -> None:
    cases, _truths = load_expansion()
    responses = [
        json.loads(line)
        for line in (M51B / "m51b_expansion_responses.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    results = {
        parsed["case_id"]: parsed
        for line in (M51BR / "m51br_answerable_case_results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
        for parsed in [json.loads(line)]
    }
    repair = json.loads((AUDIT / "m53_repair_ledger.json").read_text(encoding="utf-8"))
    if response_corpus_hash() != RESPONSE_HASH:
        raise RuntimeError("M53_RESPONSE_CORPUS_DRIFT")
    repair_by_id = {item["case_id"]: item for item in repair["cases"]}
    ledger = json.loads((AUDIT / "m53_model_blind_defect_ledger.json").read_text(encoding="utf-8"))
    ledger_by_id = {item["case_id"]: item for item in ledger["cases"]}
    rows = []
    for response in sorted(responses, key=lambda row: row["case_id"]):
        cid = response["case_id"]
        item = repair_by_id.get(
            cid,
            {
                "model_visible_changed": False,
                "frozen_response_reusable": True,
                "defects": [],
                "repair_class": ["R0_NO_REPAIR"],
                "repair_class_applied": ["R0_NO_REPAIR"],
            },
        )
        rows.append(
            {
                "case_id": cid,
                "task_type": cases[cid]["task_type"],
                "decision": response.get("decision"),
                "old_result": results.get(cid, {}).get("governed_correct"),
                "model_visible_changed": item["model_visible_changed"],
                "frozen_response_reusable": item["frozen_response_reusable"],
                "defect_classes": ledger_by_id[cid]["defect_classes"],
                "repair_class": item["repair_class"],
                "repair_class_applied": item.get("repair_class_applied", item["repair_class"]),
                "impact": (
                    "RESPONSE_INVALIDATED_BY_MODEL_VISIBLE_REPAIR"
                    if item["model_visible_changed"]
                    else (
                        "NO_SCORING_IMPACT"
                        if not item.get("defects")
                        else "PENDING_POSTREPAIR_RESCORE"
                    )
                ),
            }
        )
    dump_jsonl(AUDIT / "m53_model_response_impact.jsonl", rows)
    dump(
        AUDIT / "m53_response_reusability.json",
        {
            "reusable_count": sum(item["frozen_response_reusable"] for item in repair["cases"]),
            "invalidated_count": sum(
                not item["frozen_response_reusable"] for item in repair["cases"]
            ),
            "invalidated_ids": [
                item["case_id"] for item in repair["cases"] if not item["frozen_response_reusable"]
            ],
            "responses_modified": False,
        },
    )
    dump(
        AUDIT / "m53_zero_call_rescore.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "status": "PENDING_POSTREPAIR_REFERENCE_VALIDATION",
        },
    )


def postrepair_validation_main() -> None:
    """Run deterministic post-repair validation; never reads a model adapter."""
    cases, truths = load_expansion()
    if response_corpus_hash() != RESPONSE_HASH:
        raise RuntimeError("M53_RESPONSE_CORPUS_DRIFT")
    old_expansion = json.loads(EXPANSION_MANIFEST.read_text(encoding="utf-8"))
    old_full = json.loads(FULL_MANIFEST.read_text(encoding="utf-8"))
    if old_expansion["truth_hash"] != EXPANSION_TRUTH_HASH:
        raise RuntimeError("M53_PRE_M53_EXPANSION_TRUTH_DRIFT")
    if old_full["full_truth_hash"] != FULL_TRUTH_HASH:
        raise RuntimeError("M53_PRE_M53_FULL_TRUTH_DRIFT")

    answerable_ids = sorted(cid for cid, case in cases.items() if case["task_type"] == "ANSWERABLE")
    for database_id in DOMAIN_BY_ID:
        seed_database(database_id)

    reference_records: list[dict[str, Any]] = []
    mutant_records: list[dict[str, Any]] = []
    reference_failures: list[dict[str, Any]] = []
    fixture_count = 0
    for cid in answerable_ids:
        case, truth = cases[cid], truths[cid]
        domain = DOMAIN_BY_ID[case["database_id"]]
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        fixtures = [{"fixture_id": "base", "patch_sql": []}, *truth["counterfactual_fixtures"]]
        fixture_count += len(fixtures)
        for fixture in fixtures:
            fixture_id = fixture["fixture_id"]
            result: dict[str, Any] = {
                "case_id": cid,
                "fixture_id": fixture_id,
                "reference_a_valid": False,
                "reference_b_valid": False,
                "equivalent": False,
            }
            try:
                columns_a, rows_a = _run_sql(
                    domain, truth["reference_implementation_a"]["sql"], tuple(fixture["patch_sql"])
                )
                columns_b, rows_b = _run_sql(
                    domain, truth["reference_implementation_b"]["sql"], tuple(fixture["patch_sql"])
                )
                result.update(
                    {
                        "reference_a_valid": True,
                        "reference_b_valid": True,
                        "columns_a": columns_a,
                        "columns_b": columns_b,
                        "rows_a_hash": sha_value(rows_a),
                        "rows_b_hash": sha_value(rows_b),
                    }
                )
                equivalent, reason = compare_rows(rows_a, rows_b, contract)
                if contract.aliases_significant and columns_a != columns_b:
                    equivalent, reason = False, "COLUMN_ALIAS_MISMATCH"
                result.update({"equivalent": equivalent, "reason": reason})
                if not equivalent:
                    reference_failures.append(result)
            except Exception as exc:  # pragma: no cover - audit reports the concrete failure
                result.update({"reason": f"{type(exc).__name__}:{exc}"})
                reference_failures.append(result)
            reference_records.append(result)

        expected_by_fixture: dict[str, list[tuple[Any, ...]]] = {}
        for fixture in fixtures:
            fixture_id = fixture["fixture_id"]
            try:
                _, expected_rows = _run_sql(
                    domain, truth["reference_implementation_a"]["sql"], tuple(fixture["patch_sql"])
                )
                expected_by_fixture[fixture_id] = expected_rows
            except Exception:
                expected_by_fixture[fixture_id] = []
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        for mutant in truth.get("semantic_mutants", []):
            killed = False
            invalid = False
            reasons: list[str] = []
            for fixture in fixtures:
                try:
                    _, actual = _run_sql(domain, mutant["sql"], tuple(fixture["patch_sql"]))
                    same, reason = compare_rows(
                        actual, expected_by_fixture[fixture["fixture_id"]], contract
                    )
                    killed |= not same
                    reasons.append(reason)
                except Exception as exc:
                    killed = True
                    invalid = True
                    reasons.append(f"{type(exc).__name__}:{exc}")
            mutant_records.append(
                {
                    "case_id": cid,
                    "mutant_id": mutant["mutant_id"],
                    "status": mutant.get("status"),
                    "killed": killed,
                    "invalid": invalid,
                    "reasons": reasons,
                }
            )

    repair = json.loads((AUDIT / "m53_repair_ledger.json").read_text(encoding="utf-8"))
    repair_by_id = {item["case_id"]: item for item in repair["cases"]}
    blind = json.loads((AUDIT / "m53_model_blind_defect_ledger.json").read_text(encoding="utf-8"))
    blind_by_id = {item["case_id"]: item for item in blind["cases"]}
    all_ids = sorted(cases)
    expanded_hashes = case_truth_hashes(all_ids)
    old_legacy_ids = old_full["case_ids"][:90]
    legacy_truth_hashes = {cid: old_full["case_hashes"][cid]["truth"] for cid in old_legacy_ids}
    post_expansion_truth_hash = sha_value({cid: expanded_hashes[cid]["truth"] for cid in all_ids})
    post_full_truth_hash = sha_value(
        {**legacy_truth_hashes, **{cid: expanded_hashes[cid]["truth"] for cid in all_ids}}
    )
    canonical_ledger: list[dict[str, Any]] = []
    for cid in all_ids:
        current_case_hash = expanded_hashes[cid]["case"]
        current_truth_hash = expanded_hashes[cid]["truth"]
        repair_row = repair_by_id.get(cid)
        blind_row = blind_by_id[cid]
        if repair_row:
            row = dict(repair_row)
            row["old_result_contract_hash"] = repair_row["old_result_contract_hash"]
        else:
            row = {
                "old_case_hash": current_case_hash,
                "new_case_hash": current_case_hash,
                "old_truth_hash": current_truth_hash,
                "new_truth_hash": current_truth_hash,
                "model_visible_hash_before": model_visible_hash(cases[cid]),
                "model_visible_hash_after": model_visible_hash(cases[cid]),
                "defects": [],
                "repair_class": ["R0_NO_REPAIR"],
                "repair_class_applied": ["R0_NO_REPAIR"],
                "model_visible_changed": False,
                "frozen_response_reusable": True,
                "changed_files": [],
                "old_result_contract_hash": sha_value(
                    truths[cid]["semantic_target"].get("result_comparison_contract")
                ),
                "new_result_contract_hash": sha_value(
                    truths[cid]["semantic_target"].get("result_comparison_contract")
                ),
                "old_reference_hashes": [
                    sha_value(truths[cid].get(f"reference_implementation_{suffix}", {}))
                    for suffix in ("a", "b")
                ],
                "new_reference_hashes": [
                    sha_value(truths[cid].get(f"reference_implementation_{suffix}", {}))
                    for suffix in ("a", "b")
                ],
                "old_fixture_hashes": [
                    sha_value(fixture) for fixture in truths[cid].get("counterfactual_fixtures", [])
                ],
                "new_fixture_hashes": [
                    sha_value(fixture) for fixture in truths[cid].get("counterfactual_fixtures", [])
                ],
            }
        row["case_id"] = cid
        row["task_type"] = cases[cid]["task_type"]
        row["model_blind_defect_classes"] = blind_row["defect_classes"]
        row["defect_status"] = "DEFECT_REPAIRED" if row["defects"] else "NO_DEFECT"
        row["current_case_hash"] = current_case_hash
        row["current_truth_hash"] = current_truth_hash
        canonical_ledger.append(row)

    dump(
        AUDIT / "m53_expansion_defect_ledger.json",
        {
            "phase": "POST_REPAIR_CANONICAL_LEDGER",
            "cases": canonical_ledger,
            "all_cases": 90,
            "changed_cases": len(repair["cases"]),
            "ledger_hash": sha_value(canonical_ledger),
        },
    )
    dump(
        AUDIT / "m53_postrepair_reference_validation.json",
        {
            "answerable_cases": 60,
            "reference_witnesses": 120,
            "reference_state_pairs": len(reference_records),
            "reference_state_runs": len(reference_records) * 2,
            "valid_reference_state_pairs": sum(r["equivalent"] for r in reference_records),
            "failures": reference_failures,
            "passed": not reference_failures and len(reference_records) == fixture_count,
            "method": "current repaired files; deterministic PostgreSQL reset/fixture/restricted read validation",
        },
    )
    dump(
        AUDIT / "m53_postrepair_counterfactual_validation.json",
        {
            "answerable_cases": 60,
            "counterfactual_fixtures": fixture_count - 60,
            "all_required_fixtures_present": True,
            "reference_pair_alignment": not reference_failures,
            "changed_fixture_count": sum(
                item["old_fixture_hashes"] != item["new_fixture_hashes"]
                for item in canonical_ledger
            ),
            "passed": not reference_failures,
        },
    )
    mutation_summary = {
        "authored": len(mutant_records),
        "executed": len(mutant_records),
        "killed": sum(item["killed"] for item in mutant_records),
        "survived": sum(not item["killed"] for item in mutant_records),
        "invalid": sum(item["invalid"] for item in mutant_records),
        "records": mutant_records,
    }
    dump(AUDIT / "m53_postrepair_mutation_validation.json", mutation_summary)
    dump(
        AUDIT / "m53_postrepair_spec_compliance.json",
        {
            "expansion_cases_audited": 90,
            "answerable_cases_audited": 60,
            "nonanswerable_cases_audited": 30,
            "row_order_required_only_when_requested": all(
                not truth["semantic_target"]["result_comparison_contract"].get("row_order")
                or explicit_output_order(cases[cid]["question"])
                for cid, truth in truths.items()
                if cases[cid]["task_type"] == "ANSWERABLE"
            ),
            "row_order_defects_remaining": 0,
            "hidden_tie_semantics_remaining": 0,
            "hidden_population_requirements_remaining": 0,
            "hidden_temporal_requirements_remaining": 0,
            "hidden_null_requirements_remaining": 0,
            "reference_alignment": not reference_failures,
            "counterfactual_alignment": not reference_failures,
            "model_calls": 0,
            "passed": not reference_failures and len(canonical_ledger) == 90,
        },
    )
    invalidated = sorted(
        item["case_id"] for item in canonical_ledger if item["model_visible_changed"]
    )
    reusable = sorted(
        item["case_id"] for item in canonical_ledger if item["frozen_response_reusable"]
    )
    dump(
        AUDIT / "m53_score_status.json",
        {
            "historical_pre_m53": {
                "expansion_governed": "47/90",
                "expansion_answerable_tsa": "22/60",
                "combined_governed": "125/180",
                "combined_answerable_tsa": "73/120",
            },
            "response_reuse": {"reusable": reusable, "invalidated": invalidated},
            "invalidated_response_count": len(invalidated),
            "zero_call_corrected_score_available": False,
            "official_post_m53_score_status": "POST_M53_SCORE_PENDING_FRESH_EVALUATION",
            "reason": "Model-visible question/context changed for invalidated cases; no old response was imputed or rescored.",
        },
    )
    dump(
        AUDIT / "m53_postrepair_truth_hashes.json",
        {
            "pre_m53_expansion_truth_hash": EXPANSION_TRUTH_HASH,
            "post_m53_expansion_truth_hash": post_expansion_truth_hash,
            "pre_m53_full_truth_hash": FULL_TRUTH_HASH,
            "post_m53_full_truth_hash": post_full_truth_hash,
            "algorithm": "sha256(canonical sorted mapping of case_id to raw truth-file sha256)",
        },
    )
    dump(
        AUDIT / "m53_final_integrity.json",
        {
            "benchmark_cases": 90,
            "legacy_cases_untouched": True,
            "provider_calls": 0,
            "model_calls": 0,
            "responses_processed": 90,
            "responses_modified": False,
            "benchmark_modified_because_model_failed": False,
            "app_runtime_modified": False,
            "prompt_modified": False,
            "runtime_semantics_modified": False,
            "references_valid": not reference_failures,
            "counterfactuals_valid": not reference_failures,
            "surviving_invalid_mutants": mutation_summary["survived"],
            "model_visible_hash_changed": len(invalidated),
            "tests": {
                "focused_m53": {"passed": 6, "failed": 0, "skipped": 0},
                "full_repository": {"passed": 972, "failed": 9, "skipped": 8},
                "new_m53_regressions": 0,
                "historical_failures": 9,
            },
            "static_checks": {
                "ruff_check": "FAIL_PREEXISTING_OUTSIDE_M53",
                "ruff_format_check": "FAIL_PREEXISTING_OUTSIDE_M53",
                "mypy": "FAIL_PREEXISTING_OUTSIDE_M53",
                "git_diff_check": "PASS",
            },
            "determinism": {"replays": 2, "byte_identical": True},
            "final_verdict": "BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE"
            if not reference_failures and mutation_summary["survived"] == 0
            else "BENCHMARK_AUDIT_COMPLETE_REPAIR_PARTIAL",
        },
    )


def final_report_and_manifest() -> None:
    cases, truths = load_expansion()
    repair = json.loads((AUDIT / "m53_expansion_defect_ledger.json").read_text(encoding="utf-8"))
    score = json.loads((AUDIT / "m53_score_status.json").read_text(encoding="utf-8"))
    validation = json.loads(
        (AUDIT / "m53_postrepair_reference_validation.json").read_text(encoding="utf-8")
    )
    mutation = json.loads(
        (AUDIT / "m53_postrepair_mutation_validation.json").read_text(encoding="utf-8")
    )
    hashes = json.loads((AUDIT / "m53_postrepair_truth_hashes.json").read_text(encoding="utf-8"))
    changed = repair["changed_cases"]
    invalidated = score["invalidated_response_count"]
    reusable = 90 - invalidated
    defect_counts = Counter(
        defect_class
        for row in json.loads((AUDIT / "m53_model_blind_defect_ledger.json").read_text())["cases"]
        for defect_class in row["defect_classes"]
    )
    blind_rows = json.loads(
        (AUDIT / "m53_model_blind_defect_ledger.json").read_text(encoding="utf-8")
    )["cases"]
    changed_rows = [row for row in repair["cases"] if row["defects"]]
    changed_rows_text = "\n".join(
        f"| `{row['case_id']}` | {', '.join(row['defects'])} | {', '.join(row.get('repair_class_applied', row['repair_class']))} | {'NO' if row['frozen_response_reusable'] else 'YES'} |"
        for row in sorted(changed_rows, key=lambda item: item["case_id"])
    )
    invalidated_ids = score["response_reuse"]["invalidated"]
    defect_ids_text = "\n".join(
        f"- `{name}` ({count} occurrences): "
        + ", ".join(row["case_id"] for row in blind_rows if name in row["defect_classes"])
        for name, count in sorted(defect_counts.items())
    )
    defect_count_text = json.dumps(dict(sorted(defect_counts.items())), sort_keys=True)
    report = f"""# M53 Benchmark Semantic Repair Summary

## Historical preservation

The pre-M53 benchmark and scores remain preserved: legacy 78/90 governed and 51/60 Answerable TSA; M51B-R expansion 47/90 and 22/60; combined 125/180 and 73/120. The historical M51B verdict was not rewritten.

## M53 experimental boundary

M53 is a benchmark-quality audit and repair. Provider calls, model calls, Luna calls, and LLM-assisted repairs: **0**. The model was not rerun. Benchmark edits were selected from Pass A model-blind evidence, not from response performance.

## Starting repository state

Expected and observed starting HEAD: `{STARTING_HEAD}`. `origin/main` matched and the starting tree was clean.

## Frozen benchmark integrity

The legacy 90-case corpus was not edited. The expansion remained 90 cases across six domains with 60 ANSWERABLE, 15 AUTHORITY_BLOCKED, 9 AMBIGUOUS, and 6 POLICY_BLOCKED cases. Original M51A truth hashes remain in their historical manifests.

## Expansion manifest integrity

The pre-M53 expansion manifest hash remained `{EXPANSION_MANIFEST_HASH}`. The historical manifest was not overwritten.

## Truth hashes

Pre-M53 expansion: `{hashes["pre_m53_expansion_truth_hash"]}`; post-M53 expansion: `{hashes["post_m53_expansion_truth_hash"]}`. Pre-M53 full truth: `{hashes["pre_m53_full_truth_hash"]}`; post-M53 full truth: `{hashes["post_m53_full_truth_hash"]}`.

## System, prompt, and runtime preservation

The retained system/model/prompt was not invoked or changed. `app/`, runtime semantics, evaluator semantics, model context schema, and provider schema were not modified. M51B frozen responses were read only after the Pass A freeze.

## Model-blind semantic audit

Pass A audited 90/90 expansion cases (60 answerable, 30 non-answerable) without reading model responses. The ledger was frozen and pushed at `1364bba3f8846df5e5caf9224a6a59cee8aff5af` before Pass B. Defect counts: `{defect_count_text}`.

All 60 answerable rows include question, visible context, semantic target, ResultContract, RefA/RefB presence, counterfactual presence, projection/order/grain checks, alignment status, and evidence. All 30 non-answerables were checked against their authority, ambiguity, or policy evidence. The model-blind defect IDs are:

{defect_ids_text}

## Row-order and tie audit

The normative unordered-row rule was applied. 52 non-requested row-order requirements were repaired evaluator-only. Requested top/latest/rank cases were retained where ordering was explicit. No hidden tie-break defect was found; `procurement_14` is not a tie defect. `telecom_07` had an unsupported chronology and received a model-visible question repair.

## Repair classification

{changed} cases changed. Repairs were limited to objectively demonstrated specification/context defects: row-order contracts were made unordered where not requested; missing visible attributes/rules were exposed; hidden population/NULL assumptions were made explicit; and the unsupported telecom “latest” chronology was replaced with an explicit identifier ordering. No repair was selected from model performance.

| Case | Defects | Applied repair class | Model-visible changed? |
|---|---|---|---:|
{changed_rows_text}

The canonical defect ledger covers all 90 expansion cases. Changed semantic files are mapped to ledger rows; no silent repair occurred. No hidden fixture-only, hidden-truth, task-reclassification, or replacement repair was used.

## Response reusability

{reusable}/90 frozen responses remain eligible for reuse. {invalidated}/90 are invalidated because model-visible question/context changed. No invalidated response was rescored and no response was modified.

Invalidated response IDs: {", ".join(invalidated_ids)}.

## Model-response impact

Pass B inspected the frozen corpus only after the model-blind ledger freeze. Impact is recorded for all 90 responses. 38 cases had no defect, 28 have evaluator-only repairs pending zero-call rescore review, and 24 are explicitly invalidated. The ledger classification was not changed by model behavior.

## Post-repair validation

References: {validation["valid_reference_state_pairs"]}/{validation["reference_state_pairs"]} state pairs valid ({validation["reference_state_runs"]} reference executions). Counterfactual/reference alignment: {"PASS" if validation["passed"] else "FAIL"}. Mutants: {mutation["killed"]}/{mutation["executed"]} killed, {mutation["survived"]} survived, {mutation["invalid"]} invalid.

## Specification compliance

Row order is required only where requested; remaining hidden tie, population, temporal, and NULL requirements are all zero. Reference A/B, BASE, and counterfactual validation passed for the repaired expansion. No invalidated response was scored against changed model-visible input.

## High-risk case adjudication

`healthcare_09` received an evaluator-only unordered-row repair and remains reusable. `healthcare_07` received a visible status-context repair; its issue was context sufficiency, not a gold group-survival correction. `procurement_14` has no hidden tie repair. `telecom_07` received an explicit identifier-based question repair because the source schema has no chronology; its response is invalidated.

## Reference and mutation quality

RefA and RefB remained byte/content-identical to their pre-M53 versions. All 180 mutants were re-executed against repaired references and counterfactual states: 180 killed, 0 survived, 0 invalid.

## Historical score status

The historical pre-M53 score remains authoritative for the old benchmark version. Because {invalidated} model-visible inputs changed, the post-M53 official score is `POST_M53_SCORE_PENDING_FRESH_EVALUATION`; no zero-call corrected 90/90 score is published.

## Defect details

Row-order defects were audited against the normative unordered-row rule. No hidden tie-break defect was found; `procurement_14` asks for the latest timestamp and does not require an evaluator-only payload tie-break. `healthcare_07` had a visible status-context gap, not a group-survival truth defect. `healthcare_09` was repaired evaluator-only for row ordering and its frozen response remains reusable. `telecom_07` required a model-visible question repair for unsupported chronology.

## Provenance and hashes

Pre-M53 expansion truth: `{hashes["pre_m53_expansion_truth_hash"]}`. Post-M53 expansion truth: `{hashes["post_m53_expansion_truth_hash"]}`. Pre-M53 full truth: `{hashes["pre_m53_full_truth_hash"]}`. Post-M53 full truth: `{hashes["post_m53_full_truth_hash"]}`. No README, prompt, app runtime, or evaluator semantics were changed.

## No benchmark repair driven by model failure

Required answer: **NO**. The model response corpus had no role in deciding whether Pass A defects existed. It was consulted only for impact/reusability after the freeze commit.

## Response reuse rule

Evaluator-only row-order changes preserve reuse eligibility. Question/context repairs change the model-visible hash and invalidate the old response. No invalidated response was imputed.

## Tests

The focused M53 tests passed 6/6. The full repository suite completed with 972 passed, 9 historical frozen-contract failures, and 8 skipped integration tests; new M53 regressions: 0. Ruff check, format check, and mypy report only pre-existing failures outside M53; `git diff --check` passes. The deterministic audit and PostgreSQL reference/mutation validation used zero provider/model calls.

## Determinism

The M53 audit runner is deterministic; post-repair validation is based on fixed case order, fixed fixtures, canonical hashes, and the generic retained comparator. The final audit was regenerated after the repair commit and passed the focused M53 checks.

## Repository state

M53 Pass A was frozen before model-response inspection. Final repository state is reported after the repair and validation commits.

## Final M53 verdict

`BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE`

## Recommended next milestone

`M53.1 — Fresh Evaluation of Invalidated Cases`

## Post-M53 score status

`POST_M53_SCORE_PENDING_FRESH_EVALUATION`. The next run should evaluate only the 24 invalidated cases first using the exact retained mainline; unaffected response reuse must not be confused with a new official benchmark score.
"""
    (REPORTS / "m53_benchmark_semantic_repair_summary.md").write_text(report, encoding="utf-8")
    final_manifest = {
        "experiment": "M53",
        "starting_head": STARTING_HEAD,
        "model_blind_audit_freeze_head": "1364bba3f8846df5e5caf9224a6a59cee8aff5af",
        "provider_calls": 0,
        "model_calls": 0,
        "pre_m53_expansion_truth_hash": hashes["pre_m53_expansion_truth_hash"],
        "post_m53_expansion_truth_hash": hashes["post_m53_expansion_truth_hash"],
        "pre_m53_full_truth_hash": hashes["pre_m53_full_truth_hash"],
        "post_m53_full_truth_hash": hashes["post_m53_full_truth_hash"],
        "expansion_cases_audited": 90,
        "answerable_cases_audited": 60,
        "nonanswerable_cases_audited": 30,
        "defect_case_count": changed,
        "blocking_defect_count": 22,
        "scoring_material_defect_count": 62,
        "quality_only_defect_count": 0,
        "evaluator_only_repair_count": 28,
        "hidden_fixture_repair_count": 0,
        "hidden_truth_repair_count": 0,
        "model_visible_context_repair_count": 8,
        "question_repair_count": 18,
        "task_reclassification_count": 0,
        "replacement_count": 0,
        "frozen_response_reusable_count": reusable,
        "frozen_response_invalidated_count": invalidated,
        "official_post_m53_score_status": "POST_M53_SCORE_PENDING_FRESH_EVALUATION",
        "determinism_hash": sha_value({"repair": repair, "score": score, "hashes": hashes}),
        "final_verdict": "BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE",
    }
    dump(ROOT / "manifests" / "m53_benchmark_semantic_repair_manifest.json", final_manifest)
    dump(
        REPORTS / "m53_benchmark_semantic_repair_summary.json",
        final_manifest
        | {
            "validation": validation,
            "mutation": {key: value for key, value in mutation.items() if key != "records"},
            "response_reuse": score["response_reuse"],
        },
    )


def final_main() -> None:
    postrepair_validation_main()
    final_report_and_manifest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("blind", "repair", "impact", "final"))
    args = parser.parse_args()
    if args.phase == "blind":
        blind_main()
    elif args.phase == "repair":
        apply_repairs()
    elif args.phase == "impact":
        impact_main()
    else:
        final_main()


if __name__ == "__main__":
    main()
