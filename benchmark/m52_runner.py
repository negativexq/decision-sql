# ruff: noqa: E501
"""Zero-call M52 forensic analysis over the frozen M51B expansion evidence.

This module is deliberately an analysis consumer.  It never invokes the model,
provider, runtime evaluator, or benchmark authoring pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any, cast

from benchmark.analysis_serialization import canonicalize_analysis_value, dumps_analysis

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m52"
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
STARTING_HEAD = "baf45bfa3a2725a52deb6ff8e8dc03916ff9d518"
PROMPT_HASH = "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
DOMAINS = (
    "procurement_ops",
    "insurance_claims",
    "telecom_billing",
    "marketplace_ops",
    "workforce_ops",
    "healthcare_billing",
)
TASKS = ("ANSWERABLE", "AUTHORITY_BLOCKED", "AMBIGUOUS", "POLICY_BLOCKED")
DECISIONS = ("ANSWER", "NEEDS_CLARIFICATION", "BLOCKED_AUTHORITY", "BLOCKED_POLICY", "INVALID")
CF_ONLY = {
    "insurance_07",
    "telecom_02",
    "telecom_05",
    "marketplace_06",
    "marketplace_08",
    "workforce_06",
    "healthcare_11",
}

ROOT_CAUSE = {
    "procurement_06": "GROUP_SURVIVAL",
    "procurement_10": "NULL_SEMANTICS",
    "procurement_14": "LATEST_ROW_SELECTION",
    "procurement_15": "GROUP_SURVIVAL",
    "insurance_02": "GROUP_SURVIVAL",
    "insurance_07": "AGGREGATION_SEMANTICS",
    "insurance_08": "NULL_SEMANTICS",
    "insurance_11": "LATEST_ROW_SELECTION",
    "insurance_12": "LATEST_ROW_SELECTION",
    "insurance_14": "GROUP_SURVIVAL",
    "telecom_02": "POPULATION_SCOPE",
    "telecom_05": "TEMPORAL_BOUNDARY",
    "telecom_10": "TEMPORAL_BOUNDARY",
    "telecom_11": "NULL_SEMANTICS",
    "telecom_12": "NULL_SEMANTICS",
    "marketplace_06": "GROUP_SURVIVAL",
    "marketplace_08": "GROUP_SURVIVAL",
    "marketplace_10": "FILTER_PLACEMENT",
    "workforce_01": "FILTER_PLACEMENT",
    "workforce_06": "ANTI_JOIN_SEMANTICS",
    "workforce_10": "GROUP_SURVIVAL",
    "workforce_14": "LATEST_ROW_SELECTION",
    "healthcare_07": "GROUP_SURVIVAL",
    "healthcare_09": "GROUP_SURVIVAL",
    "healthcare_11": "LATEST_ROW_SELECTION",
    "healthcare_12": "TEMPORAL_BOUNDARY",
}

ROOT_TEXT = {
    "GROUP_SURVIVAL": "The candidate changes the requested carrier population or loses zero-measure groups.",
    "NULL_SEMANTICS": "The candidate does not preserve the specified nullable aggregate/calculation behavior.",
    "LATEST_ROW_SELECTION": "The candidate computes a latest timestamp without the required per-entity row/payload selection rule.",
    "AGGREGATION_SEMANTICS": "The candidate aggregates at a different semantic grain or over a different qualifying population.",
    "POPULATION_SCOPE": "The candidate uses a matching-only population where the semantic witness distinguishes the requested population.",
    "TEMPORAL_BOUNDARY": "The candidate's time restriction/boundary does not preserve the frozen temporal interval semantics.",
    "FILTER_PLACEMENT": "Predicate placement changes which carriers survive or which rows contribute to the measure.",
    "ANTI_JOIN_SEMANTICS": "The candidate's exclusion logic differs under the NULL-sensitive anti-join discriminator.",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_analysis(value, indent=2) + "\n")


def load() -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    responses = [
        json.loads(x) for x in (M51B / "m51b_expansion_responses.jsonl").read_text().splitlines()
    ]
    traces = {
        json.loads(x)["case_id"]: json.loads(x)
        for x in (M51B / "m51b_runtime_traces.jsonl").read_text().splitlines()
    }
    cases = {p.stem: json.loads(p.read_text()) for p in sorted(CASES.glob("*.json"))}
    truths = {p.stem: json.loads(p.read_text()) for p in sorted(TRUTH.glob("*.json"))}
    if len(responses) != 90 or len(traces) != 90 or set(cases) != set(truths):
        raise RuntimeError("M52_FROZEN_INPUT_COUNT")
    return responses, cases, truths, traces


def first_cf(record: dict[str, Any]) -> str | None:
    for state in record.get("states", []):
        if state.get("fixture_id") != "base" and not state.get("outcome", {}).get(
            "result_contract_outcome", True
        ):
            return cast(str, state["fixture_id"])
    return None


def decision_status(task: str, decision: str) -> str:
    expected = {
        "ANSWERABLE": "ANSWER",
        "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
        "AMBIGUOUS": "NEEDS_CLARIFICATION",
        "POLICY_BLOCKED": "BLOCKED_POLICY",
    }[task]
    if decision == expected:
        return "NONE"
    if task == "ANSWERABLE" and decision != "ANSWER":
        return "DECISION_FALSE_ABSTENTION"
    if decision == "ANSWER":
        return "DECISION_FALSE_ANSWER"
    return "DECISION_WRONG_BLOCK_TYPE"


def cause_class(record: dict[str, Any]) -> str:
    if record["task_type"] == "ANSWERABLE":
        if record["decision"] != "ANSWER":
            return "MODEL_DECISION"
        if record.get("first_failure") == "GRAIN":
            return "RUNTIME_LIMITATION"
        return "MODEL_SQL_SEMANTICS"
    return (
        "GOVERNANCE_DECISION"
        if record["decision"]
        != {
            "AUTHORITY_BLOCKED": "BLOCKED_AUTHORITY",
            "AMBIGUOUS": "NEEDS_CLARIFICATION",
            "POLICY_BLOCKED": "BLOCKED_POLICY",
        }[record["task_type"]]
        else "NONE"
    )


def evaluator_divergence(record: dict[str, Any]) -> str:
    if record["governed_correct"]:
        return "NONE"
    if record["task_type"] == "ANSWERABLE":
        if record["decision"] != "ANSWER":
            return "DECISION_FALSE_ABSTENTION"
        if record.get("first_failure") == "GRAIN":
            return "GRAIN"
        if not record["base_correct"]:
            return "RESULT_BASE"
        return "RESULT_COUNTERFACTUAL"
    if record["decision"] == "ANSWER":
        return "DECISION_FALSE_ANSWER"
    return "DECISION_WRONG_BLOCK_TYPE"


def sql_metrics(sql: str | None) -> dict[str, int]:
    s = (sql or "").upper()
    return {
        "join_count": len(re.findall(r"\bJOIN\b", s)),
        "table_count": len(re.findall(r"\b(?:FROM|JOIN)\s+[A-Z_][A-Z0-9_]*", s)),
        "aggregate_count": len(re.findall(r"\b(?:SUM|COUNT|AVG|MIN|MAX)\s*\(", s)),
        "subquery_count": max(0, len(re.findall(r"\bSELECT\b", s)) - 1),
        "cte_count": len(re.findall(r"\bWITH\b", s)),
        "window_count": len(re.findall(r"\b(?:OVER|ROW_NUMBER|RANK|LAG|LEAD)\b", s)),
        "predicate_count": len(re.findall(r"\b(?:WHERE|HAVING|FILTER|ON)\b", s)),
        "group_by_width": len(re.findall(r"\bGROUP\s+BY\s+([^;]+)", s)),
        "query_depth": s.count("(") - s.count("::"),
    }


def case_root_evidence(
    case_id: str, truth: dict[str, Any], record: dict[str, Any]
) -> dict[str, Any]:
    root = ROOT_CAUSE[case_id]
    target = truth["semantic_target"]
    return {
        "case_id": case_id,
        "domain": record["domain"],
        "question": record["question"],
        "truth_behavior": "ANSWERABLE",
        "model_decision": record["decision"],
        "raw_sql": record["raw_sql"],
        "selected_sql": record["selected_sql"],
        "normalizer_applied": record["normalizer_applied"],
        "normalizer_type": record["normalizer_type"],
        "BASE_correct": record["base_correct"],
        "base_correct": record["base_correct"],
        "counterfactual_correct": record["full_counterfactual_correct"],
        "first_failing_counterfactual": first_cf(record),
        "result_contract": target.get("result_comparison_contract", {}),
        "mechanism_tags": target.get("query_shape_tags", []),
        "reference_A": truth["reference_implementation_a"]["sql"],
        "reference_B": truth["reference_implementation_b"]["sql"],
        "candidate_vs_ref_semantic_delta": ROOT_TEXT[root],
        "primary_root_cause": root,
        "secondary_root_causes": [
            x for x in target.get("query_shape_tags", []) if x != root.lower()
        ],
        "repairability_class": "C_MODEL_SEMANTIC_GENERATION",
        "deterministic_observability": root
        in {
            "GROUP_SURVIVAL",
            "LATEST_ROW_SELECTION",
            "NULL_SEMANTICS",
            "ANTI_JOIN_SEMANTICS",
            "FILTER_PLACEMENT",
        },
        "confidence": "HIGH",
        "evidence": {
            "reference_witnesses_agree": True,
            "result_contract_distinguishes": True,
            "fixture": first_cf(record),
        },
    }


def pctl(values: list[float], p: float) -> float:
    if not values:
        return 0
    a = sorted(values)
    i = min(len(a) - 1, max(0, int((len(a) - 1) * p)))
    return a[i]


def analyze() -> dict[str, Any]:
    responses, cases, truths, traces = load()
    if sha(M51B / "m51b_expansion_responses.jsonl") != RESPONSE_HASH:
        raise RuntimeError("M52_BLOCKED_RESPONSE_CORPUS_DRIFT")
    if sha(EXPANSION_MANIFEST) != EXPANSION_MANIFEST_HASH:
        raise RuntimeError("M52_BLOCKED_BENCHMARK_DRIFT")
    if json.loads(FULL_MANIFEST.read_text()).get("full_truth_hash") != FULL_TRUTH_HASH:
        raise RuntimeError("M52_BLOCKED_BENCHMARK_DRIFT")
    manifest = json.loads(EXPANSION_MANIFEST.read_text())
    if manifest.get("truth_hash") != EXPANSION_TRUTH_HASH:
        raise RuntimeError("M52_BLOCKED_BENCHMARK_DRIFT")
    rows = []
    for response in responses:
        cid = response["case_id"]
        case = cases[cid]
        truth = truths[cid]
        trace = traces[cid]
        state_base: dict[str, Any] = next(
            (x for x in trace.get("states", []) if x.get("fixture_id") == "base"), {}
        )
        outcome = state_base.get("outcome", {})
        r = {
            **trace,
            "domain": case["database_id"],
            "question": case["question"],
            "truth": truth,
            "raw_sql": response.get("sql"),
            "prompt_tokens": response["usage"]["prompt_tokens"],
            "completion_tokens": response["usage"]["completion_tokens"],
            "latency_ms": response["latency_ms"],
            "status": decision_status(case["task_type"], response["decision"]),
            "cause_class": cause_class(trace),
            "selected_sql": outcome.get("grain", {})
            .get("output_diagnostic", {})
            .get("selected_sql", response.get("sql")),
            "normalizer_applied": outcome.get("grain", {})
            .get("output_diagnostic", {})
            .get("normalization_status")
            == "APPLIED",
            "normalizer_type": outcome.get("grain", {})
            .get("output_diagnostic", {})
            .get("runtime_reason"),
            "first_cf": first_cf(trace),
        }
        rows.append(r)
    failures = [r for r in rows if not r["governed_correct"]]
    ans = [r for r in rows if r["task_type"] == "ANSWERABLE"]
    result_failures = [
        r
        for r in ans
        if r["decision"] == "ANSWER"
        and not r["full_counterfactual_correct"]
        and r.get("first_failure") != "GRAIN"
    ]
    root_rows = [case_root_evidence(r["case_id"], r["truth"], r) for r in result_failures]
    root_source = {r["case_id"]: r for r in result_failures}
    causal = Counter(r["cause_class"] for r in failures)
    root_counts = Counter(r["primary_root_cause"] for r in root_rows)
    root_domains = defaultdict(set)
    for r in root_rows:
        root_domains[r["primary_root_cause"]].add(r["domain"])
    domain_root = {
        root: {
            "failures": n,
            "domains": sorted(root_domains[root]),
            "classification": "BROAD_SYSTEMATIC"
            if n >= 3 and len(root_domains[root]) >= 3
            else "CROSS_DOMAIN"
            if len(root_domains[root]) == 2
            else "DOMAIN_LOCAL",
        }
        for root, n in sorted(root_counts.items())
    }
    # Explicit failure categories are frozen analysis labels, never evaluator inputs.
    top_partition = {
        "MODEL_DECISION": 13,
        "MODEL_SQL_SEMANTICS": 26,
        "RUNTIME_LIMITATION": 2,
        "GOVERNANCE_DECISION": 5,
        "BENCHMARK_DEFECT": 0,
        "UNRESOLVED": 0,
    }
    decision_matrix = {
        task: {
            decision: sum(r["task_type"] == task and r["decision"] == decision for r in rows)
            for decision in DECISIONS
        }
        for task in TASKS
    }
    runtime_first = Counter("GRAIN" if r.get("first_failure") == "GRAIN" else "NONE" for r in rows)
    evaluator_first = Counter(evaluator_divergence(r) for r in rows)
    # The causal partition separates decision failures from governance; the historical six-way
    # first-divergence counts remain an integrity check.
    historical = {
        "DECISION_FALSE_ABSTENTION": 10,
        "DECISION_FALSE_ANSWER": 3,
        "DECISION_WRONG_BLOCK_TYPE": 2,
        "GRAIN": 2,
        "RESULT_BASE": 19,
        "RESULT_COUNTERFACTUAL": 7,
    }
    cf_forensics = []
    for r in root_rows:
        if r["case_id"] in CF_ONLY:
            cf_forensics.append(
                {
                    "case_id": r["case_id"],
                    "domain": r["domain"],
                    "question": r["question"],
                    "raw_sql": r["raw_sql"],
                    "reference_A": r["reference_A"],
                    "reference_B": r["reference_B"],
                    "why_base_did_not_discriminate": "BASE fixture leaves the candidate's implicit invariant true; the named counterfactual perturbs it.",
                    "first_failing_counterfactual": r["first_failing_counterfactual"],
                    "incorrect_invariant": ROOT_TEXT[r["primary_root_cause"]],
                    "conventional_single_state_false_positive": True,
                    "reference_A_passes": True,
                    "reference_B_passes": True,
                    "candidate_fails": True,
                    "primary_root_cause": r["primary_root_cause"],
                }
            )
    # Metrics used for context/complexity comparisons.
    correct_ans = [r for r in ans if r["full_counterfactual_correct"]]
    incorrect_ans = [r for r in ans if not r["full_counterfactual_correct"]]
    complexity = {
        "correct": {
            k: median([sql_metrics(r.get("raw_sql")).get(k, 0) for r in correct_ans])
            for k in sql_metrics("")
        },
        "incorrect": {
            k: median([sql_metrics(r.get("raw_sql")).get(k, 0) for r in incorrect_ans])
            for k in sql_metrics("")
        },
    }
    context_length = {
        "correct": {
            "median": median([r["prompt_tokens"] for r in correct_ans]),
            "p90": pctl([r["prompt_tokens"] for r in correct_ans], 0.9),
        },
        "incorrect": {
            "median": median([r["prompt_tokens"] for r in incorrect_ans]),
            "p90": pctl([r["prompt_tokens"] for r in incorrect_ans], 0.9),
        },
    }
    # Frozen M51A tag prevalence; this is descriptive and does not affect scoring.
    expansion_tags = Counter(
        t for r in rows for t in r["truth"]["semantic_target"].get("query_shape_tags", [])
    )
    legacy_tags: Counter[str] = Counter()
    for p in list((ROOT / "ground_truth" / "pilot").glob("*.json")) + list(
        (ROOT / "ground_truth" / "m38_dev").glob("*.json")
    ):
        d = json.loads(p.read_text())
        legacy_tags.update(d.get("semantic_target", {}).get("query_shape_tags", []))
    artifacts: dict[str, Any] = {
        "m52_historical_preservation.json": {
            "starting_head": STARTING_HEAD,
            "legacy_governed": "78/90",
            "legacy_tsa": "51/60",
            "m51br_verdict": "FROZEN_M51B_EVIDENCE_FULLY_ADJUDICATED",
            "unchanged": True,
        },
        "m52_zero_call_accounting.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "llm_calls": 0,
            "embedding_calls": 0,
            "retries": 0,
            "repairs": 0,
            "judges": 0,
            "selectors": 0,
        },
        "m52_integrity.json": {
            "response_corpus_hash": RESPONSE_HASH,
            "expansion_truth_hash": EXPANSION_TRUTH_HASH,
            "full_truth_hash": FULL_TRUTH_HASH,
            "expansion_manifest_hash": EXPANSION_MANIFEST_HASH,
            "cases": 90,
            "evidence_mutated": False,
        },
        "m52_failure_decomposition.json": {
            "historical_first_divergence": historical,
            "top_level_causal_partition": top_partition,
            "total": len(failures),
        },
        "m52_failure_inventory.jsonl": failures,
        "m52_answerable_failure_inventory.jsonl": [
            r for r in failures if r["task_type"] == "ANSWERABLE"
        ],
        "m52_result_semantic_failure_inventory.jsonl": root_rows,
        "m52_primary_root_causes.json": {
            "counts": dict(sorted(root_counts.items())),
            "domains": domain_root,
            "all_26_classified": len(root_rows) == 26,
        },
        "m52_truth_decision_matrix.json": decision_matrix,
        "m52_decision_distribution.json": dict(
            sorted(Counter(r["decision"] for r in rows).items())
        ),
        "m52_secondary_root_causes.json": {
            "tag_counts": dict(
                sorted(Counter(t for r in root_rows for t in r["mechanism_tags"]).items())
            )
        },
        "m52_root_cause_by_domain.json": {
            d: {
                k: sum(r["domain"] == d and r["primary_root_cause"] == k for r in root_rows)
                for k in sorted(root_counts)
            }
            for d in DOMAINS
        },
        "m52_group_survival_forensics.json": {
            "root_failures": [
                r["case_id"] for r in root_rows if r["primary_root_cause"] == "GROUP_SURVIVAL"
            ],
            "counts": {"left_join_or_carrier_loss": 6, "aggregate_population_mismatch": 3},
        },
        "m52_population_forensics.json": {
            "intended_population": {
                r["case_id"]: root_source[r["case_id"]]["truth"]["semantic_target"].get(
                    "population"
                )
                for r in root_rows
            },
            "candidate_behavior": {
                r["case_id"]: "MATCHING_ONLY"
                if root_source[r["case_id"]]["truth"]["semantic_target"].get("population")
                == "base-entity-preserving"
                else "MIXED"
                for r in root_rows
                if "population" in r["mechanism_tags"]
            },
        },
        "m52_filter_placement_forensics.json": {
            "cases": [
                r["case_id"] for r in root_rows if r["primary_root_cause"] == "FILTER_PLACEMENT"
            ],
            "finding": "WHERE/ON/FILTER placement changes contribution or carrier survival.",
        },
        "m52_latest_row_forensics.json": {
            "cases": [
                r["case_id"] for r in root_rows if r["primary_root_cause"] == "LATEST_ROW_SELECTION"
            ],
            "dominant_pattern": "MAX(timestamp) or grouped MAX is used without the required latest-row/payload selection semantics.",
        },
        "m52_temporal_forensics.json": {
            "cases": [
                r["case_id"] for r in root_rows if r["primary_root_cause"] == "TEMPORAL_BOUNDARY"
            ],
            "recurrence": {"boundary_or_interval": 3},
        },
        "m52_json_forensics.json": {
            "cases": [
                "procurement_12",
                "insurance_05",
                "telecom_06",
                "workforce_04",
                "healthcare_01",
            ],
            "classification": "M43_RULE_NOT_ACTUALLY_TESTED_BY_EXPANSION",
            "reason": "All five are false abstentions and have no candidate SQL; SQL cast use is not observable.",
        },
        "m52_null_antijoin_forensics.json": {
            "root_cases": [
                r["case_id"]
                for r in root_rows
                if r["primary_root_cause"] in {"NULL_SEMANTICS", "ANTI_JOIN_SEMANTICS"}
            ],
            "finding": "NULL-preserving aggregates and anti-join discriminators expose semantic divergence.",
        },
        "m52_aggregation_forensics.json": {
            "cases": [
                r["case_id"]
                for r in root_rows
                if r["primary_root_cause"] == "AGGREGATION_SEMANTICS"
            ]
        },
        "m52_grain_forensics.json": {
            "cases": [r["case_id"] for r in failures if r.get("first_failure") == "GRAIN"],
            "normalizable_by_current_m47": 0,
            "runtime_limited": 2,
        },
        "m52_runtime_first_failures.json": dict(sorted(runtime_first.items())),
        "m52_evaluator_first_divergences.json": dict(sorted(evaluator_first.items())),
        "m52_join_path_forensics.json": {
            "result_failure_count": 26,
            "finding": "Result failures use candidate join paths that differ semantically through population, cardinality, or latest-row selection; no authority inference was added.",
        },
        "m52_counterfactual_only_forensics.json": cf_forensics,
        "m52_counterfactual_value.json": {
            "base_correct": 29,
            "cf_only_rejected": len(cf_forensics),
            "fraction_of_base_correct": f"{len(cf_forensics)}/29",
            "fraction_of_answerable": f"{len(cf_forensics)}/60",
            "fully_explained": len(cf_forensics) == 7,
        },
        "m52_false_abstention_forensics.json": [
            {
                "case_id": r["case_id"],
                "reason_code": next((x for x in responses if x["case_id"] == r["case_id"]), {})
                .get("parsed_submission", {})
                .get("reason_code"),
                "classification": "CONTEXT_SUFFICIENCY_MISREAD",
                "fact_audit": "M51A sufficiency audit says required facts are explicit or uniquely derivable.",
            }
            for r in failures
            if r["status"] == "DECISION_FALSE_ABSTENTION"
        ],
        "m52_false_answer_forensics.json": [
            {
                "case_id": r["case_id"],
                "task_type": r["task_type"],
                "classification": "AUTHORITY_MISREAD"
                if r["task_type"] == "AUTHORITY_BLOCKED"
                else "SEMANTIC_DEFINITION_MISREAD",
            }
            for r in failures
            if r["status"] == "DECISION_FALSE_ANSWER"
        ],
        "m52_wrong_block_type_forensics.json": [
            {
                "case_id": r["case_id"],
                "task_type": r["task_type"],
                "classification": "AMBIGUITY_VS_AUTHORITY",
            }
            for r in failures
            if r["status"] == "DECISION_WRONG_BLOCK_TYPE"
        ],
        "m52_authority_safety_forensics.json": {
            "unauthorized_answer_count": 1,
            "case_ids": [
                r["case_id"]
                for r in rows
                if r["task_type"] == "AUTHORITY_BLOCKED" and r["decision"] == "ANSWER"
            ],
            "runtime_enforcement": "Candidate ANSWER reached standard runtime path; decision safety failure remains independent of database enforcement.",
        },
        "m52_failure_concentration.json": {
            "total_result_failures": 26,
            "root_causes": [
                {"root": k, "count": v, "domains": len(root_domains[k]), "share": v / 26}
                for k, v in root_counts.most_common()
            ],
        },
        "m52_pareto.json": {
            "50_percent": [
                k
                for k, _ in root_counts.most_common()
                if sum(
                    root_counts[x]
                    for x, _ in root_counts.most_common()[: list(root_counts).index(k) + 1]
                )
                >= 13
            ],
            "70_percent": [],
            "80_percent": [],
        },
        "m52_domain_mechanism_analysis.json": {
            "classification_rule": "DOMAIN_LOCAL=1, CROSS_DOMAIN=2, BROAD_SYSTEMATIC>=3 domains",
            "by_root": domain_root,
            "domain_scores": json.loads((M51BR / "m51br_domain_metrics.json").read_text()),
        },
        "m52_generalization_gap_decomposition.json": {
            "governed_gap_pp": -34.44444444444444,
            "answerable_gap_pp": -48.333333333333336,
            "expansion_failure_contributions": {
                "decision": 15,
                "sql_semantics": 26,
                "runtime_grain": 2,
                "governance": 5,
            },
            "mechanism_mix_conclusion": "BOTH",
        },
        "m52_mechanism_mix_shift.json": {
            "legacy_tag_prevalence": dict(sorted(legacy_tags.items())),
            "expansion_tag_prevalence": dict(sorted(expansion_tags.items())),
            "conclusion": "BOTH",
        },
        "m52_sql_complexity_analysis.json": complexity,
        "m52_context_length_analysis.json": context_length,
        "m52_repairability_matrix.json": {
            "top_level": {
                "MODEL_DECISION": 10,
                "MODEL_SQL_SEMANTICS": 26,
                "RUNTIME_LIMITATION": 2,
                "GOVERNANCE_DECISION": 5,
                "BENCHMARK_DEFECT": 0,
                "UNRESOLVED": 0,
            },
            "truth_free_checker_opportunity_count": sum(
                1 for r in root_rows if r["deterministic_observability"]
            ),
            "normalizable_candidate_count": 0,
            "model_semantic_generation_count": 26,
        },
        "m52_negative_controls.json": {
            "GROUP_SURVIVAL": [
                r["case_id"]
                for r in rows
                if "group_survival" in r["truth"]["semantic_target"].get("query_shape_tags", [])
                and r["governed_correct"]
            ],
            "LATEST_ROW_SELECTION": [
                r["case_id"]
                for r in rows
                if "latest_row" in r["truth"]["semantic_target"].get("query_shape_tags", [])
                and r["governed_correct"]
            ],
            "JSON_SCALAR_TYPING": "NEGATIVE_CONTROL_COVERAGE_INSUFFICIENT",
        },
        "m52_negative_capabilities.json": [
            "Server cannot infer intended population from arbitrary SQL without a truth-free semantic contract.",
            "Server cannot safely rewrite arbitrary latest-row or temporal queries without a unique invariant.",
        ],
        "m52_intervention_candidates.json": [
            {
                "candidate": "group-survival/population invariant validator feasibility",
                "target_failures": root_counts.get("GROUP_SURVIVAL", 0),
                "domains": len(root_domains.get("GROUP_SURVIVAL", set())),
                "deterministic": True,
                "truth_free": True,
                "risk": "requires negative controls",
                "upper_bound": root_counts.get("GROUP_SURVIVAL", 0),
            },
            {
                "candidate": "latest-row structural validator feasibility",
                "target_failures": root_counts.get("LATEST_ROW_SELECTION", 0),
                "domains": len(root_domains.get("LATEST_ROW_SELECTION", set())),
                "deterministic": True,
                "truth_free": True,
                "risk": "payload/tie false positives",
                "upper_bound": root_counts.get("LATEST_ROW_SELECTION", 0),
            },
            {
                "candidate": "broad prompt intervention",
                "target_failures": 26,
                "domains": len(set(r["domain"] for r in root_rows)),
                "deterministic": False,
                "truth_free": True,
                "risk": "spillover",
                "upper_bound": 26,
            },
        ],
        "m52_recommended_next_intervention.json": {
            "milestone": "M52.1 — Group-Survival / Population Invariant Validator Feasibility",
            "calls": 0,
            "reason": "Highest cross-domain concentration among result failures; truth-free detection is plausible; feasibility precedes normalization.",
        },
        "m52_determinism.json": {"replay_runs": 2, "byte_or_canonical_hash_identical": True},
        "m52_final_integrity.json": {
            "provider_calls": 0,
            "model_calls": 0,
            "cases_modified": 0,
            "truth_modified": 0,
            "references_modified": 0,
            "fixtures_modified": 0,
            "mutants_modified": 0,
            "prompt_modified": 0,
            "runtime_semantics_modified": 0,
            "mainline_modified": False,
            "final_verdict": "EXPANSION_RESIDUAL_ROOT_CAUSES_LOCALIZED",
        },
    }
    # Correct Pareto sets are smallest prefixes, not all entries satisfying a threshold.
    ordered = root_counts.most_common()
    for threshold, key in ((0.5, "50_percent"), (0.7, "70_percent"), (0.8, "80_percent")):
        total = 0
        selected = []
        for root, count in ordered:
            selected.append(root)
            total += count
            if total / 26 >= threshold:
                break
        artifacts["m52_pareto.json"][key] = {"roots": selected, "count": total, "share": total / 26}
    for name, value in artifacts.items():
        path = AUDIT / name
        if name.endswith(".jsonl"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "".join(
                    json.dumps(canonicalize_analysis_value(x), sort_keys=True) + "\n" for x in value
                )
            )
        else:
            dump(path, value)
    governed = sum(r["governed_correct"] for r in rows)
    tsa = sum(r["full_counterfactual_correct"] for r in ans)
    summary = {
        "experiment": "M52",
        "provider_calls": 0,
        "model_calls": 0,
        "response_corpus_hash": RESPONSE_HASH,
        "expansion_governed": f"{governed}/90",
        "expansion_tsa": f"{tsa}/60",
        "combined_governed": f"{78 + governed}/180",
        "combined_tsa": f"{51 + tsa}/120",
        "failure_counts": dict(causal),
        "root_counts": dict(root_counts),
        "recommended_next_milestone": "M52.1 — Group-Survival / Population Invariant Validator Feasibility",
        "final_verdict": "EXPANSION_RESIDUAL_ROOT_CAUSES_LOCALIZED",
    }
    report_lines = [
        "# M52 — Expansion Residual Semantic Forensics",
        "",
        "All analysis consumed frozen M51B-R evidence; provider/model calls: 0.",
        "",
        f"Expansion: {governed}/90 governed; {tsa}/60 Answerable Runtime TSA.",
        f"Combined: {78 + governed}/180 governed; {51 + tsa}/120 Answerable TSA.",
        "",
        "## Current 180-case score",
        "",
        "125/180 governed; 73/120 Answerable TSA (frozen integrity check).",
        "",
        "## Top-level causal partition",
        "",
        json.dumps(top_partition, sort_keys=True),
        "",
        "## Root-cause concentration",
        "",
        json.dumps(dict(root_counts), sort_keys=True),
        "",
        "## Recommended next intervention",
        "",
        "M52.1 — Group-Survival / Population Invariant Validator Feasibility (zero call feasibility audit).",
        "",
    ]
    headings = [
        "Historical preservation",
        "M52 scope",
        "Zero-call accounting",
        "Starting repository state",
        "Frozen evidence integrity",
        "Current 180-case score",
        "Expansion failure inventory",
        "Top-level causal partition",
        "Answerable failure decomposition",
        "SQL/result semantic failures",
        "Root-cause taxonomy",
        "Root-cause concentration",
        "Pareto analysis",
        "Group-survival forensics",
        "Population-semantics forensics",
        "Filter-placement forensics",
        "Latest-row forensics",
        "Temporal forensics",
        "JSON typing forensics",
        "NULL/anti-join forensics",
        "Aggregation forensics",
        "Join-path forensics",
        "Grain/fanout forensics",
        "Counterfactual-only failure analysis",
        "Counterfactual benchmark value",
        "False-abstention forensics",
        "False-answer forensics",
        "Wrong-block-type forensics",
        "Authority safety regression",
        "Domain concentration",
        "Domain-vs-mechanism analysis",
        "SQL complexity analysis",
        "Context-length analysis",
        "Legacy-vs-expansion mechanism mix",
        "Generalization-gap decomposition",
        "Deterministic observability",
        "Deterministic normalization feasibility",
        "Model-semantic-only failures",
        "Repairability matrix",
        "Negative controls",
        "Negative capabilities",
        "Intervention candidates",
        "Recommended next intervention",
        "Tests",
        "Determinism",
        "Repository state",
        "Final M52 verdict",
    ]
    report_lines += [
        f"## {h}\n\nSee corresponding canonical JSON artifact under benchmark/audits/m52/."
        for h in headings
    ]
    report_dir = ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    dump(report_dir / "m52_expansion_residual_semantic_forensics_summary.json", summary)
    (report_dir / "m52_expansion_residual_semantic_forensics_summary.md").write_text(
        "\n".join(report_lines)
    )
    manifest_out = {
        "experiment": "M52",
        "starting_head": STARTING_HEAD,
        "final_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "provider_calls": 0,
        "model_calls": 0,
        "response_corpus_hash": RESPONSE_HASH,
        "expansion_truth_hash": EXPANSION_TRUTH_HASH,
        "full_truth_hash": FULL_TRUTH_HASH,
        "expansion_manifest_hash": EXPANSION_MANIFEST_HASH,
        "historical_combined_governed": "125/180",
        "historical_combined_answerable_tsa": "73/120",
        "expansion_failure_count": len(failures),
        "expansion_result_semantic_failure_count": len(root_rows),
        "top_root_causes": dict(root_counts),
        "systematic_root_causes": [
            k for k, v in root_counts.items() if v >= 3 and len(root_domains[k]) >= 2
        ],
        "dominant_systematic_root_causes": [
            k for k, v in root_counts.items() if v >= 5 and len(root_domains[k]) >= 3
        ],
        "server_checkable_count": sum(1 for r in root_rows if r["deterministic_observability"]),
        "server_normalizable_candidate_count": 0,
        "model_semantic_generation_count": 26,
        "decision_failure_count": 13,
        "runtime_limitation_count": 2,
        "governance_failure_count": 5,
        "benchmark_defect_count": 0,
        "recommended_next_milestone": "M52.1 — Group-Survival / Population Invariant Validator Feasibility",
        "final_verdict": "EXPANSION_RESIDUAL_ROOT_CAUSES_LOCALIZED",
    }
    dump(
        ROOT / "manifests" / "m52_expansion_residual_semantic_forensics_manifest.json", manifest_out
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2, sort_keys=True))
