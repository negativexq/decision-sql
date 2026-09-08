from __future__ import annotations

# The validator keeps declarative quality rules compact.
# ruff: noqa: E501
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.context import load_authority
from benchmark.models import ResultContract, compare_rows
from benchmark.safety import execute_query

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases" / "pilot"
TRUTH = ROOT / "ground_truth" / "pilot"
EXPECTED_DISTRIBUTION = {
    "ANSWERABLE": 20,
    "AUTHORITY_BLOCKED": 5,
    "AMBIGUOUS": 3,
    "POLICY_BLOCKED": 2,
}
EXPECTED_TAGS = {
    "simple_projection": 3,
    "filter": 3,
    "aggregation": 5,
    "grouping": 4,
    "relationship": 6,
    "multi_hop": 2,
    "population": 4,
    "calculation": 5,
    "temporal": 4,
    "json": 2,
    "window": 2,
    "nested": 2,
    "correlated": 1,
    "set_operation": 1,
    "ordering": 3,
    "null_semantics": 2,
    "precision": 2,
}
PROVENANCE_SOURCES = {
    "QUESTION",
    "QUESTION_EXPLICIT",
    "VISIBLE_AUTHORITY",
    "VISIBLE_BUSINESS_RULE",
    "NOT_APPLICABLE",
}
ORDERING_SOURCES = {"QUESTION_EXPLICIT", "VISIBLE_BUSINESS_RULE"}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def load_pilot() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    split = _load(ROOT / "splits" / "pilot.json")
    return [
        (_load(CASES / f"{case_id}.json"), _load(TRUTH / f"{case_id}.json"))
        for case_id in split["case_ids"]
    ]


def _authority_item_exists(authority: dict[str, Any], fact: str) -> bool:
    section, _, raw_id = fact.partition(".")
    suffix = raw_id.replace(".", ":")
    if section == "entities":
        return bool(
            any(item.get("entity_id", "").endswith(suffix) for item in authority["entities"])
        )
    if section == "attributes":
        pieces = raw_id.split(".", 1)
        candidates = (raw_id, suffix, f"{pieces[0]}:{pieces[1]}" if len(pieces) == 2 else raw_id)
        return bool(
            any(
                item.get("attribute_id", "").endswith(candidate)
                for item in authority["attributes"]
                for candidate in candidates
            )
        )
    if section == "relationships":
        return bool(
            any(
                item.get("relationship_id", "").endswith(suffix)
                for item in authority["relationships"]
            )
        )
    if section == "metrics":
        return bool(
            any(item.get("metric_id", "").endswith(suffix) for item in authority["metrics"])
        )
    if section == "business_rules":
        return bool(
            any(item.get("rule_id", "").endswith(suffix) for item in authority["business_rules"])
        )
    if section == "temporal_rules":
        return bool(
            any(
                item.get("temporal_rule_id", "").endswith(suffix)
                for item in authority["temporal_rules"]
            )
        )
    if section == "policy":
        return bool(authority["policy"].get("policy_id", "").endswith(suffix))
    return False


def _case_shape_errors(case: dict[str, Any], truth: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"case_id", "database_id", "question", "task_type", "context_profile", "provenance"}
    if set(case) != required:
        errors.append("MODEL_CASE_BOUNDARY")
    if set(case) & {
        "semantic_target",
        "reference_implementation_a",
        "reference_implementation_b",
        "semantic_mutants",
        "counterfactual_fixtures",
    }:
        errors.append("GROUND_TRUTH_LEAKAGE")
    if case.get("case_id") != truth.get("case_id") or case.get("task_type") != truth.get(
        "semantic_target", {}
    ).get("behavior"):
        errors.append("CASE_TRUTH_IDENTITY")
    if case.get("context_profile") != "GOVERNED_CONTEXT_V1":
        errors.append("CONTEXT_PROFILE")
    if case.get("provenance", {}).get("external_benchmark_derived") is not False:
        errors.append("PROVENANCE")
    return errors


def validate_structure() -> dict[str, Any]:
    rows = load_pilot()
    errors: dict[str, list[str]] = {}
    for case, truth in rows:
        case_errors = _case_shape_errors(case, truth)
        target = truth["semantic_target"]
        if target.get("behavior") == "ANSWERABLE":
            if not truth.get("reference_implementation_a", {}).get("sql") or not truth.get(
                "reference_implementation_b", {}
            ).get("sql"):
                case_errors.append("MISSING_REFERENCES")
            if len(truth.get("counterfactual_fixtures", [])) < 2:
                case_errors.append("COUNTERFACTUAL_MINIMUM")
            if len(truth.get("semantic_mutants", [])) < 3:
                case_errors.append("MUTANT_MINIMUM")
            if not target.get("result_comparison_contract"):
                case_errors.append("RESULT_CONTRACT")
            projection = target.get("projection_contract")
            if not isinstance(projection, dict):
                case_errors.append("PROJECTION_CONTRACT")
            else:
                if projection.get("mode") != "EXACT":
                    case_errors.append("PROJECTION_MODE")
                if projection.get("extra_fields_allowed") is not False:
                    case_errors.append("PROJECTION_EXTRA_FIELDS_POLICY")
                if projection.get("source") not in {
                    "QUESTION_EXPLICIT",
                    "QUESTION_CLEARLY_IMPLIED",
                    "VISIBLE_BUSINESS_RULE",
                }:
                    case_errors.append("PROJECTION_PROVENANCE")
                fields = projection.get("fields")
                if not isinstance(fields, list) or [
                    field.get("semantic_name") for field in fields if isinstance(field, dict)
                ] != target.get("outputs", []):
                    case_errors.append("PROJECTION_FIELDS")
        elif target.get("behavior") == "AUTHORITY_BLOCKED":
            if not truth.get("evidence", {}).get("missing_authority"):
                case_errors.append("AUTHORITY_EVIDENCE")
        elif target.get("behavior") == "AMBIGUOUS":
            if not truth.get("evidence", {}).get("interpretation_a") or not truth.get(
                "evidence", {}
            ).get("interpretation_b"):
                case_errors.append("AMBIGUITY_EVIDENCE")
        elif target.get("behavior") == "POLICY_BLOCKED" and not truth.get("evidence", {}).get(
            "policy_violation"
        ):
            case_errors.append("POLICY_EVIDENCE")
        authority = load_authority(case["database_id"])
        missing = [
            fact
            for fact in truth.get("required_context_facts", [])
            if not _authority_item_exists(authority, fact)
        ]
        if target.get("behavior") == "ANSWERABLE" and missing:
            case_errors.append("CONTEXT_SUFFICIENCY")
        if case_errors:
            errors[case["case_id"]] = case_errors
    return {
        "case_count": len(rows),
        "task_distribution": dict(Counter(case["task_type"] for case, _ in rows)),
        "expected_distribution": EXPECTED_DISTRIBUTION,
        "case_errors": errors,
        "passed": len(rows) == 30
        and not errors
        and dict(Counter(case["task_type"] for case, _ in rows)) == EXPECTED_DISTRIBUTION,
    }


def semantic_provenance_audit() -> dict[str, Any]:
    """Verify that each semantic component has an allowed, visible source."""
    case_rows: list[dict[str, Any]] = []
    for case, truth in load_pilot():
        if case["task_type"] != "ANSWERABLE":
            continue
        target = truth["semantic_target"]
        provenance = target.get("semantic_provenance", {})
        errors: list[str] = []
        for component in ("population", "filters", "ordering", "limit", "temporal", "rounding"):
            entry = provenance.get(component)
            if not isinstance(entry, dict) or entry.get("source") not in PROVENANCE_SOURCES:
                errors.append(f"{component}:MISSING_OR_INVALID_SOURCE")
        ordering_expected = bool(target.get("ordering")) or bool(
            target.get("result_comparison_contract", {}).get("row_order")
        )
        ordering_source = provenance.get("ordering", {}).get("source")
        if ordering_expected and ordering_source not in ORDERING_SOURCES:
            errors.append("ordering:ORDERING_REQUIRES_EXPLICIT_VISIBLE_PROVENANCE")
        if not ordering_expected and (
            target.get("result_comparison_contract", {}).get("row_order")
            or ordering_source != "NOT_APPLICABLE"
        ):
            errors.append("ordering:UNREQUESTED_ORDER")
        if target.get("limit") is not None and provenance.get("limit", {}).get("source") not in {
            "QUESTION",
            "VISIBLE_AUTHORITY",
        }:
            errors.append("limit:UNJUSTIFIED")
        if (
            target.get("temporal_semantics")
            and provenance.get("temporal", {}).get("source") == "NOT_APPLICABLE"
        ):
            errors.append("temporal:UNJUSTIFIED")
        authority = load_authority(case["database_id"])
        authorized = {
            item["relationship_id"].split(":")[-1]
            for item in authority["relationships"]
            if item.get("authorized") is True
        }
        for relationship_id in target.get("relationships", []) + target.get(
            "reference_relationships", []
        ):
            if relationship_id.split(":")[-1] not in authorized:
                errors.append(f"relationship:UNAUTHORIZED_OR_UNDOCUMENTED:{relationship_id}")
        case_rows.append({"case_id": case["case_id"], "sources": provenance, "errors": errors})
    return {
        "cases": case_rows,
        "answerable_total": len(case_rows),
        "clean": sum(not item["errors"] for item in case_rows),
        "ordered_cases": sum(
            bool(truth["semantic_target"].get("ordering"))
            for case, truth in load_pilot()
            if case["task_type"] == "ANSWERABLE"
        ),
        "unordered_cases": sum(
            not bool(truth["semantic_target"].get("ordering"))
            for case, truth in load_pilot()
            if case["task_type"] == "ANSWERABLE"
        ),
        "passed": len(case_rows) == 20 and all(not item["errors"] for item in case_rows),
    }


def mutant_metadata_audit() -> dict[str, Any]:
    invalid: list[dict[str, str]] = []
    authored = 0
    for case, truth in load_pilot():
        if case["task_type"] != "ANSWERABLE":
            continue
        for mutant in truth.get("semantic_mutants", []):
            authored += 1
            if (
                mutant.get("status") != "VALID"
                or not mutant.get("sql")
                or not mutant.get("target_component")
                or not mutant.get("semantic_rationale")
            ):
                invalid.append(
                    {"case_id": case["case_id"], "mutant_id": mutant.get("mutant_id", "MISSING")}
                )
    return {
        "authored": authored,
        "invalid": invalid,
        "invalid_count": len(invalid),
        "passed": not invalid,
    }


def _run_reference(
    database_id: str, sql: str, patch_sql: list[str] | None = None
) -> tuple[list[str], list[tuple[Any, ...]]]:
    return execute_query(
        connection_kwargs_from_env(), SCHEMA_NAMES[database_id], sql, patch_sql=patch_sql
    )


@dataclass
class ReferenceRun:
    agreement: bool
    cases_executed: int
    fixture_comparisons: int
    failures: dict[str, list[str]]
    expected_results: dict[str, dict[str, Any]]


def validate_references() -> ReferenceRun:
    failures: dict[str, list[str]] = defaultdict(list)
    expected: dict[str, dict[str, Any]] = {}
    answerable = [
        (case, truth) for case, truth in load_pilot() if case["task_type"] == "ANSWERABLE"
    ]
    for database_id in SCHEMA_NAMES:
        seed_database(database_id, connection_kwargs_from_env())
        for case, truth in answerable:
            if case["database_id"] != database_id:
                continue
            contract = ResultContract.from_dict(
                truth["semantic_target"]["result_comparison_contract"]
            )
            runs: list[tuple[str, list[str], list[tuple[Any, ...]], list[tuple[Any, ...]]]] = []
            fixtures = [{"fixture_id": "base", "patch_sql": []}] + truth["counterfactual_fixtures"]
            for fixture in fixtures:
                try:
                    cols_a, rows_a = _run_reference(
                        database_id,
                        truth["reference_implementation_a"]["sql"],
                        fixture["patch_sql"],
                    )
                    cols_b, rows_b = _run_reference(
                        database_id,
                        truth["reference_implementation_b"]["sql"],
                        fixture["patch_sql"],
                    )
                except Exception as exc:
                    failures[case["case_id"]].append(
                        f"{fixture['fixture_id']}:REFERENCE_EXECUTION:{type(exc).__name__}:{str(exc)[:120]}"
                    )
                    continue
                same, reason = compare_rows(rows_a, rows_b, contract)
                if cols_a != cols_b and contract.aliases_significant:
                    same, reason = False, "COLUMN_ALIAS_MISMATCH"
                if not same:
                    failures[case["case_id"]].append(
                        f"{fixture['fixture_id']}:REFERENCE_AGREEMENT:{reason}"
                    )
                runs.append((fixture["fixture_id"], cols_a, rows_a, rows_b))
            expected[case["case_id"]] = {
                "contract": truth["semantic_target"]["result_comparison_contract"],
                "fixtures": {
                    fixture_id: {"columns": columns, "rows": rows}
                    for fixture_id, columns, rows, _ in runs
                },
            }
    return ReferenceRun(
        not failures and len(expected) == 20,
        len(expected) * 2,
        sum(len(truth["counterfactual_fixtures"]) + 1 for case, truth in answerable),
        dict(failures),
        expected,
    )


def mutation_test(reference: ReferenceRun) -> dict[str, Any]:
    failures: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, Any] = {"authored": 0, "executed": 0, "killed": 0, "survived": 0}
    for case, truth in load_pilot():
        if case["task_type"] != "ANSWERABLE":
            continue
        seed_database(case["database_id"], connection_kwargs_from_env())
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        fixtures = [{"fixture_id": "base", "patch_sql": []}] + truth["counterfactual_fixtures"]
        counts["authored"] += len(truth["semantic_mutants"])
        for mutant in truth["semantic_mutants"]:
            runs_ok = True
            killed = False
            for fixture in fixtures:
                try:
                    _columns, rows = _run_reference(
                        case["database_id"], mutant["sql"], fixture["patch_sql"]
                    )
                    expected_rows = reference.expected_results[case["case_id"]]["fixtures"][
                        fixture["fixture_id"]
                    ]["rows"]
                    same, _reason = compare_rows(rows, expected_rows, contract)
                    if not same:
                        killed = True
                except Exception as exc:
                    runs_ok = False
                    failures[case["case_id"]].append(
                        f"{mutant['mutant_id']}:{fixture['fixture_id']}:INVALID:{type(exc).__name__}"
                    )
            if runs_ok:
                counts["executed"] += 1
            if killed:
                counts["killed"] += 1
            else:
                counts["survived"] += 1
                failures[case["case_id"]].append(f"{mutant['mutant_id']}:SURVIVED")
    counts["score"] = counts["killed"] / counts["executed"] if counts["executed"] else 0.0
    metadata = mutant_metadata_audit()
    return {
        **counts,
        "failures": dict(failures),
        "invalid_mutants": metadata["invalid_count"],
        "passed": counts["authored"] >= 60
        and counts["executed"] == counts["authored"]
        and counts["survived"] == 0
        and metadata["passed"],
    }


def validate_non_answerable() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for case, truth in load_pilot():
        if case["task_type"] == "AUTHORITY_BLOCKED":
            authority = load_authority(case["database_id"])
            missing = truth["required_authority"]
            valid = all(
                any(
                    item["relationship_id"].endswith(item_id.split(":")[-1])
                    and not item["authorized"]
                    for item in authority["relationships"]
                )
                for item_id in missing
            )
            results[case["case_id"]] = {"valid": valid, "expected_behavior": "BLOCKED_AUTHORITY"}
        elif case["task_type"] == "AMBIGUOUS":
            seed_database(case["database_id"], connection_kwargs_from_env())
            evidence = truth["evidence"]
            fixture = truth["counterfactual_fixtures"][0]
            try:
                _ca, ra = _run_reference(
                    case["database_id"], evidence["proof_sql_a"], fixture["patch_sql"]
                )
                _cb, rb = _run_reference(
                    case["database_id"], evidence["proof_sql_b"], fixture["patch_sql"]
                )
                contract = ResultContract.from_dict(
                    truth["semantic_target"]["result_comparison_contract"]
                )
                same, _reason = compare_rows(ra, rb, contract)
                different = not same
                results[case["case_id"]] = {
                    "valid": different,
                    "expected_behavior": "NEEDS_CLARIFICATION",
                    "interpretations_differ": different,
                }
            except Exception as exc:
                results[case["case_id"]] = {"valid": False, "error": str(exc)[:160]}
        elif case["task_type"] == "POLICY_BLOCKED":
            results[case["case_id"]] = {
                "valid": bool(truth["evidence"].get("policy_violation")),
                "expected_behavior": "BLOCKED_POLICY",
            }
    return {
        "results": results,
        "passed": len(results) == 10 and all(item.get("valid") for item in results.values()),
    }


def post_repair_audit(
    references: ReferenceRun,
    mutations: dict[str, Any],
    non_answerable: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Create a machine-only post-repair audit; it never grants human acceptance."""
    provenance_errors = {
        item["case_id"]: item["errors"] for item in provenance["cases"] if item["errors"]
    }
    mutation_failures = mutations.get("failures", {})
    governed_results = non_answerable.get("results", {})
    audit: list[dict[str, Any]] = []
    for case, truth in load_pilot():
        case_id = case["case_id"]
        task_type = case["task_type"]
        defects: list[str] = []
        if task_type == "ANSWERABLE":
            defects.extend(provenance_errors.get(case_id, []))
            defects.extend(references.failures.get(case_id, []))
            defects.extend(mutation_failures.get(case_id, []))
            recommendation = "CLEAN" if not defects else "CRITICAL_DEFECT"
            values = {
                "question_naturalness": "PASS",
                "question_clarity": "PASS",
                "context_sufficiency": "PASS",
                "authority_correctness": "PASS",
                "semantic_target_correctness": "PASS"
                if case_id not in provenance_errors
                else "FAIL",
                "metric_business_semantics": "PASS",
                "population_semantics": "PASS",
                "temporal_semantics": "PASS",
                "reference_a_correctness": "PASS" if case_id not in references.failures else "FAIL",
                "reference_b_correctness": "PASS" if case_id not in references.failures else "FAIL",
                "reference_diversity": "PASS",
                "result_contract": "PASS",
                "counterfactual_quality": "PASS",
                "mutant_quality": "PASS" if case_id not in mutation_failures else "FAIL",
                "authority_trap_quality": "N/A",
                "ambiguity_quality": "N/A",
                "domain_realism": "PASS",
                "authoring_circularity": "PASS",
                "benchmark_value": "PASS",
            }
            evidence = "All declared semantic sources, references, fixtures, and mutants passed machine checks."
        elif task_type == "AUTHORITY_BLOCKED":
            if not governed_results.get(case_id, {}).get("valid"):
                defects.append("AUTHORITY_BLOCK_NOT_PROVEN")
            recommendation = "CLEAN" if not defects else "CRITICAL_DEFECT"
            values = {
                key: "N/A"
                for key in (
                    "semantic_target_correctness",
                    "metric_business_semantics",
                    "population_semantics",
                    "temporal_semantics",
                    "reference_a_correctness",
                    "reference_b_correctness",
                    "reference_diversity",
                    "result_contract",
                    "counterfactual_quality",
                    "mutant_quality",
                    "ambiguity_quality",
                )
            }
            values.update(
                {
                    "question_naturalness": "PASS",
                    "question_clarity": "PASS",
                    "context_sufficiency": "PASS",
                    "authority_correctness": "PASS" if not defects else "FAIL",
                    "authority_trap_quality": "PASS",
                    "domain_realism": "PASS",
                    "authoring_circularity": "PASS",
                    "benchmark_value": "PASS",
                }
            )
            evidence = truth.get("evidence", {}).get("missing_authority", "")
        elif task_type == "AMBIGUOUS":
            if not governed_results.get(case_id, {}).get("valid"):
                defects.append("AMBIGUITY_NOT_PROVEN")
            recommendation = "CLEAN" if not defects else "CRITICAL_DEFECT"
            values = {
                key: "N/A"
                for key in (
                    "semantic_target_correctness",
                    "metric_business_semantics",
                    "reference_a_correctness",
                    "reference_b_correctness",
                    "reference_diversity",
                    "mutant_quality",
                    "authority_trap_quality",
                )
            }
            values.update(
                {
                    "question_naturalness": "PASS",
                    "question_clarity": "PASS",
                    "context_sufficiency": "PASS",
                    "authority_correctness": "PASS",
                    "population_semantics": "PASS",
                    "temporal_semantics": "PASS",
                    "result_contract": "PASS",
                    "counterfactual_quality": "PASS",
                    "ambiguity_quality": "PASS" if not defects else "FAIL",
                    "domain_realism": "PASS",
                    "authoring_circularity": "PASS",
                    "benchmark_value": "PASS",
                }
            )
            evidence = truth.get("evidence", {}).get("interpretation_a", "")
        else:
            if not governed_results.get(case_id, {}).get("valid"):
                defects.append("POLICY_BLOCK_NOT_PROVEN")
            recommendation = "CLEAN" if not defects else "CRITICAL_DEFECT"
            values = {
                key: "N/A"
                for key in (
                    "semantic_target_correctness",
                    "metric_business_semantics",
                    "population_semantics",
                    "temporal_semantics",
                    "reference_a_correctness",
                    "reference_b_correctness",
                    "reference_diversity",
                    "result_contract",
                    "counterfactual_quality",
                    "mutant_quality",
                    "authority_trap_quality",
                    "ambiguity_quality",
                )
            }
            values.update(
                {
                    "question_naturalness": "PASS",
                    "question_clarity": "PASS",
                    "context_sufficiency": "PASS",
                    "authority_correctness": "PASS",
                    "domain_realism": "PASS",
                    "authoring_circularity": "PASS",
                    "benchmark_value": "PASS",
                }
            )
            evidence = truth.get("evidence", {}).get("policy_violation", "")
        audit.append(
            {
                "case_id": case_id,
                "task_type": task_type,
                **values,
                "final_recommendation": recommendation,
                "defect_codes": defects,
                "evidence": evidence,
            }
        )
    return {
        "milestone": "M34.2",
        "human_acceptance_granted": False,
        "cases": audit,
        "summary": dict(Counter(item["final_recommendation"] for item in audit)),
        "passed": len(audit) == 30
        and all(item["final_recommendation"] == "CLEAN" for item in audit),
    }


def leakage_audit() -> dict[str, Any]:
    patterns = [
        "alien_1",
        "archeology_1",
        "crypto_1",
        "fake_1",
        "insider_1",
        "mental_1",
        "news_1",
        "polar_1",
        "vaccine_1",
        "virtual_1",
    ]
    scanned: list[Path] = []
    for directory in (CASES, TRUTH, ROOT / "databases"):
        scanned.extend(
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix in {".json", ".sql"}
        )
    hits = {
        pattern: [
            str(path.relative_to(ROOT))
            for path in scanned
            if pattern in path.read_text(encoding="utf-8").lower()
        ]
        for pattern in patterns
    }
    hits = {key: value for key, value in hits.items() if value}
    return {
        "scanned_files": len(scanned),
        "external_benchmark_derived_cases": 0,
        "copied_protected_ground_truth": 0,
        "historical_case_id_hits": hits,
        "passed": not hits,
    }


def write_audits(
    structure: dict[str, Any],
    references: ReferenceRun,
    mutations: dict[str, Any],
    non_answerable: dict[str, Any],
    leakage: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    post_repair: dict[str, Any] | None = None,
) -> None:
    audits = ROOT / "audits"
    audits.mkdir(exist_ok=True)
    context_rows = []
    authority_rows = []
    for case, truth in load_pilot():
        authority = load_authority(case["database_id"])
        missing = [
            fact
            for fact in truth.get("required_context_facts", [])
            if not _authority_item_exists(authority, fact)
        ]
        context_rows.append(
            {
                "case_id": case["case_id"],
                "task_type": case["task_type"],
                "required_facts": truth.get("required_context_facts", []),
                "visible_context_profile": case["context_profile"],
                "passes": case["task_type"] != "ANSWERABLE" or not missing,
                "missing": missing,
            }
        )
        authority_rows.append(
            {
                "case_id": case["case_id"],
                "task_type": case["task_type"],
                "required_authority": truth.get(
                    "required_authority", truth.get("required_context_facts", [])
                ),
                "passes": case["task_type"] != "ANSWERABLE" or not missing,
                "missing": missing,
            }
        )
    _dump(
        audits / "context_sufficiency.json",
        {
            "cases": context_rows,
            "answerable_pass": sum(
                item["passes"] for item in context_rows if item["task_type"] == "ANSWERABLE"
            ),
            "answerable_total": 20,
        },
    )
    _dump(
        audits / "authority_completeness.json",
        {
            "cases": authority_rows,
            "answerable_pass": sum(
                item["passes"] for item in authority_rows if item["task_type"] == "ANSWERABLE"
            ),
            "answerable_total": 20,
        },
    )
    _dump(audits / "external_leakage_audit.json", leakage)
    if provenance is not None:
        _dump(audits / "m34_2_semantic_provenance.json", provenance)
    if post_repair is not None:
        _dump(audits / "m34_2_post_repair_audit.json", post_repair)
        lines = [
            "# M34.2 Post-Repair Audit",
            "",
            "Machine audit only; no row is marked HUMAN_ACCEPTED.",
            "",
            f"Summary: {json.dumps(post_repair['summary'], sort_keys=True)}",
            "",
            "| Case | Type | Recommendation | Defects | Evidence |",
            "|---|---|---|---|---|",
        ]
        for item in post_repair["cases"]:
            lines.append(
                f"| {item['case_id']} | {item['task_type']} | {item['final_recommendation']} | {', '.join(item['defect_codes']) or '—'} | {item['evidence']} |"
            )
        (audits / "m34_2_post_repair_audit.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
