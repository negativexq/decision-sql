"""Offline M11.0T coverage, recall, and admission-layer diagnostics.

The module evaluates frozen target SQL against immutable M4 entry SQL using the
already frozen M11.0R compatibility contract.  It never imports application
retrieval code and does not reconstruct or rerun historical retrieval.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import cast

from evaluation.m110r_memory_compatibility import (
    CompatibilityState,
    EntryCompatibilityEvidence,
    classify_entry,
)

PRIMARY_STATES = (
    "CORPUS_COVERAGE_FAILURE",
    "RETRIEVAL_RECALL_FAILURE",
    "SELECTIVITY_ADMISSION_FAILURE",
    "COMPATIBLE_CONTEXT_RETRIEVED",
    "UNRESOLVED",
)
FAILURE_STATES = PRIMARY_STATES[:3]


@dataclass(frozen=True)
class PairObservation:
    target_id: str
    entry_id: str
    state: str
    reason: str | None
    relations: tuple[tuple[str, str], ...]
    positive_dimensions: tuple[str, ...]
    conflict_dimensions: tuple[str, ...]
    evidence_hashes: tuple[str, ...]

    @classmethod
    def from_evidence(
        cls, target_id: str, entry_id: str, evidence: EntryCompatibilityEvidence
    ) -> PairObservation:
        return cls(
            target_id=target_id,
            entry_id=entry_id,
            state=evidence.state.value,
            reason=evidence.reason,
            relations=tuple((name, relation.value) for name, relation in evidence.relations),
            positive_dimensions=evidence.positive_dimensions,
            conflict_dimensions=evidence.conflict_dimensions,
            evidence_hashes=evidence.evidence_hashes,
        )


@dataclass(frozen=True)
class TargetObservation:
    target_id: str
    selected_entry_ids: tuple[str, ...]
    compatible_entry_count: int
    incompatible_entry_count: int
    unknown_entry_count: int
    selected_compatible_count: int
    selected_incompatible_count: int
    selected_unknown_count: int
    state: str | None
    partition: str | None = None
    primary_mechanism: str | None = None
    v1_correct: bool | None = None


def _state_counts(pairs: Iterable[PairObservation]) -> dict[str, int]:
    counts = {state.value: 0 for state in CompatibilityState}
    for pair in pairs:
        counts[pair.state] = counts.get(pair.state, 0) + 1
    return counts


def _target_state(
    compatible_count: int,
    selected_compatible_count: int,
    selected_states: Sequence[str],
) -> str:
    """Apply the precommitted coverage -> recall -> admission priority."""

    if compatible_count == 0:
        return "CORPUS_COVERAGE_FAILURE"
    if selected_compatible_count == 0:
        return "RETRIEVAL_RECALL_FAILURE"
    if all(state == CompatibilityState.PROVEN_COMPATIBLE.value for state in selected_states):
        return "COMPATIBLE_CONTEXT_RETRIEVED"
    return "SELECTIVITY_ADMISSION_FAILURE"


def scan_target(
    target_id: str,
    target_sql: str | None,
    m4_entries: Sequence[tuple[str, str | None]],
    selected_entry_ids: Sequence[str],
    *,
    partition: str | None = None,
    primary_mechanism: str | None = None,
    v1_correct: bool | None = None,
) -> tuple[tuple[PairObservation, ...], TargetObservation]:
    """Scan one target against every frozen M4 entry.

    ``selected_entry_ids`` is an observational list from frozen provenance;
    this function never calls a retriever or derives that list.
    """

    entries = tuple(sorted(m4_entries, key=lambda item: item[0]))
    entry_ids = {entry_id for entry_id, _ in entries}
    missing = [entry_id for entry_id in selected_entry_ids if entry_id not in entry_ids]
    if missing:
        raise ValueError(f"historical selected M4 IDs missing from corpus: {missing}")
    pairs = tuple(
        PairObservation.from_evidence(target_id, entry_id, classify_entry(target_sql, entry_sql))
        for entry_id, entry_sql in entries
    )
    by_id = {pair.entry_id: pair for pair in pairs}
    selected = tuple(by_id[entry_id] for entry_id in selected_entry_ids)
    counts = _state_counts(pairs)
    selected_counts = _state_counts(selected)
    compatible_count = counts[CompatibilityState.PROVEN_COMPATIBLE.value]
    selected_compatible_count = selected_counts[CompatibilityState.PROVEN_COMPATIBLE.value]
    observation = TargetObservation(
        target_id=target_id,
        selected_entry_ids=tuple(selected_entry_ids),
        compatible_entry_count=compatible_count,
        incompatible_entry_count=counts[CompatibilityState.PROVEN_INCOMPATIBLE.value],
        unknown_entry_count=counts[CompatibilityState.UNKNOWN.value],
        selected_compatible_count=selected_compatible_count,
        selected_incompatible_count=selected_counts[CompatibilityState.PROVEN_INCOMPATIBLE.value],
        selected_unknown_count=selected_counts[CompatibilityState.UNKNOWN.value],
        state=_target_state(
            compatible_count,
            selected_compatible_count,
            tuple(pair.state for pair in selected),
        )
        if selected_entry_ids
        else None,
        partition=partition,
        primary_mechanism=primary_mechanism,
        v1_correct=v1_correct,
    )
    return pairs, observation


def scan_population(
    targets: Sequence[Mapping[str, object]],
    m4_entries: Sequence[tuple[str, str | None]],
    selected_ids_by_target: Mapping[str, Sequence[str]],
    *,
    require_selected: bool,
) -> tuple[tuple[PairObservation, ...], tuple[TargetObservation, ...]]:
    """Scan a frozen population without using outcome or score features."""

    all_pairs: list[PairObservation] = []
    observations: list[TargetObservation] = []
    for target in sorted(targets, key=lambda item: str(item["case_id"])):
        target_id = str(target["case_id"])
        selected = tuple(selected_ids_by_target.get(target_id, ()))
        if require_selected and not selected:
            raise ValueError(f"memory-use target has no frozen selected IDs: {target_id}")
        reference_sql = cast(str | None, target.get("reference_sql"))
        partition = cast(str | None, target.get("partition"))
        primary_mechanism = cast(str | None, target.get("primary_mechanism"))
        v1_correct = cast(bool | None, target.get("v1_correct"))
        pairs, observation = scan_target(
            target_id,
            reference_sql,
            m4_entries,
            selected,
            partition=partition,
            primary_mechanism=primary_mechanism,
            v1_correct=v1_correct,
        )
        all_pairs.extend(pairs)
        observations.append(observation)
    return tuple(all_pairs), tuple(observations)


def serialize_pair(pair: PairObservation) -> dict[str, object]:
    return asdict(pair)


def serialize_target(observation: TargetObservation) -> dict[str, object]:
    return asdict(observation)


def summarize_states(observations: Iterable[TargetObservation]) -> dict[str, int]:
    counts = {state: 0 for state in PRIMARY_STATES}
    for observation in observations:
        if observation.state is not None:
            counts[observation.state] += 1
    return counts


def entry_coverage(pairs: Iterable[PairObservation], target_count: int) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, int]] = {}
    for pair in pairs:
        row = grouped.setdefault(
            pair.entry_id,
            {state.value: 0 for state in CompatibilityState},
        )
        row[pair.state] += 1
    return [
        {
            "entry_id": entry_id,
            "compatible_target_count": counts[CompatibilityState.PROVEN_COMPATIBLE.value],
            "incompatible_target_count": counts[CompatibilityState.PROVEN_INCOMPATIBLE.value],
            "unknown_target_count": counts[CompatibilityState.UNKNOWN.value],
            "target_count": target_count,
        }
        for entry_id, counts in sorted(grouped.items())
    ]


def corpus_concentration(
    pairs: Iterable[PairObservation], entry_ids: Sequence[str] | None = None
) -> dict[str, object]:
    pair_rows = tuple(pairs)
    compatible_targets: dict[str, set[str]] = {}
    for pair in pair_rows:
        if pair.state == CompatibilityState.PROVEN_COMPATIBLE.value:
            compatible_targets.setdefault(pair.entry_id, set()).add(pair.target_id)
    all_entry_ids = set(entry_ids or (pair.entry_id for pair in pair_rows))
    ranking = sorted(
        compatible_targets,
        key=lambda entry_id: (-len(compatible_targets[entry_id]), entry_id),
    )

    def union_count(limit: int) -> int:
        return len(set().union(*(compatible_targets[entry_id] for entry_id in ranking[:limit])))

    return {
        "entries_compatible_with_target": len(compatible_targets),
        "entries_never_compatible": len(all_entry_ids - set(compatible_targets)),
        "ranking": [
            {"entry_id": entry_id, "compatible_target_count": len(compatible_targets[entry_id])}
            for entry_id in ranking
        ],
        "broadest_entry_target_count": len(compatible_targets[ranking[0]]) if ranking else 0,
        "top_1_cumulative_coverage": union_count(1),
        "top_5_cumulative_coverage": union_count(5),
        "top_10_cumulative_coverage": union_count(10),
    }


def percentile(values: Sequence[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def coverage_summary(observations: Sequence[TargetObservation]) -> dict[str, object]:
    covered = [row.compatible_entry_count for row in observations if row.compatible_entry_count > 0]
    total = len(observations)
    covered_count = len(covered)
    return {
        "target_count": total,
        "targets_with_compatible": covered_count,
        "targets_without_compatible": total - covered_count,
        "coverage_rate": covered_count / total if total else None,
        "covered_distribution": {
            "min": min(covered) if covered else None,
            "p25": percentile(covered, 0.25),
            "median": percentile(covered, 0.50),
            "p75": percentile(covered, 0.75),
            "max": max(covered) if covered else None,
            "mean": sum(covered) / len(covered) if covered else None,
        },
        "capability_band": (
            "M4_CORPUS_COVERAGE_STRONG"
            if total and covered_count / total >= 0.80
            else "M4_CORPUS_COVERAGE_PARTIAL"
            if total and covered_count / total >= 0.30
            else "M4_CORPUS_COVERAGE_WEAK"
        ),
    }


def recall_summary(observations: Sequence[TargetObservation]) -> dict[str, object]:
    covered = [row for row in observations if row.compatible_entry_count > 0]
    hits = [row for row in covered if row.selected_compatible_count > 0]
    selected_compatible = sum(row.selected_compatible_count for row in covered)
    corpus_compatible = sum(row.compatible_entry_count for row in covered)
    recall = len(hits) / len(covered) if covered else None
    return {
        "covered_targets": len(covered),
        "compatible_top_k_hit_targets": len(hits),
        "compatible_top_k_miss_targets": len(covered) - len(hits),
        "target_level_compatible_recall": recall,
        "compatible_selected_entries": selected_compatible,
        "compatible_corpus_entries_across_covered_targets": corpus_compatible,
        "entry_level_compatible_selection_rate": selected_compatible / corpus_compatible
        if corpus_compatible
        else None,
        "recall_band": (
            "NOT_APPLICABLE_NO_COVERED_TARGETS"
            if recall is None
            else "RETRIEVAL_COMPATIBLE_RECALL_STRONG"
            if recall >= 0.80
            else "RETRIEVAL_COMPATIBLE_RECALL_PARTIAL"
            if recall >= 0.40
            else "RETRIEVAL_COMPATIBLE_RECALL_WEAK"
        ),
    }


def purity_summary(observations: Sequence[TargetObservation]) -> dict[str, object]:
    hits = [row for row in observations if row.selected_compatible_count > 0]
    pure = [
        row
        for row in hits
        if row.selected_incompatible_count == 0 and row.selected_unknown_count == 0
    ]
    mixed_incompatible = [
        row
        for row in hits
        if row.selected_incompatible_count > 0 and row.selected_unknown_count == 0
    ]
    mixed_unknown = [
        row
        for row in hits
        if row.selected_unknown_count > 0 and row.selected_incompatible_count == 0
    ]
    mixed_both = [
        row
        for row in hits
        if row.selected_incompatible_count > 0 and row.selected_unknown_count > 0
    ]
    rate = len(pure) / len(hits) if hits else None
    return {
        "compatible_hit_targets": len(hits),
        "all_compatible_contexts": len(pure),
        "compatible_plus_incompatible_contexts": len(mixed_incompatible),
        "compatible_plus_unknown_contexts": len(mixed_unknown),
        "compatible_plus_incompatible_plus_unknown_contexts": len(mixed_both),
        "purity_denominator": len(hits),
        "context_purity_rate": rate,
        "purity_band": (
            "NOT_APPLICABLE_NO_COMPATIBLE_HITS"
            if rate is None
            else "PURE_CONTEXT_STRONG"
            if rate >= 0.80
            else "PURE_CONTEXT_PARTIAL"
            if rate >= 0.40
            else "PURE_CONTEXT_WEAK"
        ),
    }


def dominance(counts: Mapping[str, Mapping[str, int]], denominator: int) -> dict[str, object]:
    result: dict[str, object] = {}
    for state in FAILURE_STATES:
        by_partition = counts.get(state, {})
        total = sum(by_partition.values())
        share = total / denominator if denominator else 0.0
        result[state] = {
            "DIAG_A": by_partition.get("DIAG_A", 0),
            "DIAG_B": by_partition.get("DIAG_B", 0),
            "total": total,
            "share": share,
            "count_gate": total >= 20,
            "share_gate": share >= 0.25,
            "both_partitions_gate": by_partition.get("DIAG_A", 0) > 0
            and by_partition.get("DIAG_B", 0) > 0,
            "minimum_each_partition_gate": by_partition.get("DIAG_A", 0) >= 5
            and by_partition.get("DIAG_B", 0) >= 5,
            "dominant": total >= 20
            and share >= 0.25
            and by_partition.get("DIAG_A", 0) >= 5
            and by_partition.get("DIAG_B", 0) >= 5,
        }
    return result


__all__ = [
    "FAILURE_STATES",
    "PRIMARY_STATES",
    "PairObservation",
    "TargetObservation",
    "corpus_concentration",
    "coverage_summary",
    "dominance",
    "entry_coverage",
    "purity_summary",
    "recall_summary",
    "scan_population",
    "scan_target",
    "serialize_pair",
    "serialize_target",
    "summarize_states",
]
