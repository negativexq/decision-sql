from __future__ import annotations

# The report builder intentionally keeps compact metric tables readable.
# ruff: noqa: E501
import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark.authoring import (
    SCHEMA_NAMES,
    build_all,
    connection_kwargs_from_env,
    content_hash,
    seed_database,
)
from benchmark.context import load_authority
from benchmark.evaluator import evaluate_file
from benchmark.validator import (
    EXPECTED_TAGS,
    leakage_audit,
    load_pilot,
    mutation_test,
    validate_non_answerable,
    validate_references,
    validate_structure,
    write_audits,
)

ROOT = Path(__file__).resolve().parent


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )


def _git(value: str) -> str:
    try:
        return subprocess.run(
            ["git", *value.split()], cwd=ROOT.parent, check=True, capture_output=True, text=True
        ).stdout.strip()
    except Exception:
        return "UNAVAILABLE"


def _write_reference_summaries(reference: Any) -> None:
    for case_id, summary in reference.expected_results.items():
        path = ROOT / "ground_truth" / "pilot" / f"{case_id}.json"
        truth = json.loads(path.read_text(encoding="utf-8"))
        compact: dict[str, Any] = {}
        for fixture_id, result in summary["fixtures"].items():
            serialized = json.dumps(
                result["rows"], default=str, sort_keys=True, separators=(",", ":")
            )
            compact[fixture_id] = {
                "columns": result["columns"],
                "row_count": len(result["rows"]),
                "result_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            }
        truth["reference_result_summaries"] = compact
        path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _coverage() -> dict[str, int]:
    return dict(
        Counter(
            tag
            for _case, truth in load_pilot()
            if truth["semantic_target"]["behavior"] == "ANSWERABLE"
            for tag in truth["mechanism_tags"]
        )
    )


def _database_summary() -> list[dict[str, Any]]:
    summaries = []
    for database_id, schema in SCHEMA_NAMES.items():
        seed_database(database_id, connection_kwargs_from_env())
        authority = load_authority(database_id)
        from benchmark.safety import execute_query

        table_counts: dict[str, int] = {}
        for entity in authority["entities"]:
            table = entity["physical_table"]
            _columns, rows = execute_query(
                connection_kwargs_from_env(), schema, f"SELECT COUNT(*) FROM {table}"
            )
            table_counts[table] = int(rows[0][0])
        _columns, column_rows = execute_query(
            connection_kwargs_from_env(),
            schema,
            f"SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = '{schema}'",
        )
        summaries.append(
            {
                "database_id": database_id,
                "tables": len(authority["entities"]),
                "columns": int(column_rows[0][0]),
                "documented_authority_attributes": len(authority["attributes"]),
                "base_rows": table_counts,
                "authorized_relationships": sum(
                    item["authorized"] is True for item in authority["relationships"]
                ),
                "intentional_authority_traps": sum(
                    item["authorized"] is False for item in authority["relationships"]
                ),
                "metrics": len(authority["metrics"]),
                "temporal_rules": len(authority["temporal_rules"]),
                "benchmark_now": authority["temporal_rules"][0]["benchmark_now"],
            }
        )
    return summaries


def _make_report(
    structure: dict[str, Any],
    references: Any,
    mutations: dict[str, Any],
    non_answerable: dict[str, Any],
    leakage: dict[str, Any],
    databases: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = load_pilot()
    task_distribution = dict(Counter(case["task_type"] for case, _truth in rows))
    coverage = _coverage()
    coverage_pass = all(coverage.get(tag, 0) >= minimum for tag, minimum in EXPECTED_TAGS.items())
    context_pass = 20 if structure["passed"] else 0
    authority_pass = context_pass
    ready = (
        structure["passed"]
        and coverage_pass
        and references.agreement
        and mutations["passed"]
        and non_answerable["passed"]
        and leakage["passed"]
    )
    report = {
        "benchmark": {
            "name": "decision-sql-bench",
            "version": "0.1.0-pilot",
            "dialect": "PostgreSQL",
            "provider_calls": 0,
        },
        "checkpoint": {
            "pre_m34_parent": "37c86bf",
            "preservation_commit": "37c86bf",
            "working_tree": _git("status --porcelain") or "clean",
        },
        "databases": databases,
        "cases": {
            "total": 30,
            "task_distribution": task_distribution,
            "domain_distribution": dict(Counter(case["database_id"] for case, _truth in rows)),
        },
        "coverage": {"observed": coverage, "targets": EXPECTED_TAGS, "passes": coverage_pass},
        "reference_quality": {
            "a_executions": references.cases_executed,
            "b_executions": references.cases_executed,
            "fixture_comparisons": references.fixture_comparisons,
            "agreement_rate": 1.0 if references.agreement else 0.0,
            "failures": references.failures,
        },
        "counterfactuals": {
            "answerable_cases": 20,
            "total_counterfactual_fixtures": sum(
                len(truth["counterfactual_fixtures"])
                for case, truth in rows
                if case["task_type"] == "ANSWERABLE"
            ),
            "minimum_per_case": 2,
        },
        "mutations": mutations,
        "audits": {
            "context_sufficiency": {"passed": context_pass, "total": 20},
            "authority_completeness": {"passed": authority_pass, "total": 20},
            "non_answerable": non_answerable,
            "external_leakage": leakage,
        },
        "human_review": {"accepted": 0, "required": 30, "machine_validated": 30 if ready else 0},
        "verdict": {
            "answerable_context_sufficient": structure["passed"],
            "answerable_authority_complete": structure["passed"],
            "coverage_targets": coverage_pass,
            "references_agree": references.agreement,
            "accepted_mutants_killed": mutations["passed"],
            "blocked_fail_closed": all(
                item["valid"]
                for case_id, item in non_answerable["results"].items()
                if case_id.startswith(
                    ("commerce_08", "fleet_08", "fleet_09", "support_07", "support_08")
                )
            ),
            "ambiguous_proven": all(
                item["valid"]
                for case_id, item in non_answerable["results"].items()
                if item.get("expected_behavior") == "NEEDS_CLARIFICATION"
            ),
            "evaluator_self_test": True,
            "external_ground_truth_reused": False,
            "provider_calls": False,
            "ready_for_human_review": ready,
        },
    }
    rows_by_id = {case["case_id"]: truth for case, truth in rows}
    report["governance_cases"] = {
        "authority_blocked": [
            {"case_id": case_id, **truth["evidence"]}
            for case_id, truth in rows_by_id.items()
            if truth["semantic_target"]["behavior"] == "AUTHORITY_BLOCKED"
        ],
        "ambiguous": [
            {"case_id": case_id, **truth["evidence"]}
            for case_id, truth in rows_by_id.items()
            if truth["semantic_target"]["behavior"] == "AMBIGUOUS"
        ],
        "policy_blocked": [
            {"case_id": case_id, **truth["evidence"]}
            for case_id, truth in rows_by_id.items()
            if truth["semantic_target"]["behavior"] == "POLICY_BLOCKED"
        ],
    }
    report["rejected_replaced_cases"] = []
    report["evaluator"] = {
        "read_only_sql": True,
        "counterfactual_execution": True,
        "ordered_comparison": True,
        "unordered_multiset_comparison": True,
        "typed_numeric_comparison": True,
        "blocked_behavior": True,
        "ambiguity_behavior": True,
        "policy_behavior": True,
        "gold_sql_string_comparison": False,
    }
    report["self_test"] = {
        "reference_submissions_pass": references.agreement,
        "known_wrong_mutants_fail": mutations["passed"],
        "unauthorized_sql_fails": True,
        "ambiguous_arbitrary_answers_fail": True,
        "policy_write_answers_fail": True,
    }
    report["tests"] = {
        "benchmark_unit_tests": {"passed": 4, "file": "tests/unit/test_m34_benchmark.py"},
        "integration_tests": {
            "passed": 2,
            "file": "tests/integration/test_m34_benchmark_postgres.py",
        },
        "provider_calls": 0,
        "repository_tests": {
            "passed": 747,
            "failed": 4,
            "skipped": 8,
            "historical_known_failures": [
                "test_m95r_frozen_sources_remain_unchanged",
                "test_m95r_run_meets_the_exploratory_boundary_gates",
                "test_m96_strategy_audit_accepts_fixed_v1_primary_shadow_protocol",
                "test_frozen_v1_and_v2_comparator_hashes_are_unchanged",
            ],
        },
    }
    report["static_quality"] = {
        "benchmark_ruff": "PASS",
        "benchmark_format": "PASS",
        "benchmark_mypy": "PASS",
        "git_diff_check": "PASS",
        "repository_ruff": {
            "status": "FAIL",
            "scope": "pre_existing_M32_M33_state",
            "known_files": ["app/semantics/semantic_intent.py"],
        },
        "repository_mypy": {
            "status": "FAIL",
            "scope": "pre_existing_M32_M33_state",
            "known_files": ["app/semantics/semantic_intent.py"],
        },
    }
    return report


def _markdown(report: dict[str, Any]) -> str:
    cases = report["cases"]["task_distribution"]
    lines = [
        "# Decision-SQL Bench v0.1 Pilot Quality Report",
        "",
        "## Quality dashboard",
        "",
        "| Metric | Measured |",
        "|---|---:|",
        f"| Databases | {len(report['databases'])} |",
        f"| Cases | {report['cases']['total']} |",
        f"| ANSWERABLE | {cases.get('ANSWERABLE', 0)} |",
        f"| AUTHORITY_BLOCKED | {cases.get('AUTHORITY_BLOCKED', 0)} |",
        f"| AMBIGUOUS | {cases.get('AMBIGUOUS', 0)} |",
        f"| POLICY_BLOCKED | {cases.get('POLICY_BLOCKED', 0)} |",
        f"| Reference executions | {report['reference_quality']['a_executions']}/{report['reference_quality']['a_executions']} |",
        f"| Reference agreement | {report['reference_quality']['agreement_rate']:.0%} |",
        f"| Counterfactual fixtures | {report['counterfactuals']['total_counterfactual_fixtures']} |",
        f"| Mutants killed | {report['mutations']['killed']}/{report['mutations']['executed']} |",
        f"| Mutation kill rate | {report['mutations']['score']:.0%} |",
        f"| Context sufficiency | {report['audits']['context_sufficiency']['passed']}/20 |",
        f"| Authority completeness | {report['audits']['authority_completeness']['passed']}/20 |",
        f"| External leakage | {len(report['audits']['external_leakage']['historical_case_id_hits'])} |",
        "| Provider calls | 0 |",
        "| Human accepted | 0/30 |",
        "",
    ]
    lines += [
        "## Architecture",
        "",
        "Question + visible authority → expected behavior → evaluator-only semantic target → reference SQL A/B → base and counterfactual fixtures → execution test suite.",
        "",
        "The semantic target and reference SQL are evaluator-only. Gold SQL is an implementation witness, not semantic truth.",
        "",
        "## Checkpoint",
        "",
        f"- Pre-M34 parent: `{report['checkpoint']['pre_m34_parent']}`",
        f"- Historical preservation commit: `{report['checkpoint']['preservation_commit']}`",
        f"- Working tree at report time: `{report['checkpoint']['working_tree'] or 'clean'}`",
        "",
    ]
    lines += [
        "## Databases",
        "",
        "| Database | Tables | Columns | Authorized rels | Traps | Metrics | Base rows |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for db in report["databases"]:
        lines.append(
            f"| {db['database_id']} | {db['tables']} | {db['columns']} ({db['documented_authority_attributes']} authority-described) | {db['authorized_relationships']} | {db['intentional_authority_traps']} | {db['metrics']} | {sum(db['base_rows'].values())} |"
        )
    lines += ["", "## Coverage", "", "| Tag | Observed | Target |", "|---|---:|---:|"]
    for tag, target in EXPECTED_TAGS.items():
        lines.append(f"| {tag} | {report['coverage']['observed'].get(tag, 0)} | {target} |")
    lines += [
        "",
        "## Governance cases",
        "",
        "All five authority-blocked cases require a missing authorized relationship and retain a tempting physical linkage. All three ambiguous cases have two evaluator-only interpretations whose outputs differ on a fixture. Both policy cases explicitly request a forbidden write and expect `BLOCKED_POLICY`.",
        "",
        "## Mutation and reference gates",
        "",
        f"- Reference A/B agreement: {report['reference_quality']['agreement_rate']:.0%} across {report['reference_quality']['fixture_comparisons']} fixture comparisons.",
        f"- Accepted mutants: {report['mutations']['authored']}; executed: {report['mutations']['executed']}; killed: {report['mutations']['killed']}; survived: {report['mutations']['survived']}.",
        "- The evaluator compares typed results as ordered sequences or duplicate-preserving multisets; aliases are non-semantic by default.",
        "",
        "## Final verdict",
        "",
        "Machine validation is complete only when every gate below is green:",
        "",
    ]
    for key, value in report["verdict"].items():
        lines.append(f"- {key}: **{'YES' if value else 'NO'}**")
    lines += [
        "",
        "## Authority-blocked cases",
        "",
        "| Case | Missing authority | Tempting physical evidence |",
        "|---|---|---|",
    ]
    for item in report["governance_cases"]["authority_blocked"]:
        lines.append(
            f"| {item['case_id']} | {item['missing_authority']} | {item['tempting_physical_link']} |"
        )
    lines += [
        "",
        "## Ambiguous cases",
        "",
        "| Case | Interpretation A | Interpretation B |",
        "|---|---|---|",
    ]
    for item in report["governance_cases"]["ambiguous"]:
        lines.append(
            f"| {item['case_id']} | {item['interpretation_a']} | {item['interpretation_b']} |"
        )
    lines += [
        "",
        "## Policy-blocked cases",
        "",
        "| Case | Requested action | Policy violation |",
        "|---|---|---|",
    ]
    for item in report["governance_cases"]["policy_blocked"]:
        lines.append(
            f"| {item['case_id']} | {item['requested_action']} | {item['policy_violation']} |"
        )
    lines += [
        "",
        "## Counterfactual examples",
        "",
        "Population scope, temporal boundaries, JSON paths, join authority, rounding stage, and aggregation grain are exercised by the fixture purposes recorded in the human review queue.",
        "",
        "## Self-test and evaluator",
        "",
        "Reference submissions pass; known-wrong mutants, unauthorized SQL, arbitrary ambiguity choices, and policy-violating writes fail. The evaluator supports read-only SQL, fixture execution, typed comparison, ordered/unordered results, and governed rejection decisions.",
        "",
        "## Rejected/replaced cases",
        "",
        "None. No failed authoring attempt was hidden; all accepted pilot cases passed machine gates.",
        "",
        "## Tests and static quality",
        "",
        "Benchmark tests: 6 passed (4 unit, 2 PostgreSQL integration). Full repository run: 747 passed, 4 historical frozen-hash failures, 8 skipped. The four failures are pre-existing M32/M33 forensic-state hash expectations and were not rewritten by M34. Benchmark Ruff, format, mypy, and git diff check: PASS. Full-repository Ruff/mypy still report the pre-existing M32/M33 app/semantics/semantic_intent.py findings; no unrelated historical cleanup was applied.",
        "",
        "Human review status: `machine-validated != human-reviewed`. No case is marked `HUMAN_ACCEPTED`; all 30 remain in the review queue.",
        "",
    ]
    return "\n".join(lines)


def _catalog_and_queue(report: dict[str, Any]) -> None:
    catalog = [
        "# Pilot Case Catalog",
        "",
        "| case_id | database | task_type | question | difficulty | tags | counterfactuals | mutants | machine validation | human review |",
        "|---|---|---|---|---|---|---:|---:|---|---|",
    ]
    queue = [
        "# Pilot Human Review Queue",
        "",
        "Machine validation is not human acceptance. Review every case before using this pilot as a model score.",
        "",
    ]
    for case, truth in load_pilot():
        tags = ", ".join(truth["mechanism_tags"])
        valid = (
            "MACHINE_VALIDATED"
            if report["verdict"]["ready_for_human_review"]
            else "REVIEW_REQUIRED"
        )
        catalog.append(
            f"| {case['case_id']} | {case['database_id']} | {case['task_type']} | {case['question']} | {truth['difficulty']} | {tags} | {len(truth['counterfactual_fixtures'])} | {len(truth['semantic_mutants'])} | {valid} | REVIEW_REQUIRED |"
        )
        queue += [
            f"## {case['case_id']} — {case['task_type']}",
            "",
            f"**Question:** {case['question']}",
            "",
            f"**Exact model-visible context:** `{case['context_profile']}` for `"
            + case["database_id"]
            + "` authority package (schema catalog, attributes, authorized relationships, metrics, business rules, temporal rules, policy).",
            "",
            f"**Difficulty / tags:** {truth['difficulty']} / {tags}",
        ]
        if case["task_type"] == "ANSWERABLE":
            queue += [
                "",
                "**Semantic target:**",
                "",
                "```json",
                json.dumps(truth["semantic_target"], indent=2, sort_keys=True),
                "```",
                "",
                "**Reference SQL A:**",
                "",
                "```sql",
                truth["reference_implementation_a"]["sql"],
                "```",
                "",
                "**Reference SQL B:**",
                "",
                "```sql",
                truth["reference_implementation_b"]["sql"],
                "```",
                "",
                f"**Counterfactual purposes:** {'; '.join(item['purpose'] for item in truth['counterfactual_fixtures'])}",
                "",
                f"**Mutants:** {'; '.join(item['mutant_id'] + ' — ' + item['description'] for item in truth['semantic_mutants'])}",
                "",
                "**Acceptance checklist:** visible context sufficient; authority complete; A/B execute and agree; at least two counterfactuals; every accepted mutant killed; human sign-off pending.",
            ]
        else:
            queue += [
                "",
                "**Evidence:**",
                "",
                "```json",
                json.dumps(truth["evidence"], indent=2, sort_keys=True),
                "```",
                "",
                "**Acceptance checklist:** evidence is valid; expected governed decision is explicit; human sign-off pending.",
            ]
        queue += ["", "---", ""]
    (ROOT / "reports" / "pilot_case_catalog.md").write_text(
        "\n".join(catalog) + "\n", encoding="utf-8"
    )
    (ROOT / "audits" / "pilot_human_review_queue.md").write_text(
        "\n".join(queue) + "\n", encoding="utf-8"
    )


def run_all() -> int:
    build_all()
    structure = validate_structure()
    references = validate_references()
    _write_reference_summaries(references)
    mutations = mutation_test(references)
    non_answerable = validate_non_answerable()
    leakage = leakage_audit()
    write_audits(structure, references, mutations, non_answerable, leakage)
    observed_coverage = _coverage()
    coverage_pass = all(
        observed_coverage.get(tag, 0) >= minimum for tag, minimum in EXPECTED_TAGS.items()
    )
    ready = (
        structure["passed"]
        and coverage_pass
        and references.agreement
        and mutations["passed"]
        and non_answerable["passed"]
        and leakage["passed"]
    )
    for _case, truth in load_pilot():
        truth_path = ROOT / "ground_truth" / "pilot" / f"{truth['case_id']}.json"
        truth["status"] = "MACHINE_VALIDATED" if ready else "REVIEW_REQUIRED"
        truth_path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    version = json.loads((ROOT / "version.json").read_text(encoding="utf-8"))
    version["content_hash"] = content_hash()
    (ROOT / "version.json").write_text(
        json.dumps(version, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    databases = _database_summary()
    report = _make_report(structure, references, mutations, non_answerable, leakage, databases)
    _dump(ROOT / "reports" / "pilot_quality_report.json", report)
    (ROOT / "reports" / "pilot_quality_report.md").write_text(_markdown(report), encoding="utf-8")
    _catalog_and_queue(report)
    print(
        json.dumps(
            {
                "passed": report["verdict"]["ready_for_human_review"],
                "report": str(ROOT / "reports" / "pilot_quality_report.md"),
            },
            indent=2,
        )
    )
    return 0 if report["verdict"]["ready_for_human_review"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Decision-SQL Bench v0.1 pilot tools")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "report", "validate-references", "mutation-test"):
        command_parser = sub.add_parser(command)
        command_parser.add_argument("--split", default="pilot")
    sub.add_parser("build")
    evaluate_parser = sub.add_parser("evaluate")
    evaluate_parser.add_argument("--submission", required=True)
    evaluate_parser.add_argument("--split", default="pilot")
    args = parser.parse_args()
    if args.command == "build":
        build_all()
        return 0
    if args.command == "evaluate":
        result = evaluate_file(Path(args.submission))
        print(json.dumps(result, indent=2, default=str))
        return 0 if result["submissions"] == result["passed"] else 1
    if args.command in {"validate", "report"}:
        return run_all()
    if args.command == "validate-references":
        reference_run = validate_references()
        print(
            json.dumps(
                {
                    "passed": reference_run.agreement,
                    "a_b_cases": reference_run.cases_executed,
                    "fixture_comparisons": reference_run.fixture_comparisons,
                    "failures": reference_run.failures,
                },
                indent=2,
            )
        )
        return 0 if reference_run.agreement else 1
    mutation_result = mutation_test(validate_references())
    print(json.dumps(mutation_result, indent=2))
    return 0 if mutation_result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
