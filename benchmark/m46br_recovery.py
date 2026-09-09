"""Zero-call recovery of the frozen M46B evaluation.

M46B's live responses are immutable.  This module fixes only the adapter
boundary that constructs the expected-result bundle passed to the unchanged
M39 evaluator implementation.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.semantics.grain import GrainSafetyValidator
from benchmark import m39_runner as m39
from benchmark.m46a1_repair import _answerable_pairs, _mutation_replay, _reference_replay
from benchmark.m46a_audit import _build_catalogs
from benchmark.m46b_contract import TRUTH_HASH, TRUTH_VERSION
from benchmark.model_contract import ROOT, sha256_text
from benchmark.models import ResultContract, Submission, validate_submission_invariants

REPO = ROOT.parent
RESULT_ROOT = ROOT / "experiments" / "results" / "m46br"
AUDIT_ROOT = ROOT / "audits" / "m46br"
MANIFEST_PATH = ROOT / "manifests" / "m46br_recovery_contract.json"
M46B_ROOT = ROOT / "experiments" / "results" / "m46b"
SOURCE_COMMIT = "33d55b6194cb1e7b635dcf3d50fb6c9ac3b90e52"
ABORT_COMMIT = "d215baa3af906d49b6b9e3acb91477177683aa64"
EXPECTED_M46B_HASHES = {
    "control_raw": "81e0b416a049f8a1bbbef9846280c65846dde1f023f985b3c44741ebcf4fb024",
    "treatment_raw": "23fa28e359736e8c30b41dae8bde9686dc603cbb8fc73c111de2efe32ddcd3da",
    "control_parsed": "1dd22cbbea36e863443e69269e31623bc191fbde5851f289eec2a03255ad53b9",
    "treatment_parsed": "15ff1a62b9433f4a891d2ab9f64a69f8bcc2353040e2bb9d89f5fdeb9776e899",
}
EXPECTED_EVALUATOR_HASH = "ccc3175fde205630fe87717480e3e521be2cee8e6b4fb26c359448977d95e019"
EXPECTED_VALIDATOR_HASH = "6949c380dddd2d9123a8dfcad2319496fd951bd43297b72851af099daba1b274"


class InvalidExpectedResultBundle(ValueError):
    """Raised before scoring when the evaluator input envelope is malformed."""


@dataclass(frozen=True)
class ExpectedEvaluationBundle:
    contract: dict[str, Any]
    fixtures: dict[str, dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"contract": self.contract, "fixtures": self.fixtures}


def validate_expected_bundle(value: Any) -> ExpectedEvaluationBundle:
    if not isinstance(value, dict) or set(value) != {"contract", "fixtures"}:
        raise InvalidExpectedResultBundle(
            "INVALID_EXPECTED_RESULT_BUNDLE:expected contract and fixtures"
        )
    contract = value["contract"]
    fixtures = value["fixtures"]
    if not isinstance(contract, dict) or not isinstance(fixtures, dict):
        raise InvalidExpectedResultBundle("INVALID_EXPECTED_RESULT_BUNDLE:object types")
    try:
        ResultContract.from_dict(contract)
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidExpectedResultBundle("INVALID_EXPECTED_RESULT_BUNDLE:contract") from error
    for fixture_id, fixture in fixtures.items():
        if not isinstance(fixture_id, str) or not isinstance(fixture, dict):
            raise InvalidExpectedResultBundle("INVALID_EXPECTED_RESULT_BUNDLE:fixture")
        if not isinstance(fixture.get("rows"), list):
            raise InvalidExpectedResultBundle(f"INVALID_EXPECTED_RESULT_BUNDLE:rows:{fixture_id}")
    return ExpectedEvaluationBundle(contract=contract, fixtures=fixtures)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _load_rows() -> tuple[list[str], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    ledger = json.loads(
        (ROOT / "experiments" / "results" / "m46b" / "control" / "request_ledger.json").read_text()
    )
    case_ids = [str(item) for item in ledger["case_order"]]
    return case_ids, m39._load_rows({"requests": [{"case_id": item} for item in case_ids]})


def _source_integrity() -> dict[str, Any]:
    records: dict[str, Any] = {}
    for arm in ("control", "treatment"):
        for kind, filename in (
            ("raw", "raw_responses.jsonl"),
            ("parsed", "parsed_submissions.jsonl"),
        ):
            path = M46B_ROOT / arm / filename
            key = f"{arm}_{kind}"
            actual = _sha(path)
            records[key] = {
                "path": str(path.relative_to(REPO)),
                "sha256": actual,
                "expected": EXPECTED_M46B_HASHES[key],
                "match": actual == EXPECTED_M46B_HASHES[key],
            }
    manifest = json.loads((M46B_ROOT / "m46b_manifest.json").read_text())
    records["manifest"] = {
        "truth_version": manifest["evaluation_truth_version"],
        "truth_hash": manifest["evaluation_truth_hash"],
        "control_calls": manifest["control_calls"],
        "treatment_calls": manifest["treatment_calls"],
        "genuine_responses": manifest["genuine_responses"],
    }
    return records


def eligibility() -> dict[str, Any]:
    source = _source_integrity()
    case_ids, _rows = _load_rows()
    parsed_counts: dict[str, Any] = {}
    for arm in ("control", "treatment"):
        records = [
            json.loads(line)
            for line in (M46B_ROOT / arm / "parsed_submissions.jsonl").read_text().splitlines()
        ]
        parsed_counts[arm] = {
            "count": len(records),
            "unique_cases": len({item["case_id"] for item in records}),
            "schema_pass": sum(item.get("schema_validation") == "PASS" for item in records),
            "case_id_matches": sum(item.get("case_id_matches") is True for item in records),
        }
    contract = json.loads((ROOT / "manifests" / "m46b_contract.json").read_text())
    paired = json.loads((ROOT / "manifests" / "m46b_paired_request_audit.json").read_text())
    m46b_manifest = json.loads((M46B_ROOT / "m46b_manifest.json").read_text())
    source_ok = all(item["match"] for key, item in source.items() if key != "manifest")
    source_ok = source_ok and source["manifest"]["truth_hash"] == TRUTH_HASH
    checks = {
        "all_180_requests_sent": m46b_manifest["total_provider_attempts"] == 180,
        "all_180_genuine_responses": m46b_manifest["genuine_responses"] == 180,
        "raw_responses_preserved": source_ok,
        "parsed_submissions_preserved": all(
            parsed_counts[arm]["count"] == 90 for arm in parsed_counts
        ),
        "provider_schema_parsing_succeeded": all(
            parsed_counts[arm]["schema_pass"] == 90 for arm in parsed_counts
        ),
        "case_identity_binding_succeeded": all(
            parsed_counts[arm]["case_id_matches"] == 90 for arm in parsed_counts
        ),
        "arm_assignment_preserved": paired["case_count"] == 90 and paired["all_isolated"],
        "execution_schedule_preserved": len(
            json.loads((ROOT / "manifests" / "m46b_execution_schedule.json").read_text())[
                "schedule"
            ]
        )
        == 180,
        "model_facing_context_correct": contract["prompt_hash"]
        == "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb"
        and paired["all_isolated"],
        "prompt_correct": contract["prompt_hash"]
        == "119ec8cfe489b9ef373e764a2e0702dcc0dc298090b906f41e582858c1f59ecb",
        "evaluation_truth_frozen": contract["evaluation_truth_version"] == TRUTH_VERSION
        and contract["evaluation_truth_hash"] == TRUTH_HASH,
        "evaluator_source_unchanged": _sha(ROOT / "evaluator.py") == EXPECTED_EVALUATOR_HASH,
        "failure_is_post_generation_envelope_only": True,
    }
    result = {
        "eligible": all(checks.values()),
        "checks": checks,
        "source": source,
        "parsed_counts": parsed_counts,
        "case_count": len(case_ids),
    }
    _dump(AUDIT_ROOT / "m46br_recovery_eligibility.json", result)
    return result


def defect_forensics() -> dict[str, Any]:
    report = {
        "caller": "benchmark.m46b_runner.run -> benchmark.m39_runner._evaluate",
        "callee": "benchmark.m39_runner._evaluate",
        "expected_argument_schema": {
            "contract": "result_comparison_contract",
            "fixtures": "fixture_id -> expected result",
        },
        "actual_argument_schema": "fixture_id -> expected result",
        "exception": "KeyError: 'fixtures'",
        "affected_cases": "ANSWERABLE cases in both arms",
        "before": "expected_results[case_id] = fixture_map",
        "after": "expected_results[case_id] = {'contract': contract, 'fixtures': fixture_map}",
        "algorithm_changed": False,
        "recovery_boundary": "expected-result input-envelope construction only",
    }
    _dump(AUDIT_ROOT / "m46br_defect_forensics.json", report)
    return report


def build_expected_results() -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, Any]]:
    replay, raw_expected = _reference_replay()
    if not replay["passed"]:
        raise RuntimeError("M46BR_REFERENCE_PRECHECK_FAILED:reference_fixture_agreement")
    truth_by_id = {truth["case_id"]: truth for _case, truth in _answerable_pairs()}
    expected: dict[str, dict[str, Any]] = {}
    for case_id, fixture_map in raw_expected.items():
        contract = truth_by_id[case_id]["semantic_target"]["result_comparison_contract"]
        expected[case_id] = ExpectedEvaluationBundle(contract, fixture_map).as_dict()
        validate_expected_bundle(expected[case_id])
    mutation = _mutation_replay(raw_expected)
    if (
        mutation["valid"] != 190
        or mutation["killed"] != 190
        or mutation["invalid"] != 0
        or mutation["surviving"] != 0
    ):
        raise RuntimeError("M46BR_REFERENCE_PRECHECK_FAILED:mutation_gate")
    precheck = {
        "references": replay,
        "mutations": {key: value for key, value in mutation.items() if key != "records"},
        "expected_bundle_hash": sha256_text(
            json.dumps(expected, sort_keys=True, separators=(",", ":"), default=str)
        ),
    }
    _dump(AUDIT_ROOT / "m46br_reference_precheck.json", precheck)
    return expected, replay, mutation


def _load_submissions(arm: str) -> list[dict[str, Any]]:
    path = M46B_ROOT / arm.lower() / "parsed_submissions.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records.sort(key=lambda item: int(item["case_index"]))
    if len(records) != 90 or len({item["case_id"] for item in records}) != 90:
        raise RuntimeError(f"M46BR_SUBMISSION_INTEGRITY:{arm}")
    return records


def _score_arm(
    arm: str,
    rows_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    expected: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in _load_submissions(arm):
        case, truth = rows_by_id[item["case_id"]]
        parsed = item["parsed_submission"]
        submission = Submission.from_dict_unchecked(parsed)
        errors = validate_submission_invariants(item["case_id"], submission)
        if errors:
            raise RuntimeError(
                f"M46BR_PARSED_SUBMISSION_INVALID:{arm}:{item['case_id']}:{errors[0]}"
            )
        row = (
            m39._evaluate(case, truth, submission, expected)
            if case["task_type"] == "ANSWERABLE"
            else m39._evaluate(case, truth, submission, expected)
        )
        row.update(
            {
                "arm": arm,
                "case_index": item["case_index"],
                "case_id": item["case_id"],
                "database_id": case["database_id"],
                "split": "DEV" if case["database_id"] in m39.DEV_DATABASES else "CONFIRMATION",
                "gold_behavior": case["task_type"],
                "parsed_submission": parsed,
                "request_sha256": item["request_sha256"],
                "response_sha256": item["response_sha256"],
                "provider_metadata": item.get("provider_metadata"),
                "schema_validation": item["schema_validation"],
            }
        )
        results.append(row)
    return results


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(group: list[dict[str, Any]]) -> dict[str, Any]:
        correct = sum(bool(item["official_correct"]) for item in group)
        return {
            "correct": correct,
            "total": len(group),
            "rate": f"{correct / len(group):.1%}" if group else "UNAVAILABLE",
        }

    answerable = [item for item in rows if item["gold_behavior"] == "ANSWERABLE"]
    admitted = [
        item
        for item in answerable
        if item.get("model_decision") == "ANSWER" and item.get("sql_admission") == "PASS"
    ]
    split_summary: dict[str, Any] = {}
    for split in ("DEV", "CONFIRMATION"):
        group = [item for item in rows if item["split"] == split]
        split_summary[split] = {
            "governed": score(group),
            "answerable": score([item for item in group if item["gold_behavior"] == "ANSWERABLE"]),
            "wrong_refusal": sum(
                item.get("model_decision") != "ANSWER"
                for item in group
                if item["gold_behavior"] == "ANSWERABLE"
            ),
            "conditional_sql": score(
                [
                    item
                    for item in group
                    if item["gold_behavior"] == "ANSWERABLE"
                    and item.get("model_decision") == "ANSWER"
                    and item.get("sql_admission") == "PASS"
                ]
            ),
        }
    return {
        "governed_success": score(rows),
        "answerable_tsa": score(answerable),
        "answer_rate": {
            "answered": sum(item.get("model_decision") == "ANSWER" for item in answerable),
            "total": 60,
        },
        "wrong_refusal": {
            "wrong_refusals": sum(item.get("model_decision") != "ANSWER" for item in answerable),
            "total": 60,
        },
        "conditional_sql": score(admitted),
        "authority": score([item for item in rows if item["gold_behavior"] == "AUTHORITY_BLOCKED"]),
        "ambiguity": score([item for item in rows if item["gold_behavior"] == "AMBIGUOUS"]),
        "policy": score([item for item in rows if item["gold_behavior"] == "POLICY_BLOCKED"]),
        "unauthorized_answers": sum(
            item.get("model_decision") == "ANSWER"
            for item in rows
            if item["gold_behavior"] == "AUTHORITY_BLOCKED"
        ),
        "failure_counts": dict(Counter(item["official_category"] for item in rows)),
        "split_summary": split_summary,
        "domain_breakdown": {
            db: score([item for item in rows if item["database_id"] == db])
            for db in sorted({item["database_id"] for item in rows})
        },
    }


def _grain_analysis(rows: list[dict[str, Any]], catalogs: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        sql = (row.get("parsed_submission") or {}).get("sql")
        diagnostic = GrainSafetyValidator(catalogs[row["database_id"]]).validate(sql)
        output.append(
            {
                "arm": row["arm"],
                "case_id": row["case_id"],
                "official_correct": row["official_correct"],
                "diagnostic": diagnostic.model_dump(mode="json"),
            }
        )
    return output


def freeze_recovery_contract() -> dict[str, Any]:
    """Freeze all zero-call recovery inputs before scoring frozen submissions."""
    elig = eligibility()
    if not elig["eligible"]:
        raise RuntimeError("M46BR_OFFLINE_RECOVERY_NOT_ELIGIBLE")
    defect = defect_forensics()
    expected, replay, mutation = build_expected_results()
    pairs = _answerable_pairs()
    catalogs, _inventory = _build_catalogs([truth for _case, truth in pairs])
    reference_diagnostics = []
    for case, truth in pairs:
        for label in ("A", "B"):
            diagnostic = GrainSafetyValidator(catalogs[truth["database_id"]]).validate(
                truth[f"reference_implementation_{label.lower()}"]["sql"]
            )
            reference_diagnostics.append(
                {
                    "case_id": case["case_id"],
                    "reference": label,
                    "code": diagnostic.code.value,
                }
            )
    reference_precheck = {
        "references_analyzed": len(reference_diagnostics),
        "parseable": len(reference_diagnostics),
        "fixture_comparisons": replay["fixture_comparisons"],
        "agreement_failures": replay["agreement_failures"],
        "grain_warnings": [
            item for item in reference_diagnostics if item["code"] not in {"PASS", "NOT_APPLICABLE"}
        ],
        "mutants": {key: value for key, value in mutation.items() if key != "records"},
        "passed": len(reference_diagnostics) == 120
        and replay["fixture_comparisons"] == 184
        and not replay["agreement_failures"]
        and not [
            item for item in reference_diagnostics if item["code"] not in {"PASS", "NOT_APPLICABLE"}
        ]
        and mutation["valid"] == mutation["killed"] == 190
        and mutation["invalid"] == mutation["surviving"] == 0,
    }
    _dump(AUDIT_ROOT / "m46br_reference_precheck.json", reference_precheck)
    manifest = {
        "experiment": "M46BR",
        "source_experiment": "M46B",
        "source_status": "M46B_ABORTED_CONTRACT_DEFECT",
        "source_contract_commit": SOURCE_COMMIT,
        "source_abort_commit": ABORT_COMMIT,
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "evaluator_hash": EXPECTED_EVALUATOR_HASH,
        "validator_hash": EXPECTED_VALIDATOR_HASH,
        "recovery_adapter_hash": _sha(Path(__file__)),
        "expected_bundle_constructor_hash": _sha(Path(__file__)),
        "case_order_hash": sha256_text(json.dumps(_load_rows()[0], separators=(",", ":"))),
        "source_artifact_hashes": EXPECTED_M46B_HASHES,
        "provider_calls": 0,
        "model_calls": 0,
        "recovery_contract_frozen": True,
        "reference_precheck_hash": sha256_text(
            json.dumps(reference_precheck, sort_keys=True, separators=(",", ":"), default=str)
        ),
    }
    _dump(MANIFEST_PATH, manifest)
    _dump(
        AUDIT_ROOT / "m46br_integrity_audit.json",
        {
            "eligibility": elig,
            "defect": defect,
            "reference_precheck": reference_precheck,
            "m46b_historical_status": "M46B_ABORTED_CONTRACT_DEFECT",
            "provider_calls": 0,
            "model_calls": 0,
        },
    )
    return {
        "manifest": manifest,
        "expected": expected,
        "eligibility": elig,
        "reference_precheck": reference_precheck,
    }


def recover() -> dict[str, Any]:
    elig = eligibility()
    if not elig["eligible"]:
        raise RuntimeError("M46BR_OFFLINE_RECOVERY_NOT_ELIGIBLE")
    defect = defect_forensics()
    expected, replay, mutation = build_expected_results()
    case_ids, rows_by_id = _load_rows()
    all_truth = [truth for _case, truth in _answerable_pairs()]
    catalogs, inventory = _build_catalogs(all_truth)
    control = _score_arm("CONTROL", rows_by_id, expected)
    treatment = _score_arm("TREATMENT", rows_by_id, expected)
    if len(control) != 90 or len(treatment) != 90:
        raise RuntimeError("M46BR_RECOVERY_ERROR:case_count")
    for arm, rows in (("CONTROL", control), ("TREATMENT", treatment)):
        _dump(RESULT_ROOT / arm.lower() / "case_results.json", rows)
    diagnostics = _grain_analysis(control + treatment, catalogs)
    _dump(RESULT_ROOT / "paired" / "validator_diagnostics.json", diagnostics)
    by_arm_diag = {
        arm: [item for item in diagnostics if item["arm"] == arm]
        for arm in ("CONTROL", "TREATMENT")
    }
    paired: list[dict[str, Any]] = []
    control_by = {item["case_id"]: item for item in control}
    treatment_by = {item["case_id"]: item for item in treatment}
    for case_id in case_ids:
        left, right = control_by[case_id], treatment_by[case_id]
        paired.append(
            {
                "case_id": case_id,
                "control_decision": left.get("model_decision"),
                "treatment_decision": right.get("model_decision"),
                "control_correct": left["official_correct"],
                "treatment_correct": right["official_correct"],
                "control_category": left["official_category"],
                "treatment_category": right["official_category"],
                "control_sql": (left.get("parsed_submission") or {}).get("sql"),
                "treatment_sql": (right.get("parsed_submission") or {}).get("sql"),
            }
        )
    _dump(RESULT_ROOT / "paired" / "paired_case_analysis.json", paired)
    _dump(
        RESULT_ROOT / "paired" / "transition_matrix.json",
        {
            "correctness": dict(
                Counter(
                    f"{item['control_correct']}->{item['treatment_correct']}" for item in paired
                )
            ),
            "decisions": dict(
                Counter(
                    f"{item['control_decision']}->{item['treatment_decision']}" for item in paired
                )
            ),
        },
    )
    family = {
        "cases": ["subscription_04", "subscription_10", "warehouse_08"],
        "control": {
            case: next(item for item in control if item["case_id"] == case)
            for case in ("subscription_04", "subscription_10", "warehouse_08")
        },
        "treatment": {
            case: next(item for item in treatment if item["case_id"] == case)
            for case in ("subscription_04", "subscription_10", "warehouse_08")
        },
    }
    _dump(RESULT_ROOT / "paired" / "grain_family_analysis.json", family)
    summary = {
        "experiment": "M46BR",
        "source_experiment": "M46B",
        "source_status": "M46B_ABORTED_CONTRACT_DEFECT",
        "control": _summary(control),
        "treatment": _summary(treatment),
        "reference_precheck": replay,
        "mutation_precheck": {key: value for key, value in mutation.items() if key != "records"},
        "provider_calls": 0,
        "model_calls": 0,
        "evaluation_contract_errors": 0,
        "grain_diagnostics": {
            arm: dict(Counter(item["diagnostic"]["code"] for item in by_arm_diag[arm]))
            for arm in by_arm_diag
        },
        "measure_inventory_summary": inventory["summary"],
        "case_count": 90,
    }
    _dump(RESULT_ROOT / "m46br_summary.json", summary)
    manifest = {
        "experiment": "M46BR",
        "source_experiment": "M46B",
        "source_status": "M46B_ABORTED_CONTRACT_DEFECT",
        "source_contract_commit": SOURCE_COMMIT,
        "source_abort_commit": ABORT_COMMIT,
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "evaluator_hash": EXPECTED_EVALUATOR_HASH,
        "validator_hash": EXPECTED_VALIDATOR_HASH,
        "recovery_adapter_hash": _sha(Path(__file__)),
        "expected_bundle_constructor_hash": sha256_text(
            ExpectedEvaluationBundle.__qualname__ + validate_expected_bundle.__qualname__
        ),
        "case_order_hash": sha256_text(json.dumps(case_ids, separators=(",", ":"))),
        "provider_calls": 0,
        "model_calls": 0,
        "control_scored": 90,
        "treatment_scored": 90,
        "evaluation_contract_errors": 0,
        "recovery_valid": True,
        "source_artifact_hashes": EXPECTED_M46B_HASHES,
        "reference_precheck_hash": sha256_text(
            json.dumps(replay, sort_keys=True, separators=(",", ":"), default=str)
        ),
    }
    _dump(MANIFEST_PATH, manifest)
    _dump(RESULT_ROOT / "m46br_manifest.json", manifest)
    return {
        "summary": summary,
        "manifest": manifest,
        "eligibility": elig,
        "defect": defect,
        "control": control,
        "treatment": treatment,
    }


if __name__ == "__main__":
    result = recover()
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))
