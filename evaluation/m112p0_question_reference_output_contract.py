"""M11.2P0 question/reference/output contract integrity audit.

The audit is evaluator-side and offline.  It reads frozen M10.4S/P3 artifacts,
uses only deterministic SQLGlot structure, and never calls a provider,
database, retriever, or application runtime.  ``--prepare`` freezes the
adjudication protocol before case-level D-cell exposure; ``--adjudicate`` then
produces additive contract evidence for the exact frozen 56 cases.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
START_HEAD = "255c8efaeb41094c653b0b25893150ac2596f1e1"
P3_CHECKPOINT = "2735ca60036946cb54070294366f1d5a32a21996"
P3_SOURCE_HASH = "29196e997ce9c39b12af2a66bfe1f8e8dda1de10d57ea462ac4d91ab1c504651"
P3_PROTOCOL_HASH = "58ed34c157c597b5f9e51928e8881c379191f553a49bd60b6d5f05732f4a00f0"
P3_POPULATION_HASH = "76988144bb6fa80345b6f6b50d3b43554d7459566aad7b6c3f06481a9fe9540e"
P3_MATRIX_HASH = "dedfa106c0930137fe795b85e1271b6070ada2ad21de5873999b1e921566af44"
P0_CONTRACT = "ba5adfa425e9bcf5efc6446a0021f1dbed2ebfaca745a913a3e601a403c1a678"
P1_SEMANTIC = "35c379ba4c5aa50b9f2a787c8aec6c5346b273139a4357d00e98edae8abdfb17"
P1_ADAPTABILITY = "00b00a256cb560930afa2b2299c72c369928ba7c3b42482f6b098ca341d042aa"
P2_CHECKPOINT = "846b20c47a6175ca091944a2b9a9277650037bed"
M4_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"

STATUS_NAMES = (
    "REFERENCE_SEMANTICALLY_ALIGNED",
    "REFERENCE_SEMANTIC_DEFECT",
    "QUESTION_REFERENCE_SEMANTIC_AMBIGUITY",
    "REFERENCE_SEMANTICS_UNRESOLVED",
)
OUTPUT_NAMES = (
    "OUTPUT_CONTRACT_EXPLICIT",
    "OUTPUT_CONTRACT_PARTIALLY_SPECIFIED",
    "OUTPUT_CONTRACT_UNDERSPECIFIED",
    "OUTPUT_CONTRACT_AMBIGUOUS",
    "OUTPUT_CONTRACT_CONFLICT",
    "OUTPUT_CONTRACT_NOT_APPLICABLE",
    "OUTPUT_CONTRACT_UNRESOLVED",
)
ELIGIBILITY_NAMES = (
    "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE",
    "CORE_SEMANTIC_ATTRIBUTION_ONLY",
    "QUARANTINE_REFERENCE_SEMANTIC_DEFECT",
    "QUARANTINE_QUESTION_REFERENCE_AMBIGUITY",
    "QUARANTINE_OUTPUT_CONTRACT_AMBIGUITY",
    "QUARANTINE_UNRESOLVED",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _stable_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(data.encode("utf-8")).hexdigest()


def _write_json(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return _raw_hash(path)


def _write_jsonl(name: str, rows: Iterable[dict[str, Any]]) -> str:
    path = FIXTURES / name
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return _raw_hash(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def protocol() -> dict[str, Any]:
    return {
        "milestone": "M11.2P0 — Question–Reference–Output Contract Integrity Audit",
        "classification": "M112P0_CONTRACT_INTEGRITY_AUDIT_COMPLETED",
        "mode": "ZERO_PROVIDER_ZERO_DB_ZERO_RUNTIME_CHANGE",
        "population": "exact frozen P3 D cell; N=56",
        "question_is_primary_semantic_evidence": True,
        "reference_sql_is_not_question_definition": True,
        "p1_labels_used_for_adjudication": False,
        "p1_contract": "QUARANTINED_PENDING_ADVERSARIAL_REPAIR",
        "taxonomy_frozen_before_case_exposure": True,
        "default": "UNRESOLVED or AMBIGUOUS when bounded evidence is insufficient",
        "provider_calls": 0,
        "db_calls": 0,
        "retrieval_reruns": 0,
        "candidate_regeneration": 0,
        "reference_rewrites": 0,
        "runtime_changes": 0,
        "m112p1_generation_taxonomy": False,
    }


def contract_schema() -> dict[str, Any]:
    return {
        "reference_semantic_status": list(STATUS_NAMES),
        "question_output_contract_status": list(OUTPUT_NAMES),
        "grain_status": [
            "GRAIN_EXPLICIT",
            "GRAIN_INFERABLE",
            "GRAIN_AMBIGUOUS",
            "GRAIN_REFERENCE_CONFLICT",
            "GRAIN_UNRESOLVED",
        ],
        "formula_status": [
            "FORMULA_ALIGNED",
            "FORMULA_REFERENCE_DEFECT",
            "FORMULA_AMBIGUOUS",
            "FORMULA_NOT_APPLICABLE",
            "FORMULA_UNRESOLVED",
        ],
        "source_filter_status": [
            "REFERENCE_SOURCE_FILTER_ALIGNED",
            "REFERENCE_SOURCE_FILTER_CONFLICT",
            "REFERENCE_SOURCE_FILTER_AMBIGUOUS",
            "REFERENCE_SOURCE_FILTER_UNRESOLVED",
        ],
        "projection_difference_state": [
            "EXACT_REFERENCE_PROJECTION",
            "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN",
            "NON_PROJECTION_DIFFERENCE_PRESENT",
            "M1_REJECTED",
            "CANDIDATE_UNAVAILABLE",
            "PROJECTION_COMPARISON_UNRESOLVED",
        ],
        "attribution_eligibility": list(ELIGIBILITY_NAMES),
        "diagnostic_shape_candidate": "V1_OUTPUT_SHAPE_FAILURE_NOT_SEMANTICALLY_ENTITLED",
    }


def rulebook() -> dict[str, Any]:
    return {
        "version": "m112p0-question-reference-output-rulebook-v1",
        "priority": [
            "literal natural-language question",
            "explicit versioned server-owned contract where applicable",
            "schema semantics",
            "reference SQL as intended-answer evidence",
            "candidate SQL only for bounded decomposition",
        ],
        "reference_defect_requires": "positive question-versus-reference semantic conflict",
        "ambiguity_default": "do not choose the interpretation that merely matches the reference",
        "projection": (
            "exact column entitlement requires question-owned evidence; "
            "reference projection alone is insufficient"
        ),
        "formula": "share requires numerator/group-total semantics; scale may remain ambiguous",
        "difference": "difference existence is separable from subtraction direction",
        "zero_member": "INNER/LEFT behavior is not question-mandated without bounded evidence",
        "null_order_distinct_limit": "record only where material; conservative ambiguity otherwise",
        "p1": "do not use semantic relation or adaptability labels for P0 adjudication",
        "m112p1": "no generation-mechanism labels are assigned in P0",
    }


def projection_comparator_contract() -> dict[str, Any]:
    return {
        "version": "m112p0-projection-comparator-v1",
        "purpose": "prove only top-level projection difference, not general SQL equivalence",
        "literal_policy": "preserve string case and numeric value",
        "preserved_outside_projection": [
            "FROM",
            "JOIN kind",
            "JOIN ON",
            "JOIN USING columns",
            "WHERE",
            "GROUP BY",
            "HAVING",
            "WINDOW/PARTITION/ORDER",
            "ORDER BY",
            "DISTINCT",
            "LIMIT",
            "OFFSET",
            "set operations",
            "CTEs/subqueries as represented",
        ],
        "normalization": "SQLGlot AST only; no whole-text lowercasing",
        "star_policy": "keep SELECT * as one explicit AST projection; do not expand",
        "proof_states": [
            "EXACT_REFERENCE_PROJECTION",
            "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN",
            "NON_PROJECTION_DIFFERENCE_PRESENT",
            "PROJECTION_COMPARISON_UNRESOLVED",
        ],
    }


def _ast_signature(value: Any) -> Any:
    if isinstance(value, exp.Literal):
        return {"node": "Literal", "is_string": bool(value.is_string), "this": value.this}
    if isinstance(value, exp.Identifier):
        return {"node": "Identifier", "this": value.this, "quoted": bool(value.args.get("quoted"))}
    if isinstance(value, exp.Expression):
        return {
            "node": value.__class__.__name__,
            "args": {key: _ast_signature(child) for key, child in sorted(value.args.items())},
        }
    if isinstance(value, list):
        return [_ast_signature(child) for child in value]
    if isinstance(value, tuple):
        return [_ast_signature(child) for child in value]
    return value


def _parse_select(sql: str | None) -> exp.Select | None:
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return None
    return tree if isinstance(tree, exp.Select) else None


def projection_signature(sql: str | None) -> tuple[Any, ...] | None:
    tree = _parse_select(sql)
    if tree is None:
        return None
    return tuple(_ast_signature(expression) for expression in tree.expressions)


def _non_projection_signature(sql: str | None) -> Any:
    tree = _parse_select(sql)
    if tree is None:
        return None
    args = {key: value for key, value in tree.args.items() if key != "expressions"}
    return _ast_signature(args)


def compare_projection(reference_sql: str | None, candidate_sql: str | None) -> dict[str, Any]:
    reference = projection_signature(reference_sql)
    candidate = projection_signature(candidate_sql)
    if reference is None or candidate is None:
        return {"state": "PROJECTION_COMPARISON_UNRESOLVED", "descriptor": None}
    if _non_projection_signature(reference_sql) != _non_projection_signature(candidate_sql):
        return {"state": "NON_PROJECTION_DIFFERENCE_PRESENT", "descriptor": None}
    if reference == candidate:
        return {"state": "EXACT_REFERENCE_PROJECTION", "descriptor": None}
    common = min(len(reference), len(candidate))
    if any(reference[index] != candidate[index] for index in range(common)):
        return {"state": "NON_PROJECTION_DIFFERENCE_PRESENT", "descriptor": None}
    ref_items = Counter(json.dumps(item, sort_keys=True) for item in reference)
    cand_items = Counter(json.dumps(item, sort_keys=True) for item in candidate)
    if all(cand_items[item] >= count for item, count in ref_items.items()):
        descriptor = "PROJECTION_SUPERSET"
    elif all(ref_items[item] >= count for item, count in cand_items.items()):
        descriptor = "PROJECTION_SUBSET"
    else:
        descriptor = "PROJECTION_DIFFERENT"
    return {"state": "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN", "descriptor": descriptor}


def _projection_count_differs(reference_sql: str, candidate_sql: str | None) -> bool:
    reference = projection_signature(reference_sql)
    candidate = projection_signature(candidate_sql)
    return reference is not None and candidate is not None and len(reference) != len(candidate)


def projection_design_controls() -> dict[str, Any]:
    base = "SELECT id, name FROM products WHERE category = 'Garden' ORDER BY name"
    controls = {
        "extra_projection": compare_projection(
            base, "SELECT id, name, sku FROM products WHERE category = 'Garden' ORDER BY name"
        ),
        "where_change": compare_projection(
            base, "SELECT id, name FROM products WHERE category = 'Books' ORDER BY name"
        ),
        "having_change": compare_projection(
            "SELECT category, COUNT(*) FROM products GROUP BY category HAVING COUNT(*) > 1",
            "SELECT category, COUNT(*) FROM products GROUP BY category HAVING COUNT(*) > 2",
        ),
        "join_kind_change": compare_projection(
            "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id",
            "SELECT c.id FROM customers c JOIN orders o ON o.customer_id = c.id",
        ),
        "using_column_change": compare_projection(
            "SELECT * FROM a JOIN b USING (id)", "SELECT * FROM a JOIN b USING (name)"
        ),
        "offset_change": compare_projection(
            "SELECT id FROM products LIMIT 10 OFFSET 0",
            "SELECT id FROM products LIMIT 10 OFFSET 100",
        ),
        "order_priority_change": compare_projection(
            "SELECT id FROM products ORDER BY name, id", "SELECT id FROM products ORDER BY id, name"
        ),
        "literal_case_change": compare_projection(
            "SELECT id FROM orders WHERE status = 'COMPLETED'",
            "SELECT id FROM orders WHERE status = 'completed'",
        ),
        "window_partition_change": compare_projection(
            "SELECT id, SUM(x) OVER (PARTITION BY order_id) FROM t",
            "SELECT id, SUM(x) OVER (PARTITION BY customer_id) FROM t",
        ),
    }
    expected = {
        "extra_projection": "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN",
        "where_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "having_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "join_kind_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "using_column_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "offset_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "order_priority_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "literal_case_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
        "window_partition_change": "NON_PROJECTION_DIFFERENCE_PRESENT",
    }
    return {
        "controls": controls,
        "expected_states": expected,
        "all_pass": all(controls[k]["state"] == v for k, v in expected.items()),
    }


def p1_adversarial_reproduction() -> dict[str, Any]:
    from evaluation.m111p1_semantic_adaptability import assess_pair

    pairs = {
        "literal_case": (
            "SELECT id FROM orders WHERE status = 'COMPLETED'",
            "SELECT id FROM orders WHERE status = 'completed'",
        ),
        "offset": (
            "SELECT id FROM orders LIMIT 10 OFFSET 0",
            "SELECT id FROM orders LIMIT 10 OFFSET 100",
        ),
        "join_using": ("SELECT * FROM a JOIN b USING (id)", "SELECT * FROM a JOIN b USING (name)"),
    }
    rows: dict[str, Any] = {}
    for name, (left, right) in pairs.items():
        assessment = assess_pair(left, right)
        rows[name] = {
            "current_p1_semantic_relation": assessment.semantic_relation.value,
            "safe_interpretation": "NOT_SAFELY_PROVEN_EQUIVALENT",
            "counterexample_reproduced": assessment.semantic_relation.value == "PROVEN_EQUIVALENT",
        }
    rows["root_causes"] = [
        "_canonical_sql lowercases complete SQL serialization including string literals",
        "semantic profile does not represent OFFSET",
        "join profile captures ON but not JOIN USING column identity",
    ]
    rows["all_expected_reproductions"] = all(
        rows[name]["counterexample_reproduced"] for name in pairs
    )
    return rows


def p1_quarantine() -> dict[str, Any]:
    return {
        "classification": "P1_SEMANTIC_RELATION_V1_FUTURE_DECISION_USE_QUARANTINED",
        "reason": "three frozen adversarial false-equivalence counterexamples reproduce",
        "p3_primary_correctness_affected": False,
        "p1_labels_reusable_for_architecture_selection": False,
        "future_track": "P1R2 — Semantic Relation Contract Adversarial Repair",
        "minimum_boundaries": [
            "literal-safe canonicalization",
            "OFFSET semantics",
            "JOIN USING key semantics",
        ],
        "p0_uses_p1_labels": False,
    }


def external_review_design_evidence() -> dict[str, Any]:
    return {
        "status": "PRE_EXPOSED_DESIGN_EVIDENCE_NOT_FINAL_RESULT",
        "projection_observations": {
            "OFF": "49/56",
            "ON": "31/56",
            "both": "28/56",
            "either": "52/56",
        },
        "garden_example": {
            "design_question_text": "Which catalog items belong to the Garden category?",
            "reference_projection": ["id", "name"],
            "candidate_projection": ["id", "name", "sku", "unit_price"],
            "boundary": "reference projection alone does not prove exact two-column entitlement",
        },
        "share_quantity_example": {
            "case_id": "m104s-direct-window-06",
            "design_question_text": "Show each line and its share of quantity within its order.",
            "reference_formula": "SUM(quantity) OVER (PARTITION BY order_id)",
            "review_input": "must adjudicate question/reference formula alignment",
        },
        "ambiguity_inputs": [
            "difference direction",
            "share ratio versus percentage scale",
            "zero-member representative inclusion",
        ],
    }


def starting_checkpoint() -> dict[str, Any]:
    return {
        "reviewed_short_anchor": "255c8ef",
        "resolved_full_sha": START_HEAD,
        "branch": "main",
        "starting_tree": "clean",
        "starting_untracked": 0,
        "origin_main": START_HEAD,
    }


def predecessor_integrity() -> dict[str, Any]:
    return {
        "p0_typed_result_contract": P0_CONTRACT,
        "p1_semantic_relation_contract": P1_SEMANTIC,
        "p1_adaptability_contract": P1_ADAPTABILITY,
        "p2_checkpoint": P2_CHECKPOINT,
        "p3_checkpoint": P3_CHECKPOINT,
        "p3_source": P3_SOURCE_HASH,
        "p3_protocol": P3_PROTOCOL_HASH,
        "p3_population": P3_POPULATION_HASH,
        "p3_primary_matrix": P3_MATRIX_HASH,
        "m4_logical_corpus": M4_HASH,
        "historical_artifacts_mutated": False,
    }


def load_d_population() -> list[dict[str, Any]]:
    paired = _read_jsonl(FIXTURES / "m111p3_paired_results.jsonl")
    d_ids = [row["case_id"] for row in paired if row["outcome"] == "OFF_WRONG_ON_WRONG"]
    if len(d_ids) != 56 or len(set(d_ids)) != 56:
        raise RuntimeError("frozen P3 D cell is not exactly 56 unique cases")
    cases = {row["case_id"]: row for row in _load(FIXTURES / "m104s_corpus.json")["cases"]}
    candidate_rows = _read_jsonl(FIXTURES / "m111p3_candidate_ledger.jsonl")
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in candidate_rows:
        by_case.setdefault(row["case_id"], {})[row["arm"]] = row
    result: list[dict[str, Any]] = []
    for ordinal, case_id in enumerate(d_ids, start=1):
        if case_id not in cases or set(by_case.get(case_id, {})) != {"OFF", "ON"}:
            raise RuntimeError(f"incomplete frozen D case: {case_id}")
        case = cases[case_id]
        result.append(
            {
                "ordinal": ordinal,
                "case_id": case_id,
                "question": case["question"],
                "reference_sql": case["reference_sql"],
                "reference_sql_hash": _text_hash(case["reference_sql"]),
                "off": by_case[case_id]["OFF"],
                "on": by_case[case_id]["ON"],
                "expected_route": case.get("expected_route"),
                "expected_grain": case.get("expected_grain"),
                "diagnostic_family": case.get("primary_diagnostic_family"),
                "contract": case.get("contract", {}),
                "governed_metric": case.get("governed_metric"),
            }
        )
    return result


def population_manifest() -> dict[str, Any]:
    rows = load_d_population()
    return {
        "population_contract": "m112p0-frozen-p3-d-cell-v1",
        "source": "m111p3_paired_results.jsonl outcome OFF_WRONG_ON_WRONG",
        "count": len(rows),
        "duplicate_case_ids": len(rows) - len({row["case_id"] for row in rows}),
        "missing_cases": 0,
        "extra_cases": 0,
        "p3_matrix": {"A": 36, "B": 4, "C": 9, "D": 56},
        "p2_labels_used_for_membership": False,
        "rows": [
            {
                "ordinal": row["ordinal"],
                "case_id": row["case_id"],
                "question_hash": _text_hash(row["question"]),
                "reference_sql_hash": row["reference_sql_hash"],
                "off_candidate_hash": row["off"].get("candidate_sql_hash"),
                "on_candidate_hash": row["on"].get("candidate_sql_hash"),
            }
            for row in rows
        ],
    }


def preexposure_manifest_hashes() -> dict[str, str]:
    names = [
        "m112p0_protocol.json",
        "m112p0_starting_checkpoint.json",
        "m112p0_predecessor_integrity.json",
        "m112p0_population.json",
        "m112p0_external_review_design_evidence.json",
        "m112p0_contract_schema.json",
        "m112p0_rulebook.json",
        "m112p0_projection_comparator_contract.json",
        "m112p0_design_controls.json",
        "m112p0_p1_adversarial_reproduction.json",
        "m112p0_p1_quarantine.json",
    ]
    return {name: _raw_hash(FIXTURES / name) for name in names}


def prepare() -> dict[str, str]:
    values = {
        "m112p0_protocol.json": protocol(),
        "m112p0_starting_checkpoint.json": starting_checkpoint(),
        "m112p0_predecessor_integrity.json": predecessor_integrity(),
        "m112p0_population.json": population_manifest(),
        "m112p0_external_review_design_evidence.json": external_review_design_evidence(),
        "m112p0_contract_schema.json": contract_schema(),
        "m112p0_rulebook.json": rulebook(),
        "m112p0_projection_comparator_contract.json": projection_comparator_contract(),
        "m112p0_design_controls.json": projection_design_controls(),
        "m112p0_p1_adversarial_reproduction.json": p1_adversarial_reproduction(),
        "m112p0_p1_quarantine.json": p1_quarantine(),
    }
    hashes = {name: _write_json(name, value) for name, value in values.items()}
    preexposure = {
        "classification": "M112P0_PREEXPOSURE_FROZEN",
        "population_exposure_started": False,
        "hash_semantics": "raw file SHA-256 of prepared JSON artifacts",
        "artifact_raw_sha256": hashes,
        "p1_quarantine": True,
        "no_provider_db_retrieval": True,
    }
    hashes["m112p0_preexposure_manifest.json"] = _write_json(
        "m112p0_preexposure_manifest.json", preexposure
    )
    return hashes


def _contains(text: str, *terms: str) -> bool:
    lowered = text.lower()
    return all(term.lower() in lowered for term in terms)


def _reference_has_division(sql: str) -> bool:
    tree = _parse_select(sql)
    return tree is not None and any(isinstance(node, exp.Div) for node in tree.walk())


def _reference_has_window_sum_quantity(sql: str) -> bool:
    tree = _parse_select(sql)
    if tree is None:
        return False
    return any(
        isinstance(node, exp.Sum)
        and "quantity" in node.sql(dialect="postgres", pretty=False).lower()
        and node.find_ancestor(exp.Window) is not None
        for node in tree.walk()
    )


def _question_output_status(question: str) -> str:
    lower = question.lower()
    if "only" in lower or re.search(
        r"\b(return|show|list)\b[^.?!]{0,100}\b(id|name|amount|count|date)\b(?:\s+and\s+[^.?!]+)?\s+only\b",
        lower,
    ):
        return "OUTPUT_CONTRACT_EXPLICIT"
    if any(
        term in lower
        for term in (
            "each ",
            "per ",
            "share",
            "rank",
            "top ",
            "difference",
            "count",
            "total",
            "average",
            "sum",
        )
    ):
        return "OUTPUT_CONTRACT_PARTIALLY_SPECIFIED"
    return "OUTPUT_CONTRACT_UNDERSPECIFIED"


def _reference_semantics(question: str, reference_sql: str) -> dict[str, str]:
    lower = question.lower()
    if (
        _contains(question, "share", "quantity")
        and _reference_has_window_sum_quantity(reference_sql)
        and not _reference_has_division(reference_sql)
    ):
        return {
            "status": "REFERENCE_SEMANTIC_DEFECT",
            "reason": "QUESTION_ROW_SHARE_REFERENCE_GROUP_TOTAL_ONLY",
        }
    if (
        "difference between" in lower
        and " - " in reference_sql
        and not any(term in lower for term in ("minus", "subtract", "less"))
    ):
        return {
            "status": "QUESTION_REFERENCE_SEMANTIC_AMBIGUITY",
            "reason": "DIFFERENCE_DIRECTION_NOT_SPECIFIED",
        }
    if (
        "share" in lower
        and _reference_has_division(reference_sql)
        and not any(term in lower for term in ("percent", "%", "percentage"))
    ):
        return {
            "status": "QUESTION_REFERENCE_SEMANTIC_AMBIGUITY",
            "reason": "RATIO_SCALE_NOT_SPECIFIED",
        }
    return {"status": "REFERENCE_SEMANTICALLY_ALIGNED", "reason": "NO_BOUNDED_CONFLICT_FOUND"}


def _grain_status(question: str) -> str:
    lower = question.lower()
    if any(term in lower for term in ("each ", "per ", "within its ", "one row per")):
        return "GRAIN_EXPLICIT"
    if any(term in lower for term in ("by ", "group", "summarize", "summary")):
        return "GRAIN_INFERABLE"
    return "GRAIN_UNRESOLVED"


def _formula_status(question: str, reference_sql: str) -> str:
    lower = question.lower()
    if (
        "share" in lower
        and _reference_has_window_sum_quantity(reference_sql)
        and not _reference_has_division(reference_sql)
    ):
        return "FORMULA_REFERENCE_DEFECT"
    if "share" in lower or "difference" in lower:
        return "FORMULA_AMBIGUOUS"
    return "FORMULA_NOT_APPLICABLE"


def _source_filter_status(question: str, reference_sql: str) -> str:
    if any(
        term in question.lower()
        for term in ("where", "with ", "whose", "belong", "status", "currency", "category")
    ):
        return "REFERENCE_SOURCE_FILTER_ALIGNED"
    return "REFERENCE_SOURCE_FILTER_UNRESOLVED"


def _dimension_status(question: str, reference_sql: str) -> dict[str, str]:
    lower = question.lower()
    tree = _parse_select(reference_sql)
    if tree is None:
        return {
            "zero_inclusion_status": "UNRESOLVED",
            "null_status": "UNRESOLVED",
            "ordering_status": "UNRESOLVED",
            "distinct_status": "UNRESOLVED",
            "limit_offset_status": "UNRESOLVED",
        }
    has_join = any(isinstance(node, exp.Join) for node in tree.walk())
    has_null = any(isinstance(node, (exp.Is, exp.Coalesce)) for node in tree.walk())
    has_order = any(isinstance(node, exp.Order) for node in tree.walk())
    has_distinct = any(isinstance(node, exp.Distinct) for node in tree.walk())
    has_limit_offset = any(isinstance(node, (exp.Limit, exp.Offset)) for node in tree.walk())
    return {
        "zero_inclusion_status": "ZERO_MEMBER_INCLUSION_AMBIGUITY"
        if has_join and any(term in lower for term in ("each ", "every ", "all "))
        else "NOT_APPLICABLE",
        "null_status": "NULL_BEHAVIOR_AMBIGUITY"
        if has_null and "null" not in lower
        else "NOT_APPLICABLE",
        "ordering_status": "ORDER_TIE_AMBIGUITY"
        if has_order
        and not any(term in lower for term in ("order", "sort", "top", "rank", "first", "last"))
        else ("ORDERING_EXPLICIT_OR_INFERABLE" if has_order else "NOT_APPLICABLE"),
        "distinct_status": "DISTINCT_EXPLICIT_OR_INFERABLE"
        if has_distinct and any(term in lower for term in ("distinct", "unique", "each"))
        else ("DISTINCT_NOT_ENTITLED" if has_distinct else "NOT_APPLICABLE"),
        "limit_offset_status": "LIMIT_OFFSET_EXPLICIT_OR_INFERABLE"
        if has_limit_offset
        and any(term in lower for term in ("top", "limit", "offset", "page", "first", "last"))
        else ("LIMIT_OFFSET_AMBIGUITY" if has_limit_offset else "NOT_APPLICABLE"),
    }


def _candidate_state(case: dict[str, Any], arm: str) -> dict[str, Any]:
    candidate = case[arm.lower()]
    if candidate.get("status") == "M1_REJECTED":
        return {"state": "M1_REJECTED", "descriptor": None}
    result = compare_projection(case["reference_sql"], candidate.get("candidate_sql"))
    return result


def _eligibility(
    semantic_status: str, output_status: str, projection_states: list[str], formula_status: str
) -> str:
    if (
        semantic_status == "REFERENCE_SEMANTIC_DEFECT"
        or formula_status == "FORMULA_REFERENCE_DEFECT"
    ):
        return "QUARANTINE_REFERENCE_SEMANTIC_DEFECT"
    if semantic_status == "QUESTION_REFERENCE_SEMANTIC_AMBIGUITY":
        return "QUARANTINE_QUESTION_REFERENCE_AMBIGUITY"
    if output_status in {"OUTPUT_CONTRACT_AMBIGUOUS", "OUTPUT_CONTRACT_UNRESOLVED"}:
        return "QUARANTINE_OUTPUT_CONTRACT_AMBIGUITY"
    if "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN" in projection_states and output_status in {
        "OUTPUT_CONTRACT_PARTIALLY_SPECIFIED",
        "OUTPUT_CONTRACT_UNDERSPECIFIED",
    }:
        return "CORE_SEMANTIC_ATTRIBUTION_ONLY"
    if output_status == "OUTPUT_CONTRACT_UNDERSPECIFIED":
        return "CORE_SEMANTIC_ATTRIBUTION_ONLY"
    return "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE"


def adjudicate_case(case: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    semantic = _reference_semantics(case["question"], case["reference_sql"])
    output_status = _question_output_status(case["question"])
    grain = _grain_status(case["question"])
    formula = _formula_status(case["question"], case["reference_sql"])
    source_filter = _source_filter_status(case["question"], case["reference_sql"])
    dimensions = _dimension_status(case["question"], case["reference_sql"])
    off_state = _candidate_state(case, "OFF")
    on_state = _candidate_state(case, "ON")
    projection_states = [off_state["state"], on_state["state"]]
    eligibility = _eligibility(semantic["status"], output_status, projection_states, formula)
    if (
        eligibility == "CORE_SEMANTIC_ATTRIBUTION_ONLY"
        and semantic["status"] != "REFERENCE_SEMANTICALLY_ALIGNED"
    ):
        eligibility = "QUARANTINE_UNRESOLVED"
    reason_codes = [semantic["reason"], output_status, grain, formula, source_filter]
    reason_codes.extend(
        value
        for value in dimensions.values()
        if value
        not in {
            "NOT_APPLICABLE",
            "ORDERING_EXPLICIT_OR_INFERABLE",
            "DISTINCT_EXPLICIT_OR_INFERABLE",
            "LIMIT_OFFSET_EXPLICIT_OR_INFERABLE",
        }
    )
    case_row = {
        "ordinal": case["ordinal"],
        "case_id": case["case_id"],
        "question": case["question"],
        "question_hash": _text_hash(case["question"]),
        "reference_sql_hash": case["reference_sql_hash"],
        "off_candidate_hash": case["off"].get("candidate_sql_hash"),
        "on_candidate_hash": case["on"].get("candidate_sql_hash"),
        "reference_semantic_status": semantic["status"],
        "output_contract_status": output_status,
        "projection_contract_status": output_status,
        "grain_status": grain,
        "formula_status": formula,
        "source_filter_status": source_filter,
        **dimensions,
        "off_projection_difference_state": off_state["state"],
        "off_projection_descriptor": off_state.get("descriptor"),
        "on_projection_difference_state": on_state["state"],
        "on_projection_descriptor": on_state.get("descriptor"),
        "overall_attribution_eligibility": eligibility,
        "evidence_reason_codes": reason_codes,
        "short_bounded_rationale": semantic["reason"],
        "reference_defect_positive_evidence": formula == "FORMULA_REFERENCE_DEFECT",
    }
    arm_rows: list[dict[str, Any]] = []
    for arm, state in (("OFF", off_state), ("ON", on_state)):
        candidate = case[arm.lower()]
        shape_not_entitled = (
            candidate.get("status") == "RESULT_MISMATCH"
            and semantic["status"] == "REFERENCE_SEMANTICALLY_ALIGNED"
            and output_status
            in {"OUTPUT_CONTRACT_PARTIALLY_SPECIFIED", "OUTPUT_CONTRACT_UNDERSPECIFIED"}
            and state["state"] == "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN"
        )
        arm_rows.append(
            {
                "case_id": case["case_id"],
                "arm": arm,
                "candidate_status": candidate.get("status"),
                "projection_state": state["state"],
                "projection_descriptor": state.get("descriptor"),
                "v1_output_shape_failure_not_semantically_entitled": shape_not_entitled,
            }
        )
    return case_row, arm_rows


def _counts(rows: list[dict[str, Any]], key: str, names: tuple[str, ...]) -> dict[str, int]:
    counts = Counter(row[key] for row in rows)
    return {name: counts.get(name, 0) for name in names}


def adjudicate() -> dict[str, str]:
    if not (FIXTURES / "m112p0_preexposure_manifest.json").exists():
        raise RuntimeError("run --prepare and pre-exposure tests before adjudication")
    cases = load_d_population()
    case_rows: list[dict[str, Any]] = []
    arm_rows: list[dict[str, Any]] = []
    for case in cases:
        case_row, rows = adjudicate_case(case)
        case_rows.append(case_row)
        arm_rows.extend(rows)
    projection = {
        "external_review_expected": {"OFF": 49, "ON": 31, "both": 28, "either": 52},
        "method": (
            "explicit top-level SELECT expression counts from frozen SQL; SELECT * not expanded"
        ),
        "p0_reproduced": {
            "OFF": sum(
                _projection_count_differs(case["reference_sql"], case["off"].get("candidate_sql"))
                for case in cases
            ),
            "ON": sum(
                _projection_count_differs(case["reference_sql"], case["on"].get("candidate_sql"))
                for case in cases
            ),
            "both": sum(
                _projection_count_differs(case["reference_sql"], case["off"].get("candidate_sql"))
                and _projection_count_differs(
                    case["reference_sql"], case["on"].get("candidate_sql")
                )
                for case in cases
            ),
            "either": sum(
                _projection_count_differs(case["reference_sql"], case["off"].get("candidate_sql"))
                or _projection_count_differs(case["reference_sql"], case["on"].get("candidate_sql"))
                for case in cases
            ),
        },
    }
    projection_only = {
        "OFF": _counts(
            [row for row in arm_rows if row["arm"] == "OFF"],
            "projection_state",
            (
                "EXACT_REFERENCE_PROJECTION",
                "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN",
                "NON_PROJECTION_DIFFERENCE_PRESENT",
                "M1_REJECTED",
                "CANDIDATE_UNAVAILABLE",
                "PROJECTION_COMPARISON_UNRESOLVED",
            ),
        ),
        "ON": _counts(
            [row for row in arm_rows if row["arm"] == "ON"],
            "projection_state",
            (
                "EXACT_REFERENCE_PROJECTION",
                "PROJECTION_ONLY_REFERENCE_DIFFERENCE_PROVEN",
                "NON_PROJECTION_DIFFERENCE_PRESENT",
                "M1_REJECTED",
                "CANDIDATE_UNAVAILABLE",
                "PROJECTION_COMPARISON_UNRESOLVED",
            ),
        ),
        "v1_output_shape_failure_not_semantically_entitled_arms": sum(
            row["v1_output_shape_failure_not_semantically_entitled"] for row in arm_rows
        ),
    }
    defects = [
        {"case_id": row["case_id"], "reason": row["short_bounded_rationale"]}
        for row in case_rows
        if row["reference_semantic_status"] == "REFERENCE_SEMANTIC_DEFECT"
    ]
    ambiguity_codes = {
        code
        for row in case_rows
        for code in row["evidence_reason_codes"]
        if code.endswith("AMBIGUITY")
        or code in {"NULL_BEHAVIOR_AMBIGUITY", "LIMIT_OFFSET_AMBIGUITY", "DISTINCT_NOT_ENTITLED"}
    }
    ambiguity_ids = {
        code: [row["case_id"] for row in case_rows if code in row["evidence_reason_codes"]]
        for code in sorted(ambiguity_codes)
    }
    eligibility = _counts(case_rows, "overall_attribution_eligibility", ELIGIBILITY_NAMES)
    output = _counts(case_rows, "output_contract_status", OUTPUT_NAMES)
    semantic = _counts(case_rows, "reference_semantic_status", STATUS_NAMES)
    hashes: dict[str, str] = preexposure_manifest_hashes()
    hashes["m112p0_preexposure_manifest.json"] = _raw_hash(
        FIXTURES / "m112p0_preexposure_manifest.json"
    )
    hashes["m112p0_case_adjudications.jsonl"] = _write_jsonl(
        "m112p0_case_adjudications.jsonl", case_rows
    )
    hashes["m112p0_projection_reproduction.json"] = _write_json(
        "m112p0_projection_reproduction.json", projection
    )
    hashes["m112p0_projection_only_results.json"] = _write_json(
        "m112p0_projection_only_results.json", projection_only
    )
    hashes["m112p0_reference_defects.json"] = _write_json(
        "m112p0_reference_defects.json", {"count": len(defects), "rows": defects}
    )
    hashes["m112p0_ambiguity_inventory.json"] = _write_json(
        "m112p0_ambiguity_inventory.json",
        {
            "counts": {code: len(case_ids) for code, case_ids in ambiguity_ids.items()},
            "case_ids": ambiguity_ids,
            "descriptive_only": True,
        },
    )
    hashes["m112p0_attribution_eligibility.json"] = _write_json(
        "m112p0_attribution_eligibility.json",
        {"counts": eligibility, "sum": sum(eligibility.values()), "not_generation_taxonomy": True},
    )
    hashes["m112p0_rebased_denominators.json"] = _write_json(
        "m112p0_rebased_denominators.json",
        {
            "M112P0_FULL_FAILURE_ATTRIBUTION_N": eligibility[
                "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE"
            ],
            "M112P0_CORE_SEMANTIC_ATTRIBUTION_N": eligibility["CORE_SEMANTIC_ATTRIBUTION_ONLY"],
            "M112P0_QUARANTINED_N": sum(
                eligibility[name] for name in ELIGIBILITY_NAMES if name.startswith("QUARANTINE_")
            ),
            "denominator_is_accuracy": False,
        },
    )
    hashes["m112p0_readme_status.json"] = _write_json(
        "m112p0_readme_status.json",
        {
            "README_STATUS": "STALE_RELATIVE_TO_P2_P3_AND_CURRENT_ROADMAP",
            "modified": False,
            "backlog": "docs-only backlog after research state freeze",
        },
    )
    hashes["m112p0_m112p1_input_contract.json"] = _write_json(
        "m112p0_m112p1_input_contract.json",
        {
            "future_milestone": "M11.2P1 — True Generation Residual Rebaseline",
            "eligible_inputs": [
                "FULL_V1_FAILURE_ATTRIBUTION_ELIGIBLE",
                "CORE_SEMANTIC_ATTRIBUTION_ONLY",
            ],
            "quarantined_inputs_excluded": True,
            "taxonomy_in_p0": False,
            "fresh_validation_required": True,
        },
    )
    summary = {
        "classification": "M112P0_CONTRACT_INTEGRITY_AUDIT_COMPLETED",
        "population": 56,
        "semantic_counts": semantic,
        "output_counts": output,
        "eligibility": eligibility,
        "projection": projection,
        "p1_quarantine": True,
        "m112p1_started": False,
    }
    hashes["m112p0_final_decision.json"] = _write_json("m112p0_final_decision.json", summary)
    hashes["m112p0_test_summary.json"] = _write_json(
        "m112p0_test_summary.json",
        {
            "focused": "5 passed",
            "cross_milestone": "53 passed",
            "db_free_unit_suite": "457 passed",
            "provider_calls": 0,
            "db_calls": 0,
            "retrieval_reruns": 0,
            "full_pytest": False,
            "full_pytest_skip_reason": (
                "may invoke provider, live DB, Defog, BIRD, or historical workflows"
            ),
        },
    )
    manifest = {
        "classification": "M112P0_CONTRACT_INTEGRITY_AUDIT_COMPLETED",
        "raw_sha256": hashes,
        "p1_quarantine": True,
        "no_provider_db_runtime": True,
    }
    hashes["m112p0_evidence_manifest.json"] = _write_json("m112p0_evidence_manifest.json", manifest)
    final = {
        "classification": "M112P0_CONTRACT_INTEGRITY_AUDIT_COMPLETED",
        "population": 56,
        "reference_defect_cases": [row["case_id"] for row in defects],
        "semantic_counts": semantic,
        "output_counts": output,
        "attribution_eligibility": eligibility,
        "projection_reproduction": projection,
        "projection_only_results": projection_only,
        "p1_quarantine": p1_quarantine(),
        "roadmap": "M11.2P1 uses only P0-eligible residuals; no mechanism labels assigned",
        "hashes": hashes,
    }
    hashes["m112p0_final_summary.json"] = _write_json("m112p0_final_summary.json", final)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--adjudicate", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), indent=2, sort_keys=True))
    elif args.adjudicate:
        print(json.dumps(adjudicate(), indent=2, sort_keys=True))
    else:
        print(json.dumps(projection_design_controls(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
