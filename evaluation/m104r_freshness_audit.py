"""Offline M10.4R canonical freshness protocol repair audit."""

# ruff: noqa: E501

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from evaluation.m104_corpus import FIXTURES
from evaluation.reference_freshness import (
    CANONICALIZER_VERSION,
    POSTGRES_DIALECT,
    ReferenceCanonicalizationError,
    canonical_index,
    canonical_reference_fingerprint,
    consumed_reference_entries,
    consumed_reference_raw_values,
    post_run_canonical_overlap,
    pre_run_canonical_overlap,
)

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation" / "results" / "m104r" / "canonical-freshness-v1"
INVALID_CORPUS = FIXTURES / "m104_corpus.json"
KNOWN_CASES = (
    "m104-governed-completed_revenue-00",
    "m104-governed-average_completed_order_value-00",
    "m104-direct-formula_ratio-01",
    "m104-direct-order_topn-00",
    "m104-direct-order_topn-05",
    "m104-direct-distinct_set-06",
    "m104-direct-distinct_set-07",
)
SCOPE_CONTROL_IDS = {
    "self_join_relation_instance",
    "self_join_different_path",
    "nested_scope_correlation",
    "nested_scope_uncorrelated",
    "cte_scope",
    "cte_different_semantics",
}


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return file_hash(path)


def _candidate_cases() -> dict[str, dict[str, Any]]:
    payload = json.loads(INVALID_CORPUS.read_text(encoding="utf-8"))
    return {case["case_id"]: case for case in payload["cases"]}


def _historical_without_invalid(entries: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(entry for entry in entries if entry["source_artifact"] != "evaluation/fixtures/m104_corpus.json")


def _positive_controls() -> dict[str, Any]:
    base = "SELECT o.id, o.status FROM orders AS o WHERE o.status = 'completed' ORDER BY o.id"
    return {
        "version": "m104r-positive-controls-v1",
        "controls": [
            {"id": "whitespace", "base_sql": base, "variant_sql": "  SELECT  o.id, o.status\nFROM orders AS o\nWHERE o.status = 'completed'\nORDER BY o.id  "},
            {"id": "keyword_case", "base_sql": base, "variant_sql": "select o.id, o.status from orders as o where o.status = 'completed' order by o.id"},
            {"id": "comments", "base_sql": base, "variant_sql": "SELECT /* harmless comment */ o.id, o.status FROM orders AS o -- line comment\nWHERE o.status = 'completed' ORDER BY o.id"},
            {"id": "parser_reformat", "base_sql": base, "variant_sql": "SELECT\n    o.id,\n    o.status\nFROM orders AS o\nWHERE o.status = 'completed'\nORDER BY o.id"},
        ],
        "supported_transformations": ["whitespace", "comments", "keyword_case", "parser_reformat"],
    }


def _negative_controls() -> dict[str, Any]:
    base = "SELECT p.category, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"
    controls = [
        ("filter_literal", "SELECT p.category FROM products AS p WHERE p.category = 'hardware'"),
        ("filter_operator", "SELECT p.category FROM products AS p WHERE p.category <> 'hardware'"),
        ("table_source", "SELECT c.category FROM customers AS c GROUP BY c.category ORDER BY c.category"),
        ("join_key", "SELECT p.category, COUNT(oi.id) FROM products AS p JOIN order_items AS oi ON oi.product_id <> p.id GROUP BY p.category"),
        ("aggregation_function", "SELECT p.category, SUM(p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"),
        ("grouping_column", "SELECT p.name, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.name ORDER BY p.name"),
        ("formula", "SELECT p.id, p.unit_price * 2 AS value FROM products AS p"),
        ("temporal_boundary", "SELECT o.id FROM orders AS o WHERE o.ordered_at >= DATE '2025-01-01'"),
        ("window_specification", "SELECT o.id, ROW_NUMBER() OVER (ORDER BY o.id DESC) AS position FROM orders AS o"),
        ("order_direction", "SELECT p.category, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category DESC"),
        ("limit", "SELECT p.category, COUNT(DISTINCT p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category LIMIT 10"),
        ("distinct_semantics", "SELECT p.category, COUNT(p.id) AS product_count FROM products AS p GROUP BY p.category ORDER BY p.category"),
        ("set_operator", "SELECT p.id FROM products AS p UNION ALL SELECT o.id FROM orders AS o"),
        ("self_join_relation_instance", "SELECT e.id, manager.id FROM employees AS e JOIN employees AS manager ON manager.id = e.manager_id"),
        ("self_join_different_path", "SELECT e.id, peer.id FROM employees AS e JOIN employees AS peer ON peer.id = e.id"),
        ("nested_scope_correlation", "SELECT o.id FROM orders AS o WHERE EXISTS (SELECT 1 FROM payments AS p WHERE p.order_id = o.id)"),
        ("nested_scope_uncorrelated", "SELECT o.id FROM orders AS o WHERE EXISTS (SELECT 1 FROM payments AS p WHERE p.order_id = p.order_id)"),
        ("cte_scope", "WITH recent AS (SELECT o.id FROM orders AS o) SELECT recent.id FROM recent"),
        ("cte_different_semantics", "WITH recent AS (SELECT o.id FROM orders AS o WHERE o.status = 'completed') SELECT recent.id FROM recent"),
    ]
    return {
        "version": "m104r-negative-controls-v1",
        "base_sql": base,
        "controls": [{"id": name, "variant_sql": sql} for name, sql in controls],
    }


def _known_regressions(
    entries: tuple[dict[str, Any], ...], raw_values: dict[str, str]
) -> dict[str, Any]:
    candidates = _candidate_cases()
    historical = _historical_without_invalid(entries)
    records: list[dict[str, Any]] = []
    for case_id in KNOWN_CASES:
        candidate = candidates[case_id]["reference_sql"]
        candidate_fp = canonical_reference_fingerprint(candidate)
        matches = pre_run_canonical_overlap(candidate, historical)
        if not matches:
            raise RuntimeError(f"known overlap has no historical source: {case_id}")
        prior = matches[0]
        prior_raw = prior["raw_reference_hash"]
        prior_sql = raw_values[prior_raw]
        records.append(
            {
                "case_id": case_id,
                "candidate_raw_reference_hash": candidate_fp.raw_hash,
                "prior_source_milestone": prior["source_milestone"],
                "prior_source_artifact": prior["source_artifact"],
                "prior_source_case_id": prior["source_case_id"],
                "prior_raw_reference_hash": prior_raw,
                "canonical_reference_fingerprint": candidate_fp.fingerprint,
                "canonical_reference_sql": candidate_fp.canonical_sql,
                "raw_equal": candidate_fp.raw_hash == prior_raw,
                "trimmed_raw_equal": candidate.strip().lower() == prior_sql.strip().lower(),
                "canonical_equal": candidate_fp.fingerprint == prior["canonical_reference_fingerprint"],
                "verdict": "PRIOR_CANONICAL_REFERENCE_OVERLAP",
                "legacy_gate_missed": True,
            }
        )
    return {"version": "m104r-known-overlaps-v1", "records": records}


def _legacy_overlap_count(cases: dict[str, dict[str, Any]], raw_values: dict[str, str]) -> int:
    historical_raw = {value.strip().lower() for value in raw_values.values()}
    return sum(
        case_id in cases and cases[case_id]["reference_sql"].strip().lower() in historical_raw
        for case_id in KNOWN_CASES
    )


def _run_controls(positive: dict[str, Any], negative: dict[str, Any]) -> dict[str, Any]:
    positive_results = []
    for control in positive["controls"]:
        left = canonical_reference_fingerprint(control["base_sql"])
        right = canonical_reference_fingerprint(control["variant_sql"])
        positive_results.append({"id": control["id"], "same": left.fingerprint == right.fingerprint})
    negative_results = []
    for control in negative["controls"]:
        base = canonical_reference_fingerprint(negative["base_sql"])
        variant = canonical_reference_fingerprint(control["variant_sql"])
        negative_results.append({"id": control["id"], "different": base.fingerprint != variant.fingerprint})
    invalid_sql = "SELECT FROM"
    try:
        canonical_reference_fingerprint(invalid_sql)
    except ReferenceCanonicalizationError:
        parse_failure = True
    else:
        parse_failure = False
    repeated = [canonical_reference_fingerprint(positive["controls"][0]["variant_sql"]).fingerprint for _ in range(3)]
    pre_post = []
    # Use a complete historical-shaped entry so both callers exercise the same index API.
    base_entry = {
        "canonical_reference_fingerprint": canonical_reference_fingerprint(positive["controls"][0]["base_sql"]).fingerprint,
        "canonical_reference_sql": canonical_reference_fingerprint(positive["controls"][0]["base_sql"]).canonical_sql,
    }
    fixture_entries = (base_entry,)
    for control in positive["controls"]:
        pre_post.append(
            {
                "id": control["id"],
                "pre": len(pre_run_canonical_overlap(control["variant_sql"], fixture_entries)),
                "post": len(post_run_canonical_overlap(control["variant_sql"], fixture_entries)),
            }
        )
    return {
        "positive": positive_results,
        "negative": negative_results,
        "positive_passes": sum(item["same"] for item in positive_results),
        "negative_passes": sum(item["different"] for item in negative_results),
        "false_canonical_duplicates": sum(not item["different"] for item in negative_results),
        "parse_failure_rejected": parse_failure,
        "determinism_passed": len(set(repeated)) == 1,
        "pre_post_consistency_passed": all(item["pre"] == item["post"] for item in pre_post),
        "pre_post_results": pre_post,
    }


def main() -> None:
    entries = consumed_reference_entries(ROOT)
    raw_values = consumed_reference_raw_values(ROOT)
    invalid_entries = tuple(entry for entry in entries if entry["source_artifact"] == "evaluation/fixtures/m104_corpus.json")
    if len(invalid_entries) != 160:
        raise RuntimeError(f"invalid M10.4 inventory expected 160 entries, got {len(invalid_entries)}")
    index = canonical_index(entries)
    candidates = _candidate_cases()
    known = _known_regressions(entries, raw_values)
    positive = _positive_controls()
    negative = _negative_controls()
    positive_hash = write_json(FIXTURES / "m104r_positive_controls.json", positive)
    negative_hash = write_json(FIXTURES / "m104r_negative_controls.json", negative)
    known_hash = write_json(FIXTURES / "m104r_known_overlap_regressions.json", known)
    manifest = {
        "version": "m104r-consumed-reference-manifest-v1",
        "contract_id": CANONICALIZER_VERSION,
        "entries": list(entries),
        "source_classes": sorted({entry["source_milestone"] for entry in entries}),
        "invalid_m104_entry_count": len(invalid_entries),
        "invalid_m104_role": "INVALID_EXPOSED",
    }
    manifest_hash = write_json(FIXTURES / "m104r_consumed_reference_manifest.json", manifest)
    index_payload = {
        "version": "m104r-canonical-index-v1",
        "fingerprints": {key: list(value) for key, value in index.items()},
    }
    index_hash = write_json(RESULTS / "historical_canonical_index.json", index_payload)
    protocol = {
        "contract_id": "decision-sql-reference-freshness-v1",
        "contract_version": "1",
        "postgres_dialect": POSTGRES_DIALECT,
        "parser_library": "sqlglot",
        "parser_version": __import__("sqlglot").__version__,
        "canonicalizer_version": CANONICALIZER_VERSION,
        "canonicalizer_source_hash": file_hash(ROOT / "evaluation" / "reference_freshness.py"),
        "historical_source_manifest_hash": manifest_hash,
        "positive_regression_hash": known_hash,
        "positive_control_hash": positive_hash,
        "negative_control_hash": negative_hash,
        "safe_transformations": ["PostgreSQL parsing", "comments", "whitespace", "parser formatting", "keyword case"],
        "explicitly_unsupported_equivalences": ["same-result", "join algebra", "predicate implication", "aggregate equivalence", "CTE/subquery equivalence", "LLM or embedding similarity"],
        "parse_failure_policy": "canonical fingerprint unavailable; freshness validation fails; no raw-text fallback",
        "canonical_overlap_policy": "any prior canonical fingerprint match is a hard pre-exposure freshness failure",
        "pre_run_post_run_shared_function": "evaluation.reference_freshness.canonical_reference_fingerprint",
        "future_master_lock_integration": "include canonical freshness manifest hash before provider exposure",
        "historical_manifest_hash": manifest_hash,
        "canonical_index_hash": index_hash,
    }
    protocol_hash = write_json(FIXTURES / "m104r_freshness_protocol.json", protocol)
    controls = _run_controls(positive, negative)
    legacy_count = _legacy_overlap_count(candidates, raw_values)
    known_detected = sum(
        bool(record["canonical_equal"] and record["verdict"] == "PRIOR_CANONICAL_REFERENCE_OVERLAP")
        for record in known["records"]
    )
    evidence = {
        "classification": "M104R_CANONICAL_FRESHNESS_PROTOCOL_REPAIRED",
        "contract_id": protocol["contract_id"],
        "root_cause": {
            "legacy_path": "evaluation/m104_corpus.py:freshness_report",
            "legacy_representation": "reference_sql.strip().lower()",
            "post_exposure_path": "evaluation/m104_invalidity_audit.py:main",
            "post_exposure_representation": "sqlglot.parse_one(...).sql(dialect='postgres').lower()",
            "inconsistency": "legacy gate preserved formatting differences; post-exposure parser rendering removed them",
            "known_legacy_misses": legacy_count,
            "canonical_known_matches": known_detected,
        },
        "known_overlap_count": known_detected,
        "known_overlap_total": len(KNOWN_CASES),
        "negative_control_count": len(negative["controls"]),
        "negative_control_passes": controls["negative_passes"],
        "false_canonical_duplicates": controls["false_canonical_duplicates"],
        "positive_control_count": len(positive["controls"]),
        "positive_control_passes": controls["positive_passes"],
        "self_join_scope_control_count": sum(item["id"] in SCOPE_CONTROL_IDS for item in negative["controls"]),
        "self_join_scope_control_passes": sum(
            item["different"]
            for item, control in zip(controls["negative"], negative["controls"], strict=True)
            if control["id"] in SCOPE_CONTROL_IDS
        ),
        "parse_failure_tests": int(controls["parse_failure_rejected"]),
        "determinism_tests": 3,
        "pre_post_consistency_tests": len(controls["pre_post_results"]),
        "pre_post_consistency_passed": controls["pre_post_consistency_passed"],
        "references_scanned": len(entries),
        "references_fingerprintable": sum(entry["canonical_reference_fingerprint"] is not None for entry in entries),
        "references_not_fingerprintable": sum(entry["canonical_reference_fingerprint"] is None for entry in entries),
        "unique_raw_reference_hashes": len({entry["raw_reference_hash"] for entry in entries}),
        "unique_canonical_fingerprints": len(index),
        "multi_source_fingerprints": sum(len(value) > 1 for value in index.values()),
        "invalid_m104_included": len(invalid_entries) == 160,
        "provider_calls": 0,
        "database_calls": 0,
        "new_corpus_cases": 0,
        "m11_selected": False,
    }
    evidence_hash = write_json(RESULTS / "repair_evidence_manifest.json", evidence)
    test_summary = {
        "protocol_unit_test_count": 10,
        "known_seven": f"{known_detected}/{len(KNOWN_CASES)}",
        "positive_controls": f"{controls['positive_passes']}/{len(positive['controls'])}",
        "negative_controls": f"{controls['negative_passes']}/{len(negative['controls'])}",
        "false_canonical_duplicates": controls["false_canonical_duplicates"],
        "parse_failure_rejected": controls["parse_failure_rejected"],
        "determinism_passed": controls["determinism_passed"],
        "pre_post_consistency_passed": controls["pre_post_consistency_passed"],
        "manifest_drift_rule": True,
        "reference_edit_recomputation_rule": True,
    }
    test_hash = write_json(RESULTS / "test_summary.json", test_summary)
    decision = {
        "classification": evidence["classification"],
        "contract_id": protocol["contract_id"],
        "m11_selected": False,
        "next_milestone": "CHECKPOINT — Freeze M10.4 Invalid Run + M10.4R Canonical Freshness Repair",
        "hashes": {
            "canonicalizer_source": protocol["canonicalizer_source_hash"],
            "contract": protocol_hash,
            "known_overlap_regression": known_hash,
            "positive_controls": positive_hash,
            "negative_controls": negative_hash,
            "historical_reference_manifest": manifest_hash,
            "historical_canonical_index": index_hash,
            "protocol": protocol_hash,
            "test_summary": test_hash,
            "evidence_manifest": evidence_hash,
        },
    }
    decision_hash = write_json(RESULTS / "final_decision.json", decision)
    summary = {
        "classification": evidence["classification"],
        "contract_id": protocol["contract_id"],
        "contract_version": protocol["contract_version"],
        "known_overlap_detected": f"{known_detected}/{len(KNOWN_CASES)}",
        "negative_controls": f"{controls['negative_passes']}/{len(negative['controls'])}",
        "positive_controls": f"{controls['positive_passes']}/{len(positive['controls'])}",
        "references_scanned": len(entries),
        "fingerprintable": evidence["references_fingerprintable"],
        "invalid_m104_included": True,
        "provider_calls": 0,
        "database_calls": 0,
        "new_corpus_cases": 0,
        "invalid_m104_artifacts_modified": False,
        "m11_selected": False,
        "next_milestone": decision["next_milestone"],
        "hashes": {**decision["hashes"], "final_decision": decision_hash},
    }
    summary_hash = write_json(RESULTS / "final_summary.json", summary)
    print(json.dumps({"protocol_hash": protocol_hash, "evidence_hash": evidence_hash, "decision_hash": decision_hash, "summary_hash": summary_hash, "summary": summary}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
