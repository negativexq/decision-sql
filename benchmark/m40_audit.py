"""M40 full benchmark integrity audit.

The audit is offline and model-independent.  It reads the repaired benchmark,
executes references and mutants in isolated PostgreSQL transactions, and emits
the M40 contract evidence used to gate M41.
"""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

from benchmark.authoring import SCHEMA_NAMES, connection_kwargs_from_env, seed_database
from benchmark.context import load_authority
from benchmark.m38_authoring import M38_DATABASES, seed_m38_database
from benchmark.m40_repair import (
    M38_CASES,
    M38_TRUTH,
    _reference_source_fields,
    historical_m39_hashes,
)
from benchmark.model_contract import (
    context_contains_fact,
    frozen_benchmark_content_hash,
    governance_instructions,
    request_leakage,
    serialize_governed_context_v1,
)
from benchmark.models import ResultContract, compare_rows
from benchmark.safety import execute_query

ROOT = Path(__file__).resolve().parent
M40_VERSION = "0.2.1-dev"
PILOT_CASES = ROOT / "cases" / "pilot"
PILOT_TRUTH = ROOT / "ground_truth" / "pilot"
M39_ROOT = ROOT / "experiments" / "results" / "m39"
ALL_DATABASES = set(SCHEMA_NAMES) | set(M38_DATABASES)
DEV_DATABASES = {"commerce_ops", "fleet_ops", "support_ops", "subscription_billing"}
CONFIRMATION_DATABASES = {"warehouse_logistics", "risk_operations"}


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    split = _load(ROOT / "splits" / "m38_dev.json")
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case_id in split["case_ids"]:
        if str(case_id).startswith(("commerce_", "fleet_", "support_")):
            case_dir, truth_dir = PILOT_CASES, PILOT_TRUTH
        else:
            case_dir, truth_dir = M38_CASES, M38_TRUTH
        rows.append((_load(case_dir / f"{case_id}.json"), _load(truth_dir / f"{case_id}.json")))
    return rows


def _schema(database_id: str) -> str:
    if database_id in M38_DATABASES:
        return M38_DATABASES[database_id]
    return SCHEMA_NAMES[database_id]


def _seed(database_id: str) -> None:
    if database_id in M38_DATABASES:
        seed_m38_database(database_id)
    else:
        seed_database(database_id, connection_kwargs_from_env())


def _run(
    database_id: str, sql: str, patch_sql: list[str] | None = None
) -> tuple[list[str], list[tuple[Any, ...]]]:
    return execute_query(
        connection_kwargs_from_env(), _schema(database_id), sql, patch_sql=patch_sql
    )


def _fixtures(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"fixture_id": "base", "patch_sql": []}] + list(
        truth.get("counterfactual_fixtures", [])
    )


def _authority_field_visible(database_id: str, table: str, column: str) -> bool:
    attrs = load_authority(database_id)["attributes"]
    return any(
        str(item.get("entity_id", "")).endswith(f":{table}")
        and (
            str(item.get("physical_column_or_path", "")) == column
            or str(item.get("physical_column_or_path", "")).startswith(f"{column} ")
        )
        for item in attrs
    )


def _json_path_visible(database_id: str, table: str, key: str) -> bool:
    attrs = load_authority(database_id)["attributes"]
    return any(
        str(item.get("entity_id", "")).endswith(f":{table}")
        and "payload" in str(item.get("physical_column_or_path", ""))
        and key in str(item.get("physical_column_or_path", ""))
        for item in attrs
    )


def context_coverage(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    required_fields = 0
    visible_fields = 0
    required_paths = 0
    visible_paths = 0
    tie_break_required = 0
    tie_break_visible = 0
    fact_valid = 0
    for case, truth in rows:
        if case["task_type"] != "ANSWERABLE":
            continue
        sqls = [
            str(truth.get("reference_implementation_a", {}).get("sql", "")),
            str(truth.get("reference_implementation_b", {}).get("sql", "")),
        ]
        fields, paths = _reference_source_fields(case["database_id"], sqls)
        missing_fields = [
            f"{table}.{column}"
            for table, column in sorted(fields)
            if not _authority_field_visible(case["database_id"], table, column)
        ]
        missing_paths = []
        for path in sorted(paths):
            table, expression = path.split(".", 1)
            key_match = re.search(r"\{([^}]+)\}", expression)
            key = key_match.group(1).split(",")[-1] if key_match else expression
            if not _json_path_visible(case["database_id"], table, key):
                missing_paths.append(path)
        required_fields += len(fields)
        visible_fields += len(fields) - len(missing_fields)
        required_paths += len(paths)
        visible_paths += len(paths) - len(missing_paths)
        tie_required = bool(
            re.search(r"latest|tie-break|row_number|distinct on", case["question"], re.I)
        )
        tie_fields = {
            column
            for table, column in fields
            if column.endswith("_id")
            or column in {"assessed_at", "snapshot_at", "starts_on", "changed_at", "attempted_at"}
        }
        missing_ties = [
            f"{table}.{column}"
            for table, column in fields
            if tie_required
            and column
            in {"assessment_id", "snapshot_id", "subscription_id", "change_id", "attempt_id"}
            and not _authority_field_visible(case["database_id"], table, column)
        ]
        if tie_required:
            tie_break_required += len(tie_fields)
            tie_break_visible += len(tie_fields) - len(missing_ties)
        invalid_facts = [
            fact
            for fact in truth.get("required_context_facts", [])
            if not context_contains_fact(case["database_id"], fact)
        ]
        if not invalid_facts:
            fact_valid += 1
        cases.append(
            {
                "case_id": case["case_id"],
                "database_id": case["database_id"],
                "physical_fields": sorted(f"{table}.{column}" for table, column in fields),
                "missing_fields": missing_fields,
                "required_json_paths": sorted(paths),
                "missing_json_paths": missing_paths,
                "missing_tie_break_fields": missing_ties,
                "invalid_required_context_facts": invalid_facts,
                "passed": not missing_fields
                and not missing_paths
                and not missing_ties
                and not invalid_facts,
            }
        )
    return {
        "answerable_cases": len(cases),
        "cases": cases,
        "complete_cases": sum(item["passed"] for item in cases),
        "required_fields": required_fields,
        "visible_fields": visible_fields,
        "required_json_paths": required_paths,
        "visible_json_paths": visible_paths,
        "required_tie_break_fields": tie_break_required,
        "visible_tie_break_fields": tie_break_visible,
        "required_context_facts_valid": fact_valid,
        "passed": len(cases) == 60 and all(item["passed"] for item in cases),
    }


def reference_and_fixture_audit(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    failures: dict[str, list[str]] = defaultdict(list)
    expected: dict[str, Any] = {}
    comparisons = 0
    answerable = [(case, truth) for case, truth in rows if case["task_type"] == "ANSWERABLE"]
    for case, truth in answerable:
        _seed(case["database_id"])
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        fixture_results: dict[str, Any] = {}
        for fixture in _fixtures(truth):
            try:
                cols_a, rows_a = _run(
                    case["database_id"],
                    truth["reference_implementation_a"]["sql"],
                    fixture["patch_sql"],
                )
                cols_b, rows_b = _run(
                    case["database_id"],
                    truth["reference_implementation_b"]["sql"],
                    fixture["patch_sql"],
                )
                same, reason = compare_rows(rows_a, rows_b, contract)
                comparisons += 1
                if not same:
                    failures[case["case_id"]].append(
                        f"{fixture['fixture_id']}:REFERENCE_AGREEMENT:{reason}"
                    )
                fixture_results[fixture["fixture_id"]] = {"columns": cols_a, "rows": rows_a}
            except Exception as exc:
                failures[case["case_id"]].append(
                    f"{fixture['fixture_id']}:EXECUTION:{type(exc).__name__}:{str(exc)[:160]}"
                )
        expected[case["case_id"]] = {
            "contract": truth["semantic_target"]["result_comparison_contract"],
            "fixtures": fixture_results,
        }
    return {
        "answerable_cases": len(expected),
        "reference_pairs": len(expected) * 2,
        "fixture_comparisons": comparisons,
        "failures": dict(failures),
        "expected": expected,
        "passed": len(expected) == 60 and not failures,
    }


def mutation_audit(
    rows: list[tuple[dict[str, Any], dict[str, Any]]], references: dict[str, Any]
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    failures: dict[str, list[str]] = defaultdict(list)
    families: Counter[str] = Counter()
    killed_by_fixture: Counter[str] = Counter()
    for case, truth in rows:
        if case["task_type"] != "ANSWERABLE":
            continue
        _seed(case["database_id"])
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        expected = references["expected"].get(case["case_id"], {}).get("fixtures", {})
        for mutant in truth.get("semantic_mutants", []):
            counts["authored"] += 1
            families[
                str(mutant.get("failure_category", mutant.get("target_component", "OTHER")))
            ] += 1
            if (
                mutant.get("status") != "VALID"
                or not mutant.get("sql")
                or not mutant.get("semantic_rationale")
            ):
                counts["invalid"] += 1
                continue
            killed = False
            executable = True
            for fixture in _fixtures(truth):
                try:
                    _columns, rows_mutant = _run(
                        case["database_id"], mutant["sql"], fixture["patch_sql"]
                    )
                    same, _reason = compare_rows(
                        rows_mutant,
                        expected.get(fixture["fixture_id"], {}).get("rows", []),
                        contract,
                    )
                    if not same:
                        killed = True
                        killed_by_fixture[fixture["fixture_id"]] += 1
                except Exception:
                    executable = False
            if executable:
                counts["executed"] += 1
            else:
                counts["invalid"] += 1
            if killed:
                counts["killed"] += 1
            else:
                counts["survived"] += 1
                failures[case["case_id"]].append(str(mutant.get("mutant_id", "MISSING")))
    counts.setdefault("authored", 0)
    counts.setdefault("executed", 0)
    counts.setdefault("killed", 0)
    counts.setdefault("survived", 0)
    counts.setdefault("invalid", 0)
    return {
        **dict(counts),
        "families": dict(families),
        "killed_by_fixture": dict(killed_by_fixture),
        "failures": dict(failures),
        "passed": counts["authored"] > 0
        and counts["executed"] == counts["authored"]
        and counts["killed"] == counts["authored"]
        and counts["invalid"] == 0
        and counts["survived"] == 0,
    }


def ordering_population_audit(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for case, truth in rows:
        if case["task_type"] != "ANSWERABLE":
            continue
        target = truth["semantic_target"]
        question_order = bool(
            re.search(
                r"\bordered\s+by\b|\bascending\b|\bdescending\b|\bsorted\s+by\b",
                case["question"],
                re.I,
            )
        )
        contract_order = bool(target["result_comparison_contract"].get("row_order"))
        order_source = target.get("semantic_provenance", {}).get("ordering", {}).get("source")
        population = target.get("population")
        population_visible = target.get("semantic_provenance", {}).get("population", {}).get(
            "source"
        ) in {
            "QUESTION",
            "QUESTION_EXPLICIT",
            "VISIBLE_AUTHORITY",
            "VISIBLE_BUSINESS_RULE",
        }
        records.append(
            {
                "case_id": case["case_id"],
                "ordering_valid": (not contract_order and order_source == "NOT_APPLICABLE")
                or (
                    contract_order
                    and order_source in {"QUESTION_EXPLICIT", "VISIBLE_BUSINESS_RULE"}
                ),
                "population_valid": population
                in {"matching-only", "all-anchors", "preserve-anchor", "exists", "anti-exists"}
                and population_visible,
                "population": population,
                "row_order": contract_order,
                "question_explicit_order": question_order,
            }
        )
    return {
        "cases": records,
        "ordering_valid": sum(item["ordering_valid"] for item in records),
        "population_valid": sum(item["population_valid"] for item in records),
        "passed": len(records) == 60
        and all(item["ordering_valid"] and item["population_valid"] for item in records),
    }


def governance_audit(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    invalid: dict[str, list[str]] = defaultdict(list)
    for case, truth in rows:
        counts[case["task_type"]] += 1
        if case["task_type"] == "AUTHORITY_BLOCKED":
            missing = truth.get("evidence", {}).get("missing_relationship", "")
            target_relations = truth.get("semantic_target", {}).get("relationships", [])
            relationships = load_authority(case["database_id"])["relationships"]
            absent = bool(missing) and not any(
                item.get("relationship_id") == missing and item.get("authorized") is True
                for item in relationships
            )
            absent = absent or any(
                relation
                not in {
                    item.get("relationship_id")
                    for item in relationships
                    if item.get("authorized") is True
                }
                for relation in target_relations
            )
            absent = absent or bool(truth.get("evidence", {}).get("missing_authority"))
            if not absent:
                invalid[case["case_id"]].append("AUTHORITY_RELATIONSHIP_NOT_ABSENT")
        elif case["task_type"] == "AMBIGUOUS":
            if not truth.get("evidence", {}).get("proof_sql_a") or not truth.get(
                "evidence", {}
            ).get("proof_sql_b"):
                invalid[case["case_id"]].append("AMBIGUITY_PROOF_MISSING")
        elif case["task_type"] == "POLICY_BLOCKED" and not truth.get("evidence", {}).get(
            "policy_violation"
        ):
            invalid[case["case_id"]].append("POLICY_EVIDENCE_MISSING")
    return {
        "distribution": dict(counts),
        "invalid": dict(invalid),
        "passed": not invalid
        and counts
        == Counter(
            {"ANSWERABLE": 60, "AUTHORITY_BLOCKED": 15, "AMBIGUOUS": 9, "POLICY_BLOCKED": 6}
        ),
    }


def request_leakage_audit(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    hits: dict[str, list[str]] = {}
    for case, _truth in rows:
        instructions = governance_instructions()
        context = serialize_governed_context_v1(case["database_id"])
        text = f"SYSTEM:\n{instructions}\n\nUSER:\nCase ID:\n{case['case_id']}\n\nQuestion:\n{case['question']}\n\nGoverned context:\n{context}"
        leaked = request_leakage(type("Request", (), {"request_text": text})(), case)
        if leaked:
            hits[case["case_id"]] = leaked
    return {
        "cases": len(rows),
        "leakage_cases": len(hits),
        "details": hits,
        "passed": len(rows) == 90 and not hits,
    }


def m39_failure_adjudication(
    rows: list[tuple[dict[str, Any], dict[str, Any]]], coverage: dict[str, Any]
) -> list[dict[str, Any]]:
    result_path = M39_ROOT / "m39_case_results.json"
    historical_value = json.loads(result_path.read_text(encoding="utf-8"))
    historical = (
        historical_value
        if isinstance(historical_value, list)
        else historical_value.get("cases", [])
    )
    by_id = {item["case_id"]: item for item in historical}
    coverage_by_id = {item["case_id"]: item for item in coverage["cases"]}
    out: list[dict[str, Any]] = []
    for case, _truth in rows:
        item = by_id.get(case["case_id"])
        if not item or item.get("official_correct"):
            continue
        cov = coverage_by_id.get(case["case_id"], {})
        if case["task_type"] == "ANSWERABLE" and cov.get("invalid_required_context_facts"):
            primary = "CONTEXT_DEFECT"
        elif case["case_id"] in {"warehouse_02"}:
            primary = "POPULATION_CONTRACT_DEFECT"
        elif case["case_id"] in {"warehouse_11"}:
            primary = "ORDERING_CONTRACT_DEFECT"
        elif item.get("official_category") == "WRONG_GOVERNED_DECISION":
            primary = "CONFIRMED_MODEL_OVER_ABSTENTION"
        else:
            primary = "CONFIRMED_MODEL_SQL_ERROR"
        out.append(
            {
                "case_id": case["case_id"],
                "database": case["database_id"],
                "split": "DEV" if case["database_id"] in DEV_DATABASES else "CONFIRMATION",
                "historical_category": item.get("official_category"),
                "historical_model_decision": item.get("model_decision"),
                "question_sufficiency": not bool(cov.get("invalid_required_context_facts")),
                "model_visible_context_sufficiency": cov.get("passed", True),
                "benchmark_defect": primary.endswith("DEFECT")
                and not primary.startswith("CONFIRMED"),
                "primary_adjudication": primary,
                "repair_needed": primary
                not in {"CONFIRMED_MODEL_OVER_ABSTENTION", "CONFIRMED_MODEL_SQL_ERROR"},
                "evidence": {
                    "invalid_required_context_facts": cov.get("invalid_required_context_facts", [])
                },
            }
        )
    return out


def run_audit() -> dict[str, Any]:
    rows = _rows()
    coverage = context_coverage(rows)
    refs = reference_and_fixture_audit(rows)
    mutants = mutation_audit(rows, refs)
    ordering = ordering_population_audit(rows)
    governance = governance_audit(rows)
    leakage = request_leakage_audit(rows)
    failures = m39_failure_adjudication(rows, coverage)
    return {
        "benchmark_version": M40_VERSION,
        "benchmark_content_hash": frozen_benchmark_content_hash(),
        "provider_calls": 0,
        "case_count": len(rows),
        "task_distribution": dict(Counter(case["task_type"] for case, _ in rows)),
        "context_coverage": coverage,
        "reference_validation": {key: value for key, value in refs.items() if key != "expected"},
        "mutation_validation": mutants,
        "ordering_population": ordering,
        "governance": governance,
        "leakage": leakage,
        "m39_failure_adjudication": failures,
        "m39_artifacts_after_audit": historical_m39_hashes(),
        "passed": coverage["passed"]
        and refs["passed"]
        and mutants["passed"]
        and ordering["passed"]
        and governance["passed"]
        and leakage["passed"],
        "expected_results": refs["expected"],
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), indent=2, sort_keys=True, default=str))
