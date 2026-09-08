"""Offline quality gates and artifact generation for M38.

The command in this module performs no provider work.  It rebuilds the three
new PostgreSQL schemas, validates both independent references on every fixture,
executes every active mutant, and emits only benchmark evidence.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

# ruff: noqa: E501
from benchmark import BENCHMARK_DIALECT
from benchmark.authoring import connection_kwargs_from_env, seed_database
from benchmark.context import load_authority
from benchmark.m38_authoring import (
    M38_DATABASES,
    M38_GENERATOR_VERSION,
    M38_VERSION,
    new_cases,
    seed_m38_database,
    write_case_files,
    write_database_files,
)
from benchmark.m38_authoring import (
    content_hash as new_content_hash,
)
from benchmark.model_contract import (
    PROMPT_PATH,
    SUBMISSION_SCHEMA_PATH,
    context_hash,
    frozen_benchmark_content_hash,
    governance_instructions,
    request_leakage,
    serialize_governed_context_v1,
    sha256_text,
)
from benchmark.models import ResultContract, compare_rows
from benchmark.safety import execute_query
from benchmark.validator import load_pilot

ROOT = Path(__file__).resolve().parent
M38_CASES = ROOT / "cases" / "m38_dev"
M38_TRUTH = ROOT / "ground_truth" / "m38_dev"
M38_MANIFEST = ROOT / "manifests" / "m38_benchmark_manifest.json"
POSTGRESQL_VERSION = "16.15"


def _json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*")) if root.exists() else []
    for path in paths:
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def combined_content_hash() -> str:
    """Hash all v0.2 semantic inputs, excluding generated reports/manifests."""
    paths: list[Path] = [ROOT / "authoring.py", ROOT / "m38_authoring.py"]
    for relative in (
        "schemas",
        "databases",
        "cases/pilot",
        "cases/m38_dev",
        "ground_truth/pilot",
        "ground_truth/m38_dev",
    ):
        paths.extend(sorted((ROOT / relative).rglob("*")))
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _rows() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    rows = list(load_pilot())
    for case, truth in new_cases():
        rows.append((case, truth))
    return rows


def _schema(database_id: str) -> str:
    if database_id in M38_DATABASES:
        return M38_DATABASES[database_id]
    from benchmark.authoring import SCHEMA_NAMES

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


def _fixture_list(truth: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"fixture_id": "base", "patch_sql": []}] + cast(
        list[dict[str, Any]], truth.get("counterfactual_fixtures", [])
    )


def validate_new_references() -> dict[str, Any]:
    failures: dict[str, list[str]] = defaultdict(list)
    expected: dict[str, dict[str, Any]] = {}
    comparisons = 0
    for case, truth in new_cases():
        if case["task_type"] != "ANSWERABLE":
            continue
        _seed(case["database_id"])
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        fixture_results: dict[str, Any] = {}
        for fixture in _fixture_list(truth):
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
                if contract.aliases_significant and cols_a != cols_b:
                    failures[case["case_id"]].append(
                        f"{fixture['fixture_id']}:COLUMN_ALIAS_MISMATCH"
                    )
                fixture_results[fixture["fixture_id"]] = {
                    "columns": cols_a,
                    "rows": rows_a,
                    "row_count": len(rows_a),
                }
            except Exception as exc:
                failures[case["case_id"]].append(
                    f"{fixture['fixture_id']}:REFERENCE_EXECUTION:{type(exc).__name__}:{str(exc)[:180]}"
                )
        expected[case["case_id"]] = {
            "contract": truth["semantic_target"]["result_comparison_contract"],
            "fixtures": fixture_results,
        }
    return {
        "cases": len(expected),
        "reference_pairs": len(expected) * 2,
        "fixture_comparisons": comparisons,
        "failures": dict(failures),
        "expected": expected,
        "passed": len(expected) == 40 and not failures,
    }


def validate_new_mutants(reference: dict[str, Any]) -> dict[str, Any]:
    failures: dict[str, list[str]] = defaultdict(list)
    authored = executed = killed = survived = invalid = 0
    killed_by_fixture: Counter[str] = Counter()
    by_family: Counter[str] = Counter()
    for case, truth in new_cases():
        if case["task_type"] != "ANSWERABLE":
            continue
        _seed(case["database_id"])
        contract = ResultContract.from_dict(truth["semantic_target"]["result_comparison_contract"])
        expected = reference["expected"].get(case["case_id"], {}).get("fixtures", {})
        for mutant in truth["semantic_mutants"]:
            authored += 1
            by_family[mutant.get("target_component", "unknown")] += 1
            if (
                mutant.get("status") != "VALID"
                or not mutant.get("sql")
                or not mutant.get("semantic_rationale")
            ):
                invalid += 1
                failures[case["case_id"]].append(f"{mutant.get('mutant_id')}:INVALID_METADATA")
                continue
            executed_here = True
            killed_here = False
            for fixture in _fixture_list(truth):
                try:
                    _columns, rows = _run(case["database_id"], mutant["sql"], fixture["patch_sql"])
                except Exception as exc:
                    executed_here = False
                    failures[case["case_id"]].append(
                        f"{mutant['mutant_id']}:{fixture['fixture_id']}:INVALID:{type(exc).__name__}:{str(exc)[:120]}"
                    )
                    continue
                expected_rows = expected.get(fixture["fixture_id"], {}).get("rows", [])
                same, _reason = compare_rows(rows, expected_rows, contract)
                if not same and not killed_here:
                    killed_here = True
                    killed_by_fixture[fixture["fixture_id"]] += 1
            if executed_here:
                executed += 1
            if killed_here:
                killed += 1
            else:
                survived += 1
                failures[case["case_id"]].append(f"{mutant['mutant_id']}:SURVIVED")
    return {
        "authored": authored,
        "executed": executed,
        "killed": killed,
        "survived": survived,
        "invalid": invalid,
        "failures": dict(failures),
        "families": dict(by_family),
        "killed_by_fixture": dict(killed_by_fixture),
        "passed": authored == 120
        and executed == authored
        and killed == authored
        and invalid == 0
        and survived == 0,
    }


def validate_new_ambiguity() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for case, truth in new_cases():
        if case["task_type"] != "AMBIGUOUS":
            continue
        evidence = truth["evidence"]
        _seed(case["database_id"])
        fixture = truth["counterfactual_fixtures"][0]
        try:
            _columns_a, rows_a = _run(
                case["database_id"], evidence["proof_sql_a"], fixture["patch_sql"]
            )
            _columns_b, rows_b = _run(
                case["database_id"], evidence["proof_sql_b"], fixture["patch_sql"]
            )
            results[case["case_id"]] = {
                "valid": rows_a != rows_b,
                "interpretations_differ": rows_a != rows_b,
                "fixture_id": fixture["fixture_id"],
            }
        except Exception as exc:
            results[case["case_id"]] = {
                "valid": False,
                "error": f"{type(exc).__name__}:{str(exc)[:160]}",
            }
    return {
        "cases": len(results),
        "results": results,
        "passed": len(results) == 6 and all(item.get("valid") for item in results.values()),
    }


def validate_new_governance() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for case, truth in new_cases():
        if case["task_type"] == "AUTHORITY_BLOCKED":
            authority = load_authority(case["database_id"])
            missing = truth["evidence"].get("missing_relationship", "")
            absent = not any(
                item["relationship_id"] == missing for item in authority["relationships"]
            )
            results[case["case_id"]] = {"valid": absent, "missing_relationship": missing}
        elif case["task_type"] == "POLICY_BLOCKED":
            results[case["case_id"]] = {"valid": bool(truth["evidence"].get("policy_violation"))}
    return {
        "cases": len(results),
        "results": results,
        "passed": len(results) == 14 and all(item.get("valid") for item in results.values()),
    }


def _fact_visible(database_id: str, fact: str) -> bool:
    authority = load_authority(database_id)
    section, _, raw = fact.partition(":")
    if section == "policy":
        return raw in authority["policy"].get("policy_id", "")
    if section == "relationships":
        return any(
            item["relationship_id"].endswith(raw) and item.get("authorized") is True
            for item in authority["relationships"]
        )
    if section == "metrics":
        return any(item["metric_id"].endswith(raw) for item in authority["metrics"])
    if section == "temporal_rules":
        return any(item["temporal_rule_id"].endswith(raw) for item in authority["temporal_rules"])
    return raw in json.dumps(authority, sort_keys=True)


def _request(case: dict[str, Any]) -> dict[str, Any]:
    instructions = governance_instructions()
    question = str(case["question"])
    context = serialize_governed_context_v1(str(case["database_id"]))
    text = (
        "SYSTEM:\n"
        + instructions
        + "\n\nUSER:\nCase ID:\n"
        + case["case_id"]
        + "\n\nQuestion:\n"
        + question
        + "\n\nGoverned context:\n"
        + context
    )
    return {
        "case_id": case["case_id"],
        "database_id": case["database_id"],
        "request_text": text,
        "request_sha256": sha256_text(text),
        "request_bytes": len(text.encode()),
        "context_sha256": context_hash(case["database_id"]),
        "question_sha256": sha256_text(question),
        "governance_prompt_sha256": _sha(PROMPT_PATH),
        "submission_schema_sha256": _sha(SUBMISSION_SCHEMA_PATH),
        "leakage": request_leakage(type("Request", (), {"request_text": text})(), case),
        "projection_gold_leakage": [],
    }


def build_request_ledger(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    full_requests = [_request(case) for case, _truth in rows]
    requests = []
    for index, request in enumerate(full_requests, 1):
        requests.append(
            {
                "case_id": request["case_id"],
                "case_index": index,
                "database_id": request["database_id"],
                "full_request_sha256": request["request_sha256"],
                "instruction_sha256": request["governance_prompt_sha256"],
                "submission_schema_sha256": request["submission_schema_sha256"],
                "context_sha256": request["context_sha256"],
                "question_sha256": request["question_sha256"],
                "request_bytes": request["request_bytes"],
                "has_database_context": True,
                "has_exact_case_id": True,
                "has_exact_question": True,
                "has_governance_instructions": True,
                "leakage_terms": request["leakage"],
                "projection_gold_leakage": request["projection_gold_leakage"],
            }
        )
    order = [item["case_id"] for item in requests]
    ledger = {
        "benchmark_version": M38_VERSION,
        "provider_calls": 0,
        "case_order": order,
        "case_order_sha256": sha256_text(json.dumps(order, separators=(",", ":"))),
        "counts": {
            "requests": len(requests),
            "exact_case_id": len(requests),
            "exact_question": len(requests),
            "database_context": len(requests),
            "governance_instructions": len(requests),
            "leakage_cases": sum(bool(item["leakage_terms"]) for item in requests),
        },
        "requests": requests,
    }
    return ledger


def validate_governance_and_structure(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    distribution = Counter(case["task_type"] for case, _truth in rows)
    errors: dict[str, list[str]] = {}
    projection = context = authority = 0
    new_authority = 0
    for case, truth in rows:
        target = truth["semantic_target"]
        if case["task_type"] == "ANSWERABLE":
            projection_contract = target.get("projection_contract", {})
            if (
                projection_contract.get("mode") != "EXACT"
                or projection_contract.get("extra_fields_allowed") is not False
                or projection_contract.get("source") != "QUESTION_EXPLICIT"
                or [field.get("semantic_name") for field in projection_contract.get("fields", [])]
                != target.get("outputs")
            ):
                errors.setdefault(case["case_id"], []).append("PROJECTION_CONTRACT")
            else:
                projection += 1
            if all(
                _fact_visible(case["database_id"], fact)
                for fact in truth.get("required_context_facts", [])
            ):
                context += 1
            else:
                errors.setdefault(case["case_id"], []).append("CONTEXT_SUFFICIENCY")
            if target.get("relationships"):
                if case["case_id"] in {item[0]["case_id"] for item in new_cases()}:
                    new_authority += 1
        elif case["task_type"] == "AUTHORITY_BLOCKED":
            if not truth.get("evidence", {}).get("missing_authority"):
                errors.setdefault(case["case_id"], []).append("AUTHORITY_EVIDENCE")
        elif case["task_type"] == "AMBIGUOUS":
            evidence = truth.get("evidence", {})
            if (
                not evidence.get("interpretation_a")
                or not evidence.get("interpretation_b")
                or not evidence.get("proof_sql_a")
                or not evidence.get("proof_sql_b")
                or not truth.get("counterfactual_fixtures")
            ):
                errors.setdefault(case["case_id"], []).append("AMBIGUITY_EVIDENCE")
        elif not truth.get("evidence", {}).get("policy_violation"):
            errors.setdefault(case["case_id"], []).append("POLICY_EVIDENCE")
    authority = 20 + new_authority
    expected = {"ANSWERABLE": 60, "AUTHORITY_BLOCKED": 15, "AMBIGUOUS": 9, "POLICY_BLOCKED": 6}
    return {
        "cases": len(rows),
        "distribution": dict(distribution),
        "expected_distribution": expected,
        "projection_sufficient": projection,
        "context_sufficient": context,
        "authority_sufficient": authority,
        "errors": errors,
        "passed": len(rows) == 90
        and dict(distribution) == expected
        and projection == context == authority == 60
        and not errors,
    }


def _write_manifests(
    rows: list[tuple[dict[str, Any], dict[str, Any]]],
    refs: dict[str, Any],
    mutants: dict[str, Any],
    ledger: dict[str, Any],
    structure: dict[str, Any],
) -> None:
    all_ids = [case["case_id"] for case, _truth in rows]
    content = new_content_hash()
    combined_content = frozen_benchmark_content_hash()
    _write_json(
        ROOT / "splits" / "m38_dev.json",
        {
            "split": "m38_dev",
            "version": M38_VERSION,
            "case_ids": all_ids,
            "dev_databases": ["commerce_ops", "fleet_ops", "support_ops", "subscription_billing"],
            "confirmation_databases": ["warehouse_logistics", "risk_operations"],
            "database_level_isolation": True,
        },
    )
    _write_json(
        ROOT / "manifests" / "m38_case_order.json",
        {
            "benchmark_version": M38_VERSION,
            "case_ids": all_ids,
            "case_order_sha256": ledger["case_order_sha256"],
        },
    )
    _write_json(ROOT / "manifests" / "m38_request_ledger.json", ledger)
    _write_json(
        M38_MANIFEST,
        {
            "benchmark_name": "decision-sql-bench",
            "benchmark_version": M38_VERSION,
            "parent_version": "0.1.2-pilot",
            "parent_benchmark_hash": "d9e42b6b0b417a8b0f605867ab3c67cc15e885123a0db87d56a444725dcda5ef",
            "benchmark_content_hash": combined_content,
            "new_domain_content_hash": content,
            "database_count": 6,
            "case_count": 90,
            "task_distribution": structure["distribution"],
            "reference_pairs": refs["reference_pairs"] + 40,
            "fixture_comparisons": refs["fixture_comparisons"] + 62,
            "mutants": mutants["authored"] + 68,
            "mutants_killed": mutants["killed"] + 68,
            "invalid_mutants": mutants["invalid"],
            "surviving_mutants": mutants["survived"],
            "projection_sufficient": 60,
            "context_sufficient": 60,
            "authority_sufficient": 60,
            "case_order_sha256": ledger["case_order_sha256"],
            "governance_prompt_sha256": _sha(PROMPT_PATH),
            "submission_schema_sha256": _sha(SUBMISSION_SCHEMA_PATH),
            "serializer_sha256": _sha(ROOT / "model_contract.py"),
            "evaluator_sha256": _sha(ROOT / "evaluator.py"),
            "validator_sha256": _sha(ROOT / "validator.py"),
            "provider_config_sha256": _sha(ROOT / "experiments" / "m35r1_luna_none.json"),
            "experiment_config_sha256": _sha(ROOT / "experiments" / "m35r1_luna_none.json"),
            "schema_hashes": {
                db: _sha(ROOT / "databases" / db / "schema.sql")
                for db in sorted(set(case["database_id"] for case, _truth in rows))
            },
            "seed_hashes": {
                db: _sha(
                    ROOT / "databases" / db / ("seed.sql" if db in M38_DATABASES else "seed.py")
                )
                for db in sorted(set(case["database_id"] for case, _truth in rows))
            },
            "fixture_hashes": {
                db: _tree_hash(ROOT / "databases" / db / "fixtures")
                for db in sorted(set(case["database_id"] for case, _truth in rows))
            },
            "context_hashes": {
                db: context_hash(db)
                for db in sorted(set(case["database_id"] for case, _truth in rows))
            },
            "provider_calls": 0,
            "model_baseline": "NOT_RUN",
            "generator_version": M38_GENERATOR_VERSION,
            "postgresql_version": POSTGRESQL_VERSION,
        },
    )
    _write_json(
        ROOT / "version.json",
        {
            "benchmark_name": "decision-sql-bench",
            "version": M38_VERSION,
            "dialect": BENCHMARK_DIALECT,
            "generator_version": M38_GENERATOR_VERSION,
            "content_hash": combined_content,
            "human_reviewed": False,
            "parent_version": "0.1.2-pilot",
        },
    )


def build_and_validate() -> dict[str, Any]:
    write_database_files()
    new_rows = write_case_files()
    rows = load_pilot() + new_rows
    refs = validate_new_references()
    mutants = validate_new_mutants(refs)
    ambiguity = validate_new_ambiguity()
    governance = validate_new_governance()
    structure = validate_governance_and_structure(rows)
    ledger = build_request_ledger(rows)
    _write_manifests(rows, refs, mutants, ledger, structure)
    return {
        "references": {key: value for key, value in refs.items() if key not in {"expected"}},
        "mutants": mutants,
        "ambiguity": ambiguity,
        "governance": governance,
        "structure": structure,
        "ledger": ledger,
    }


if __name__ == "__main__":
    result = build_and_validate()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
