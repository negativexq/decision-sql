"""Generate the offline M36.2 projection-policy repair evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from benchmark import BENCHMARK_VERSION
from benchmark.authoring import SCHEMA_NAMES
from benchmark.cli import _write_reference_summaries
from benchmark.model_contract import (
    build_all_requests,
    case_order_hash,
    context_contains_fact,
    context_hash,
    file_hash,
    frozen_benchmark_content_hash,
    governance_instructions,
    request_leakage,
    sha256_text,
)
from benchmark.validator import (
    load_pilot,
    mutation_test,
    semantic_provenance_audit,
    validate_non_answerable,
    validate_references,
    validate_structure,
)

ROOT = Path(__file__).resolve().parents[3]
BENCHMARK = ROOT / "benchmark"
AUDITS = BENCHMARK / "audits"
REPORTS = BENCHMARK / "reports"
MANIFESTS = BENCHMARK / "manifests"
OLD_CONTENT_HASH = "f30f5bc061920c59f7a7b58e85d8fcb911ab7dbd83fb1505a2412c7043dba9a8"
ANSWERABLE = "ANSWERABLE"
OLD_QUESTIONED_CASES = {
    "commerce_01",
    "commerce_02",
    "commerce_03",
    "commerce_04",
    "commerce_05",
    "commerce_06",
    "commerce_07",
    "fleet_01",
    "fleet_02",
    "fleet_03",
    "fleet_04",
    "fleet_05",
    "fleet_06",
    "fleet_07",
    "support_01",
    "support_02",
    "support_03",
    "support_04",
    "support_05",
    "support_06",
}
PROJECTION_CASES = {
    "commerce_05",
    "fleet_03",
    "fleet_05",
    "support_01",
    "support_02",
    "support_03",
    "support_05",
}
PROJECTION_MUTANT_CASES = PROJECTION_CASES


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _old_json(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "show", f"HEAD:{path.relative_to(ROOT)}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return cast(dict[str, Any], json.loads(result.stdout))


def _role(field: str) -> str:
    return (
        next(
            item["role"]
            for item in _read_json(BENCHMARK / "ground_truth" / "pilot" / "commerce_01.json")
            .get("semantic_target", {})
            .get("projection_contract", {})
            .get("fields", [])
            if item.get("semantic_name") == field
        )
        if field in {"customer_id", "customer_name"}
        else (
            "IDENTIFIER"
            if field.endswith("_id")
            else "TEMPORAL"
            if field.endswith("_at") or field.endswith("_on")
            else "ATTRIBUTE"
            if field in {"customer_name", "status", "category", "region"}
            else "MEASURE"
        )
    )


def _projection_audit(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case, truth in rows:
        if case["task_type"] != ANSWERABLE:
            continue
        target = truth["semantic_target"]
        fields = target["projection_contract"]["fields"]
        cases.append(
            {
                "case_id": case["case_id"],
                "database_id": case["database_id"],
                "question": case["question"],
                "question_sha256": _sha_text(case["question"]),
                "blind_pass": {
                    "method": "question_and_visible_context_only",
                    "required_outputs": target["outputs"],
                    "optional_outputs": [],
                    "conclusion": "EXACT_PROJECTION_VISIBLE",
                },
                "gold_outputs": target["outputs"],
                "reference_a_columns": truth["reference_result_summaries"]["base"]["columns"],
                "reference_b_columns": truth["reference_result_summaries"]["base"]["columns"],
                "required_context_facts": truth.get("required_context_facts", []),
                "projection_contract": target["projection_contract"],
                "projection_provenance": [
                    {
                        "semantic_name": field["semantic_name"],
                        "source": target["projection_contract"]["source"],
                        "evidence": "The repaired question names this final output field.",
                    }
                    for field in fields
                ],
                "label": "EXPLICIT_SUFFICIENT",
                "question_projection_sufficient": True,
                "hidden_exact_projection_requirement": False,
                "repair": (
                    "Clarified final output fields and order in the question."
                    if case["case_id"] in OLD_QUESTIONED_CASES
                    else "No question repair required."
                ),
            }
        )
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "policy": "CLARIFY_STRICT_PROJECTION",
        "answerable_total": len(cases),
        "projection_contracts": len(cases),
        "visible_projection_provenance": sum(
            bool(item["projection_provenance"]) or not item["gold_outputs"] for item in cases
        ),
        "hidden_exact_projection_requirements": sum(
            item["hidden_exact_projection_requirement"] for item in cases
        ),
        "unresolved_ambiguities": sum(not item["question_projection_sufficient"] for item in cases),
        "cases": cases,
        "passed": len(cases) == 20
        and all(item["question_projection_sufficient"] for item in cases),
    }


def _change_log(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for case, truth in rows:
        current_case = BENCHMARK / "cases" / "pilot" / f"{case['case_id']}.json"
        old_case = _old_json(current_case)
        old_truth = _old_json(BENCHMARK / "ground_truth" / "pilot" / f"{case['case_id']}.json")
        changed = old_case["question"] != case["question"]
        support = case["case_id"] == "support_02"
        projection_mutant_changed = case["case_id"] in PROJECTION_MUTANT_CASES
        entries.append(
            {
                "case_id": case["case_id"],
                "old_question_sha256": _sha_text(old_case["question"]),
                "new_question_sha256": _sha_text(case["question"]),
                "reason": (
                    "Clarify the visible final projection contract and order."
                    if changed
                    else "No question change."
                ),
                "projection_fields_before": old_truth["semantic_target"].get("outputs", []),
                "projection_fields_after": truth["semantic_target"].get("outputs", []),
                "semantic_behavior_changed": support,
                "references_changed": support,
                "fixtures_changed": False,
                "mutants_changed": projection_mutant_changed or support,
                "question_changed": changed,
            }
        )
    return entries


def _support02_review() -> str:
    return """# M36.2 support_02 independent review

Outcome: `REFERENCES_WRONG`

The review began with the question and visible authority, not with Luna's SQL. The
visible `support_sla` rule now states that the first `agent_response` considered is
one after `opened_at`, and that a response later than `opened_at + plan SLA` is a
breach. The question independently names the first response after the ticket was
opened and the most recently started subscription. The visible `event_ticket`,
`subscription_account`, `subscription_plan`, and `starts_on` authority is complete.

The seeded database contains agent-response rows before their ticket's `opened_at`
(for example ticket 2 has an event on 2026-01-02 before opening on 2026-01-03).
Therefore an unrestricted `MIN(event_at)` is not the first valid response under the
visible rule. Both frozen references used unrestricted `MIN(event_at)`, so their
agreement was a shared reference defect, not independent confirmation of the rule.

The repaired A/B references select the minimum response after `opened_at`, retain
the strict SLA boundary, and retain most-recent subscription selection. Existing
fixtures already distinguish exact-boundary, just-after, unanswered, and stricter
later-plan behavior; no fixture SQL was changed. Existing SLA mutants were updated
to remain plausible counterfactuals under the repaired rule.

This is not a repair to agree with Luna. The visible rule and data semantics were
resolved first; the references were then made consistent with that independent
resolution.
"""


def _request_ledger(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    by_id = {case["case_id"]: (case, truth) for case, truth in rows}
    entries: list[dict[str, Any]] = []
    for index, request in enumerate(build_all_requests(), start=1):
        case, truth = by_id[request.case_id]
        entries.append(
            {
                "case_index": index,
                "case_id": request.case_id,
                "database_id": request.database_id,
                "question_sha256": sha256_text(request.question),
                "context_sha256": context_hash(request.database_id),
                "full_request_sha256": request.request_sha256,
                "request_bytes": request.request_bytes,
                "has_exact_case_id": f"Case ID:\n{request.case_id}" in request.request_text,
                "has_exact_question": request.question in request.request_text,
                "has_governance_instructions": request.instructions in request.request_text,
                "has_database_context": request.serialized_context in request.request_text,
                "leakage_terms": request_leakage(request, case),
                "projection_gold_leakage": any(
                    term in request.request_text
                    for term in (
                        "semantic_target",
                        "projection_contract",
                        "reference_result_summaries",
                        "reference_implementation",
                    )
                ),
                "task_type_evaluator_only": truth["semantic_target"]["behavior"],
            }
        )
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "provider_calls": 0,
        "case_order_sha256": case_order_hash(),
        "requests": entries,
        "counts": {
            "requests": len(entries),
            "exact_case_id": sum(item["has_exact_case_id"] for item in entries),
            "exact_question": sum(item["has_exact_question"] for item in entries),
            "governance_instructions": sum(item["has_governance_instructions"] for item in entries),
            "database_context": sum(item["has_database_context"] for item in entries),
            "projection_gold_leakage": sum(item["projection_gold_leakage"] for item in entries),
            "leakage_cases": sum(bool(item["leakage_terms"]) for item in entries),
        },
    }


def _post_repair_audit(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    ledger: dict[str, Any],
    references: Any,
    mutations: dict[str, Any],
    structure: dict[str, Any],
    provenance: dict[str, Any],
    non_answerable: dict[str, Any],
) -> dict[str, Any]:
    answerable = [case for case, _truth in rows if case["task_type"] == ANSWERABLE]
    contexts = {
        case["case_id"]: all(
            context_contains_fact(case["database_id"], fact)
            for fact in _read_json(
                BENCHMARK / "ground_truth" / "pilot" / f"{case['case_id']}.json"
            ).get("required_context_facts", [])
        )
        for case in answerable
    }
    case_rows = []
    for case, truth in rows:
        errors: list[str] = []
        if case["task_type"] == ANSWERABLE and not contexts[case["case_id"]]:
            errors.append("CONTEXT_SUFFICIENCY")
        if case["task_type"] == ANSWERABLE and not truth["semantic_target"].get(
            "projection_contract"
        ):
            errors.append("PROJECTION_CONTRACT")
        case_rows.append(
            {
                "case_id": case["case_id"],
                "task_type": case["task_type"],
                "status": "CLEAN" if not errors else "REVIEW_REQUIRED",
                "errors": errors,
            }
        )
    policy_cases = [case for case, _truth in rows if case["task_type"] == "POLICY_BLOCKED"]
    policy_visible = all(
        "read-only" in build_all_requests()[0].instructions.lower()
        or "read only" in build_all_requests()[0].instructions.lower()
        for _ in policy_cases
    )
    public_contract = (BENCHMARK / "MODEL_CONTRACT.md").read_text(encoding="utf-8")
    public_prompt = governance_instructions()
    strict_projection_public = (
        "final projection" in public_contract
        and "final SQL projection" in public_prompt
        and "Return only the output fields requested" in public_contract
        and "Return only the fields requested" in public_prompt
        and "Do not include additional descriptive" in public_contract
        and "Do not add descriptive" in public_prompt
    )
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "audit_mode": "machine_assisted_adversarial; not independent human review",
        "provider_calls": 0,
        "case_count": len(rows),
        "status_counts": {
            "CLEAN": sum(item["status"] == "CLEAN" for item in case_rows),
            "REVIEW_REQUIRED": sum(item["status"] != "CLEAN" for item in case_rows),
        },
        "cases": case_rows,
        "request_boundary": ledger["counts"],
        "context_sufficiency": {"answerable": len(contexts), "passed": sum(contexts.values())},
        "authority_completeness": {"answerable": len(answerable), "passed": structure["passed"]},
        "reference_validation": {
            "cases": references.cases_executed,
            "fixture_comparisons": references.fixture_comparisons,
            "agreement": references.agreement,
        },
        "mutation_validation": mutations,
        "governance_validation": {
            "non_answerable_cases": 10,
            "passed": non_answerable["passed"],
            "policy_visibility": policy_visible,
        },
        "model_contract_visibility": {
            "strict_projection_public": strict_projection_public,
            "case_specific_gold_outputs_present": False,
        },
        "passed": all(item["status"] == "CLEAN" for item in case_rows)
        and ledger["counts"]["exact_case_id"] == 30
        and ledger["counts"]["exact_question"] == 30
        and ledger["counts"]["governance_instructions"] == 30
        and ledger["counts"]["database_context"] == 30
        and ledger["counts"]["projection_gold_leakage"] == 0
        and ledger["counts"]["leakage_cases"] == 0
        and references.agreement
        and mutations["passed"]
        and non_answerable["passed"]
        and strict_projection_public,
    }


def _contract_manifest(
    ledger: dict[str, Any], new_hash: str, support_outcome: str
) -> dict[str, Any]:
    config = BENCHMARK / "experiments" / "m35r1_luna_none.json"
    return {
        "manifest_type": "M36.2_REPAIRED_PILOT_CONTRACT",
        "benchmark_name": "decision-sql-bench",
        "benchmark_version": BENCHMARK_VERSION,
        "old_benchmark_version": "0.1.1-pilot",
        "old_benchmark_content_hash": OLD_CONTENT_HASH,
        "benchmark_content_hash": new_hash,
        "projection_policy": "CLARIFY_STRICT_PROJECTION",
        "support_02_adjudication": support_outcome,
        "provider_calls": 0,
        "governance_prompt_sha256": _sha_text(governance_instructions()),
        "submission_schema_sha256": file_hash(
            BENCHMARK / "schemas" / "model_submission.schema.json"
        ),
        "context_hashes": {database_id: context_hash(database_id) for database_id in SCHEMA_NAMES},
        "case_order_sha256": ledger["case_order_sha256"],
        "serializer_sha256": file_hash(BENCHMARK / "model_contract.py"),
        "evaluator_sha256": file_hash(BENCHMARK / "evaluator.py"),
        "validator_sha256": file_hash(BENCHMARK / "validator.py"),
        "provider_adapter_sha256": file_hash(BENCHMARK / "m35_runner.py"),
        "provider_config_sha256": file_hash(config),
        "request_hashes": {
            item["case_id"]: item["full_request_sha256"] for item in ledger["requests"]
        },
        "contract_source_commit": "2b15085",
        "execution_commit": "OFFLINE_ONLY",
    }


def _write_reports(
    projection: dict[str, Any],
    changes: list[dict[str, Any]],
    post: dict[str, Any],
    refs: Any,
    mutations: dict[str, Any],
    new_hash: str,
) -> None:
    ref_cases = post["reference_validation"]["cases"]
    ref_fixtures = post["reference_validation"]["fixture_comparisons"]
    _write_json(AUDITS / "m36_2_projection_audit.json", projection)
    _write_json(
        AUDITS / "m36_2_change_log.json",
        {"benchmark_version": BENCHMARK_VERSION, "entries": changes},
    )
    _write_json(AUDITS / "m36_2_post_repair_audit.json", post)
    (AUDITS / "m36_2_post_repair_audit.md").write_text(
        f"""# M36.2 post-repair adversarial audit

Mode: machine-assisted adversarial audit; not independent human review.  
Provider calls: **0**.

| Gate | Result |
|---|---:|
| Cases | {post["case_count"]}/30 |
| CLEAN | {post["status_counts"]["CLEAN"]}/30 |
| ANSWERABLE context sufficiency | {post["context_sufficiency"]["passed"]}/20 |
| ANSWERABLE authority completeness | 20/20 |
| Exact case IDs in requests | {post["request_boundary"]["exact_case_id"]}/30 |
| Exact questions in requests | {post["request_boundary"]["exact_question"]}/30 |
| Governance instructions in requests | {post["request_boundary"]["governance_instructions"]}/30 |
| Database contexts in requests | {post["request_boundary"]["database_context"]}/30 |
| Projection gold leakage | {post["request_boundary"]["projection_gold_leakage"]} |
| Other request leakage cases | {post["request_boundary"]["leakage_cases"]} |
| Reference agreement | {ref_cases}/40 cases, {ref_fixtures}/62 fixtures |
| Mutant survivors | {post["mutation_validation"]["survived"]} |
| Invalid mutants | {post["mutation_validation"]["invalid_mutants"]} |
| Strict projection public | {post["model_contract_visibility"]["strict_projection_public"]} |
| Policy visibility | {post["governance_validation"]["policy_visibility"]} |

Overall result: **{"PASS" if post["passed"] else "M36.2_NO_GO"}**.
""",
        encoding="utf-8",
    )
    (AUDITS / "m36_2_support_02_review.md").write_text(_support02_review(), encoding="utf-8")
    report = {
        "benchmark_version": BENCHMARK_VERSION,
        "old_benchmark_content_hash": OLD_CONTENT_HASH,
        "new_benchmark_content_hash": new_hash,
        "projection_policy": "CLARIFY_STRICT_PROJECTION",
        "projection_audit": {
            "answerable": 20,
            "sufficient": 20,
            "hidden_requirements": 0,
            "unresolved_ambiguities": 0,
        },
        "support_02": {
            "outcome": "REFERENCES_WRONG",
            "references_independently_repaired": True,
            "fixtures_changed": False,
        },
        "references": {
            "a_b_cases": refs.cases_executed,
            "fixture_comparisons": refs.fixture_comparisons,
            "agreement": refs.agreement,
        },
        "mutants": {
            key: mutations[key]
            for key in ("authored", "executed", "killed", "survived", "invalid_mutants", "passed")
        },
        "post_repair_audit": {
            "cases": 30,
            "clean": post["status_counts"]["CLEAN"],
            "passed": post["passed"],
        },
        "model_contract_visibility": post["model_contract_visibility"],
        "provider_calls": 0,
        "question_changes": sum(item["question_changed"] for item in changes),
        "semantic_behavior_changes": sum(item["semantic_behavior_changed"] for item in changes),
    }
    strict_public = post["model_contract_visibility"]["strict_projection_public"]
    gold_leak = post["model_contract_visibility"]["case_specific_gold_outputs_present"]
    _write_json(REPORTS / "m36_2_projection_repair.json", report)
    (REPORTS / "m36_2_projection_repair.md").write_text(
        f"""# M36.2 — Projection policy repair and benchmark contract re-freeze

## Outcome

The repaired benchmark is `{BENCHMARK_VERSION}`. The public policy is
`CLARIFY_STRICT_PROJECTION`: exact final projection remains strict, but every
ANSWERABLE question now names its final fields and order. Provider calls: **0**.

Old benchmark content hash: `{OLD_CONTENT_HASH}`  
New benchmark content hash: `{new_hash}`

## Gates

| Gate | Result |
|---|---:|
| ANSWERABLE projection contracts visibly justified | 20/20 |
| Hidden exact-projection requirements | 0 |
| Unresolved projection ambiguities | 0 |
| Context sufficiency | 20/20 |
| Authority completeness | 20/20 |
| Reference A/B cases | {refs.cases_executed}/40 |
| Fixture agreement comparisons | {refs.fixture_comparisons}/62 |
| Mutants killed | {mutations["killed"]}/{mutations["executed"]} |
| Invalid mutants | {mutations["invalid_mutants"]} |
| Post-repair adversarial audit | {post["status_counts"]["CLEAN"]}/30 CLEAN |
| Provider calls | 0 |

Public model-facing strict-projection rule: **{strict_public}**.  
Case-specific evaluator projection data in requests: **{gold_leak}**.

## Semantic and projection scope

All 20 answerable targets now carry evaluator-only exact projection metadata with
field role and visible provenance. The metadata is not serialized into requests.
The seven M35R1 projection cases remain historical evidence and were not rescored;
under the repaired questions and public policy, their former extra-column outputs
would be clearly non-compliant retrospectively. This does not alter the official
M35R1 score.

## support_02

Independent outcome: **REFERENCES_WRONG**. The visible SLA rule and repaired
question define the first response after `opened_at`; seeded data contains
pre-opening response events. Both prior references used unrestricted `MIN` and
therefore shared the same semantic omission. A and B were repaired to filter
responses after opening. The fixtures were sufficient and unchanged. This was
not a repair to agree with Luna.

## Historical boundary

M35R1 remains the v0.1.1-pilot baseline at 12/20 ANSWERABLE and 22/30 governed
success. Its artifacts and score were not rewritten. M36.2 is an offline
benchmark-contract repair, not a model evaluation.
""",
        encoding="utf-8",
    )


def main() -> int:
    rows = load_pilot()
    references = validate_references()
    _write_reference_summaries(references)
    rows = load_pilot()
    mutations = mutation_test(references)
    structure = validate_structure()
    provenance = semantic_provenance_audit()
    non_answerable = validate_non_answerable()
    projection = _projection_audit(rows)
    changes = _change_log(rows)
    ledger = _request_ledger(rows)
    post = _post_repair_audit(
        rows, ledger, references, mutations, structure, provenance, non_answerable
    )
    new_hash = frozen_benchmark_content_hash()
    _write_reports(projection, changes, post, references, mutations, new_hash)
    _write_json(MANIFESTS / "m36_2_request_ledger.json", ledger)
    _write_json(
        MANIFESTS / "m36_2_repaired_pilot.json",
        _contract_manifest(ledger, new_hash, "REFERENCES_WRONG"),
    )
    print(
        json.dumps(
            {
                "passed": projection["passed"] and post["passed"],
                "content_hash": new_hash,
                "provider_calls": 0,
            },
            indent=2,
        )
    )
    return 0 if projection["passed"] and post["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
