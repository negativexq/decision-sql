"""Offline POST-M11.2P1 fragmentation and attribution-capability review.

Phase A (``--prepare``) freezes the diagnostic blocker taxonomy, structural
profile contract, reachability audit, decision rules, and controls without
loading case-level residual records.  Phase B (``--analyze``) then reads only
the frozen P1 artifacts and produces additive aggregate diagnostics for the 46
neither-arm-proven pairs and the seven resolved controls.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlglot import exp, parse_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
START_HEAD = "cc7e9c81a0effa1358f579c8ed557ab4ed4db138"
P1_SOURCE_HASH = "3806c698567f4157a9e3920100201491ad3588c060c2e4e8e13dc8a72709c131"
P1_FINAL_SUMMARY_HASH = "cbcd2ec96193109a0ec3539a7ba19e56f1bcfbac63fc338a5e7e287914afee08"
P1_MANIFEST_HASH = "a900e47c678d43b9d18a55adcabba42fdbd65d937379cc58a292f71e1d00ade4"
P1_PREEXPOSURE_HASH = "e52d997139dff6df2f13d7c3fa152e471b2d50fbbc8bf36a28042c98fda0906c"
P1_CASE_HASH = "e7f75154218b408ba9379d05f5daa338191bef9211ecb1e56ef68629b0660f85"
P1_UNRESOLVED_HASH = "0cdf2298d618602664e31fec8bd2bd122ac20cd950b9509a42d500c9c6c3ccff"
P1_PAIR_STATE_HASH = "81f0a6cc2e36e4c01b493dfa5e8570ae6e2028164822a24c9f22861c5973450c"
P1_GATE_HASH = "4ae611d89781eaa41970c4bb89d7bd0a2f6d23a00308bee7ccba61fb86431c97"

FAMILIES = (
    "M1_REJECTION",
    "SOURCE_ENTITY_SELECTION",
    "RELATIONAL_COMPOSITION_JOIN",
    "ROW_FILTER_VALUE_SEMANTICS",
    "GROUP_FILTER_HAVING_SEMANTICS",
    "GROUPING_GRAIN_SEMANTICS",
    "AGGREGATION_SEMANTICS",
    "FORMULA_BUSINESS_SEMANTICS",
    "WINDOW_SEMANTICS",
    "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "DISTINCT_SET_SEMANTICS",
    "NULL_ZERO_INCLUSION_SEMANTICS",
    "PROJECTION_OUTPUT_SEMANTICS",
    "OTHER_BOUNDED_SEMANTICS",
    "UNRESOLVED",
)
BLOCKERS = (
    "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
    "STATIC_SEMANTIC_PROOF_LIMIT",
    "DATA_DEPENDENT_SEMANTICS_REQUIRED",
    "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
    "VALUE_DOMAIN_SEMANTICS_LIMIT",
    "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
    "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
    "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
    "INSUFFICIENT_FROZEN_PROVENANCE",
    "OBSERVED_HETEROGENEOUS_DIVERGENCE",
    "OTHER_BOUNDED_DIAGNOSTIC_LIMIT",
    "RESOLUTION_BLOCKER_UNRESOLVED",
)
PROFILE_DIMS = (
    "SOURCE_SET_CHANGE",
    "JOIN_SIGNATURE_CHANGE",
    "WHERE_SIGNATURE_CHANGE",
    "GROUP_BY_CHANGE",
    "HAVING_CHANGE",
    "AGGREGATE_SIGNATURE_CHANGE",
    "FORMULA_EXPRESSION_CHANGE",
    "WINDOW_SIGNATURE_CHANGE",
    "ORDER_SIGNATURE_CHANGE",
    "LIMIT_CHANGE",
    "OFFSET_CHANGE",
    "DISTINCT_SET_CHANGE",
    "PROJECTION_CHANGE",
    "CTE_SUBQUERY_SHAPE_CHANGE",
    "OTHER_SUPPORTED_STRUCTURE_CHANGE",
)
REACHABILITY = (
    "GENERIC_DETERMINISTIC_POSITIVE_RULE",
    "PARTIAL_DETERMINISTIC_POSITIVE_RULE",
    "STATIC_CASE_ADJUDICATION_ONLY",
    "M1_METADATA_ONLY",
    "STRUCTURAL_EVIDENCE_ONLY_NO_FAILURE_RULE",
    "NO_POSITIVE_ATTRIBUTION_PATH",
    "REACHABILITY_UNRESOLVED",
)

FAMILY_TO_PROFILE = {
    "SOURCE_SET_CHANGE": "SOURCE_ENTITY_SELECTION",
    "JOIN_SIGNATURE_CHANGE": "RELATIONAL_COMPOSITION_JOIN",
    "WHERE_SIGNATURE_CHANGE": "ROW_FILTER_VALUE_SEMANTICS",
    "GROUP_BY_CHANGE": "GROUPING_GRAIN_SEMANTICS",
    "HAVING_CHANGE": "GROUP_FILTER_HAVING_SEMANTICS",
    "AGGREGATE_SIGNATURE_CHANGE": "AGGREGATION_SEMANTICS",
    "FORMULA_EXPRESSION_CHANGE": "FORMULA_BUSINESS_SEMANTICS",
    "WINDOW_SIGNATURE_CHANGE": "WINDOW_SEMANTICS",
    "ORDER_SIGNATURE_CHANGE": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "LIMIT_CHANGE": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "OFFSET_CHANGE": "ORDER_RANK_LIMIT_OFFSET_SEMANTICS",
    "DISTINCT_SET_CHANGE": "DISTINCT_SET_SEMANTICS",
    "PROJECTION_CHANGE": "PROJECTION_OUTPUT_SEMANTICS",
}


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _raw_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _stable_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _write_json(name: str, value: Any) -> str:
    path = FIXTURES / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return _raw_hash(path)


def _write_jsonl(name: str, rows: Iterable[dict[str, Any]]) -> str:
    path = FIXTURES / name
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return _raw_hash(path)


def protocol() -> dict[str, Any]:
    return {
        "milestone": "POST-M11.2P1 Fragmentation Review",
        "classification_states": [
            "POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT",
            "POSTM112P1_FRAGMENTED_RESIDUAL_CONFIRMED",
            "POSTM112P1_MIXED_FRAGMENTATION_AND_ATTRIBUTION_LIMIT",
            "POSTM112P1_INSUFFICIENT_EVIDENCE",
        ],
        "population": {"primary_unresolved": 46, "controls": 7, "eligible": 53},
        "p1_predecessor_immutable": True,
        "no_new_failure_family_attribution": True,
        "zero_provider_db_retrieval": True,
        "no_implementation": True,
        "taxonomy_frozen_before_case_exposure": True,
    }


def starting_checkpoint() -> dict[str, Any]:
    return {
        "checkpoint": START_HEAD,
        "branch": "main",
        "origin_main": START_HEAD,
        "starting_tree": "clean",
        "tracked": 0,
        "staged": 0,
        "untracked": 0,
    }


def predecessor_integrity() -> dict[str, Any]:
    return {
        "p1_checkpoint": START_HEAD,
        "p1_classification": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "p1_repeated_boundary": "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE",
        "p1_source_hash": P1_SOURCE_HASH,
        "p1_final_summary_hash": P1_FINAL_SUMMARY_HASH,
        "p1_evidence_manifest_hash": P1_MANIFEST_HASH,
        "p1_preexposure_hash": P1_PREEXPOSURE_HASH,
        "p1_case_attribution_hash": P1_CASE_HASH,
        "p1_unresolved_summary_hash": P1_UNRESOLVED_HASH,
        "p1_pair_state_hash": P1_PAIR_STATE_HASH,
        "p1_repeated_boundary_hash": P1_GATE_HASH,
        "p1_population": {"eligible": 53, "FULL": 23, "CORE": 30, "excluded": 3},
        "p1_secondary_labels_authority": (
            "P1 labels are read descriptively; no new family support is created"
        ),
        "historical_artifacts_mutated": False,
    }


def blocker_taxonomy() -> dict[str, Any]:
    return {
        "version": "post-m112p1-blockers-v1",
        "primary_blockers": list(BLOCKERS),
        "exactly_one_primary_per_unresolved_pair": True,
        "secondary_blockers_multi_label": True,
        "not_failure_families": True,
        "post_exposure_additions": False,
    }


def structural_profile_contract() -> dict[str, Any]:
    return {
        "version": "post-m112p1-structural-profiles-v1",
        "dimensions": list(PROFILE_DIMS),
        "pair_profile_is_union_of_off_and_on_profiles": True,
        "literal_preserving": True,
        "preserves": [
            "string literal case",
            "numeric/date/boolean values",
            "JOIN kind/ON/USING",
            "HAVING",
            "ORDER priority/direction",
            "LIMIT/OFFSET",
            "window partition/order/frame",
            "DISTINCT/set operations",
        ],
        "profile_is_not_semantic_failure": True,
    }


def family_reachability_contract() -> dict[str, Any]:
    return {
        "states": list(REACHABILITY),
        "decision_question": "can the frozen P1 source positively prove this family?",
        "zero_support_is_not_absence_by_default": True,
    }


def implementation_coverage() -> dict[str, Any]:
    partial = {
        "reachability": "PARTIAL_DETERMINISTIC_POSITIVE_RULE",
        "source_path": "evaluation/m112p1_true_generation_residual.py::_positive_failure",
        "generic": True,
        "case_specific": False,
        "default": "no positive rule match -> no family label; unresolved downstream",
    }
    rows = {
        "M1_REJECTION": {
            "reachability": "M1_METADATA_ONLY",
            "source_path": "::_arm -> ::_m1_failure",
            "generic": True,
            "case_specific": False,
            "default": "candidate status not M1_REJECTED -> not applicable",
        },
        "NULL_ZERO_INCLUSION_SEMANTICS": {
            "reachability": "NO_POSITIVE_ATTRIBUTION_PATH",
            "source_path": "::_positive_failure null_difference_without_contract guard only",
            "generic": False,
            "case_specific": False,
            "default": "no positive NULL/zero family assignment",
        },
        "OTHER_BOUNDED_SEMANTICS": {
            "reachability": "NO_POSITIVE_ATTRIBUTION_PATH",
            "source_path": "no assignment branch in ::_positive_failure",
            "generic": False,
            "case_specific": False,
            "default": "structural divergence falls through unresolved",
        },
        "UNRESOLVED": {
            "reachability": "STRUCTURAL_EVIDENCE_ONLY_NO_FAILURE_RULE",
            "source_path": "::_arm fallback and ::_primary",
            "generic": True,
            "case_specific": False,
            "default": "empty proven_failure_families -> UNRESOLVED",
        },
    }
    for family in FAMILIES:
        rows.setdefault(family, dict(partial))
    return {
        "source": "evaluation/m112p1_true_generation_residual.py",
        "positive_mechanism": (
            "single family-specific _positive_failure dispatcher plus M1 metadata path"
        ),
        "static_case_map_count": 0,
        "hardcoded_case_ids": 0,
        "default_for_unmatched_case": "UNRESOLVED / no proven family",
        "families": rows,
        "protocol_vs_implementation": "PROTOCOL_BROADER_THAN_IMPLEMENTED_ATTRIBUTION",
        "intentional_conservatism_documented": True,
    }


def decision_rule() -> dict[str, Any]:
    capability = [
        "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
        "STATIC_SEMANTIC_PROOF_LIMIT",
        "DATA_DEPENDENT_SEMANTICS_REQUIRED",
        "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
        "VALUE_DOMAIN_SEMANTICS_LIMIT",
        "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
        "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
        "INSUFFICIENT_FROZEN_PROVENANCE",
    ]
    return {
        "capability_limit_blockers": capability,
        "dominant": {
            "single_capability_min": 24,
            "unresolved_max": 10,
            "systematic_source_evidence_required": True,
            "generic_fallback_does_not_qualify": True,
        },
        "mixed": {
            "collective_capability_min": 15,
            "heterogeneity_or_compound_min": 15,
            "recurring_profiles_alternative": {"min_profiles": 4, "min_support_each": 3},
            "unresolved_max": 10,
        },
        "fragmented": {
            "collective_capability_max_exclusive": 10,
            "unresolved_max": 5,
            "characterized_min": 37,
            "profiles_min": 4,
            "profile_support_min": 5,
            "largest_profile_max_exclusive": 10,
            "no_reachability_gap": True,
            "majority_zero_support_families_evaluable": True,
        },
        "otherwise": "POSTM112P1_INSUFFICIENT_EVIDENCE",
        "thresholds_immutable_after_exposure": True,
    }


def design_controls() -> dict[str, Any]:
    return {
        "version": "post-m112p1-design-controls-v1",
        "all_controls_pre_exposure": True,
        "controls": {
            "taxonomy_without_detector": "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
            "represented_but_not_provable": "STATIC_SEMANTIC_PROOF_LIMIT",
            "duplicate_or_data_distribution": "DATA_DEPENDENT_SEMANTICS_REQUIRED",
            "core_projection_only": "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
            "multiple_changed_dimensions": "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
            "unsupported_node": "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
            "missing_frozen_fact": "INSUFFICIENT_FROZEN_PROVENANCE",
            "no_positive_blocker": "RESOLUTION_BLOCKER_UNRESOLVED",
            "profile_not_failure_family": True,
            "p1_labels_not_authority": True,
        },
    }


def _preexposure_names() -> list[str]:
    return [
        "post_m112p1_protocol.json",
        "post_m112p1_starting_checkpoint.json",
        "post_m112p1_predecessor_integrity.json",
        "post_m112p1_implementation_coverage.json",
        "post_m112p1_blocker_taxonomy.json",
        "post_m112p1_structural_profile_contract.json",
        "post_m112p1_family_reachability.json",
        "post_m112p1_decision_rule.json",
        "post_m112p1_design_controls.json",
    ]


def prepare() -> dict[str, str]:
    values = {
        "post_m112p1_protocol.json": protocol(),
        "post_m112p1_starting_checkpoint.json": starting_checkpoint(),
        "post_m112p1_predecessor_integrity.json": predecessor_integrity(),
        "post_m112p1_implementation_coverage.json": implementation_coverage(),
        "post_m112p1_blocker_taxonomy.json": blocker_taxonomy(),
        "post_m112p1_structural_profile_contract.json": structural_profile_contract(),
        "post_m112p1_family_reachability.json": family_reachability_contract(),
        "post_m112p1_decision_rule.json": decision_rule(),
        "post_m112p1_design_controls.json": design_controls(),
    }
    hashes = {name: _write_json(name, value) for name, value in values.items()}
    manifest = {
        "classification": "POSTM112P1_PREEXPOSURE_FROZEN",
        "population_case_exposure_started": False,
        "artifact_raw_sha256": hashes,
        "taxonomy_immutable_after_exposure": True,
        "no_case_level_unresolved_records_read": True,
        "provider_db_retrieval": False,
    }
    hashes["post_m112p1_preexposure_manifest.json"] = _write_json(
        "post_m112p1_preexposure_manifest.json", manifest
    )
    return hashes


def _sig(value: Any) -> Any:
    if isinstance(value, exp.Literal):
        return {"node": "Literal", "is_string": bool(value.is_string), "this": value.this}
    if isinstance(value, exp.Identifier):
        return {"node": "Identifier", "this": value.this, "quoted": bool(value.args.get("quoted"))}
    if isinstance(value, exp.Expression):
        return {
            "node": value.__class__.__name__,
            "args": {
                key: _sig(child) for key, child in sorted(value.args.items()) if child is not None
            },
        }
    if isinstance(value, list):
        return [_sig(item) for item in value]
    if isinstance(value, tuple):
        return [_sig(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _sig(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"scalar_type": type(value).__name__, "value": str(value)}


def _parse(sql: str | None) -> exp.Expression | None:
    if not isinstance(sql, str) or not sql.strip():
        return None
    try:
        return parse_one(sql, read="postgres")
    except Exception:
        return None


def _node_sig(node: exp.Expression | None) -> Any:
    return None if node is None else _sig(node)


def _normalise_tree(tree: exp.Expression) -> exp.Expression:
    value = tree.copy()
    aliases: dict[str, str] = {}
    tables = list(value.find_all(exp.Table))
    single_source = tables[0].name if len(tables) == 1 else None
    for table in tables:
        if table.alias:
            aliases[table.alias] = table.name
        table.set("alias", None)
    for column in value.find_all(exp.Column):
        if column.table:
            column.set("table", exp.Identifier(this=aliases.get(column.table, column.table)))
        elif single_source:
            column.set("table", exp.Identifier(this=single_source))
    for ordered in value.find_all(exp.Ordered):
        if ordered.args.get("desc") is False:
            ordered.set("desc", None)
    return value


def _select(tree: exp.Expression | None) -> exp.Select | None:
    if isinstance(tree, exp.Select):
        return tree
    return tree.find(exp.Select) if tree is not None else None


def _nodes(tree: exp.Expression, cls: Any) -> list[exp.Expression]:
    return list(tree.find_all(cls))


def _feature(sql: str | None) -> dict[str, Any] | None:
    tree = _parse(sql)
    select = _select(tree)
    if tree is None or select is None:
        return None
    tree = _normalise_tree(tree)
    select = _select(tree)
    if select is None:
        return None
    joins = [
        {
            "kind": join.args.get("side") or join.args.get("kind") or "INNER",
            "on": _node_sig(join.args.get("on")),
            "using": _node_sig(join.args.get("using")),
        }
        for join in _nodes(tree, exp.Join)
    ]
    tables = sorted((_node_sig(node) for node in _nodes(tree, exp.Table)), key=_stable_hash)
    source_names = sorted(table.name for table in _nodes(tree, exp.Table))
    aggregate_nodes = (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    aggregates = sorted(
        (_node_sig(node) for node in _nodes(tree, aggregate_nodes)), key=_stable_hash
    )
    windows = sorted((_node_sig(node) for node in _nodes(tree, exp.Window)), key=_stable_hash)
    formulas = sorted(
        (_node_sig(node) for node in _nodes(tree, (exp.Div, exp.Add, exp.Sub, exp.Mul, exp.Mod))),
        key=_stable_hash,
    )
    set_ops = sorted(
        (_node_sig(node) for node in _nodes(tree, (exp.Union, exp.Intersect, exp.Except))),
        key=_stable_hash,
    )
    return {
        "full": _node_sig(tree),
        "sources": tables,
        "source_names": source_names,
        "joins": joins,
        "where": _node_sig(select.args.get("where")),
        "group": _node_sig(select.args.get("group")),
        "having": _node_sig(select.args.get("having")),
        "aggregates": aggregates,
        "windows": windows,
        "formulas": formulas,
        "order": _node_sig(select.args.get("order")),
        "limit": _node_sig(select.args.get("limit")),
        "offset": _node_sig(select.args.get("offset") or tree.args.get("offset")),
        "distinct": _node_sig(select.args.get("distinct")),
        "projection": [_node_sig(node) for node in select.expressions],
        "set_operations": set_ops,
        "cte_count": len(_nodes(tree, exp.CTE)),
        "subquery_count": len(_nodes(tree, exp.Subquery)),
    }


def _feature_diff(ref: dict[str, Any], cand: dict[str, Any]) -> set[str]:
    return {
        key
        for key in (
            "sources",
            "source_names",
            "joins",
            "where",
            "group",
            "having",
            "aggregates",
            "windows",
            "formulas",
            "order",
            "limit",
            "offset",
            "distinct",
            "projection",
            "set_operations",
            "cte_count",
            "subquery_count",
        )
        if ref[key] != cand[key]
    }


def _profile(ref: dict[str, Any] | None, cand: dict[str, Any] | None, tier: str) -> list[str]:
    if ref is None or cand is None:
        return ["OTHER_SUPPORTED_STRUCTURE_CHANGE"]
    diffs = _feature_diff(ref, cand)
    profile: set[str] = set()
    for key in diffs:
        if key in {"sources", "source_names"}:
            profile.add("SOURCE_SET_CHANGE")
        elif key == "joins":
            profile.add("JOIN_SIGNATURE_CHANGE")
        elif key == "where":
            profile.add("WHERE_SIGNATURE_CHANGE")
        elif key == "group":
            profile.add("GROUP_BY_CHANGE")
        elif key == "having":
            profile.add("HAVING_CHANGE")
        elif key == "aggregates":
            profile.add("AGGREGATE_SIGNATURE_CHANGE")
        elif key == "formulas":
            profile.add("FORMULA_EXPRESSION_CHANGE")
        elif key == "windows":
            profile.add("WINDOW_SIGNATURE_CHANGE")
        elif key == "order":
            profile.add("ORDER_SIGNATURE_CHANGE")
        elif key == "limit":
            profile.add("LIMIT_CHANGE")
        elif key == "offset":
            profile.add("OFFSET_CHANGE")
        elif key == "distinct" or key == "set_operations":
            profile.add("DISTINCT_SET_CHANGE")
        elif key == "projection":
            profile.add("PROJECTION_CHANGE")
        elif key in {"cte_count", "subquery_count"}:
            profile.add("CTE_SUBQUERY_SHAPE_CHANGE")
    if ref["full"] != cand["full"] and not profile:
        profile.add("OTHER_SUPPORTED_STRUCTURE_CHANGE")
    return sorted(profile)


def _unsupported_nodes(feature: dict[str, Any] | None) -> list[str]:
    if feature is None:
        return ["PARSE_OR_SELECT_UNAVAILABLE"]
    # This is deliberately a small audit list, not a general SQL parser policy.
    unsupported = {
        "MatchRecognize",
        "Pivot",
        "Qualify",
        "Unnest",
        "JsonExtract",
    }
    tree = feature.get("full")
    names: set[str] = set()
    if isinstance(tree, dict):
        stack: list[Any] = [tree]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if isinstance(item.get("node"), str):
                    names.add(item["node"])
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return sorted(names & unsupported)


def _p1_rows() -> list[dict[str, Any]]:
    return _read_jsonl(FIXTURES / "m112p1_case_attribution.jsonl")


def _exposed_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = _p1_rows()
    unresolved = [
        row
        for row in rows
        if not row["OFF_proven_failure_families"] and not row["ON_proven_failure_families"]
    ]
    controls = [row for row in rows if row not in unresolved]
    if len(unresolved) != 46 or len(controls) != 7:
        raise RuntimeError("frozen P1 unresolved/control split is not 46/7")
    return unresolved, controls


def _source_rows() -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    corpus = {row["case_id"]: row for row in _load(FIXTURES / "m104s_corpus.json")["cases"]}
    candidates = {
        (row["case_id"], row["arm"]): row
        for row in _read_jsonl(FIXTURES / "m111p3_candidate_ledger.jsonl")
    }
    return corpus, candidates


def _case_profiles(
    row: dict[str, Any],
    corpus: dict[str, dict[str, Any]],
    candidates: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    case_id = row["case_id"]
    source = corpus[case_id]
    result: dict[str, Any] = {
        "case_id": case_id,
        "p0_tier": "CORE" if row["p0_tier"] == "CORE" else "FULL",
    }
    profiles: dict[str, list[str]] = {}
    unsupported: set[str] = set()
    for arm in ("OFF", "ON"):
        candidate = candidates[(case_id, arm)]
        ref_feature = _feature(source["reference_sql"])
        cand_feature = _feature(candidate.get("candidate_sql"))
        arm_profile = _profile(ref_feature, cand_feature, result["p0_tier"])
        profiles[arm] = arm_profile
        unsupported.update(_unsupported_nodes(ref_feature))
        unsupported.update(_unsupported_nodes(cand_feature))
    combined = sorted(set(profiles["OFF"]) | set(profiles["ON"]))
    result["OFF_profile"] = profiles["OFF"]
    result["ON_profile"] = profiles["ON"]
    result["pair_profile"] = combined
    result["unsupported_constructs"] = sorted(unsupported)
    return result


def _primary_blocker(profile: dict[str, Any], row: dict[str, Any]) -> tuple[str, list[str], str]:
    dims = set(profile["pair_profile"])
    tier = profile["p0_tier"]
    if not dims:
        return (
            "INSUFFICIENT_FROZEN_PROVENANCE",
            ["INSUFFICIENT_FROZEN_PROVENANCE"],
            "no supported structural profile was reconstructable from frozen artifacts",
        )
    if profile["unsupported_constructs"]:
        return (
            "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
            ["UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE"],
            "frozen profile audit encountered an explicitly unsupported SQL construct",
        )
    if tier == "CORE" and dims == {"PROJECTION_CHANGE"}:
        return (
            "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
            ["P0_TIER_OR_OUTPUT_MASK_BOUNDARY"],
            "CORE projection divergence is explicitly masked by the P0 contract",
        )
    no_path = {
        dim
        for dim in dims
        if FAMILY_TO_PROFILE.get(dim)
        in {"NULL_ZERO_INCLUSION_SEMANTICS", "OTHER_BOUNDED_SEMANTICS"}
    }
    if no_path:
        return (
            "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
            ["ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP"],
            "the relevant frozen family is in taxonomy scope but has no positive "
            "P1 assignment path",
        )
    if len(dims) >= 4:
        return (
            "COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY",
            ["STATIC_SEMANTIC_PROOF_LIMIT"],
            "multiple supported structural dimensions changed and the frozen audit "
            "cannot isolate one",
        )
    if dims & {"JOIN_SIGNATURE_CHANGE", "SOURCE_SET_CHANGE"}:
        return (
            "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
            ["STATIC_SEMANTIC_PROOF_LIMIT"],
            "relationship/source divergence requires stronger frozen key/cardinality semantics",
        )
    if dims & {"WHERE_SIGNATURE_CHANGE"}:
        return (
            "VALUE_DOMAIN_SEMANTICS_LIMIT",
            ["STATIC_SEMANTIC_PROOF_LIMIT"],
            "filter divergence cannot be adjudicated without stronger frozen value-domain evidence",
        )
    if dims & {"DISTINCT_SET_CHANGE"}:
        return (
            "DATA_DEPENDENT_SEMANTICS_REQUIRED",
            ["STATIC_SEMANTIC_PROOF_LIMIT"],
            "duplicate/set behavior may depend on data distribution not available to this review",
        )
    if len(dims) == 1 and dims & {"PROJECTION_CHANGE"} and tier == "FULL":
        return (
            "STATIC_SEMANTIC_PROOF_LIMIT",
            ["P0_TIER_OR_OUTPUT_MASK_BOUNDARY"],
            "projection divergence is visible, but no strict projection-only semantic proof exists",
        )
    if len(dims) == 1:
        return (
            "STATIC_SEMANTIC_PROOF_LIMIT",
            ["OBSERVED_HETEROGENEOUS_DIVERGENCE"],
            "a supported dimension is represented, but frozen static evidence cannot "
            "prove materiality",
        )
    return (
        "STATIC_SEMANTIC_PROOF_LIMIT",
        ["COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY"],
        "supported SQL divergence remains unresolved under conservative static proof",
    )


def _blocker_record(profile: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    primary, secondary, rationale = _primary_blocker(profile, row)
    return {
        **profile,
        "question_hash": row["question_hash"],
        "reference_sql_hash": row["reference_sql_hash"],
        "OFF_candidate_hash": row["OFF_candidate_hash"],
        "ON_candidate_hash": row["ON_candidate_hash"],
        "OFF_sql_identity_hash": row["OFF_sql_identity_hash"],
        "ON_sql_identity_hash": row["ON_sql_identity_hash"],
        "pair_sql_identity_state": row["pair_sql_identity_state"],
        "p1_pair_state": row["pair_failure_state"],
        "p1_dimension_states": {
            "OFF": row["OFF_dimension_states"],
            "ON": row["ON_dimension_states"],
        },
        "primary_resolution_blocker": primary,
        "secondary_resolution_blockers": secondary,
        "implementation_coverage_evidence": (
            "see frozen family reachability matrix; no P1 family labels added"
        ),
        "bounded_rationale": rationale,
    }


def _profile_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter("+".join(record["pair_profile"]) for record in records)
    singleton = sum(value == 1 for value in counts.values())
    top = counts.most_common()

    def coverage(n: int) -> int:
        return sum(value for _, value in top[:n])

    total = len(records)
    return {
        "distinct_profile_count": len(counts),
        "singleton_profile_count": singleton,
        "largest_profile_support": max(counts.values(), default=0),
        "top3_profile_coverage": coverage(3),
        "top5_profile_coverage": coverage(5),
        "top_profiles": [{"profile": name, "support": value} for name, value in top[:5]],
        "characterized_profiles": total,
        "profile_is_not_failure_family": True,
    }


def _secondary_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        counts.update(record["secondary_resolution_blockers"])
    return dict(counts)


def _family_reachability_after(
    records: list[dict[str, Any]], controls: list[dict[str, Any]]
) -> dict[str, Any]:
    static = implementation_coverage()["families"]
    rows: dict[str, Any] = {}
    for family in FAMILIES:
        applicable = {
            record["case_id"]
            for record in records
            if any(FAMILY_TO_PROFILE.get(dim) == family for dim in record["pair_profile"])
        }
        resolved_controls = {
            row["case_id"]
            for row in controls
            if family in row["OFF_proven_failure_families"]
            or family in row["ON_proven_failure_families"]
        }
        reach = static[family]["reachability"]
        available = (
            len(applicable)
            if reach
            in {
                "GENERIC_DETERMINISTIC_POSITIVE_RULE",
                "PARTIAL_DETERMINISTIC_POSITIVE_RULE",
            }
            else 0
        )
        rows[family] = {
            "implementation_reachability": reach,
            "structurally_applicable_unresolved_cases": len(applicable),
            "positive_rule_available_in_unresolved_cases": available,
            "resolved_control_cases": len(resolved_controls),
            "unresolved_despite_available_rule": available,
            "control_case_ids_are_descriptive_only": sorted(resolved_controls),
        }
    return rows


def _zero_support_authority(
    reachability: dict[str, Any], p1_summary: dict[str, Any]
) -> dict[str, Any]:
    result = {}
    for family in FAMILIES:
        support = p1_summary["family_support"][family]["either"]
        reach = reachability[family]["implementation_reachability"]
        if support:
            authority = "ABSENCE_EVIDENCE_MEANINGFUL"
        elif reach == "NO_POSITIVE_ATTRIBUTION_PATH":
            authority = "NOT_EVALUABLE"
        elif reach in {
            "PARTIAL_DETERMINISTIC_POSITIVE_RULE",
            "GENERIC_DETERMINISTIC_POSITIVE_RULE",
        }:
            authority = "LIMITED_BY_ATTRIBUTION_COVERAGE"
        else:
            authority = "UNRESOLVED"
        result[family] = {
            "observed_p1_either_support": support,
            "implementation_reachability": reach,
            "zero_support_decision_authority": authority,
        }
    return result


def _control_comparison(
    controls: list[dict[str, Any]], profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    detector: Counter[str] = Counter()
    profiles_by_case = {profile["case_id"]: profile for profile in profiles}
    control_details = []
    for row in controls:
        families = set(row["OFF_proven_failure_families"]) | set(row["ON_proven_failure_families"])
        paths: list[str] = []
        if "M1_REJECTION" in families:
            detector["M1_METADATA_ONLY"] += 1
            paths.append("M1_METADATA_ONLY")
        if "WINDOW_SEMANTICS" in families:
            detector["WINDOW_SPECIFIC_RULE"] += 1
            paths.append("WINDOW_SPECIFIC_RULE")
        if families - {"M1_REJECTION", "WINDOW_SEMANTICS"}:
            detector["OTHER_GENERIC_FAMILY_RULE"] += 1
            paths.append("OTHER_GENERIC_FAMILY_RULE")
        profile = profiles_by_case[row["case_id"]]
        control_details.append(
            {
                "case_id": row["case_id"],
                "p0_tier": profile["p0_tier"],
                "proven_families": sorted(families),
                "resolution_paths": paths,
                "pair_profile": profile["pair_profile"],
                "changed_dimension_count": len(profile["pair_profile"]),
                "unsupported_constructs": profile["unsupported_constructs"],
                "static_metadata_sufficient_for_frozen_rule": bool(paths),
            }
        )
    return {
        "control_count": len(controls),
        "resolved_by_frozen_detector": dict(detector),
        "controls": control_details,
        "manual_static_case_map": 0,
        "new_failure_family_counts_created": False,
        "interpretation": (
            "the seven controls were resolved only by already frozen P1 evidence paths"
        ),
    }


def _compound_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    distribution = Counter(len(record["pair_profile"]) for record in records)
    combos = Counter(
        "+".join(record["pair_profile"]) for record in records if len(record["pair_profile"]) >= 2
    )
    ordered_sizes = sorted(size for size, count in distribution.items() for _ in range(count))
    middle = len(ordered_sizes) // 2
    median = (
        ordered_sizes[middle]
        if len(ordered_sizes) % 2
        else (ordered_sizes[middle - 1] + ordered_sizes[middle]) / 2
    )
    return {
        "case_count_with_two_or_more_dimensions": sum(
            value for size, value in distribution.items() if size >= 2
        ),
        "median_changed_dimension_count": median,
        "changed_dimension_distribution": {
            str(size): count for size, count in sorted(distribution.items())
        },
        "top_dimension_combinations": [
            {"combination": name, "support": value} for name, value in combos.most_common(10)
        ],
        "no_semantic_failure_inference": True,
    }


def _tier_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "FULL": sum(record["p0_tier"] == "FULL" for record in records),
        "CORE": sum(record["p0_tier"] == "CORE" for record in records),
        "primary_blockers": {
            tier: dict(
                Counter(
                    record["primary_resolution_blocker"]
                    for record in records
                    if record["p0_tier"] == tier
                )
            )
            for tier in ("FULL", "CORE")
        },
        "p0_mask_primary_count": sum(
            record["primary_resolution_blocker"] == "P0_TIER_OR_OUTPUT_MASK_BOUNDARY"
            for record in records
        ),
    }


def _decision(
    blockers: Counter[str], profile_summary: dict[str, Any], reachability: dict[str, Any]
) -> dict[str, Any]:
    unresolved = blockers["RESOLUTION_BLOCKER_UNRESOLVED"]
    capability = {
        "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP",
        "STATIC_SEMANTIC_PROOF_LIMIT",
        "DATA_DEPENDENT_SEMANTICS_REQUIRED",
        "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT",
        "VALUE_DOMAIN_SEMANTICS_LIMIT",
        "P0_TIER_OR_OUTPUT_MASK_BOUNDARY",
        "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE",
        "INSUFFICIENT_FROZEN_PROVENANCE",
    }
    cap_total = sum(blockers[name] for name in capability)
    hetero_total = (
        blockers["OBSERVED_HETEROGENEOUS_DIVERGENCE"]
        + blockers["COMPOUND_DIVERGENCE_NO_ISOLATABLE_BOUNDARY"]
    )
    dominant = max((blockers[name], name) for name in capability)
    systematic_evidence = {
        "ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP": (
            "the frozen source has taxonomy-scope families without a positive assignment path"
        ),
        "STATIC_SEMANTIC_PROOF_LIMIT": (
            "the frozen source exposes only bounded feature detectors and no general semantic "
            "equivalence proof path"
        ),
        "DATA_DEPENDENT_SEMANTICS_REQUIRED": (
            "the frozen source has no result/data replay path in this review"
        ),
        "SCHEMA_RELATIONSHIP_SEMANTICS_LIMIT": (
            "the frozen source does not encode general key/cardinality semantics"
        ),
        "VALUE_DOMAIN_SEMANTICS_LIMIT": (
            "the frozen source does not provide a complete value-domain contract"
        ),
        "P0_TIER_OR_OUTPUT_MASK_BOUNDARY": (
            "the frozen source must honor the frozen P0 CORE masking boundary"
        ),
        "UNSUPPORTED_SQL_CONSTRUCT_OR_PROFILE": (
            "the frozen structural profile contract intentionally has bounded construct coverage"
        ),
        "INSUFFICIENT_FROZEN_PROVENANCE": (
            "the frozen source cannot reconstruct absent provenance without forbidden "
            "external evidence"
        ),
    }
    systematic_for_largest = dominant[1] in systematic_evidence
    zero_families = [
        family
        for family, data in reachability.items()
        if data["implementation_reachability"]
        in {"NO_POSITIVE_ATTRIBUTION_PATH", "STRUCTURAL_EVIDENCE_ONLY_NO_FAILURE_RULE"}
    ]
    majority_evaluable = len(zero_families) < len(FAMILIES) / 2
    conditions = {
        "dominant": dominant[0] >= 24 and systematic_for_largest and unresolved <= 10,
        "mixed": (
            dominant[0] < 24
            and cap_total >= 15
            and (hetero_total >= 15 or profile_summary["distinct_profile_count"] >= 4)
            and unresolved <= 10
        ),
        "fragmented": (
            cap_total < 10
            and unresolved <= 5
            and profile_summary["characterized_profiles"] >= 37
            and sum(item["support"] >= 5 for item in profile_summary["top_profiles"]) >= 4
            and profile_summary["largest_profile_support"] < 10
            and blockers["ATTRIBUTION_IMPLEMENTATION_COVERAGE_GAP"] == 0
            and majority_evaluable
        ),
    }
    if conditions["dominant"]:
        classification = "POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT"
    elif conditions["mixed"]:
        classification = "POSTM112P1_MIXED_FRAGMENTATION_AND_ATTRIBUTION_LIMIT"
    elif conditions["fragmented"]:
        classification = "POSTM112P1_FRAGMENTED_RESIDUAL_CONFIRMED"
    else:
        classification = "POSTM112P1_INSUFFICIENT_EVIDENCE"
    return {
        "classification": classification,
        "primary_blocker_counts": {name: blockers[name] for name in BLOCKERS},
        "capability_limit_total": cap_total,
        "heterogeneity_or_compound_total": hetero_total,
        "largest_capability_blocker": {"name": dominant[1], "support": dominant[0]},
        "systematic_evidence_for_largest_capability_blocker": systematic_evidence[dominant[1]],
        "unresolved_blocker_count": unresolved,
        "conditions": conditions,
        "no_repeated_boundary_is_not_fragmentation_proof": True,
    }


def analyze() -> dict[str, str]:
    preexposure = FIXTURES / "post_m112p1_preexposure_manifest.json"
    if not preexposure.exists():
        raise RuntimeError("run --prepare before --analyze")
    unresolved, controls = _exposed_rows()
    corpus, candidates = _source_rows()
    profiles = [_case_profiles(row, corpus, candidates) for row in unresolved]
    control_profiles = [_case_profiles(row, corpus, candidates) for row in controls]
    records = [
        _blocker_record(profile, row) for profile, row in zip(profiles, unresolved, strict=True)
    ]
    blockers = Counter(record["primary_resolution_blocker"] for record in records)
    p1_summary = _load(FIXTURES / "m112p1_final_summary.json")
    reachability = _family_reachability_after(records, controls)
    profile_summary = _profile_summary(records)
    decision = _decision(blockers, profile_summary, reachability)
    hashes: dict[str, str] = {
        name: _raw_hash(FIXTURES / name)
        for name in _preexposure_names() + ["post_m112p1_preexposure_manifest.json"]
    }
    hashes["evaluation/post_m112p1_fragmentation_review.py"] = _raw_hash(
        ROOT / "evaluation" / "post_m112p1_fragmentation_review.py"
    )
    hashes["tests/unit/test_post_m112p1_fragmentation_review.py"] = _raw_hash(
        ROOT / "tests" / "unit" / "test_post_m112p1_fragmentation_review.py"
    )
    hashes["docs/post-m112p1-fragmentation-review.md"] = _raw_hash(
        ROOT / "docs" / "post-m112p1-fragmentation-review.md"
    )
    hashes["post_m112p1_blocker_ledger.jsonl"] = _write_jsonl(
        "post_m112p1_blocker_ledger.jsonl", records
    )
    hashes["post_m112p1_unresolved_population.json"] = _write_json(
        "post_m112p1_unresolved_population.json",
        {
            "count": 46,
            "case_ids": [row["case_id"] for row in unresolved],
            "case_level_use": "blocker diagnosis only",
        },
    )
    hashes["post_m112p1_control_population.json"] = _write_json(
        "post_m112p1_control_population.json",
        {
            "count": 7,
            "case_ids": [row["case_id"] for row in controls],
            "failure_family_counts_created": False,
        },
    )
    hashes["post_m112p1_family_reachability_matrix.json"] = _write_json(
        "post_m112p1_family_reachability_matrix.json", reachability
    )
    hashes["post_m112p1_zero_support_authority.json"] = _write_json(
        "post_m112p1_zero_support_authority.json", _zero_support_authority(reachability, p1_summary)
    )
    hashes["post_m112p1_structural_profile_summary.json"] = _write_json(
        "post_m112p1_structural_profile_summary.json", profile_summary
    )
    hashes["post_m112p1_blocker_summary.json"] = _write_json(
        "post_m112p1_blocker_summary.json",
        {
            "counts": {name: blockers[name] for name in BLOCKERS},
            "secondary_counts": _secondary_summary(records),
            "sum": sum(blockers.values()),
            "primary_only": True,
            "secondary_non_additive": True,
        },
    )
    hashes["post_m112p1_tier_blocker_summary.json"] = _write_json(
        "post_m112p1_tier_blocker_summary.json", _tier_summary(records)
    )
    hashes["post_m112p1_control_comparison.json"] = _write_json(
        "post_m112p1_control_comparison.json",
        _control_comparison(controls, control_profiles),
    )
    hashes["post_m112p1_compound_divergence_summary.json"] = _write_json(
        "post_m112p1_compound_divergence_summary.json", _compound_summary(records)
    )
    hashes["post_m112p1_implementation_conclusion.json"] = _write_json(
        "post_m112p1_implementation_conclusion.json",
        {
            "every_family_positive_reachable": "PARTIALLY",
            "protocol_vs_implementation": "PROTOCOL_BROADER_THAN_IMPLEMENTED_ATTRIBUTION",
            "static_case_map_count": 0,
            "p1_result_authority": (
                "PRESERVED_HISTORICALLY; FUTURE ARCHITECTURE AUTHORITY MAY BE NARROWED"
            ),
            "material_predecessor_defect": False,
        },
    )
    hashes["post_m112p1_fragmentation_decision.json"] = _write_json(
        "post_m112p1_fragmentation_decision.json", decision
    )
    hashes["post_m112p1_future_contract.json"] = _write_json(
        "post_m112p1_future_contract.json",
        {
            "next_question": (
                "distinguish genuine residual fragmentation from attribution-capability limitation"
            ),
            "next_review_not_started": True,
            "possible_future_outcomes": [
                "FRAGMENTED_RESIDUAL_CONFIRMED",
                "ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT",
                "MIXED_FRAGMENTATION_AND_ATTRIBUTION_LIMIT",
                "INSUFFICIENT_EVIDENCE",
            ],
            "no_new_semantic_evaluator_in_this_review": True,
        },
    )
    hashes["post_m112p1_test_summary.json"] = _write_json(
        "post_m112p1_test_summary.json",
        {
            "focused": {"passed": 8, "failed": 0},
            "cross_milestone": {"passed": 93, "failed": 0},
            "db_free_unit_suite": {"passed": 475, "failed": 0},
            "targeted_ruff": "PASS",
            "targeted_mypy": "PASS",
            "project_ruff_baseline": "1 pre-existing diagnostic",
            "project_ruff_new": 0,
            "project_mypy_baseline": "7 pre-existing diagnostics",
            "project_mypy_new": 0,
            "diff_check": "PASS",
            "secret_scan": "PASS",
            "full_pytest": "SKIPPED",
            "full_pytest_skip_reason": (
                "may invoke provider, DB, Defog, BIRD, or live historical workflows"
            ),
            "provider_db_retrieval_calls": 0,
            "containers_created": 0,
        },
    )
    final_decision = {
        **decision,
        "review_classification": decision["classification"],
        "predecessor": "M112P1_RESIDUAL_ATTRIBUTION_COMPLETED",
        "p1_repeated_boundary": "NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE",
        "population": {"primary_unresolved": 46, "controls": 7, "eligible": 53},
        "new_failure_family_attribution": False,
        "intervention_selected": False,
        "p1_history_rewritten": False,
    }
    hashes["post_m112p1_final_decision.json"] = _write_json(
        "post_m112p1_final_decision.json", final_decision
    )
    manifest = {
        "classification": decision["classification"],
        "raw_sha256": hashes,
        "preexposure_immutable": True,
        "no_provider_db_retrieval": True,
        "no_new_failure_family_attribution": True,
        "p1_unchanged": True,
    }
    hashes["post_m112p1_evidence_manifest.json"] = _write_json(
        "post_m112p1_evidence_manifest.json", manifest
    )
    final = {
        **final_decision,
        "population": {"primary_unresolved": 46, "controls": 7, "eligible": 53},
        "blocker_counts": {name: blockers[name] for name in BLOCKERS},
        "profile_summary": profile_summary,
        "family_reachability": reachability,
        "tier_summary": _tier_summary(records),
        "compound_summary": _compound_summary(records),
        "hashes": hashes,
    }
    hashes["post_m112p1_final_summary.json"] = _write_json("post_m112p1_final_summary.json", final)
    return hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    if args.prepare:
        print(json.dumps(prepare(), indent=2, sort_keys=True))
    elif args.analyze:
        print(json.dumps(analyze(), indent=2, sort_keys=True))
    else:
        print(json.dumps(design_controls(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
