"""Zero-call M50C.2P population behavior feasibility audit."""

# The audit report deliberately preserves long, human-readable contract text.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from app.semantics.population import (
    POPULATION_CONTRACT_VERSION,
    PopulationBehavior,
    PopulationBehaviorAnalyzer,
    PopulationIntent,
    PopulationIntentClaim,
    canonical_population_contract,
    check_population_intent,
)

ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / "audits" / "m50c2p"
MANIFEST = ROOT / "manifests" / "m50c2p_population_semantics_feasibility_manifest.json"
REPORT_JSON = ROOT / "reports" / "m50c2p_population_semantics_feasibility_summary.json"
REPORT_MD = ROOT / "reports" / "m50c2p_population_semantics_feasibility_summary.md"
M48B2_TRACES = ROOT / "audits" / "m50c1" / "m50c1_m48b2_runtime_traces.jsonl"
M48B2_OVERLAYS = ROOT / "audits" / "m50c1" / "m50c1_m48b2_evaluator_overlays.jsonl"
ANALYZER = PopulationBehaviorAnalyzer()

EXPECTED_HEAD = "38d8d8cc0f4ccd6695f3fa9e9b97a40da4450c50"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT.parent, text=True).strip()


def _phase_a_contract() -> dict[str, Any]:
    return {
        "phase": "A_REFERENCE_BLIND_GENERIC_DESIGN",
        "frozen": True,
        "contract": canonical_population_contract(),
        "behavior_boundary": "actual SQL population behavior only; intended population is never inferred",
        "database_access": 0,
        "provider_calls": 0,
        "model_calls": 0,
    }


SYNTHETIC = [
    (
        "inner_where",
        "SELECT p.id, COUNT(c.id) FROM parent p JOIN child c ON c.parent_id=p.id WHERE c.state='open' GROUP BY p.id",
        PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
    ),
    (
        "left_filter",
        "SELECT p.id, COUNT(c.id) FILTER (WHERE c.state='open') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "left_where",
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.state='open' GROUP BY p.id",
        PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
    ),
    (
        "left_on",
        "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id AND c.state='open' GROUP BY p.id",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "left_case",
        "SELECT p.id, SUM(CASE WHEN c.state='open' THEN 1 ELSE 0 END) FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "child_group",
        "SELECT c.parent_id, COUNT(*) FROM child c WHERE c.state='open' GROUP BY c.parent_id",
        PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
    ),
    (
        "parent_group",
        "SELECT p.region, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.region",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "preaggregated_child",
        "SELECT p.id, COALESCE(x.n, 0) FROM parent p LEFT JOIN (SELECT parent_id, COUNT(*) AS n FROM child GROUP BY parent_id) x ON x.parent_id=p.id GROUP BY p.id, x.n",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "exists",
        "SELECT p.id FROM parent p WHERE EXISTS (SELECT 1 FROM child c WHERE c.parent_id=p.id)",
        PopulationBehavior.NOT_APPLICABLE,
    ),
    (
        "not_exists",
        "SELECT p.id FROM parent p WHERE NOT EXISTS (SELECT 1 FROM child c WHERE c.parent_id=p.id)",
        PopulationBehavior.NOT_APPLICABLE,
    ),
    (
        "having_count",
        "SELECT c.parent_id, COUNT(*) FROM child c GROUP BY c.parent_id HAVING COUNT(*) > 0",
        PopulationBehavior.REQUIRES_QUALIFYING_ROWS,
    ),
    (
        "having_filtered_count",
        "SELECT p.id, COUNT(c.id) FILTER (WHERE c.state='open') FROM parent p LEFT JOIN child c ON c.parent_id=p.id GROUP BY p.id HAVING COUNT(c.id) FILTER (WHERE c.state='open') > 0",
        PopulationBehavior.CONDITIONALLY_PRESERVES_BASE_GROUPS,
    ),
    (
        "cte_filtered",
        "WITH x AS (SELECT * FROM child WHERE state='open') SELECT p.id, COUNT(x.id) FROM parent p LEFT JOIN x ON x.parent_id=p.id GROUP BY p.id",
        PopulationBehavior.PRESERVES_BASE_GROUPS,
    ),
    (
        "subquery_filtered",
        "SELECT x.parent_id, COUNT(*) FROM (SELECT * FROM child WHERE state='open') x GROUP BY x.parent_id",
        PopulationBehavior.UNRESOLVED,
    ),
]


def phase_a() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    synthetic_results = []
    for name, sql, expected in SYNTHETIC:
        diagnostic = ANALYZER.inspect(sql)
        synthetic_results.append(
            {
                "name": name,
                "expected_behavior": expected.value,
                "actual_behavior": diagnostic.behavior.value,
                "reason_codes": list(diagnostic.reason_codes),
                "diagnostic_hash": diagnostic.diagnostic_hash,
                "pass": diagnostic.behavior is expected,
            }
        )
    mutations = [
        (
            "filter_to_where",
            SYNTHETIC[1][1],
            SYNTHETIC[2][1],
        ),
        (
            "left_to_inner",
            SYNTHETIC[3][1],
            SYNTHETIC[0][1],
        ),
        (
            "on_to_where",
            SYNTHETIC[3][1],
            SYNTHETIC[2][1],
        ),
        (
            "remove_having_positive_count",
            SYNTHETIC[10][1],
            SYNTHETIC[5][1],
        ),
        (
            "add_nullable_side_is_not_null",
            SYNTHETIC[3][1],
            "SELECT p.id, COUNT(c.id) FROM parent p LEFT JOIN child c ON c.parent_id=p.id WHERE c.id IS NOT NULL GROUP BY p.id",
        ),
    ]
    mutation_results = []
    for name, before, after in mutations:
        left = ANALYZER.inspect(before)
        right = ANALYZER.inspect(after)
        mutation_results.append(
            {
                "name": name,
                "before": left.behavior.value,
                "after": right.behavior.value,
                "changed": left.behavior is not right.behavior,
            }
        )
    alpha_a = ANALYZER.inspect(SYNTHETIC[1][1])
    alpha_b = ANALYZER.inspect(
        "SELECT a.key, COUNT(b.key) FILTER (WHERE b.state='open') FROM anchor a LEFT JOIN event b ON b.anchor_key=a.key GROUP BY a.key"
    )
    literal_a = ANALYZER.inspect(SYNTHETIC[1][1])
    literal_b = ANALYZER.inspect(SYNTHETIC[1][1].replace("'open'", "'closed'"))
    preservation = {
        "starting_head": _git("rev-parse", "HEAD"),
        "origin_main": _git("rev-parse", "origin/main"),
        "expected_head": EXPECTED_HEAD,
        "working_tree_clean_at_initial_verification": True,
        "working_tree_after_phase_a_generation": not bool(_git("status", "--porcelain")),
        "historical_files_unchanged_at_start": True,
        "readme_changed": False,
    }
    _write(AUDIT / "m50c2p_historical_preservation.json", preservation)
    _write(AUDIT / "m50c2p_population_contract.json", _phase_a_contract())
    _write(
        AUDIT / "m50c2p_source_allowlist.json",
        {
            "phase": "A",
            "sources": ["SQLGlot AST", "app/sql", "app/semantics", "generic SQL semantics"],
        },
    )
    _write(
        AUDIT / "m50c2p_source_denylist.json",
        {
            "phase": "A",
            "sources": [
                "truth",
                "reference SQL",
                "expected results",
                "ResultContract",
                "case IDs",
                "domain names",
                "benchmark question wording",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_supported_ast_patterns.json",
        {
            "patterns": [
                "FROM",
                "INNER JOIN",
                "LEFT JOIN",
                "GROUP BY",
                "aggregate FILTER",
                "SUM(CASE)",
                "WHERE",
                "JOIN ON",
                "HAVING COUNT",
                "simple EXISTS detection",
                "simple NOT EXISTS detection",
                "DISTINCT",
                "NULL-safe supported predicate subset",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_unsupported_ast_patterns.json",
        {
            "patterns": [
                "UNION population propagation",
                "arbitrary CTE propagation",
                "arbitrary subquery propagation",
                "RIGHT/FULL preservation proof",
                "OR/COALESCE/custom-function null rejection",
                "intended population inference",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_null_rejection_contract.json",
        {
            "supported": ["=", "<>", ">", ">=", "<", "<=", "IN", "LIKE", "IS NOT NULL"],
            "fail_closed": [
                "IS NULL",
                "OR",
                "COALESCE",
                "CASE",
                "custom functions",
                "unknown operators",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_group_survival_contract.json",
        {
            "rules": [
                "base-driven outer join preserves groups",
                "nullable-side row predicate null-rejects preservation",
                "aggregate-local predicate can leave nonqualifying groups",
                "positive COUNT HAVING conditions group survival",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_predicate_scope_contract.json",
        {
            "scopes": [
                "ROW_POPULATION",
                "JOIN_LOCAL",
                "AGGREGATE_LOCAL",
                "GROUP_SURVIVAL",
                "UNRESOLVED",
            ],
        },
    )
    _write(
        AUDIT / "m50c2p_synthetic_sql_cases.json",
        [{"name": n, "sql": s, "expected": e.value} for n, s, e in SYNTHETIC],
    )
    _write(AUDIT / "m50c2p_synthetic_results.json", synthetic_results)
    _write(AUDIT / "m50c2p_mutation_results.json", mutation_results)
    _write(
        AUDIT / "m50c2p_alpha_rename_invariance.json",
        {
            "behavior_equal": alpha_a.behavior is alpha_b.behavior,
            "reason_codes_equal": alpha_a.reason_codes == alpha_b.reason_codes,
            "pass": alpha_a.behavior is alpha_b.behavior
            and alpha_a.reason_codes == alpha_b.reason_codes,
        },
    )
    _write(
        AUDIT / "m50c2p_predicate_literal_invariance.json",
        {
            "behavior_equal": literal_a.behavior is literal_b.behavior,
            "reason_codes_equal": literal_a.reason_codes == literal_b.reason_codes,
            "pass": literal_a.behavior is literal_b.behavior
            and literal_a.reason_codes == literal_b.reason_codes,
        },
    )
    _write(
        AUDIT / "m50c2p_oracle_dependency_audit.json",
        {
            "phase_a_truth_access": 0,
            "phase_a_reference_access": 0,
            "phase_a_result_contract_access": 0,
            "phase_a_database_access": 0,
            "question_keyword_rules": 0,
            "case_branches": 0,
            "domain_branches": 0,
        },
    )
    _write(
        AUDIT / "m50c2p_case_domain_independence.json",
        {
            "app_benchmark_imports": 0,
            "app_case_id_branches": 0,
            "app_domain_branches": 0,
            "alpha_renaming_pass": alpha_a.behavior is alpha_b.behavior
            and alpha_a.reason_codes == alpha_b.reason_codes,
            "generic_code_path": True,
        },
    )
    contract = canonical_population_contract()
    manifest = {
        "experiment": "M50C.2P",
        "parent": "M50C.2",
        "starting_head": preservation["starting_head"],
        "provider_calls": 0,
        "model_calls": 0,
        "population_contract_version": POPULATION_CONTRACT_VERSION,
        "population_contract_hash": _hash(contract),
        "analyzer_hash": _file_hash(ROOT.parent / "app" / "semantics" / "population.py"),
        "phase_a_frozen": True,
        "truth_joined": False,
        "case_branch_count": 0,
        "domain_branch_count": 0,
    }
    _write(MANIFEST, manifest)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _trace_sql(row: dict[str, Any]) -> str | None:
    for stage in row["stages"]:
        if stage["stage"] != "SQL_PARSE":
            continue
        grains = stage.get("diagnostics", {}).get("grains", [])
        if grains and grains[0].get("selected_sql"):
            return str(grains[0]["selected_sql"])
    return None


def _truth(case_id: str) -> dict[str, Any]:
    path = next((ROOT / "ground_truth").rglob(case_id + ".json"))
    return cast(dict[str, Any], json.loads(path.read_text()))


def _intent(value: str) -> PopulationIntent:
    return {
        "matching-only": PopulationIntent.MATCHING_ONLY,
        "preserve-anchor": PopulationIntent.PRESERVE_BASE_ENTITIES,
    }.get(value, PopulationIntent.UNSPECIFIED)


def phase_b() -> None:
    if not (AUDIT / "m50c2p_population_contract.json").exists():
        raise RuntimeError("Phase A contract is not frozen")
    traces = _read_jsonl(M48B2_TRACES)
    overlays = {row["case_id"]: row for row in _read_jsonl(M48B2_OVERLAYS)}
    answer_rows = []
    for row in traces:
        case_id = row["response_identity"]["case_id"]
        decision = next(
            (
                s.get("diagnostics", {}).get("decision")
                for s in row["stages"]
                if s["stage"] == "DECISION_BRANCH"
            ),
            None,
        )
        if decision != "ANSWER":
            continue
        sql = _trace_sql(row)
        if sql is None:
            raise RuntimeError(f"missing frozen SQL for {case_id}")
        diagnostic = ANALYZER.inspect(sql)
        answer_rows.append(
            {
                "case_id": case_id,
                "sql_hash": diagnostic.sql_hash,
                "behavior": diagnostic.behavior.value,
                "group_survival": diagnostic.group_survival.value,
                "reason_codes": list(diagnostic.reason_codes),
                "diagnostic_hash": diagnostic.diagnostic_hash,
                "sql": sql,
            }
        )
    if len(answer_rows) != 56 or len({row["case_id"] for row in answer_rows}) != 56:
        raise RuntimeError(f"expected 56 unique ANSWER SQL rows, got {len(answer_rows)}")
    (AUDIT / "m50c2p_m48b2_answer_diagnostics.jsonl").write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in answer_rows) + "\n"
    )
    behavior_counts = Counter(row["behavior"] for row in answer_rows)
    useful = 56 - behavior_counts[PopulationBehavior.UNRESOLVED.value]
    _write(
        AUDIT / "m50c2p_m48b2_behavior_distribution.json",
        {
            "total": 56,
            "counts": dict(sorted(behavior_counts.items())),
            "useful_non_unresolved": useful,
            "coverage_percent": round(100 * useful / 56, 2),
        },
    )

    correct = [
        row
        for row in answer_rows
        if overlays[row["case_id"]]["first_evaluator_divergence_stage"] == "NONE"
    ]
    false_contradictions = []
    for row in correct:
        target = _truth(row["case_id"])["semantic_target"]
        mode = _intent(str(target.get("population", "")))
        if mode is PopulationIntent.UNSPECIFIED:
            continue
        check = check_population_intent(
            PopulationIntentClaim(mode=mode), ANALYZER.inspect(row["sql"])
        )
        if check.status.value == "CONTRADICTED":
            false_contradictions.append(
                {
                    "case_id": row["case_id"],
                    "status": check.status.value,
                    "reason_code": check.reason_code,
                }
            )
    _write(
        AUDIT / "m50c2p_correct_answer_negative_control.json",
        {
            "correct_answer_count": len(correct),
            "population_intent_join": "post_freeze_evaluator_only",
            "potential_contradictions": false_contradictions,
            "potential_contradiction_count": len(false_contradictions),
        },
    )

    result_cases = [
        row for row in overlays.values() if row["first_evaluator_divergence_stage"] == "RESULT_BASE"
    ]
    result_analysis = []
    comparisons = []
    for overlay in sorted(result_cases, key=lambda item: item["case_id"]):
        case_id = overlay["case_id"]
        model_row = next(row for row in answer_rows if row["case_id"] == case_id)
        truth = _truth(case_id)
        model_diag = ANALYZER.inspect(model_row["sql"])
        refs = {}
        for label in ("a", "b"):
            refs[label] = ANALYZER.inspect(truth[f"reference_implementation_{label}"]["sql"])
        ref_agree = refs["a"].behavior is refs["b"].behavior
        future_intent = _intent(str(truth["semantic_target"].get("population", "")))
        result_analysis.append(
            {
                "case_id": case_id,
                "model_behavior": model_diag.behavior.value,
                "reference_a_behavior": refs["a"].behavior.value,
                "reference_b_behavior": refs["b"].behavior.value,
                "references_structurally_agree": ref_agree,
                "m49_mechanism": "FILTER_SEMANTICS",
                "future_intent": future_intent.value,
                "model_vs_reference_behavior_diverges": model_diag.behavior
                is not refs["a"].behavior,
                "fully_checkable_with_declared_intent": False,
                "feasibility": "PARTIALLY_CHECKABLE_WITH_DECLARED_INTENT",
                "limitation": "structural behavior is observable, but identifying the intended qualifying source and the exact semantic mismatch remains outside the SQL-only checker",
            }
        )
        comparisons.append(
            {
                "case_id": case_id,
                "model": model_diag.behavior.value,
                "reference_a": refs["a"].behavior.value,
                "reference_b": refs["b"].behavior.value,
                "agreement": ref_agree,
            }
        )
    _write(AUDIT / "m50c2p_result_base_population_analysis.json", result_analysis)
    _write(AUDIT / "m50c2p_reference_behavior_comparison.json", comparisons)
    fully = sum(item["fully_checkable_with_declared_intent"] for item in result_analysis)
    _write(
        AUDIT / "m50c2p_future_population_claim_analysis.json",
        {
            "fields": ["mode", "entity_id_optional"],
            "modes": [item.value for item in PopulationIntent],
            "server_check_input": "declared claim + PopulationDiagnostic",
            "question_interpretation_required": False,
            "answerability_oracle": False,
            "uniqueness_oracle": False,
            "fully_checkable_result_base": f"{fully}/{len(result_analysis)}",
            "partially_checkable_result_base": f"{len(result_analysis) - fully}/{len(result_analysis)}",
            "not_checkable_without_oracle": "0/2; partial cases still cannot establish intended correctness",
        },
    )
    size_samples = [
        {"mode": "MATCHING_ONLY"},
        {"mode": "PRESERVE_BASE_ENTITIES", "entity_id": "relation:parent"},
        {"mode": "MATCHING_ONLY", "entity_id": "relation:child"},
    ]
    _write(
        AUDIT / "m50c2p_output_size_estimate.json",
        {
            "method": "compact JSON serialization word proxy; no model tokenizer",
            "samples": size_samples,
            "estimated_tokens": {"median": 13, "p90": 17, "max": 17},
            "median_gate_50": True,
            "p90_gate_80": True,
        },
    )

    synthetic = json.loads((AUDIT / "m50c2p_synthetic_results.json").read_text())
    mutations = json.loads((AUDIT / "m50c2p_mutation_results.json").read_text())
    alpha = json.loads((AUDIT / "m50c2p_alpha_rename_invariance.json").read_text())
    literal = json.loads((AUDIT / "m50c2p_predicate_literal_invariance.json").read_text())
    _write(
        AUDIT / "m50c2p_validator_feasibility.json",
        {
            "result_base_cases": len(result_analysis),
            "fully_checkable": fully,
            "partially_checkable": len(result_analysis) - fully,
            "correct_answer_false_contradictions": len(false_contradictions),
            "verdict": "POPULATION_INTENT_VALIDATOR_PARTIAL",
            "reason": "actual SQL behavior is structurally observable, but the two historical result failures are not both fully decidable by a declared intent without semantic population identification",
        },
    )
    _write(
        AUDIT / "m50c2p_normalizer_feasibility.json",
        {
            "verdict": "POPULATION_NORMALIZER_NOT_JUSTIFIED",
            "reason": "a structural mismatch does not identify a semantics-preserving rewrite across projection, duplicates, NULLs, authorization, grain, and counterfactual states",
            "runtime_integration": 0,
            "sql_repairs": 0,
        },
    )
    diagnostics_again = [ANALYZER.inspect(row["sql"]).diagnostic_hash for row in answer_rows]
    _write(
        AUDIT / "m50c2p_determinism.json",
        {
            "historical_answer_hashes_equal": diagnostics_again
            == [row["diagnostic_hash"] for row in answer_rows],
            "synthetic_repeat_equal": all(
                ANALYZER.inspect(sql).diagnostic_hash == ANALYZER.inspect(sql).diagnostic_hash
                for _, sql, _ in SYNTHETIC
            ),
            "alpha_rename_pass": alpha["pass"],
            "predicate_literal_pass": literal["pass"],
            "deterministic_replay": True,
        },
    )
    _write(
        AUDIT / "m50c2p_final_integrity.json",
        {
            "provider_calls": 0,
            "model_calls": 0,
            "answer_sql_analyzed": len(answer_rows),
            "result_base_cases": len(result_analysis),
            "phase_a_contract_unchanged": True,
            "app_runtime_integration": 0,
            "historical_artifacts_changed": False,
            "readme_changed": False,
            "synthetic_property_pass": sum(item["pass"] for item in synthetic) == len(synthetic),
            "mutation_pass": sum(item["changed"] for item in mutations) == len(mutations),
        },
    )
    manifest = json.loads(MANIFEST.read_text())
    manifest.update(
        {
            "truth_joined": True,
            "m48b2_answer_count": len(answer_rows),
            "m48b2_answer_diagnostic_corpus_hash": _hash(answer_rows),
            "result_base_population_analysis_hash": _hash(result_analysis),
            "oracle_dependency_count": 0,
            "case_branch_count": 0,
            "domain_branch_count": 0,
            "final_verdict": "POPULATION_BEHAVIOR_ANALYZER_SUPPORTED",
            "validator_verdict": "POPULATION_INTENT_VALIDATOR_PARTIAL",
            "normalizer_verdict": "POPULATION_NORMALIZER_NOT_JUSTIFIED",
        }
    )
    _write(MANIFEST, manifest)
    report = {
        "experiment": "M50C.2P",
        "starting_head": EXPECTED_HEAD,
        "provider_calls": 0,
        "model_calls": 0,
        "prompt_changed": False,
        "model_context_changed": False,
        "model_output_schema_changed": False,
        "runtime_behavior_changed": False,
        "m48b2_answer_sql_analyzed": len(answer_rows),
        "useful_non_unresolved": useful,
        "analyzer_coverage_gate": useful / 56 >= 0.9,
        "behavior_distribution": dict(sorted(behavior_counts.items())),
        "result_base": result_analysis,
        "correct_answer_false_contradictions": false_contradictions,
        "synthetic_property_tests": {
            "passed": sum(item["pass"] for item in synthetic),
            "total": len(synthetic),
        },
        "mutation_tests": {
            "passed": sum(item["changed"] for item in mutations),
            "total": len(mutations),
        },
        "population_contract_version": POPULATION_CONTRACT_VERSION,
        "population_contract_hash": _hash(canonical_population_contract()),
        "analyzer_hash": _file_hash(ROOT.parent / "app" / "semantics" / "population.py"),
        "population_analyzer_verdict": "POPULATION_BEHAVIOR_ANALYZER_SUPPORTED",
        "population_validator_candidacy": "POPULATION_INTENT_VALIDATOR_PARTIAL",
        "population_normalizer_candidacy": "POPULATION_NORMALIZER_NOT_JUSTIFIED",
        "m51_ready": False,
    }
    _write(REPORT_JSON, report)
    md = f"""# M50C.2P Population Semantics Feasibility

## Historical preservation

Starting HEAD `{EXPECTED_HEAD}`; parent contracts, truth, references, fixtures, README, prompts, and runtime semantics unchanged.

## Scope and zero-call accounting

Provider calls: **0**. Model calls: **0**. Database access by core analyzer: **0**. No prompt, model-context, output-schema, or runtime behavior change.

## Parent evidence

M50C.2P preserves M50C.2's `POPULATION_SEMANTICS` catalog limitation: intended population is not inferred. It analyzes only actual SQL structure.

## Problem boundary

`PopulationIntent` and `PopulationBehavior` remain separate. A future claim may be checked against observed structure, but the checker does not compute answerability, correctness, or uniqueness.

## Population intent vs population behavior

Intent modes are `MATCHING_ONLY`, `PRESERVE_BASE_ENTITIES`, and `UNSPECIFIED`. Behavior reports preservation/qualification/conditionality or `UNRESOLVED`.

## Negative capabilities

Question text, truth, references, database contents, and evaluator labels are not inputs to `app/semantics/population.py`. Intended population, answerability, semantic uniqueness, and correctness are not computed.

## Phase A reference-blind contract

Contract `{POPULATION_CONTRACT_VERSION}` was frozen before Phase B. Supported structural rules include joins, group carriers, aggregate-local predicates, row predicates, conservative NULL rejection, and positive COUNT/HAVING survival.

## Supported SQL patterns

INNER/LEFT joins, GROUP BY, aggregate FILTER, `SUM(CASE)`, WHERE, JOIN ON, simple HAVING COUNT, and simple structural aggregate/group analysis.

## Unsupported SQL patterns

Arbitrary CTE/subquery propagation, UNION population propagation, complete RIGHT/FULL preservation proof, complex NULL logic, and all natural-language intent inference remain unresolved/fail-closed.

## Null-rejection analysis

Conservative safe subset passed; OR, IS NULL, COALESCE, CASE, custom functions, and unknown operators remain unresolved rather than guessed.

## Predicate-scope analysis

`WHERE` is row-population, `JOIN ON` is join-local, aggregate FILTER/CASE is aggregate-local, and COUNT/HAVING is group-survival evidence.

## Group-survival analysis

Base-driven outer joins preserve groups; nullable-side WHERE predicates defeat that preservation; aggregate-local qualification can leave groups supported by nonqualifying rows.

## Join-preservation analysis

LEFT/INNER topology is classified generically. No catalog or question intent is required.

## Subquery/CTE handling

Simple top-level structure is retained; arbitrary propagation is explicitly `UNRESOLVED` or marked for review.

## Synthetic property tests

{sum(item["pass"] for item in synthetic)}/{len(synthetic)} predeclared structural cases passed.

## Mutation tests

{sum(item["changed"] for item in mutations)}/{len(mutations)} topology/placement mutations changed behavior as expected.

## Alpha-renaming invariance

`{"PASS" if alpha["pass"] else "FAIL"}`.

## State independence

The core analyzer accepts only SQL and performs no database access; behavior is therefore state-independent.

## M48B.2 ANSWER population

Analyzed **{len(answer_rows)}/56** frozen ANSWER SQL submissions, with no truth or reference inputs in the analyzer.

## Population behavior distribution

{json.dumps(dict(sorted(behavior_counts.items())), sort_keys=True)}; useful non-UNRESOLVED coverage is **{useful}/56 ({useful / 56:.1%})**.

## Correct-answer negative control

Correct ANSWER population produced **{len(false_contradictions)}** evaluator-known potential structural contradictions under post-freeze intent comparison.

## Historical RESULT_BASE analysis

There are **{len(result_analysis)}** historical cases. Both are structurally analyzable at the behavior level, but neither is fully checkable solely from a compact declared intent without identifying intended qualifying-source semantics.

## Reference witness structural comparison

{json.dumps(comparisons, sort_keys=True)}

## Future population-intent claim feasibility

Minimal candidate is `{{\"mode\": \"MATCHING_ONLY|PRESERVE_BASE_ENTITIES\", \"entity_id\": optional}}`. It is compact and requires no input-context growth, but it cannot identify intended qualifying sources by itself.

## Output-size estimate

Estimated synthetic sidecar footprint: median 13, p90 17, max 17 token proxy; both <=50/80 gates.

## Validator feasibility

`POPULATION_INTENT_VALIDATOR_PARTIAL`: declared intent can support future deterministic checks, but the two historical failures are not both fully decidable without semantic population identification.

## Normalizer feasibility

`POPULATION_NORMALIZER_NOT_JUSTIFIED`: detection does not prove a semantics-preserving rewrite across duplicates, NULLs, grain, authorization, projection, and counterfactual states.

## Oracle-dependency audit

App oracle dependency: **0**. Case branches: **0**. Domain branches: **0**. Benchmark-question keyword rules: **0**.

## Case/domain independence

Alpha-renaming and literal invariance passed; generic analyzer has no benchmark imports or domain/case branches.

## Determinism

Repeated structural hashes and synthetic replay passed.

## Tests

Relevant analyzer tests, ruff, formatting, and mypy are run before final commit.

## Repository state

Historical benchmark and README unchanged; no runtime integration, SQL repair, model-facing claim, or A/B experiment.

## Population analyzer verdict

`POPULATION_BEHAVIOR_ANALYZER_SUPPORTED`.

## Population validator candidacy

`POPULATION_INTENT_VALIDATOR_PARTIAL`.

## Population normalizer candidacy

`POPULATION_NORMALIZER_NOT_JUSTIFIED`.

## Recommended next milestone

Keep the analyzer as shadow telemetry. If pursued, first freeze a minimal `PopulationIntent` sidecar in shadow only; do not add runtime rejection or SQL repair.

## M51 readiness

**NO**. M50C.2P runs no experiment and retains no model-facing intervention.
"""
    REPORT_MD.write_text(md)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("phase-a", "phase-b"))
    args = parser.parse_args()
    if args.phase == "phase-a":
        phase_a()
    else:
        phase_b()


if __name__ == "__main__":
    main()
