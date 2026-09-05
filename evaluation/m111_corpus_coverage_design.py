"""Offline M11.1 exact-query reuse and semantic-factorization audit.

This module consumes frozen evaluation artifacts and the frozen M11.0R
compatibility evaluator.  It creates hypothetical target-derived candidates
only for design analysis; it never writes to or imports application memory
runtime code, reruns retrieval, or executes SQL.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from evaluation.m110r_memory_compatibility import (
    DIMENSIONS,
    CompatibilityState,
    SemanticSignature,
    _parse_signature,
    classify_entry,
)

TARGET_SIGNATURE_FIELDS = (
    "source_relation",
    "join_relationship",
    "result_grain",
    "aggregation",
    "formula",
    "filter",
    "temporal",
    "window_semantics",
    "projection_role",
    "ordering",
    "top_n",
    "distinct_set",
)


@dataclass(frozen=True)
class HypotheticalCandidate:
    candidate_id: str
    source_target_id: str
    reference_sql: str


@dataclass(frozen=True)
class TargetPairObservation:
    consumer_target_id: str
    candidate_id: str
    source_target_id: str
    state: str
    diagonal: bool
    reason: str | None
    relations: tuple[tuple[str, str], ...]
    positive_dimensions: tuple[str, ...]
    conflict_dimensions: tuple[str, ...]
    evidence_hashes: tuple[str, ...]


@dataclass(frozen=True)
class SemanticSignatureObservation:
    target_id: str
    complete: bool
    atoms: tuple[str, ...]
    full_signature_hash: str | None
    reason: str | None


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def build_hypothetical_candidates(
    targets: Sequence[Mapping[str, object]],
) -> tuple[HypotheticalCandidate, ...]:
    """Adapt frozen target SQL to evaluator-only exact-query candidates."""

    candidates: list[HypotheticalCandidate] = []
    for target in sorted(targets, key=lambda item: str(item["case_id"])):
        target_id = str(target["case_id"])
        sql = target.get("reference_sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ValueError(f"target lacks frozen reference SQL: {target_id}")
        candidates.append(HypotheticalCandidate(f"hyp:{target_id}", target_id, sql))
    return tuple(candidates)


def validate_self_compatibility(
    targets: Sequence[Mapping[str, object]],
    candidates: Sequence[HypotheticalCandidate],
) -> dict[str, object]:
    """Apply the hard 105/105 diagonal self-compatibility gate."""

    by_target = {str(target["case_id"]): target for target in targets}
    states: list[str] = []
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        target = by_target[candidate.source_target_id]
        evidence = classify_entry(cast(str, target.get("reference_sql")), candidate.reference_sql)
        states.append(evidence.state.value)
        rows.append(
            {
                "target_id": candidate.source_target_id,
                "candidate_id": candidate.candidate_id,
                "state": evidence.state.value,
                "reason": evidence.reason,
                "evidence_hashes": list(evidence.evidence_hashes),
            }
        )
    counts = Counter(states)
    return {
        "candidate_count": len(candidates),
        "self_pairs": len(candidates),
        "compatible": counts[CompatibilityState.PROVEN_COMPATIBLE.value],
        "incompatible": counts[CompatibilityState.PROVEN_INCOMPATIBLE.value],
        "unknown": counts[CompatibilityState.UNKNOWN.value],
        "valid": counts[CompatibilityState.PROVEN_COMPATIBLE.value] == len(candidates)
        and counts[CompatibilityState.PROVEN_INCOMPATIBLE.value] == 0
        and counts[CompatibilityState.UNKNOWN.value] == 0,
        "rows": rows,
    }


def build_target_pairwise_matrix(
    targets: Sequence[Mapping[str, object]],
    candidates: Sequence[HypotheticalCandidate],
) -> tuple[TargetPairObservation, ...]:
    """Compare every frozen target consumer with every hypothetical candidate."""

    target_by_id = {str(target["case_id"]): target for target in targets}
    rows: list[TargetPairObservation] = []
    for target_id in sorted(target_by_id):
        target_sql = cast(str | None, target_by_id[target_id].get("reference_sql"))
        for candidate in candidates:
            evidence = classify_entry(target_sql, candidate.reference_sql)
            rows.append(
                TargetPairObservation(
                    consumer_target_id=target_id,
                    candidate_id=candidate.candidate_id,
                    source_target_id=candidate.source_target_id,
                    state=evidence.state.value,
                    diagonal=target_id == candidate.source_target_id,
                    reason=evidence.reason,
                    relations=tuple(
                        (name, relation.value) for name, relation in evidence.relations
                    ),
                    positive_dimensions=evidence.positive_dimensions,
                    conflict_dimensions=evidence.conflict_dimensions,
                    evidence_hashes=evidence.evidence_hashes,
                )
            )
    return tuple(rows)


def serialize_pair(row: TargetPairObservation) -> dict[str, object]:
    return {
        "consumer_target_id": row.consumer_target_id,
        "candidate_id": row.candidate_id,
        "source_target_id": row.source_target_id,
        "state": row.state,
        "diagonal": row.diagonal,
        "reason": row.reason,
        "relations": [list(item) for item in row.relations],
        "positive_dimensions": list(row.positive_dimensions),
        "conflict_dimensions": list(row.conflict_dimensions),
        "evidence_hashes": list(row.evidence_hashes),
    }


def coverage_sets(
    pairs: Iterable[TargetPairObservation],
) -> dict[str, dict[str, object]]:
    """Build all-target and off-diagonal coverage sets per candidate."""

    rows: dict[str, dict[str, object]] = {}
    for pair in pairs:
        row = rows.setdefault(
            pair.candidate_id,
            {
                "source_target_id": pair.source_target_id,
                "coverage_set": set(),
                "off_diagonal_set": set(),
            },
        )
        if pair.state == CompatibilityState.PROVEN_COMPATIBLE.value:
            cast(set[str], row["coverage_set"]).add(pair.consumer_target_id)
            if not pair.diagonal:
                cast(set[str], row["off_diagonal_set"]).add(pair.consumer_target_id)
    for row in rows.values():
        row["coverage_set"] = sorted(cast(set[str], row["coverage_set"]))
        row["off_diagonal_set"] = sorted(cast(set[str], row["off_diagonal_set"]))
        row["coverage_count"] = len(cast(list[str], row["coverage_set"]))
        row["off_diagonal_coverage_count"] = len(cast(list[str], row["off_diagonal_set"]))
    return dict(sorted(rows.items()))


def cross_target_reuse(
    pairs: Sequence[TargetPairObservation], target_ids: Sequence[str]
) -> dict[str, object]:
    by_target: dict[str, set[str]] = {target_id: set() for target_id in target_ids}
    for pair in pairs:
        if not pair.diagonal and pair.state == CompatibilityState.PROVEN_COMPATIBLE.value:
            by_target[pair.consumer_target_id].add(pair.source_target_id)
    counts = [len(by_target[target_id]) for target_id in target_ids]
    reusable = sum(count > 0 for count in counts)
    ordered = [
        {
            "target_id": target_id,
            "off_diagonal_compatible_candidate_count": len(by_target[target_id]),
            "off_diagonal_candidate_ids": sorted(
                f"hyp:{source}" for source in by_target[target_id]
            ),
            "cross_target_reusable": bool(by_target[target_id]),
            "self_only_target": not bool(by_target[target_id]),
        }
        for target_id in sorted(target_ids)
    ]
    return {
        "target_count": len(target_ids),
        "targets_with_off_diagonal_compatible_candidate": reusable,
        "self_only_targets": len(target_ids) - reusable,
        "cross_target_reuse_rate": reusable / len(target_ids) if target_ids else None,
        "off_diagonal_distribution": _distribution(counts),
        "targets": ordered,
    }


def _distribution(values: Sequence[int]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"min": None, "p25": None, "median": None, "p75": None, "max": None, "mean": None}

    def pct(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "min": ordered[0],
        "p25": pct(0.25),
        "median": pct(0.5),
        "p75": pct(0.75),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


def candidate_coverage_report(
    coverage: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    counts = [int(cast(int, row["coverage_count"])) for row in coverage.values()]
    buckets = {"only_self": 0, "two_to_three": 0, "four_to_ten": 0, "over_ten": 0}
    for count in counts:
        if count == 1:
            buckets["only_self"] += 1
        elif count <= 3:
            buckets["two_to_three"] += 1
        elif count <= 10:
            buckets["four_to_ten"] += 1
        else:
            buckets["over_ten"] += 1
    return {
        "candidate_count": len(counts),
        "buckets": buckets,
        "distribution": _distribution(counts),
    }


def greedy_set_cover(
    coverage: Mapping[str, Mapping[str, object]], target_ids: Sequence[str]
) -> dict[str, object]:
    """Select deterministic representatives by largest uncovered coverage."""

    uncovered = set(target_ids)
    steps: list[dict[str, object]] = []
    remaining = set(coverage)
    while uncovered and remaining:
        ranked = sorted(
            remaining,
            key=lambda candidate_id: (
                -len(uncovered & set(cast(list[str], coverage[candidate_id]["coverage_set"]))),
                str(candidate_id),
            ),
        )
        candidate_id = ranked[0]
        covered_now = sorted(
            uncovered & set(cast(list[str], coverage[candidate_id]["coverage_set"]))
        )
        if not covered_now:
            break
        uncovered.difference_update(covered_now)
        steps.append(
            {
                "step": len(steps) + 1,
                "candidate_id": candidate_id,
                "source_target_id": coverage[candidate_id]["source_target_id"],
                "new_targets": covered_now,
                "new_target_count": len(covered_now),
                "cumulative_covered": len(target_ids) - len(uncovered),
                "cumulative_rate": (len(target_ids) - len(uncovered)) / len(target_ids),
            }
        )
        remaining.remove(candidate_id)
    thresholds = {}
    for threshold in (0.5, 0.8, 0.9, 0.95, 1.0):
        thresholds[str(threshold)] = next(
            (
                int(cast(int, step["step"]))
                for step in steps
                if float(cast(float, step["cumulative_rate"])) >= threshold
            ),
            None,
        )
    after = {
        str(limit): min(len(steps), limit)
        and next(
            (
                int(cast(int, step["cumulative_covered"]))
                for step in steps
                if int(cast(int, step["step"])) == min(len(steps), limit)
            ),
            len(target_ids) - len(uncovered),
        )
        for limit in (5, 10, 20, 30, 50)
    }
    return {
        "target_count": len(target_ids),
        "candidate_count": len(coverage),
        "steps": steps,
        "representatives_needed_for_rate": thresholds,
        "coverage_after_representatives": after,
        "fully_covered": not uncovered,
        "uncovered_after_greedy": sorted(uncovered),
        "heuristic_boundary": "deterministic greedy heuristic; not a globally minimal corpus proof",
    }


def semantic_atoms(signature: SemanticSignature) -> tuple[str, ...]:
    atoms: list[str] = []
    for dimension, field in zip(DIMENSIONS, TARGET_SIGNATURE_FIELDS, strict=True):
        values = cast(tuple[str, ...], getattr(signature, field))
        if values:
            atoms.append(f"{dimension}={json.dumps(list(values), separators=(',', ':'))}")
    return tuple(atoms)


def build_semantic_signatures(
    targets: Sequence[Mapping[str, object]],
) -> tuple[SemanticSignatureObservation, ...]:
    rows: list[SemanticSignatureObservation] = []
    for target in sorted(targets, key=lambda item: str(item["case_id"])):
        target_id = str(target["case_id"])
        signature, reason = _parse_signature(cast(str | None, target.get("reference_sql")))
        if signature is None:
            rows.append(SemanticSignatureObservation(target_id, False, (), None, reason))
            continue
        atoms = semantic_atoms(signature)
        rows.append(SemanticSignatureObservation(target_id, True, atoms, _sha256_json(atoms), None))
    return tuple(rows)


def factorization_report(signatures: Sequence[SemanticSignatureObservation]) -> dict[str, object]:
    complete = [row for row in signatures if row.complete]
    atom_targets: dict[str, set[str]] = defaultdict(set)
    full_targets: dict[str, list[str]] = defaultdict(list)
    for row in complete:
        for atom in row.atoms:
            atom_targets[atom].add(row.target_id)
        assert row.full_signature_hash is not None
        full_targets[row.full_signature_hash].append(row.target_id)
    singleton_atoms = {atom for atom, target_ids in atom_targets.items() if len(target_ids) == 1}
    unique_full = {
        signature for signature, target_ids in full_targets.items() if len(target_ids) == 1
    }
    all_atoms_reused = [
        row
        for row in complete
        if row.atoms and all(len(atom_targets[atom]) >= 2 for atom in row.atoms)
    ]
    novel_atoms = [row for row in complete if any(atom in singleton_atoms for atom in row.atoms)]
    compositional = [
        row
        for row in complete
        if row.full_signature_hash in unique_full
        and row.atoms
        and all(len(atom_targets[atom]) >= 2 for atom in row.atoms)
    ]
    denominator = len(complete)
    rates = {
        "unique_full_signature_target_rate": len(unique_full) / denominator
        if denominator
        else None,
        "all_atoms_reused_target_rate": len(all_atoms_reused) / denominator
        if denominator
        else None,
        "novel_atom_target_rate": len(novel_atoms) / denominator if denominator else None,
        "compositionally_novel_target_rate": len(compositional) / denominator
        if denominator
        else None,
    }
    semantic_pressure = (
        "REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_STRONG"
        if all(
            (
                (rates["unique_full_signature_target_rate"] or 0) >= 0.70,
                (rates["all_atoms_reused_target_rate"] or 0) >= 0.70,
                (rates["compositionally_novel_target_rate"] or 0) >= 0.50,
                (rates["novel_atom_target_rate"] or 0) < 0.30,
            )
        )
        else "REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_WEAK"
        if (rates["compositionally_novel_target_rate"] or 0) < 0.20
        or (rates["novel_atom_target_rate"] or 0) >= 0.60
        else "REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_PARTIAL"
    )
    return {
        "target_count": len(signatures),
        "complete_signature_targets": denominator,
        "incomplete_signature_targets": len(signatures) - denominator,
        "distinct_full_signatures": len(full_targets),
        "repeated_full_signatures": sum(len(ids) > 1 for ids in full_targets.values()),
        "singleton_full_signatures": len(unique_full),
        "atom_count": len(atom_targets),
        "singleton_atom_count": len(singleton_atoms),
        "reused_atom_count": len(atom_targets) - len(singleton_atoms),
        "all_atoms_reused_target_count": len(all_atoms_reused),
        "novel_atom_target_count": len(novel_atoms),
        "compositionally_novel_target_count": len(compositional),
        "rates": rates,
        "semantic_pressure_classification": semantic_pressure,
        "new_semantic_content_pressure": (
            "NEW_SEMANTIC_CONTENT_PRESSURE_STRONG"
            if (rates["novel_atom_target_rate"] or 0) >= 0.60
            else "NEW_SEMANTIC_CONTENT_PRESSURE_NOT_STRONG"
        ),
        "atom_frequencies": [
            {"atom": atom, "target_count": len(target_ids), "target_ids": sorted(target_ids)}
            for atom, target_ids in sorted(atom_targets.items())
        ],
        "signatures": [
            {
                "target_id": row.target_id,
                "complete": row.complete,
                "atoms": list(row.atoms),
                "full_signature_hash": row.full_signature_hash,
                "reason": row.reason,
            }
            for row in signatures
        ],
    }


def m4_signature_coverage(
    entries: Sequence[tuple[str, str | None]],
    target_signatures: Sequence[SemanticSignatureObservation],
) -> dict[str, object]:
    m4_rows: list[SemanticSignatureObservation] = []
    for entry_id, sql in sorted(entries):
        signature, reason = _parse_signature(sql)
        if signature is None:
            m4_rows.append(SemanticSignatureObservation(entry_id, False, (), None, reason))
        else:
            atoms = semantic_atoms(signature)
            m4_rows.append(
                SemanticSignatureObservation(entry_id, True, atoms, _sha256_json(atoms), None)
            )
    target_hashes = {row.full_signature_hash for row in target_signatures if row.complete}
    m4_hashes = {row.full_signature_hash for row in m4_rows if row.complete}
    represented = target_hashes & m4_hashes
    return {
        "m4_entry_count": len(entries),
        "m4_complete_signatures": sum(row.complete for row in m4_rows),
        "m4_incomplete_signatures": sum(not row.complete for row in m4_rows),
        "m4_distinct_full_signatures": len(m4_hashes),
        "target_complete_signature_count": len(target_hashes),
        "target_signatures_represented_by_m4": len(represented),
        "target_signatures_absent_from_m4": len(target_hashes - m4_hashes),
        "m4_signatures": [
            {
                "entry_id": row.target_id,
                "complete": row.complete,
                "full_signature_hash": row.full_signature_hash,
                "reason": row.reason,
            }
            for row in m4_rows
        ],
    }


def exact_representation_classification(
    reuse: Mapping[str, object], greedy: Mapping[str, object]
) -> str:
    rate = cast(float | None, reuse["cross_target_reuse_rate"]) or 0.0
    self_only_rate = int(cast(int, reuse["self_only_targets"])) / int(
        cast(int, reuse["target_count"])
    )
    reps_80 = cast(
        int | None, cast(dict[str, object], greedy["representatives_needed_for_rate"])["0.8"]
    )
    if rate >= 0.80 and reps_80 is not None and reps_80 <= 20 and self_only_rate <= 0.10:
        return "EXACT_QUERY_REPRESENTATION_REUSE_STRONG"
    if rate < 0.40 or (reps_80 is not None and reps_80 > 50) or self_only_rate >= 0.40:
        return "EXACT_QUERY_REPRESENTATION_REUSE_WEAK"
    return "EXACT_QUERY_REPRESENTATION_REUSE_PARTIAL"


def select_intervention_family(
    exact_classification: str,
    factorization: Mapping[str, object],
    reuse: Mapping[str, object],
) -> dict[str, object]:
    semantic_class = str(factorization["semantic_pressure_classification"])
    semantic_strong = semantic_class == "REUSABLE_SEMANTIC_REPRESENTATION_PRESSURE_STRONG"
    new_strong = (
        str(factorization["new_semantic_content_pressure"])
        == "NEW_SEMANTIC_CONTENT_PRESSURE_STRONG"
    )
    split_detected = False
    if exact_classification == "EXACT_QUERY_REPRESENTATION_REUSE_STRONG" and not semantic_strong:
        classification = "M111_COVERAGE_BALANCED_EXACT_EXAMPLES_JUSTIFIED"
        family = "COVERAGE_BALANCED_EXACT_VERIFIED_EXAMPLES"
    elif (
        exact_classification == "EXACT_QUERY_REPRESENTATION_REUSE_WEAK"
        and semantic_strong
        and not new_strong
    ):
        classification = "M111_REUSABLE_SEMANTIC_MEMORY_JUSTIFIED"
        family = "REUSABLE_VERIFIED_SEMANTIC_MEMORY"
    elif semantic_strong and (
        exact_classification == "EXACT_QUERY_REPRESENTATION_REUSE_PARTIAL" or split_detected
    ):
        classification = "M111_HYBRID_VERIFIED_MEMORY_JUSTIFIED"
        family = "HYBRID_VERIFIED_MEMORY"
    elif (
        exact_classification == "EXACT_QUERY_REPRESENTATION_REUSE_WEAK"
        and new_strong
        and not semantic_strong
    ):
        classification = "M111_NEW_SEMANTIC_COVERAGE_REQUIRED_REPRESENTATION_UNRESOLVED"
        family = None
    else:
        classification = "M111_MULTIPLE_INTERVENTION_FAMILIES_PLAUSIBLE"
        family = None
    return {
        "classification": classification,
        "selected_intervention_family": family,
        "exact_classification": exact_classification,
        "semantic_pressure_classification": semantic_class,
        "new_semantic_content_pressure": str(factorization["new_semantic_content_pressure"]),
        "materially_sized_split_detected": split_detected,
        "implementation_authorized": False,
    }


__all__ = [
    "HypotheticalCandidate",
    "SemanticSignatureObservation",
    "TargetPairObservation",
    "build_hypothetical_candidates",
    "build_semantic_signatures",
    "build_target_pairwise_matrix",
    "candidate_coverage_report",
    "coverage_sets",
    "cross_target_reuse",
    "exact_representation_classification",
    "factorization_report",
    "greedy_set_cover",
    "m4_signature_coverage",
    "select_intervention_family",
    "serialize_pair",
    "validate_self_compatibility",
]
