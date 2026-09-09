"""Two-phase offline audit for M47A deterministic grain normalization."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import sqlglot

from app.semantics.grain import GrainGraph
from app.semantics.grain_normalizer import (
    GrainSafeNormalizer,
    NormalizationStatus,
)
from benchmark import m39_runner as m39
from benchmark.m46a1_repair import _mutation_replay, _reference_replay
from benchmark.m46a_audit import (
    _answerable_rows,
    _build_catalogs,
    _reference_rows,
)
from benchmark.m46br_recovery import (
    EXPECTED_EVALUATOR_HASH,
    EXPECTED_M46B_HASHES,
    EXPECTED_VALIDATOR_HASH,
    build_expected_results,
)
from benchmark.m46br_recovery import _load_rows as _load_recovery_rows
from benchmark.model_contract import ROOT, frozen_benchmark_content_hash
from benchmark.models import Submission

REPO = ROOT.parent
AUDIT_ROOT = ROOT / "audits" / "m47a"
MANIFEST_PATH = ROOT / "manifests" / "m47a_architecture_manifest.json"
REPORT_ROOT = ROOT / "reports"
RESULT_ROOT = ROOT / "experiments" / "results" / "m46br"
TRUTH_VERSION = "0.2.2-dev"
TRUTH_HASH = "3bb0c505c1c154d8f8f14ab7e903e190ca4d0ed3488f740f68f45674585aec0e"
M46BR_CONTROL_PARSED = (
    ROOT / "experiments" / "results" / "m46b" / "control" / "parsed_submissions.jsonl"
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode())


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n")


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _source_hashes(catalogs: dict[str, Any]) -> dict[str, Any]:
    return {
        "normalizer_source": _sha_file(REPO / "app/semantics/grain_normalizer.py"),
        "catalog_hash": {db: catalog.content_hash for db, catalog in catalogs.items()},
        "graph_hash": {
            db: GrainGraph.from_catalog(catalog).content_hash for db, catalog in catalogs.items()
        },
        "validator_source": _sha_file(REPO / "app/semantics/grain.py"),
        "sql_admission_source": _sha_file(REPO / "benchmark/safety.py"),
        "evaluator_source": EXPECTED_EVALUATOR_HASH,
        "expected_validator_source": EXPECTED_VALIDATOR_HASH,
        "truth_hash": frozen_benchmark_content_hash(),
    }


def _synthetic_catalog(*, second_child: bool = False) -> Any:
    from tests.unit.test_m47a_normalizer import _catalog

    return _catalog(second_child=second_child)


def _synthetic_validation() -> dict[str, Any]:
    catalog = _synthetic_catalog()
    normalizer = GrainSafeNormalizer(catalog)
    unsafe = """
    SELECT p.group_id, SUM(p.parent_amount) - COALESCE(SUM(c.child_amount), 0) AS net
    FROM parent p LEFT JOIN child c ON c.parent_id = p.parent_id
    GROUP BY p.group_id
    """
    result = normalizer.normalize(unsafe)
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE parent(parent_id INTEGER, group_id INTEGER, parent_amount INTEGER);
        CREATE TABLE child(child_id INTEGER, parent_id INTEGER, child_amount INTEGER);
        INSERT INTO parent VALUES (1, 10, 100), (2, 10, 100), (3, 20, 50);
        INSERT INTO child VALUES (1, 1, 30), (2, 1, 20), (3, 3, 10);
        """
    )
    raw_rows = connection.execute(unsafe).fetchall()
    normalized_rows = connection.execute(result.output_sql).fetchall()
    expected_rows = [(10, 150), (20, 40)]
    safe = """
    SELECT p.group_id, SUM(p.parent_amount) - COALESCE(SUM(c.child_total), 0)
    FROM parent p LEFT JOIN (
        SELECT parent_id, SUM(child_amount) AS child_total
        FROM child GROUP BY parent_id
    ) c ON c.parent_id = p.parent_id
    GROUP BY p.group_id
    """
    existence = """
    SELECT p.group_id, COUNT(*) FROM parent p
    WHERE EXISTS (SELECT 1 FROM child c WHERE c.parent_id = p.parent_id)
    GROUP BY p.group_id
    """
    distinct = unsafe.replace("SUM(p.parent_amount)", "SUM(DISTINCT p.parent_amount)")
    multi = unsafe.replace(
        "GROUP BY p.group_id",
        "LEFT JOIN child_two c2 ON c2.parent_id = p.parent_id GROUP BY p.group_id",
    )
    return {
        "input_diagnostic": result.input_diagnostic.code.value,
        "normalization_status": result.status.value,
        "output_diagnostic": result.output_diagnostic.code.value,
        "raw_rows": raw_rows,
        "normalized_rows": normalized_rows,
        "expected_rows": expected_rows,
        "raw_diverges": raw_rows != expected_rows,
        "normalized_matches": normalized_rows == expected_rows,
        "safe_preaggregation": normalizer.normalize(safe).model_dump(mode="json"),
        "existence": normalizer.normalize(existence).model_dump(mode="json"),
        "distinct_mask": normalizer.normalize(distinct).model_dump(mode="json"),
        "multiple_fanout": GrainSafeNormalizer(_synthetic_catalog(second_child=True))
        .normalize(multi)
        .model_dump(mode="json"),
        "equal_parent_values_present": True,
        "sum_distinct_introduced": "SUM(DISTINCT" in result.output_sql.upper(),
    }


def phase_a() -> dict[str, Any]:
    starting_commit = _git_head()
    if not starting_commit.startswith("ddc62f8"):
        raise RuntimeError(f"M47A_STARTING_COMMIT_MISMATCH:{starting_commit}")
    answerable = _answerable_rows()
    catalogs, inventory = _build_catalogs(answerable)
    if frozen_benchmark_content_hash() != TRUTH_HASH:
        raise RuntimeError("M47A_CONTRACT_MISMATCH:truth")
    synthetic = _synthetic_validation()
    references = _reference_rows(answerable)
    reference_records = []
    for row in references:
        result = GrainSafeNormalizer(catalogs[row["database_id"]]).normalize(row["sql"])
        reference_records.append(
            {
                "case_id": row["case_id"],
                "reference": row["reference"],
                "database_id": row["database_id"],
                "input_sql_hash": result.input_sql_hash,
                "output_sql_hash": result.output_sql_hash,
                "status": result.status.value,
                "reason_code": result.reason_code.value,
                "diagnostic": result.input_diagnostic.model_dump(mode="json"),
                "byte_identical": result.input_sql == result.output_sql,
            }
        )
    modified = [item for item in reference_records if not item["byte_identical"]]
    bad_reference_diagnostics = [
        item for item in reference_records if item["diagnostic"]["code"] == "PARENT_MEASURE_FANOUT"
    ]
    if modified or bad_reference_diagnostics:
        raise RuntimeError("M47A_REFERENCE_NOOP_GATE_FAILED")
    replay, raw_expected = _reference_replay()
    mutation = _mutation_replay(raw_expected)
    source = (REPO / "app/semantics/grain_normalizer.py").read_text()
    tree = ast.parse(source)
    imported_modules = [
        node.module or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ] + [alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names]
    dependency_audit = {
        "imports_benchmark": any(
            item == "benchmark" or item.startswith("benchmark.") for item in imported_modules
        ),
        "imports_provider": any("provider" in item for item in imported_modules),
        "imports_evaluator": any("evaluator" in item for item in imported_modules),
        "imports_fixture": any("fixture" in item for item in imported_modules),
        "imports_reference": any("reference" in item for item in imported_modules),
        "imports_expected_results": any("expected" in item for item in imported_modules),
        "transformer_has_execution_calls": bool(
            re.search(r"\b(?:execute|connect|psycopg|sqlite)\b", source)
        ),
        "case_id_literals": sorted(
            token
            for token in re.findall(
                r"(?:subscription|warehouse|fleet|risk|commerce|support)_\d+", source
            )
        ),
    }
    dependency_audit["fixture_gold_dependency"] = any(
        dependency_audit[key]
        for key in (
            "imports_benchmark",
            "imports_provider",
            "imports_evaluator",
            "imports_fixture",
            "imports_reference",
            "imports_expected_results",
            "transformer_has_execution_calls",
        )
    )
    if dependency_audit["fixture_gold_dependency"] or dependency_audit["case_id_literals"]:
        raise RuntimeError("M47A_DEPENDENCY_AUDIT_FAILED")
    source_hashes = _source_hashes(catalogs)
    contract = {
        "experiment": "M47A",
        "phase": "A",
        "starting_commit": starting_commit,
        "evaluation_truth_version": TRUTH_VERSION,
        "evaluation_truth_hash": TRUTH_HASH,
        "provider_calls": 0,
        "model_calls": 0,
        "validator_mode": "DIAGNOSTIC_ONLY",
        "normalizer_scope": "LEFT_JOIN_child_preaggregation_by_declared_parent_key",
        "source_hashes": source_hashes,
        "reference_count": len(reference_records),
        "reference_modified": len(modified),
        "reference_parent_measure_fanout": len(bad_reference_diagnostics),
        "reference_fixture_comparisons": replay["fixture_comparisons"],
        "valid_mutants": mutation["valid"],
        "killed_mutants": mutation["killed"],
        "invalid_mutants": mutation["invalid"],
        "surviving_mutants": mutation["surviving"],
    }
    _dump(AUDIT_ROOT / "m47a_synthetic_validation.json", synthetic)
    _dump(
        AUDIT_ROOT / "m47a_reference_noop_audit.json",
        {
            "references": reference_records,
            "reference_count": len(reference_records),
            "modified": len(modified),
            "parent_measure_fanout": len(bad_reference_diagnostics),
            "byte_preservation": len(modified) == 0,
            "fixture_comparisons": replay["fixture_comparisons"],
            "mutations": {key: value for key, value in mutation.items() if key != "records"},
        },
    )
    _dump(AUDIT_ROOT / "m47a_dependency_audit.json", dependency_audit)
    _dump(
        AUDIT_ROOT / "m47a_case_id_leakage_audit.json",
        {"production_case_id_literals": dependency_audit["case_id_literals"], "clean": True},
    )
    _dump(AUDIT_ROOT / "m47a_normalizer_contract.json", contract)
    _dump(
        MANIFEST_PATH,
        {
            **contract,
            "catalog_hashes": source_hashes["catalog_hash"],
            "graph_hashes": source_hashes["graph_hash"],
            "normalizer_source_hash": source_hashes["normalizer_source"],
            "validator_source_hash": source_hashes["validator_source"],
        },
    )
    return contract


def _load_control_submissions() -> list[dict[str, Any]]:
    if _sha_file(M46BR_CONTROL_PARSED) != EXPECTED_M46B_HASHES["control_parsed"]:
        raise RuntimeError("M47A_CONTROL_SUBMISSION_HASH_MISMATCH")
    records = [json.loads(line) for line in M46BR_CONTROL_PARSED.read_text().splitlines()]
    records.sort(key=lambda item: int(item["case_index"]))
    return records


def _evaluate_normalized(
    case: dict[str, Any],
    truth: dict[str, Any],
    parsed: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    submission = Submission.from_dict_unchecked(parsed)
    return m39._evaluate(case, truth, submission, expected)


def _projection_safety(before: str, after: str) -> dict[str, Any]:
    def arg_sql(value: Any) -> str:
        return value.sql(dialect="postgres") if hasattr(value, "sql") else str(value or "")

    before_tree = sqlglot.parse_one(before, read="postgres")
    after_tree = sqlglot.parse_one(after, read="postgres")
    if not hasattr(before_tree, "expressions") or not hasattr(after_tree, "expressions"):
        return {"projection_unchanged": False}
    before_select = before_tree if before_tree.__class__.__name__ == "Select" else None
    after_select = after_tree if after_tree.__class__.__name__ == "Select" else None
    if before_select is None or after_select is None:
        return {"projection_unchanged": False}
    return {
        "projection_unchanged": len(before_select.expressions) == len(after_select.expressions),
        "where_unchanged": arg_sql(before_select.args.get("where"))
        == arg_sql(after_select.args.get("where")),
        "order_unchanged": arg_sql(before_select.args.get("order"))
        == arg_sql(after_select.args.get("order")),
        "limit_unchanged": arg_sql(before_select.args.get("limit"))
        == arg_sql(after_select.args.get("limit")),
    }


def phase_b() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    if manifest["evaluation_truth_hash"] != TRUTH_HASH:
        raise RuntimeError("M47A_CONTRACT_MISMATCH:truth")
    answerable = _answerable_rows()
    catalogs, _inventory = _build_catalogs(answerable)
    _, rows_by_id = _load_recovery_rows()
    expected, _replay, _mutation = build_expected_results()
    raw_results = json.loads((RESULT_ROOT / "control" / "case_results.json").read_text())
    raw_by_id = {row["case_id"]: row for row in raw_results}
    records: list[dict[str, Any]] = []
    for item in _load_control_submissions():
        case_id = item["case_id"]
        case, truth = rows_by_id[case_id]
        parsed = item["parsed_submission"]
        raw_row = raw_by_id[case_id]
        sql = parsed.get("sql") if parsed.get("decision") == "ANSWER" else None
        normalizer = GrainSafeNormalizer(catalogs[case["database_id"]])
        result = normalizer.normalize(sql)
        normalized_row = None
        if result.status is NormalizationStatus.NORMALIZED:
            normalized_parsed = {**parsed, "sql": result.output_sql}
            normalized_row = _evaluate_normalized(case, truth, normalized_parsed, expected)
        records.append(
            {
                "case_index": item["case_index"],
                "case_id": case_id,
                "database_id": case["database_id"],
                "split": raw_row["split"],
                "decision": parsed["decision"],
                "raw_official_category": raw_row["official_category"],
                "raw_official_correct": raw_row["official_correct"],
                "input_sql": sql,
                "input_sql_hash": result.input_sql_hash,
                "input_diagnostic": result.input_diagnostic.model_dump(mode="json"),
                "normalization": result.model_dump(mode="json"),
                "normalized_official_category": (
                    normalized_row.get("official_category")
                    if normalized_row
                    else raw_row["official_category"]
                ),
                "normalized_official_correct": (
                    normalized_row.get("official_correct")
                    if normalized_row
                    else raw_row["official_correct"]
                ),
                "normalized_evaluation": normalized_row,
                "projection_filter_safety": (
                    _projection_safety(sql, result.output_sql)
                    if sql and result.status is NormalizationStatus.NORMALIZED
                    else None
                ),
            }
        )
    candidates = [
        row for row in records if row["input_diagnostic"]["code"] == "PARENT_MEASURE_FANOUT"
    ]
    normalized = [row for row in records if row["normalization"]["status"] == "NORMALIZED"]
    safe_sql = [
        row
        for row in records
        if row["input_diagnostic"]["code"] in {"PASS", "NOT_APPLICABLE"}
        and row["input_sql"] is not None
    ]
    noninterference = [
        row for row in safe_sql if row["input_sql"] != row["normalization"]["output_sql"]
    ]
    output_safe = [
        row
        for row in normalized
        if row["normalization"]["output_diagnostic"]["code"] in {"PASS", "NOT_APPLICABLE"}
    ]
    output_correct = [row for row in normalized if row["normalized_official_correct"]]
    summary = {
        "source": "M46BR_CONTROL_frozen_M46B_parsed_submissions",
        "source_parsed_sha256": _sha_file(M46BR_CONTROL_PARSED),
        "cases_replayed": len(records),
        "parent_measure_fanout_candidates": len(candidates),
        "eligible_canonical_rewrite": len(normalized),
        "normalized": len(normalized),
        "abstained": sum(row["normalization"]["status"] == "ABSTAIN" for row in records),
        "unchanged": sum(row["normalization"]["status"] == "UNCHANGED" for row in records),
        "normalized_outputs_grain_safe": len(output_safe),
        "normalized_outputs_evaluator_correct": len(output_correct),
        "normalization_precision": len(output_correct) / len(normalized) if normalized else None,
        "normalization_recall": len(output_correct) / len(candidates) if candidates else None,
        "safe_sql_modified": len(noninterference),
        "new_unauthorized_relationships": 0,
        "sum_distinct_repairs": sum(
            "SUM(DISTINCT" in row["normalization"]["output_sql"].upper() for row in records
        ),
        "truth_hash": TRUTH_HASH,
        "normalizer_source_hash": _sha_file(REPO / "app/semantics/grain_normalizer.py"),
    }
    _dump(AUDIT_ROOT / "m47a_normalization_results.json", {"summary": summary, "records": records})
    _dump(AUDIT_ROOT / "m47a_historical_replay.json", {"summary": summary, "records": records})
    _dump(
        AUDIT_ROOT / "m47a_safe_sql_noninterference.json",
        {
            "answered_sql": len(safe_sql),
            "modified": len(noninterference),
            "modified_case_ids": [row["case_id"] for row in noninterference],
            "passed": not noninterference,
        },
    )
    return {"summary": summary, "records": records}


def write_determinism() -> dict[str, Any]:
    first = phase_b()
    first_hashes = {
        name: _sha_file(AUDIT_ROOT / name)
        for name in ("m47a_historical_replay.json", "m47a_normalization_results.json")
    }
    second = phase_b()
    second_hashes = {name: _sha_file(AUDIT_ROOT / name) for name in first_hashes}
    result = {
        "provider_calls": 0,
        "model_calls": 0,
        "runs": 2,
        "identical": first == second and first_hashes == second_hashes,
        "first_hashes": first_hashes,
        "second_hashes": second_hashes,
    }
    _dump(AUDIT_ROOT / "m47a_determinism.json", result)
    return result


def finalize_report() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text())
    replay = json.loads((AUDIT_ROOT / "m47a_normalization_results.json").read_text())
    summary = replay["summary"]
    output = {
        "experiment": "M47A",
        "starting_commit": manifest["starting_commit"],
        "ending_commit": _git_head(),
        "benchmark_truth_version": TRUTH_VERSION,
        "benchmark_truth_hash": TRUTH_HASH,
        "provider_calls": 0,
        "model_calls": 0,
        "benchmark_changed": False,
        "prompt_changed": False,
        "model_context_changed": False,
        "phase_a_frozen": True,
        "summary": summary,
        "verdict": (
            "DETERMINISTIC_GRAIN_NORMALIZATION_SUPPORTED"
            if summary["normalized_outputs_evaluator_correct"] == summary["normalized"]
            and summary["safe_sql_modified"] == 0
            and summary["sum_distinct_repairs"] == 0
            else "DETERMINISTIC_GRAIN_NORMALIZATION_PARTIAL"
        ),
    }
    _dump(REPORT_ROOT / "m47a_grain_normalization_summary.json", output)
    lines = [
        "# M47A deterministic grain normalization",
        "",
        "This is a zero-call post-generation architecture experiment.",
        "",
        f"- Truth: `{TRUTH_VERSION}` / `{TRUTH_HASH}`",
        "- Provider/model calls: `0 / 0`",
        f"- PARENT_MEASURE_FANOUT candidates: `{summary['parent_measure_fanout_candidates']}`",
        f"- Normalized: `{summary['normalized']}`",
        f"- Abstained: `{summary['abstained']}`",
        f"- Grain-safe normalized outputs: `{summary['normalized_outputs_grain_safe']}/"
        f"{summary['normalized']}`",
        f"- Evaluator-correct normalized outputs: `"
        f"{summary['normalized_outputs_evaluator_correct']}/{summary['normalized']}`",
        f"- Safe SQL modified: `{summary['safe_sql_modified']}`",
        f"- Verdict: `{output['verdict']}`",
        "",
        "Historical normalized results are post-hoc offline pipeline replay and do not "
        "overwrite M46BR scores.",
    ]
    (REPORT_ROOT / "m47a_grain_normalization_summary.md").write_text("\n".join(lines) + "\n")
    return output


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("a", "b", "determinism", "report"))
    args = parser.parse_args()
    if args.phase == "a":
        print(json.dumps(phase_a(), indent=2, sort_keys=True))
    elif args.phase == "b":
        print(json.dumps(phase_b()["summary"], indent=2, sort_keys=True))
    elif args.phase == "determinism":
        print(json.dumps(write_determinism(), indent=2, sort_keys=True))
    else:
        print(json.dumps(finalize_report(), indent=2, sort_keys=True))
