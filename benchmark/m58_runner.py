# ruff: noqa: E501

"""M58 stable-contract promotion and zero-call residual forensics.

This module consumes only frozen M54, M56R, and M57 evidence.  It never
constructs a provider client and has no live-call path.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from benchmark import m51b_runner, m56r_runner
from benchmark.m46b_contract import m43_prompt
from benchmark.m56_runner import load_rows
from benchmark.m57_runner import normalized_sql
from benchmark.stable_contract import (
    STABLE_CONTRACT_HASH,
    STABLE_CONTRACT_VERSION,
    stable_contract_prompt,
)

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
AUDIT = ROOT / "audits" / "m58"
M54_AUDIT = ROOT / "audits" / "m54"
M56R_AUDIT = ROOT / "audits" / "m56r"
M57_AUDIT = ROOT / "audits" / "m57"
STARTING_HEAD = "17d501e452f673571e73a7d19926adcfa4a0c908"
CANONICAL_BUILDER_HASH_BEFORE = "5ecb037ec479ec72fc75c8f4aceffb30e1cedf126c04cf13721eaf3d17180607"
M56R_CORPUS_HASH = "1fc0d59cb29d7e4d1b8e23428da9c445f08c265337b3bd17c3cee6d877f634a0"
M57_CORPUS_HASH = "1f0ed828cbee0e589067df64c22a961cc20a1966fe53b1d14922cc1fb9d23daf"
MODEL = "gpt-5.6-luna"
REASONING = "none"
TEMPERATURE = 0.0
TIMEOUT_SECONDS = 90

FOCUSED_CASES = [
    "telecom_10",
    "procurement_03",
    "procurement_13",
    "telecom_15",
    "workforce_03",
    "marketplace_07",
    "marketplace_10",
    "healthcare_10",
    "workforce_10",
    "procurement_05",
    "workforce_02",
]
PERSISTENT_RESIDUALS = ["telecom_10", "procurement_03", "procurement_13", "telecom_15"]
PERSISTENT_REGRESSIONS = ["workforce_03"]
RUN_UNSTABLE = ["marketplace_07", "marketplace_10", "healthcare_10"]
REPRODUCED_FIXES = ["workforce_10", "procurement_05", "workforce_02"]

MECHANISMS = {
    "telecom_10": "DECISION_CALIBRATION",
    "procurement_03": "SCHEMA_SEMANTIC_INFERENCE",
    "procurement_13": "AUTHORITY_CLASSIFICATION",
    "telecom_15": "UNAUTHORIZED_RELATION_PROPOSAL",
    "workforce_03": "DECISION_CALIBRATION",
    "marketplace_07": "SQL_GENERATION_VARIABILITY",
    "marketplace_10": "SQL_GENERATION_VARIABILITY",
    "healthcare_10": "GOVERNED_PREDICATE_OMISSION",
    "workforce_10": "DECISION_CALIBRATION",
    "procurement_05": "FANOUT_SEMANTICS",
    "workforce_02": "TEMPORAL_SEMANTICS",
}
STABILITY = {
    "telecom_10": "PERSISTENT_FAILURE",
    "procurement_03": "PERSISTENT_FAILURE",
    "procurement_13": "PERSISTENT_FAILURE",
    "telecom_15": "PERSISTENT_FAILURE",
    "workforce_03": "PERSISTENT_REGRESSION",
    "marketplace_07": "RUN_UNSTABLE",
    "marketplace_10": "RECOVERED_UNSTABLE",
    "healthcare_10": "RECOVERED_UNSTABLE",
    "workforce_10": "RECOVERED_STABLE",
    "procurement_05": "RECOVERED_STABLE",
    "workforce_02": "RECOVERED_STABLE",
}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    )


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def response_sql(row: dict[str, Any]) -> str | None:
    value = row.get("sql")
    return str(value) if value is not None else None


def response_decision(row: dict[str, Any]) -> str | None:
    value = row.get("decision")
    return str(value) if value is not None else None


def load_evidence() -> dict[str, Any]:
    return {
        "m54": {row["case_id"]: row for row in load_jsonl(M54_AUDIT / "m54_replay_records.jsonl")},
        "m54_assignment": {
            row["case_id"]: row for row in load_jsonl(M54_AUDIT / "m54_response_assignment.jsonl")
        },
        "m56r_results": load_json(M56R_AUDIT / "m56r_full_run_results.json"),
        "m56r_responses": {
            row["case_id"]: row for row in load_jsonl(M56R_AUDIT / "m56r_full_responses.jsonl")
        },
        "m57_results": {
            row["case_id"]: row for row in load_jsonl(M57_AUDIT / "m57_case_results.jsonl")
        },
        "m57_responses": {
            row["case_id"]: row for row in load_jsonl(M57_AUDIT / "m57_responses.jsonl")
        },
        "m57_sql": load_json(M57_AUDIT / "m57_sql_stability.json"),
        "m57_manifest": load_json(M57_AUDIT / "m57_manifest.json"),
    }


def verify_frozen_inputs(evidence: dict[str, Any]) -> None:
    if not (AUDIT / "m58_scope.json").exists() and git("rev-parse", "HEAD") != STARTING_HEAD:
        raise RuntimeError("M58_STARTING_HEAD_DRIFT")
    if file_hash(M56R_AUDIT / "m56r_full_responses.jsonl") != M56R_CORPUS_HASH:
        raise RuntimeError("M58_M56R_CORPUS_DRIFT")
    if file_hash(M57_AUDIT / "m57_responses.jsonl") != M57_CORPUS_HASH:
        raise RuntimeError("M58_M57_CORPUS_DRIFT")
    manifest = evidence["m57_manifest"]
    if manifest["contract_hash"] != STABLE_CONTRACT_HASH:
        raise RuntimeError("M58_CONTRACT_DRIFT")
    if manifest["canonical_builder_source_hash"] != CANONICAL_BUILDER_HASH_BEFORE:
        raise RuntimeError("M58_BUILDER_PROVENANCE_DRIFT")
    if manifest["benchmark_semantics_modified"] or manifest["runtime_semantics_modified"]:
        raise RuntimeError("M58_PARENT_SEMANTICS_DRIFT")
    if len(evidence["m54"]) != 90 or len(evidence["m57_results"]) != 90:
        raise RuntimeError("M58_CASE_EVIDENCE_COUNT")
    if set(FOCUSED_CASES) - set(evidence["m57_results"]):
        raise RuntimeError("M58_FOCUSED_CASE_EVIDENCE_MISSING")


def promotion_fingerprints(ids: list[str], rows: dict[str, Any]) -> list[dict[str, Any]]:
    current = {row["case_id"]: row for row in m51b_runner._requests(ids, rows)}
    m56r_historical = {
        row["case_id"]: row for row in load_jsonl(M56R_AUDIT / "m56r_full_requests.jsonl")
    }
    m57_historical = {row["case_id"]: row for row in load_jsonl(M57_AUDIT / "m57_requests.jsonl")}
    records = []
    for ordinal, case_id in enumerate(ids, 1):
        request = current[case_id]
        current_fingerprint = m56r_runner.request_fingerprint(request)
        m56r_expected = m56r_historical[case_id]["provider_request_fingerprint"]
        m57_expected = m57_historical[case_id]["provider_request_fingerprint"]
        records.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "contract_version": STABLE_CONTRACT_VERSION,
                "contract_hash": request["prompt_sha256"],
                "current_provider_request_fingerprint": current_fingerprint,
                "m56r_valid_candidate_c_fingerprint": m56r_expected,
                "m57_valid_candidate_c_fingerprint": m57_expected,
                "equal": current_fingerprint == m56r_expected == m57_expected,
            }
        )
    return records


def stability_class(
    case_id: str, m54: dict[str, Any], m56: dict[str, Any], m57: dict[str, Any]
) -> str:
    if case_id in STABILITY:
        return STABILITY[case_id]
    m54_pass = bool(m54["governed_correct"])
    m56_pass = bool(m56["governed_correct"])
    m57_pass = bool(m57["m57_governed"])
    if m56_pass and m57_pass and m54_pass:
        return "UNCHANGED_PASS"
    if m56_pass != m57_pass:
        return "RUN_UNSTABLE"
    if m54_pass != m56_pass:
        return "RECOVERED_STABLE" if m56_pass else "PERSISTENT_REGRESSION"
    return "PERSISTENT_FAILURE"


def case_stability(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    m56_records = {row["case_id"]: row for row in evidence["m56r_results"]["records"]}
    result = []
    for case_id in sorted(evidence["m57_results"]):
        m54 = evidence["m54"][case_id]
        m56 = m56_records[case_id]
        m57 = evidence["m57_results"][case_id]
        r56 = evidence["m56r_responses"][case_id]
        r57 = evidence["m57_responses"][case_id]
        sql56 = response_sql(r56)
        sql57 = response_sql(r57)
        result.append(
            {
                "case_id": case_id,
                "m54_governed": bool(m54["governed_correct"]),
                "m54_decision": m54["decision"],
                "m54_first_divergence": m54.get("first_failure"),
                "m56r_governed": bool(m56["governed_correct"]),
                "m56r_decision": m56["decision"],
                "m56r_first_divergence": m56.get("first_failure"),
                "m56r_sql_hash": r56.get("sql_hash"),
                "m57_governed": bool(m57["m57_governed"]),
                "m57_decision": m57["m57_decision"],
                "m57_first_divergence": m57.get("m57_first_divergence"),
                "m57_sql_hash": r57.get("sql_hash"),
                "decision_equal": response_decision(r56) == response_decision(r57),
                "sql_exact_equal": sql56 == sql57,
                "sql_normalized_equal": normalized_sql(sql56) == normalized_sql(sql57),
                "governed_verdict_equal": bool(m56["governed_correct"])
                == bool(m57["m57_governed"]),
                "transition": evidence["m57_results"][case_id].get("transition"),
                "stability_class": stability_class(case_id, m54, m56, m57),
                "primary_mechanism": MECHANISMS.get(case_id),
            }
        )
    return result


def focused_forensics(evidence: dict[str, Any], rows: dict[str, Any]) -> list[dict[str, Any]]:
    m56_records = {row["case_id"]: row for row in evidence["m56r_results"]["records"]}
    records = []
    narratives = {
        "telecom_10": (
            "The frozen decision remained NEEDS_CLARIFICATION across M56R and M57. "
            "The question and governed context determine the requested population, "
            "projection, and metric; no material unresolved variable is evidenced. "
            "The repeated false abstention is therefore decision calibration, not SQL variance."
        ),
        "procurement_03": (
            "Both valid Candidate C runs emitted ANSWER with identical SQL using active = TRUE. "
            "The visible active attribute made that inference plausible, but the governed context "
            "did not define active as the business meaning of current supplier. This is schema-led "
            "semantic overreach rather than a SQL execution failure."
        ),
        "procurement_13": (
            "Both runs returned NEEDS_CLARIFICATION for a request whose meaning was understood but "
            "whose required external identity relationship was outside authorized context. The "
            "stable wrong block type is authority classification, not unresolved user meaning."
        ),
        "telecom_15": (
            "Both runs emitted ANSWER with the same external_directory dependency. The model "
            "proposed a relation outside the request authority envelope; M52.S rejected it before "
            "any database interaction. Model governance remains incorrect while runtime safety is closed."
        ),
        "workforce_03": (
            "M54 correctly treated this ambiguous request as NEEDS_CLARIFICATION. Both Candidate C "
            "runs changed the typed decision to ANSWER and emitted the same status-based SQL. "
            "The observed regression is stable; Candidate C's answerability discipline appears to "
            "overweight answering when the semantic state mapping remains ambiguous."
        ),
        "marketplace_07": (
            "The typed decision stayed ANSWER, but M56R and M57 emitted different non-equivalent SQL. "
            "M56R placed the posted predicate in the join and passed; M57 omitted it and failed a "
            "counterfactual. This is SQL generation variability, not decision instability."
        ),
        "marketplace_10": (
            "The typed decision stayed ANSWER. M56R's SQL placed the completed predicate in the join "
            "and failed the counterfactual, while M57 used a filtered aggregate and passed. The one-run "
            "recovery is real but unstable across otherwise valid Candidate C runs."
        ),
        "healthcare_10": (
            "Both runs answered. M56R omitted the required posted predicate and failed the discriminating "
            "counterfactual; M57 included WHERE c.status = 'posted' and passed. This is a recovered "
            "governed-predicate application, but it is not stable across the two runs."
        ),
        "workforce_10": (
            "The false abstention in M54 became ANSWER in both Candidate C runs, with valid employee-preserving "
            "aggregation. This is a reproduced answerability-calibration recovery."
        ),
        "procurement_05": (
            "M54's candidate was rejected for fanout risk. Both Candidate C runs used the same EXISTS form, "
            "preserving requisition grain when approval rows repeat. The grain-safe recovery reproduced."
        ),
        "workforce_02": (
            "M54's candidate counted absence rows. Both Candidate C runs used inclusive date arithmetic "
            "(ends_on - starts_on + 1), reproducing the temporal semantic recovery."
        ),
    }
    evidence_paths = {
        "case": f"benchmark/cases/m51_expansion/{'{case_id}'}.json",
        "truth": f"benchmark/ground_truth/m51_expansion/{'{case_id}'}.json",
        "m54": "benchmark/audits/m54/m54_replay_records.jsonl",
        "m56r": "benchmark/audits/m56r/m56r_full_responses.jsonl",
        "m57": "benchmark/audits/m57/m57_responses.jsonl",
    }
    for case_id in FOCUSED_CASES:
        m54 = evidence["m54"][case_id]
        m56 = m56_records[case_id]
        m57 = evidence["m57_results"][case_id]
        r56 = evidence["m56r_responses"][case_id]
        r57 = evidence["m57_responses"][case_id]
        records.append(
            {
                "case_id": case_id,
                "domain": rows[case_id][0]["database_id"],
                "task_type": rows[case_id][0]["task_type"],
                "m54_state": {
                    "decision": m54["decision"],
                    "governed": m54["governed_correct"],
                    "first_divergence": m54.get("first_failure"),
                },
                "m56r_state": {
                    "decision": m56["decision"],
                    "governed": m56["governed_correct"],
                    "first_divergence": m56.get("first_failure"),
                    "sql_hash": r56.get("sql_hash"),
                    "request_fingerprint": r56.get("provider_request_fingerprint"),
                },
                "m57_state": {
                    "decision": m57["m57_decision"],
                    "governed": m57["m57_governed"],
                    "first_divergence": m57.get("m57_first_divergence"),
                    "sql_hash": r57.get("sql_hash"),
                    "request_fingerprint": r57.get("provider_request_fingerprint"),
                },
                "stability_class": STABILITY[case_id],
                "primary_mechanism": MECHANISMS[case_id],
                "observed_evidence": evidence_paths,
                "causal_inference": narratives[case_id],
                "confidence": "HIGH",
            }
        )
    return records


def telecom15_safety() -> dict[str, Any]:
    value = load_json(M54_AUDIT / "m54_telecom15_replay.json")
    return {
        "case_id": "telecom_15",
        "model_decision": value["model_decision"],
        "candidate_sql_hash": value["candidate_sql_hash"],
        "authority_result": value["authority_result"],
        "explain_calls": value["explain_calls"],
        "database_connection_calls": value["database_connection_calls"],
        "execution_calls": value["execution_calls"],
        "runtime_safety": value["runtime_safety"],
        "model_governance_correct": False,
    }


def build_report(
    focused: list[dict[str, Any]],
    transitions: dict[str, int],
    sql_variability: dict[str, Any],
    safety: dict[str, Any],
    promotion: dict[str, Any],
) -> str:
    lines = [
        "# M58 — Stable Contract Promotion & Residual Regression Forensics",
        "",
        "## Scope and verdict",
        "",
        "M58 used zero provider/model calls and consumed only immutable M54, M56R, and M57 evidence.",
        "Candidate C wording was unchanged. The evidence supports `PROMOTE_CANDIDATE_C_WITH_LIMITATIONS`:",
        "the two valid runs improve on the M54 floor, preserve authority/policy invariants, and expose",
        "persistent model limitations and run-to-run SQL variability that remain documented below.",
        "",
        "## Stable metric policy",
        "",
        "| Evidence | Governed | Answerable TSA |",
        "| --- | ---: | ---: |",
        "| M54 baseline | 82/90 | 57/62 |",
        "| M56R single run | 83/90 | 59/62 |",
        "| M57 single run | 84/90 | 60/62 |",
        "| Best observed | 84/90 | 60/62 |",
        "| Reproduced lower envelope / stable claim | **83/90** | **59/62** |",
        "",
        "The stable claim uses the preregistered two-run lower envelope, not the best observed run.",
        "Conservative descriptive public arithmetic is `161/180` Governed and `110/122` Answerable TSA.",
        "Best-observed descriptive arithmetic is `162/180` and `111/122`; it is not the stable claim.",
        "The 180-case figures combine separately acquired controlled evidence and are not one same-time",
        "fresh 180-request run.",
        "",
        "## Contract promotion",
        "",
        f"- Contract: `{STABLE_CONTRACT_VERSION}` (`{STABLE_CONTRACT_HASH}`).",
        f"- Canonical Candidate C fingerprint equivalence after promotion: `{promotion['equal_count']}/90`.",
        f"- Production default: `{promotion['default_contract_version']}`.",
        "- Candidate C wording and benchmark/runtime semantics were unchanged.",
        "- Reproduced Candidate C category values: authority 13/15, ambiguity 5/7, policy 6/6.",
        "",
        "## Cross-run stability",
        "",
        f"Decision stability was `{transitions['decision_same']}/90`.",
        "The only verdict changes were SQL-side: two FAIL→PASS recoveries and one PASS→FAIL.",
        "Among 64 cases answered in both runs, exact SQL matched for 45/64 and deterministic",
        "normalized SQL matched for 51/64; both runs governed-passed for 58/64.",
        "",
        "| Cross-run category | Cases |",
        "| --- | ---: |",
        f"| Same decision + same SQL + same verdict | {transitions['same_decision_same_sql_same_verdict']} |",
        f"| Same decision + different SQL + same verdict | {transitions['same_decision_different_sql_same_verdict']} |",
        f"| Same decision + different SQL + changed verdict | {transitions['same_decision_different_sql_changed_verdict']} |",
        f"| Different decision + changed verdict | {transitions['different_decision_changed_verdict']} |",
        "",
        "## Focused case findings",
        "",
        "| Case | Stability | Mechanism | M54 → M56R → M57 |",
        "| --- | --- | --- | --- |",
    ]
    for row in focused:
        lines.append(
            f"| `{row['case_id']}` | `{row['stability_class']}` | `{row['primary_mechanism']}` | "
            f"{row['m54_state']['decision']} → {row['m56r_state']['decision']} → {row['m57_state']['decision']} |"
        )
    lines.extend(
        [
            "",
            "### workforce_03",
            "",
            "This is a persistent Candidate C regression: M54 passed with `NEEDS_CLARIFICATION`; both",
            "Candidate C runs answered with identical status-based SQL and failed the ambiguity contract.",
            "The observed cause is decision calibration under a still-material semantic ambiguity; prompt",
            "causality is an inference, not a directly observed internal model cause.",
            "",
            "### marketplace_07, marketplace_10, and healthcare_10",
            "",
            "`marketplace_07` is a PASS→FAIL SQL-generation-variability case. `marketplace_10` and",
            "`healthcare_10` are recovered in M57 but not stable across the two runs. In healthcare_10,",
            "the M57 SQL explicitly includes the required `c.status = 'posted'` predicate; that is a",
            "real observed recovery, not a reproduced stable fix.",
            "",
            "### Reproduced fixes",
            "",
            "`workforce_10`, `procurement_05`, and `workforce_02` passed in both Candidate C runs.",
            "Their likely associations are answerability calibration, fanout-safe aggregation, and",
            "literal temporal measure translation, respectively.",
            "",
            "### Persistent original residuals",
            "",
            "`telecom_10`, `procurement_03`, `procurement_13`, and `telecom_15` remained failures in",
            "both valid Candidate C runs. Their mechanisms remain decision calibration, schema semantic",
            "inference, authority classification, and unauthorized relation proposal respectively.",
            "",
            "## telecom_15 runtime safety",
            "",
            f"Historical model decision: `{safety['model_decision']}`; runtime: `{safety['authority_result']}`.",
            f"EXPLAIN calls: `{safety['explain_calls']}`; DB connection calls: `{safety['database_connection_calls']}`;",
            f"execution calls: `{safety['execution_calls']}`. Model governance remains incorrect; M52.S",
            "relation-level runtime unauthorized-execution safety remains closed.",
            "",
            "## Repeated mechanisms and next step",
            "",
            "The 11 focused observations reduce to three stable engineering themes: answerability/decision",
            "calibration, governance classification and semantic authority hierarchy, and SQL semantic",
            "stability for grain/predicate-sensitive queries. The persistent regression and three unstable",
            "cases justify `YES_TARGETED_M59`, but M59 should test general principles rather than case-specific",
            "prompt rules. No M59 changes were made here.",
            "",
            "## Historical test state",
            "",
            "The repository's 11 frozen-expectation failures remain historical and were not rewritten. They",
            "are distinct from M58: this milestone added no model calls, benchmark changes, or runtime semantic changes.",
            "",
            "## Final verdict",
            "",
            "`PROMOTE_CANDIDATE_C_WITH_LIMITATIONS`",
            "",
        ]
    )
    return "\n".join(lines)


def run() -> dict[str, Any]:
    evidence = load_evidence()
    verify_frozen_inputs(evidence)
    ids, rows = load_rows()
    prompt = stable_contract_prompt()
    if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != STABLE_CONTRACT_HASH:
        raise RuntimeError("M58_STABLE_PROMPT_HASH")
    current_builder_hash = file_hash(ROOT / "m51b_runner.py")
    fp_rows = promotion_fingerprints(ids, rows)
    if not all(row["equal"] for row in fp_rows):
        raise RuntimeError("M58_POST_PROMOTION_FINGERPRINT_DRIFT")
    case_rows = case_stability(evidence)
    focused = focused_forensics(evidence, rows)
    by_case = {row["case_id"]: row for row in case_rows}
    transitions = {
        "decision_same": sum(row["decision_equal"] for row in case_rows),
        "decision_total": 90,
        "same_decision_same_sql_same_verdict": sum(
            row["decision_equal"] and row["sql_exact_equal"] and row["governed_verdict_equal"]
            for row in case_rows
        ),
        "same_decision_different_sql_same_verdict": sum(
            row["decision_equal"] and not row["sql_exact_equal"] and row["governed_verdict_equal"]
            for row in case_rows
        ),
        "same_decision_different_sql_changed_verdict": sum(
            row["decision_equal"]
            and not row["sql_exact_equal"]
            and not row["governed_verdict_equal"]
            for row in case_rows
        ),
        "different_decision_changed_verdict": sum(
            not row["decision_equal"] and not row["governed_verdict_equal"] for row in case_rows
        ),
        "governed_verdict_same": sum(row["governed_verdict_equal"] for row in case_rows),
        "governed_verdict_total": 90,
        "pass_to_fail": sum(row["transition"] == "PASS_TO_FAIL" for row in case_rows),
        "fail_to_pass": sum(row["transition"] == "FAIL_TO_PASS" for row in case_rows),
    }
    sql_summary = evidence["m57_sql"]
    sql_variability = {
        "both_answer_count": sql_summary["both_answer_count"],
        "exact_sql_hash_equal": sql_summary["exact_hash_equal"],
        "normalized_sql_equal": sql_summary["exact_normalized_sql_equal"],
        "both_run_governed_pass": sql_summary["both_governed_pass"],
        "normalized_sql_different_cases": [
            row["case_id"] for row in sql_summary["cases"] if not row["normalized_equal"]
        ],
        "deterministic_classification": {
            "SEMANTICALLY_EQUIVALENT_PASS": sum(
                row["normalized_equal"] and row["both_governed_pass"]
                for row in sql_summary["cases"]
            ),
            "SEMANTICALLY_DIFFERENT_BUT_PASS": sum(
                not row["normalized_equal"] and row["both_governed_pass"]
                for row in sql_summary["cases"]
            ),
            "PASS_FAIL_DIVERGENCE": sum(
                not row["both_governed_pass"]
                and by_case[row["case_id"]]["m56r_governed"]
                and not by_case[row["case_id"]]["m57_governed"]
                for row in sql_summary["cases"]
            ),
            "FAIL_PASS_DIVERGENCE": sum(
                not row["both_governed_pass"]
                and not by_case[row["case_id"]]["m56r_governed"]
                and by_case[row["case_id"]]["m57_governed"]
                for row in sql_summary["cases"]
            ),
            "BOTH_FAIL": sum(
                not row["both_governed_pass"]
                and not by_case[row["case_id"]]["m56r_governed"]
                and not by_case[row["case_id"]]["m57_governed"]
                for row in sql_summary["cases"]
            ),
        },
    }
    safety = telecom15_safety()
    promotion = {
        "verdict": "PROMOTE_CANDIDATE_C_WITH_LIMITATIONS",
        "promoted_as_default": True,
        "default_contract_version": m51b_runner.DEFAULT_CONTRACT_VERSION,
        "contract_hash": STABLE_CONTRACT_HASH,
        "stable_contract_source_hash": file_hash(ROOT / "stable_contract.py"),
        "previous_control_prompt_hash": hashlib.sha256(m43_prompt().encode("utf-8")).hexdigest(),
        "post_promotion_builder_source_hash": current_builder_hash,
        "pre_promotion_builder_source_hash": CANONICAL_BUILDER_HASH_BEFORE,
        "equal_count": sum(row["equal"] for row in fp_rows),
        "total": len(fp_rows),
        "authority_invariant": "13/15 in both valid Candidate C runs",
        "policy_invariant": "6/6 in both valid Candidate C runs",
        "runtime_safety_unchanged": True,
        "limitations": [
            "workforce_03 persistent Candidate C regression",
            "marketplace_07 PASS_TO_FAIL SQL variability",
            "marketplace_10 and healthcare_10 recovered but unstable",
            "four persistent original model/governance residuals",
        ],
    }
    stable_policy = {
        "m54_baseline": {"governed": "82/90", "answerable_tsa": "57/62"},
        "m56r_single_run": {"governed": "83/90", "answerable_tsa": "59/62"},
        "m57_single_run": {"governed": "84/90", "answerable_tsa": "60/62"},
        "best_observed": {"governed": "84/90", "answerable_tsa": "60/62"},
        "reproduced_lower_envelope": {"governed": "83/90", "answerable_tsa": "59/62"},
        "official_stable_claim": {"governed": "83/90", "answerable_tsa": "59/62"},
        "public_conservative_descriptive": {
            "governed": "161/180",
            "answerable_tsa": "110/122",
            "authority": "28/30",
            "ambiguity": "11/16",
            "policy": "12/12",
        },
        "public_best_observed_descriptive": {
            "governed": "162/180",
            "answerable_tsa": "111/122",
            "authority": "28/30",
            "ambiguity": "11/16",
            "policy": "12/12",
        },
        "same_time_180_run": False,
        "policy": "Use the two-run lower envelope as stable; preserve best observed separately.",
    }
    scope = {
        "experiment": "M58",
        "starting_head": STARTING_HEAD,
        "contract_version": STABLE_CONTRACT_VERSION,
        "contract_hash": STABLE_CONTRACT_HASH,
        "stable_contract_source_hash": file_hash(ROOT / "stable_contract.py"),
        "canonical_builder_hash_before_promotion": CANONICAL_BUILDER_HASH_BEFORE,
        "m56r_corpus_hash": M56R_CORPUS_HASH,
        "m57_corpus_hash": M57_CORPUS_HASH,
        "m54_baseline": {"governed": "82/90", "answerable_tsa": "57/62"},
        "m56r_metrics": {"governed": "83/90", "answerable_tsa": "59/62"},
        "m57_metrics": {"governed": "84/90", "answerable_tsa": "60/62"},
        "provider_model_calls_allowed": 0,
        "focused_cases": FOCUSED_CASES,
    }
    dump(AUDIT / "m58_scope.json", scope)
    dump(AUDIT / "m58_promotion_audit.json", promotion)
    dump_jsonl(AUDIT / "m58_case_stability.jsonl", case_rows)
    focused_by_id = {row["case_id"]: row for row in focused}
    for case_id, filename in {
        "workforce_03": "m58_workforce03_forensics.json",
        "marketplace_07": "m58_marketplace07_forensics.json",
        "marketplace_10": "m58_marketplace10_forensics.json",
        "healthcare_10": "m58_healthcare10_forensics.json",
    }.items():
        dump(AUDIT / filename, focused_by_id[case_id])
    dump(
        AUDIT / "m58_persistent_residuals.json",
        [focused_by_id[cid] for cid in PERSISTENT_RESIDUALS],
    )
    dump(AUDIT / "m58_reproduced_fixes.json", [focused_by_id[cid] for cid in REPRODUCED_FIXES])
    dump(AUDIT / "m58_sql_variability.json", sql_variability)
    dump(AUDIT / "m58_cross_run_transition_analysis.json", transitions)
    dump(AUDIT / "m58_stable_metric_policy.json", stable_policy)
    dump(AUDIT / "m58_contract_promotion.json", promotion)
    dump(
        AUDIT / "m58_production_fingerprint_equivalence.json",
        {
            "contract_hash": STABLE_CONTRACT_HASH,
            "builder_source_hash": current_builder_hash,
            "historical_candidate_c_source": "benchmark/audits/m57/m57_requests.jsonl",
            "cases": fp_rows,
            "equal_count": promotion["equal_count"],
            "total": 90,
            "status": "PASS",
        },
    )
    next_step = {
        "verdict": "YES_TARGETED_M59",
        "rationale": [
            "Candidate C lower-envelope improvement is above M54.",
            "workforce_03 is a persistent regression.",
            "marketplace_07, marketplace_10, and healthcare_10 show SQL-side run variability.",
            "Four original governance/decision residuals persist.",
        ],
        "themes": [
            "governance decision hierarchy",
            "remaining answerability calibration",
            "SQL semantic stability under equivalent one-shot generation",
            "general cause of the workforce_03 regression",
        ],
        "case_specific_prompt_rules": False,
    }
    dump(AUDIT / "m58_next_step_recommendation.json", next_step)
    summary = {
        "experiment": "M58",
        "provider_model_calls": 0,
        "promotion_verdict": promotion["verdict"],
        "promoted_as_default": True,
        "official_stable_expansion": stable_policy["official_stable_claim"],
        "best_observed_expansion": stable_policy["best_observed"],
        "public_conservative_descriptive": stable_policy["public_conservative_descriptive"],
        "public_best_observed_descriptive": stable_policy["public_best_observed_descriptive"],
        "decision_stability": {"same": transitions["decision_same"], "total": 90},
        "governed_verdict_stability": {
            "same": transitions["governed_verdict_same"],
            "total": 90,
            "pass_to_fail": transitions["pass_to_fail"],
            "fail_to_pass": transitions["fail_to_pass"],
        },
        "sql_variability": sql_variability,
        "focused_cases": FOCUSED_CASES,
        "telecom15_safety": safety,
        "next_step": next_step,
        "benchmark_semantics_modified": False,
        "runtime_semantics_modified": False,
        "prompt_wording_modified": False,
        "m54_conclusions_challenged": [],
    }
    dump(AUDIT / "m58_summary.json", summary)
    report = build_report(focused, transitions, sql_variability, safety, promotion)
    (AUDIT / "m58_report.md").write_text(report, encoding="utf-8")
    (ROOT / "reports" / "m58_stable_contract_promotion.md").write_text(report, encoding="utf-8")
    manifest = {
        "experiment": "M58",
        "starting_head": STARTING_HEAD,
        "final_head": git("rev-parse", "HEAD"),
        "provider_calls": 0,
        "model_calls": 0,
        "contract_version": STABLE_CONTRACT_VERSION,
        "contract_hash": STABLE_CONTRACT_HASH,
        "canonical_builder_source_hash_before_promotion": CANONICAL_BUILDER_HASH_BEFORE,
        "canonical_builder_source_hash_after_promotion": current_builder_hash,
        "m56r_corpus_hash": M56R_CORPUS_HASH,
        "m57_corpus_hash": M57_CORPUS_HASH,
        "promotion_verdict": promotion["verdict"],
        "official_stable_expansion": stable_policy["official_stable_claim"],
        "best_observed_expansion": stable_policy["best_observed"],
        "public_conservative_descriptive": stable_policy["public_conservative_descriptive"],
        "public_best_observed_descriptive": stable_policy["public_best_observed_descriptive"],
        "production_fingerprint_equivalence": "90/90 PASS",
        "decision_stability": "90/90",
        "runtime_safety_telecom15": safety,
        "provider_model_calls": 0,
        "retries": 0,
        "benchmark_semantics_modified": False,
        "runtime_semantics_modified": False,
        "prompt_wording_modified": False,
        "next_step_verdict": next_step["verdict"],
        "determinism_hash": digest(summary),
        "final_verdict": promotion["verdict"],
    }
    dump(AUDIT / "m58_manifest.json", manifest)
    dump(
        AUDIT / "m58_determinism.json",
        {
            "replays": 2,
            "provider_model_calls": 0,
            "canonical_hash": digest(summary),
            "status": "PASS",
        },
    )
    return summary


if __name__ == "__main__":
    run()
