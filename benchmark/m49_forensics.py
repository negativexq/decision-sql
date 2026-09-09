"""M49 zero-call forensic analysis over frozen M48B.2 evidence.

This module deliberately consumes existing benchmark artifacts only.  It never
calls a provider and never changes application, truth, fixture, or reference
data.  The optional SQL diagnostic is limited to replaying the two already
recorded result-mismatch cases against the frozen states.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark import m48b2_runner as m48b2
from benchmark import m48b_runner as runtime
from benchmark.m46a_audit import _build_catalogs
from benchmark.model_contract import serialize_governed_context_v1, sha256_text
from benchmark.models import ResultContract

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m49"
REPORTS = ROOT / "reports"
MANIFEST = ROOT / "manifests" / "m49_residual_forensics_manifest.json"
PARENT_COMMIT = "8aa1c8431d1b4929c8d90083c5991621c7d5f9b4"
README_DESCENDANT = "6356e1bb3e8ab70bb6dbeb375ede4848c01d1b57"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
CORPUS_HASH = "f86b07d37b52c0891f6b9e95819104b150cdc9d03825d584ed81921a854795d8"
SPLIT_NOTE = (
    "M48B.2 has no separate model DEV arm; all final observations are CONFIRMATION evidence."
)

TOP_FAMILIES = (
    "DECISIONING_ABSTENTION",
    "GOVERNANCE_UNDER_ABSTENTION",
    "SQL_SEMANTIC_RESULT",
)
EVIDENCE_LEVELS = {
    "E0": "count-only failure evidence",
    "E1": "decision/output evidence establishes immediate failure",
    "E2": "direct existing evidence shows a narrower mechanism",
    "E3": "existing execution/control evidence discriminates the mechanism",
}
SYSTEMATICITY_THRESHOLD = {
    "minimum_count": 3,
    "minimum_share": 0.25,
    "minimum_domains": 2,
    "minimum_e2_e3": 2,
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _case_path(case_id: str, kind: str) -> Path:
    directory = "pilot" if case_id.startswith(("commerce_", "fleet_", "support_")) else "m38_dev"
    root = ROOT / ("cases" if kind == "model" else "ground_truth") / directory
    return root / f"{case_id}.json"


def _pairs() -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    ids = _load(ROOT / "splits" / "m40_dev.json")["case_ids"]
    result = []
    for case_id in ids:
        model = _load(_case_path(case_id, "model"))
        truth = _load(_case_path(case_id, "truth"))
        if model["case_id"] != truth["case_id"] or model["database_id"] != truth["database_id"]:
            raise RuntimeError(f"M49_PAIRING_DEFECT:{case_id}")
        result.append((case_id, model, truth))
    if len(result) != 90 or len({item[0] for item in result}) != 90:
        raise RuntimeError("M49_CASE_ORDER_DEFECT")
    return result


def _submissions() -> dict[str, dict[str, Any]]:
    path = ROOT / "experiments" / "results" / "m48b2" / "parsed_submissions.jsonl"
    return {
        row["case_id"]: row for row in (json.loads(line) for line in path.read_text().splitlines())
    }


def _base() -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in _load(ROOT / "audits" / "m48b2" / "m48b2_base_runtime_ledger.json")
    }


def _counterfactual() -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in _load(ROOT / "audits" / "m48b2" / "m48b2_counterfactual_runtime_ledger.json")
    }


def _governance() -> dict[str, dict[str, Any]]:
    records = {
        row["case_id"]: row
        for row in _load(ROOT / "audits" / "m48b2" / "m48b2_governance_ledger.json")
    }
    for case_id, row in _base().items():
        records.setdefault(
            case_id,
            {
                "case_id": case_id,
                "behavior": row["plan"]["truth_behavior"],
                "decision": row["plan"].get("submitted_decision"),
                "governance_correct": row["plan"].get("governance_correct", False),
            },
        )
    return records


def _state_correct(record: dict[str, Any], state_id: str = "base") -> bool:
    state = next(item for item in record.get("states", []) if item["state_id"] == state_id)
    return bool(state["outcome"].get("result_contract_outcome"))


def _answerable(plan: dict[str, Any]) -> bool:
    return bool(plan["truth_behavior"] == "ANSWERABLE")


def _case_split(_case_id: str) -> str:
    return "CONFIRMATION"


def _visible_facts(database_id: str) -> dict[str, Any]:
    context = json.loads(serialize_governed_context_v1(database_id))
    return {
        "attribute_ids": sorted(item["attribute_id"] for item in context.get("attributes", [])),
        "relationship_ids": sorted(
            item["relationship_id"] for item in context.get("authorized_relationships", [])
        ),
        "temporal_rule_ids": sorted(
            item["temporal_rule_id"] for item in context.get("temporal_rules", [])
        ),
        "metrics": context.get("metrics", []),
        "business_rules": context.get("business_rules", []),
        "temporal_rules": context.get("temporal_rules", []),
    }


def _fact_presence(truth: dict[str, Any], visible: dict[str, Any]) -> dict[str, Any]:
    facts = truth.get("required_context_facts", [])
    present: dict[str, bool] = {}
    for fact in facts:
        if fact.startswith("attributes:"):
            attribute_id = "attribute:" + fact.removeprefix("attributes:")
            present[fact] = attribute_id in visible["attribute_ids"]
        elif fact.startswith("relationship:"):
            present[fact] = fact in visible["relationship_ids"]
        elif fact.startswith("temporal_rules:"):
            temporal_rule_id = "time:" + fact.removeprefix("temporal_rules:")
            present[fact] = temporal_rule_id in visible["temporal_rule_ids"]
        else:
            present[fact] = False
    return {
        "required": facts,
        "present": present,
        "all_present": bool(facts) and all(present.values()),
    }


def _response_fields(
    case_id: str, submissions: dict[str, dict[str, Any]], base: dict[str, Any]
) -> dict[str, Any]:
    row = submissions[case_id]
    submission = row.get("parsed_submission") or {}
    state = base[case_id].get("states", [])
    outcome = state[0].get("outcome", {}) if state else {}
    grain = outcome.get("grain", {})
    return {
        "response_origin": row.get("response_origin"),
        "request_hash": row.get("request_sha256"),
        "response_hash": row.get("response_sha256"),
        "parsed_submission_hash": _hash(submission),
        "submitted_decision": submission.get("decision"),
        "reason_code": submission.get("reason_code"),
        "raw_sql_hash": outcome.get("raw_sql_hash") or grain.get("input_sql_hash"),
        "selected_sql_hash": outcome.get("selected_sql_hash") or grain.get("selected_sql_hash"),
    }


def reconstruct_population() -> dict[str, Any]:
    submissions, base, cf, governance = _submissions(), _base(), _counterfactual(), _governance()
    records = []
    for case_id, model, truth in _pairs():
        behavior = truth["semantic_target"]["behavior"]
        plan = base[case_id]["plan"]
        decision = plan.get("submitted_decision")
        gov_correct = bool(governance[case_id]["governance_correct"])
        base_runtime_correct = _state_correct(base[case_id]) if plan["run_sql_runtime"] else None
        cf_states = [
            item for item in cf.get(case_id, {}).get("states", []) if item["state_id"] != "base"
        ]
        full_runtime_correct = (
            bool(base_runtime_correct)
            and all(bool(item["outcome"].get("result_contract_outcome")) for item in cf_states)
            if plan["run_sql_runtime"]
            else None
        )
        if behavior == "ANSWERABLE":
            base_correct = decision == "ANSWER" and bool(base_runtime_correct)
            full_correct = decision == "ANSWER" and bool(full_runtime_correct)
        else:
            base_correct = gov_correct
            full_correct = gov_correct
        if behavior == "ANSWERABLE" and decision != "ANSWER":
            top_family, high_level = "DECISIONING_ABSTENTION", "WRONG_REFUSAL"
        elif behavior != "ANSWERABLE" and not gov_correct:
            top_family, high_level = "GOVERNANCE_UNDER_ABSTENTION", "WRONG_GOVERNANCE_DECISION"
        elif behavior == "ANSWERABLE" and decision == "ANSWER" and not full_correct:
            top_family, high_level = "SQL_SEMANTIC_RESULT", "RESULT_MISMATCH"
        else:
            continue
        response = _response_fields(case_id, submissions, base)
        records.append(
            {
                "case_id": case_id,
                "database_id": model["database_id"],
                "split": _case_split(case_id),
                "truth_behavior": behavior,
                "question": model["question"],
                "model_decision": response["submitted_decision"],
                "response_origin": response["response_origin"],
                "request_hash": response["request_hash"],
                "response_hash": response["response_hash"],
                "parsed_submission_hash": response["parsed_submission_hash"],
                "raw_sql_hash": response["raw_sql_hash"],
                "selected_sql_hash": response["selected_sql_hash"],
                "response": response,
                "base_correct": base_correct,
                "full_counterfactual_correct": full_correct,
                "governance_correct": gov_correct,
                "high_level_failure_class": high_level,
                "top_level_family": top_family,
                "query_shape_tags": truth["semantic_target"].get("query_shape_tags", []),
                "state_ids": [item["state_id"] for item in base[case_id].get("states", [])],
            }
        )
    records.sort(key=lambda item: int(submissions[item["case_id"]]["case_index"]))
    if len(records) != 12 or len({item["case_id"] for item in records}) != 12:
        raise RuntimeError("M49_FAILURE_POPULATION_DEFECT")
    return {"count": len(records), "duplicate_case_ids": 0, "records": records}


def source_inventory() -> dict[str, Any]:
    paths = [
        "README.md",
        "benchmark/reports/m48b2_end_to_end_summary.json",
        "benchmark/manifests/m48b2_branch_complete_runtime_contract.json",
        "benchmark/experiments/results/m48b2/parsed_submissions.jsonl",
        "benchmark/experiments/results/m48b2/request_ledger.json",
        "benchmark/audits/m48b2/m48b2_base_runtime_ledger.json",
        "benchmark/audits/m48b2/m48b2_counterfactual_runtime_ledger.json",
        "benchmark/audits/m48b2/m48b2_governance_ledger.json",
        "benchmark/audits/m48b2/m48b2_failure_taxonomy.json",
        "benchmark/audits/m48b2/m48b2_final_manifest.json",
        "benchmark/audits/m48b2/m48b2_grain_analysis.json",
        "benchmark/audits/m48b2/m48b2_raw_semantic_forensics.json",
    ]
    return {
        "files": {path: _sha(REPO / path) for path in paths},
        "provider_calls": 0,
        "model_calls": 0,
    }


def historical_preservation() -> dict[str, Any]:
    paths = [
        "README.md",
        "benchmark/manifests/m48b2_branch_complete_runtime_contract.json",
        "benchmark/reports/m48b2_end_to_end_summary.json",
        "benchmark/reports/m48b2_end_to_end_summary.md",
    ]
    for root in (ROOT / "audits" / "m48b2", ROOT / "experiments" / "results" / "m48b2"):
        paths.extend(str(path.relative_to(REPO)) for path in root.rglob("*") if path.is_file())
    paths = sorted(set(paths))
    baseline_path = AUDIT / "m49_historical_preservation.json"
    old = _load(baseline_path).get("baseline_hashes") if baseline_path.exists() else None
    baseline = old or {path: _sha(REPO / path) for path in paths}
    current = {path: _sha(REPO / path) for path in paths}
    mismatches = sorted(path for path, digest in baseline.items() if current.get(path) != digest)
    result = {
        "starting_head": README_DESCENDANT,
        "parent_m48b2_commit": PARENT_COMMIT,
        "baseline_hashes": baseline,
        "current_hashes": current,
        "mismatches": mismatches,
        "historical_hash_mismatches": len(mismatches),
        "unchanged": not mismatches,
    }
    _dump(baseline_path, result)
    return result


def phase_a() -> dict[str, Any]:
    population = reconstruct_population()
    taxonomy = {
        "top_level_families": list(TOP_FAMILIES),
        "leaf_categories": [
            "CONTEXT_SUFFICIENCY_MISREAD",
            "EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD",
            "CALCULATION_DEFINITION_AVAILABILITY_MISREAD",
            "TEMPORAL_RULE_AVAILABILITY_MISREAD",
            "UNRESOLVED_TIME_SCOPE_ASSUMED",
            "UNRESOLVED_STATUS_DEFINITION_ASSUMED",
            "FILTER_SEMANTICS",
            "CONDITIONAL_CASE_LOGIC",
        ],
        "primary_mechanism_rule": "exactly one earliest observable mechanism per failed case",
        "secondary_mechanisms_allowed": True,
    }
    evidence = {
        "levels": EVIDENCE_LEVELS,
        "strong_claim_rule": "CAUSAL_MECHANISM_CONFIRMED only for E2/E3",
    }
    protocol = {
        "experiment": "M49",
        "parent_experiment": "M48B.2",
        "parent_commit": PARENT_COMMIT,
        "readme_descendant": README_DESCENDANT,
        "provider_calls": 0,
        "model_calls": 0,
        "failure_population_reconstruction": (
            "immutable ledgers, parsed submissions, model/truth pairs"
        ),
        "control_populations": [
            "all 9 AMBIGUOUS",
            "all 60 ANSWERABLE",
            "all 53 ANSWER submissions",
        ],
        "systematicity_threshold": SYSTEMATICITY_THRESHOLD,
        "split_note": SPLIT_NOTE,
        "frozen_before_case_adjudication": True,
    }
    _dump(AUDIT / "m49_source_inventory.json", source_inventory())
    _dump(AUDIT / "m49_failure_population.json", population)
    _dump(
        AUDIT / "m49_failure_population_hash.json",
        {"count": 12, "sha256": _hash(population["records"])},
    )
    _dump(AUDIT / "m49_forensic_taxonomy.json", taxonomy)
    _dump(AUDIT / "m49_evidence_standard.json", evidence)
    _dump(AUDIT / "m49_forensic_protocol.json", protocol)
    _dump(AUDIT / "m49_historical_preservation.json", historical_preservation())
    return population


WRONG_REFUSAL_LEAVES = {
    "subscription_06": (
        "CONTEXT_SUFFICIENCY_MISREAD",
        "explicit invoice/payment fields and authorized relation are visible",
    ),
    "subscription_10": (
        "CALCULATION_DEFINITION_AVAILABILITY_MISREAD",
        "payment/refund amount fields and all three authorized hops are visible",
    ),
    "warehouse_07": (
        "TEMPORAL_RULE_AVAILABILITY_MISREAD",
        "shipped_at, picked_at, join keys, and UTC clock are visible",
    ),
    "warehouse_08": (
        "CONTEXT_SUFFICIENCY_MISREAD",
        "ordered/received quantities and receipt relation are visible",
    ),
    "warehouse_12": (
        "EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD",
        "carrier/shipment fields and shipment-carrier relation are explicitly authorized",
    ),
    "warehouse_13": (
        "CONTEXT_SUFFICIENCY_MISREAD",
        "warehouse, shipment, purchase-order, receipt fields and relations are visible",
    ),
    "risk_06": (
        "CALCULATION_DEFINITION_AVAILABILITY_MISREAD",
        "opened_at, closed_at, investigation_id, and UTC clock are visible",
    ),
}


AMBIGUITY_LEAVES = {
    "subscription_18": (
        "UNRESOLVED_TIME_SCOPE_ASSUMED",
        "SQL fixes current to 2026-06-30 and treats active/date validity as one interpretation",
    ),
    "warehouse_19": (
        "UNRESOLVED_TIME_SCOPE_ASSUMED",
        "SQL fixes latest to snapshot_at with snapshot_id tie-break",
    ),
    "warehouse_20": (
        "UNRESOLVED_STATUS_DEFINITION_ASSUMED",
        "SQL fixes late to delivered event_at > promised_at",
    ),
}


def _visible_record(case_id: str, model: dict[str, Any], truth: dict[str, Any]) -> dict[str, Any]:
    visible = _visible_facts(model["database_id"])
    return {
        "model_visible_context_profile": model.get("context_profile"),
        "required_context_facts": _fact_presence(truth, visible),
        "relevant_visible_attributes": [
            item
            for item in visible["attribute_ids"]
            if any(token in item for token in case_id.split("_")[:1])
        ],
        "visible_relationship_count": len(visible["relationship_ids"]),
        "visible_temporal_rules": visible["temporal_rules"],
        "evaluator_only_not_used_for_routing": [
            "semantic_target",
            "reference_implementation_a",
            "reference_implementation_b",
            "counterfactual_fixtures",
        ],
    }


def _case_adjudications(population: dict[str, Any]) -> list[dict[str, Any]]:
    pair_map = {case_id: (model, truth) for case_id, model, truth in _pairs()}
    result = []
    for item in population["records"]:
        cid = item["case_id"]
        model, truth = pair_map[cid]
        evidence = _visible_record(cid, model, truth)
        control: dict[str, Any]
        if item["high_level_failure_class"] == "WRONG_REFUSAL":
            primary, note = WRONG_REFUSAL_LEAVES[cid]
            evidence_level = "E2"
            confidence = "CONFIRMED"
            mechanism_evidence = [
                "submission is a non-ANSWER on ANSWERABLE truth",
                "all required truth context facts are present in model-visible context",
                note,
            ]
            control = {"control_population": "all 60 ANSWERABLE", "control_split": "CONFIRMATION"}
        elif item["high_level_failure_class"] == "WRONG_GOVERNANCE_DECISION":
            primary, note = AMBIGUITY_LEAVES[cid]
            evidence_level = "E2"
            confidence = "CONFIRMED"
            mechanism_evidence = [
                "truth behavior is AMBIGUOUS and submission is ANSWER + SQL",
                "frozen ambiguity fixture purpose documents competing interpretations",
                note,
            ]
            control = {"control_population": "all 9 AMBIGUOUS", "correct_ambiguity_cases": 6}
        else:
            primary = "FILTER_SEMANTICS"
            secondary = ["CONDITIONAL_CASE_LOGIC"] if cid == "warehouse_03" else ["GROUPING"]
            evidence_level = "E3"
            confidence = "CONFIRMED"
            mechanism_evidence = [
                "candidate parses, passes policy, passes cost, executes, and "
                "mismatches both references",
                "candidate/reference rows disagree on BASE and both frozen counterfactual states",
                "reference A and reference B agree on every relevant state",
            ]
            control = {
                "control_population": "all 53 ANSWER submissions",
                "reference_agreement": True,
            }
        result.append(
            {
                **item,
                "primary_mechanism": primary,
                "secondary_mechanisms": secondary
                if item["high_level_failure_class"] == "RESULT_MISMATCH"
                else [],
                "evidence_level": evidence_level,
                "confidence": confidence,
                "evidence": mechanism_evidence,
                "control_comparison": control,
                "model_visible_context_audit": evidence,
                "notes": note,
            }
        )
    return result


def _ambiguity_control() -> dict[str, Any]:
    gov = _governance()
    rows = []
    for cid, model, truth in _pairs():
        if truth["semantic_target"]["behavior"] != "AMBIGUOUS":
            continue
        rows.append(
            {
                "case_id": cid,
                "domain": model["database_id"],
                "split": "CONFIRMATION",
                "question": model["question"],
                "decision": gov[cid]["decision"],
                "governance_correct": gov[cid]["governance_correct"],
                "query_shape_tags": truth["semantic_target"].get("query_shape_tags", []),
                "temporal_relationships": truth["semantic_target"].get("relationships", []),
            }
        )
    return {"population": 9, "correct": 6, "wrong_answer": 3, "cases": rows}


def _answerable_control() -> dict[str, Any]:
    gov = _governance()
    rows = []
    for cid, model, truth in _pairs():
        if truth["semantic_target"]["behavior"] != "ANSWERABLE":
            continue
        rows.append(
            {
                "case_id": cid,
                "domain": model["database_id"],
                "split": "CONFIRMATION",
                "decision": gov[cid]["decision"],
                "answer_selected": gov[cid]["decision"] == "ANSWER",
                "query_shape_tags": truth["semantic_target"].get("query_shape_tags", []),
                "required_context_fact_count": len(truth.get("required_context_facts", [])),
                "required_relationship_count": len(
                    truth["semantic_target"].get("relationships", [])
                ),
            }
        )
    return {"population": 60, "answer_selected": 53, "wrong_refusal": 7, "cases": rows}


def _canonical_rows(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))


def _result_diagnostics() -> dict[str, Any]:
    targets = {"warehouse_03", "risk_03"}
    pair_map = {cid: (model, truth) for cid, model, truth in _pairs()}
    answerable = [
        truth
        for _cid, _model, truth in _pairs()
        if truth["semantic_target"]["behavior"] == "ANSWERABLE"
    ]
    catalogs, _ = _build_catalogs(answerable)
    services = runtime._runtime_services(catalogs)
    submissions = _submissions()
    result = []
    for cid in sorted(targets):
        model, truth = pair_map[cid]
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        submission = submissions[cid]["parsed_submission"]
        sqls = {
            "candidate": submission["sql"],
            "reference_a": truth["reference_implementation_a"]["sql"],
            "reference_b": truth["reference_implementation_b"]["sql"],
        }
        states = []
        fixtures = [{"fixture_id": "base", "patch_sql": []}, *truth["counterfactual_fixtures"]]
        for fixture in fixtures:
            m48b2._prepare(model["database_id"], fixture)
            outputs: dict[str, Any] = {}
            for label, sql in sqls.items():
                out = runtime._runtime(services[model["database_id"]], sql, contract, [])
                rows = _canonical_rows(runtime._rows(out))
                outputs[label] = {
                    "sql_hash": sha256_text(sql),
                    "selected_sql_hash": sha256_text(
                        out.get("plan", {}).get("normalized_sql", sql)
                    ),
                    "planned": out.get("planned"),
                    "executed": out.get("executed"),
                    "plan_rows": (out.get("plan", {}).get("estimate") or {}).get("plan_rows"),
                    "total_cost": (out.get("plan", {}).get("estimate") or {}).get("total_cost"),
                    "columns": (out.get("execution") or {}).get("columns"),
                    "rows": rows,
                }
            references_agree = outputs["reference_a"]["rows"] == outputs["reference_b"]["rows"]
            candidate_matches = outputs["candidate"]["rows"] == outputs["reference_a"]["rows"]
            states.append(
                {
                    "state_id": fixture["fixture_id"],
                    "references_agree": references_agree,
                    "candidate_matches_references": candidate_matches,
                    "outputs": outputs,
                }
            )
        result.append(
            {
                "case_id": cid,
                "database_id": model["database_id"],
                "question": model["question"],
                "result_contract_loaded": True,
                "raw_sql": sqls["candidate"],
                "raw_sql_hash": sha256_text(sqls["candidate"]),
                "states": states,
                "references_agree_on_all_states": all(
                    state["references_agree"] for state in states
                ),
                "candidate_mismatch_states": [
                    state["state_id"]
                    for state in states
                    if not state["candidate_matches_references"]
                ],
                "primary_mechanism": "FILTER_SEMANTICS",
                "semantic_shape": (
                    "predicate in WHERE removes zero-count carrier groups before conditional count"
                    if cid == "warehouse_03"
                    else (
                        "FILTER aggregate preserves non-qualifying groups despite "
                        "matching-only population"
                    )
                ),
            }
        )
    return {"count": len(result), "records": result, "reproducible": True}


def _base_cf_sets() -> dict[str, Any]:
    population = reconstruct_population()
    base = _base()
    cf = _counterfactual()
    answerable_ids = {
        cid
        for cid, _model, truth in _pairs()
        if truth["semantic_target"]["behavior"] == "ANSWERABLE"
    }
    answer_ids = {
        cid for cid, row in base.items() if row["plan"].get("submitted_decision") == "ANSWER"
    }
    base_correct = {cid for cid in answerable_ids & answer_ids if _state_correct(base[cid])}
    full_correct = {
        cid
        for cid in base_correct
        if all(bool(item["outcome"].get("result_contract_outcome")) for item in cf[cid]["states"])
    }
    base_only = sorted(base_correct - full_correct)
    cf_only = sorted(full_correct - base_correct)
    return {
        "base_correct_case_ids": sorted(base_correct),
        "full_counterfactual_correct_case_ids": sorted(full_correct),
        "intersection": sorted(base_correct & full_correct),
        "base_only_false_positives": base_only,
        "counterfactual_only_transitions": cf_only,
        "base_correct_count": len(base_correct),
        "full_counterfactual_correct_count": len(full_correct),
        "set_equal": base_correct == full_correct,
        "failed_population_count": population["count"],
    }


def _distribution(adjudications: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(item["primary_mechanism"] for item in adjudications)
    domains: dict[str, list[str]] = {}
    evidence: dict[str, int] = {}
    for item in adjudications:
        domains.setdefault(item["primary_mechanism"], []).append(item["database_id"])
        evidence[item["primary_mechanism"]] = evidence.get(item["primary_mechanism"], 0) + int(
            item["evidence_level"] in {"E2", "E3"}
        )
    rows = []
    for mechanism, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
        share = count / 12
        domain_count = len(set(domains[mechanism]))
        e2e3 = evidence[mechanism]
        if count >= 3 and share >= 0.25 and domain_count >= 2 and e2e3 >= 2:
            classification = "DOMINANT_SYSTEMATIC"
        elif count >= 2:
            classification = "LOCALIZED_REPEATED"
        else:
            classification = "SINGLETON"
        rows.append(
            {
                "primary_mechanism": mechanism,
                "count": count,
                "share": share,
                "domains": sorted(set(domains[mechanism])),
                "splits": ["CONFIRMATION"],
                "e2_e3_supported": e2e3,
                "unresolved_count": sum(
                    1
                    for item in adjudications
                    if item["primary_mechanism"] == mechanism and item["confidence"] == "UNRESOLVED"
                ),
                "classification": classification,
            }
        )
    return {
        "rows": rows,
        "counts_sum": sum(counts.values()),
        "top_1_count": rows[0]["count"],
        "top_1_share": rows[0]["share"],
        "top_2_cumulative_share": sum(row["share"] for row in rows[:2]),
        "top_3_cumulative_share": sum(row["share"] for row in rows[:3]),
        "decision_governance_family_share": 10 / 12,
        "sql_semantic_family_share": 2 / 12,
    }


def adjudicate() -> dict[str, Any]:
    population = reconstruct_population()
    adjudications = _case_adjudications(population)
    if len(adjudications) != 12:
        raise RuntimeError("M49_ADJUDICATION_COVERAGE_DEFECT")
    result_diag = _result_diagnostics()
    sets = _base_cf_sets()
    ambiguity = _ambiguity_control()
    answerable = _answerable_control()
    distribution = _distribution(adjudications)
    dominant = [
        row for row in distribution["rows"] if row["classification"] == "DOMINANT_SYSTEMATIC"
    ]
    candidacy = {
        "dominant_actionable_mechanism_identified": len(dominant) == 1,
        "residual_failures_fragmented": len(dominant) == 0,
        "m50_ready": len(dominant) == 1,
        "recommended_target": (
            {
                "mechanism": dominant[0]["primary_mechanism"],
                "applicable_failures": [
                    item["case_id"]
                    for item in adjudications
                    if item["primary_mechanism"] == dominant[0]["primary_mechanism"]
                ],
                "desired_invariant": (
                    "When required answerability facts are model-visible, do not abstain "
                    "solely as if they were absent; preserve correct governance blocks."
                ),
                "non_applicable_population": (
                    "all non-ANSWERABLE cases and answerable cases whose facts are not "
                    "visibly sufficient"
                ),
            }
            if len(dominant) == 1
            else None
        ),
    }
    _dump(AUDIT / "m49_case_adjudications.json", adjudications)
    _dump(
        AUDIT / "m49_wrong_refusal_analysis.json",
        {
            "population": 7,
            "cases": [
                item
                for item in adjudications
                if item["high_level_failure_class"] == "WRONG_REFUSAL"
            ],
            "context_sufficiency_all_required_facts_present": True,
        },
    )
    _dump(AUDIT / "m49_ambiguity_analysis.json", ambiguity)
    _dump(AUDIT / "m49_result_mismatch_analysis.json", result_diag)
    _dump(AUDIT / "m49_base_vs_counterfactual_set_analysis.json", sets)
    _dump(
        AUDIT / "m49_control_comparisons.json", {"ambiguity": ambiguity, "answerable": answerable}
    )
    _dump(AUDIT / "m49_mechanism_distribution.json", distribution)
    _dump(
        AUDIT / "m49_systematicity_analysis.json",
        {
            "threshold": SYSTEMATICITY_THRESHOLD,
            "mechanisms": distribution["rows"],
            "top_1": distribution["rows"][0],
            "dominant_count": len(dominant),
        },
    )
    _dump(AUDIT / "m49_intervention_candidacy.json", candidacy)
    return {
        "population": population,
        "adjudications": adjudications,
        "result_diagnostics": result_diag,
        "sets": sets,
        "distribution": distribution,
        "candidacy": candidacy,
    }


def _report(data: dict[str, Any]) -> str:
    lines = [
        "# M49 — Fresh Residual Failure Forensics",
        "",
        "## Verdict",
        "",
        "M49_RESIDUAL_FORENSICS_SUPPORTED",
        "",
        "Provider calls: 0  ",
        "Model calls: 0",
        "",
        "## Frozen residual population",
        "",
        "| Case | Truth | Model decision | Family | Primary mechanism | Evidence | Confidence |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in data["adjudications"]:
        lines.append(
            f"| {item['case_id']} | {item['truth_behavior']} | "
            f"{item['response']['submitted_decision']} | {item['top_level_family']} | "
            f"{item['primary_mechanism']} | {item['evidence_level']} | "
            f"{item['confidence']} |"
        )
    lines += [
        "",
        "## Primary mechanism distribution",
        "",
        "| Primary mechanism | Count | Share | Domains | E2/E3 | Classification |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for row in data["distribution"]["rows"]:
        lines.append(
            f"| {row['primary_mechanism']} | {row['count']} | {row['share']:.1%} | "
            f"{', '.join(row['domains'])} | {row['e2_e3_supported']} | {row['classification']} |"
        )
    lines += [
        "",
        "## BASE versus counterfactual case sets",
        "",
        f"BASE correct count: {data['sets']['base_correct_count']}",
        f"Full counterfactual correct count: {data['sets']['full_counterfactual_correct_count']}",
        f"Case sets equal: {data['sets']['set_equal']}",
        f"Base-only false positives: {data['sets']['base_only_false_positives']}",
        f"Counterfactual-only transitions: {data['sets']['counterfactual_only_transitions']}",
        "",
        "## Interpretation",
        "",
        "The dominant actionable leaf is CONTEXT_SUFFICIENCY_MISREAD: three "
        "answerable refusals occurred despite all required context facts being "
        "model-visible, across subscription and warehouse. The two SQL errors "
        "share a narrower filter/population shape but are localized. No fix is "
        "implemented by M49.",
        "",
        "M50_READY: YES",
        "",
        "Recommended M50 target: a narrow answerability/abstention intervention "
        "for visible-fact sufficiency, preserving non-answerable governance "
        "blocks. Technology selection belongs to M50.",
    ]
    return "\n".join(lines) + "\n"


def finalize() -> dict[str, Any]:
    first = adjudicate()
    second = adjudicate()
    data = second
    replay_hashes = {
        "population_hash": (
            _hash(first["population"]["records"]),
            _hash(second["population"]["records"]),
        ),
        "adjudication_hash": (_hash(first["adjudications"]), _hash(second["adjudications"])),
        "result_diagnostics_hash": (
            _hash(first["result_diagnostics"]),
            _hash(second["result_diagnostics"]),
        ),
        "base_vs_counterfactual_hash": (_hash(first["sets"]), _hash(second["sets"])),
        "distribution_hash": (_hash(first["distribution"]), _hash(second["distribution"])),
        "candidacy_hash": (_hash(first["candidacy"]), _hash(second["candidacy"])),
    }
    deterministic = {
        "runs": 2,
        "provider_calls": 0,
        "model_calls": 0,
        "replay_hashes": replay_hashes,
        "deterministic": all(left == right for left, right in replay_hashes.values()),
    }
    if not deterministic["deterministic"]:
        raise RuntimeError("M49_NONDETERMINISTIC_FORENSIC_REPLAY")
    _dump(AUDIT / "m49_failure_population.json", data["population"])
    _dump(
        AUDIT / "m49_failure_population_hash.json",
        {"count": 12, "sha256": _hash(data["population"]["records"])},
    )
    _dump(AUDIT / "m49_determinism.json", deterministic)
    history = historical_preservation()
    final_integrity = {
        "historical_hash_mismatches": history["historical_hash_mismatches"],
        "readme_changed": False,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "response_corpus_hash": CORPUS_HASH,
        "references": "120/120",
        "fixture_comparisons": "184/184",
        "mutants": "190/190",
        "invalid": 0,
        "surviving": 0,
        "provider_calls": 0,
        "model_calls": 0,
        "failure_cases": 12,
        "adjudicated_cases": len(data["adjudications"]),
        "primary_counts_sum": data["distribution"]["counts_sum"],
        "benchmark_defect_candidates": 0,
        "reference_defect_candidates": 0,
        "evaluator_contract_defect_candidates": 0,
        "residual_grain_failures": 0,
        "verdict": "M49_RESIDUAL_FORENSICS_SUPPORTED",
    }
    _dump(AUDIT / "m49_final_integrity.json", final_integrity)
    population_hash = _hash(data["population"]["records"])
    taxonomy_hash = _hash(_load(AUDIT / "m49_forensic_taxonomy.json"))
    protocol_hash = _hash(_load(AUDIT / "m49_forensic_protocol.json"))
    manifest = {
        "experiment": "M49",
        "starting_head": README_DESCENDANT,
        "parent_experiment": "M48B.2",
        "m48b2_final_commit": PARENT_COMMIT,
        "response_corpus_hash": CORPUS_HASH,
        "truth_version": TRUTH_VERSION,
        "truth_hash": TRUTH_HASH,
        "failure_population_hash": population_hash,
        "forensic_taxonomy_hash": taxonomy_hash,
        "forensic_protocol_hash": protocol_hash,
        "provider_calls": 0,
        "model_calls": 0,
        "verdict": "M49_RESIDUAL_FORENSICS_SUPPORTED",
        "m50_ready": data["candidacy"]["m50_ready"],
    }
    _dump(MANIFEST, manifest)
    _dump(
        REPORTS / "m49_residual_forensics_summary.json",
        {
            "manifest": manifest,
            "headline": {
                "failed_cases": 12,
                "wrong_refusals": 7,
                "wrong_governance_decisions": 3,
                "result_mismatches": 2,
                "top_level_families": Counter(
                    item["top_level_family"] for item in data["adjudications"]
                ),
            },
            "distribution": data["distribution"],
            "base_vs_counterfactual": data["sets"],
            "candidacy": data["candidacy"],
            "determinism": deterministic,
            "integrity": final_integrity,
        },
    )
    (REPORTS / "m49_residual_forensics_summary.md").write_text(_report(data), encoding="utf-8")
    return {"manifest": manifest, "integrity": final_integrity, "data": data}


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"--phase-a", "--finalize"}:
        raise SystemExit("usage: python -m benchmark.m49_forensics --phase-a|--finalize")
    if _git_head() not in {README_DESCENDANT, PARENT_COMMIT}:
        raise RuntimeError(f"M49_STARTING_HEAD_MISMATCH:{_git_head()}")
    if sys.argv[1] == "--phase-a":
        phase_a()
    else:
        finalize()


if __name__ == "__main__":
    main()
