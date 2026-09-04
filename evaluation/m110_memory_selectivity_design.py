"""Offline M11.0 memory-admission policy design audit.

This module consumes frozen M10.4S provenance and causal labels only.  It does
not import application retrieval code, call a provider, call a database, or
replay retrieval.  Policy functions accept only the frozen reference-blind
feature boundary declared in ``m110_feature_manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
M104S_RESULT_ROOT = ROOT / "evaluation" / "results" / "m104s" / "fresh-provenance-20260905"
FIXTURE_ROOT = ROOT / "evaluation" / "fixtures"

FEATURE_COLUMNS = frozenset(
    {
        "selected_count",
        "s1",
        "s2",
        "s3",
        "min_selected_score",
        "top1_top2_gap",
    }
)
LABEL_COLUMNS = frozenset(
    {
        "case_id",
        "partition",
        "primary_mechanism",
        "compatibility_class",
        "compatibility_evidence_source",
        "v1_correct",
        "secondary_phenotypes",
        "included_primary_positive",
        "included_compatible_control",
        "included_correct_compatible_control",
        "included_compatible_but_wrong_control",
        "included_overtransfer_secondary",
        "included_unknown",
    }
)
POLICY_IDS = (
    "P0_CURRENT",
    "P1_TOP1_MIN_SCORE",
    "P2_MIN_SELECTED_SCORE",
    "P3_TOP1_PLUS_MARGIN",
)


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 digest used for bounded artifact identity."""

    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    """Hash JSON using the repository's deterministic compact encoding."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _event_payload(event: Mapping[str, Any], event_type: str) -> Mapping[str, Any] | None:
    if event.get("event_type") == event_type:
        payload = event.get("payload")
        return payload if isinstance(payload, Mapping) else None
    return None


def extract_memory_features(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract preserved ranked scores without rerunning or reconstructing retrieval."""

    retrievals: dict[str, Mapping[str, Any]] = {}
    selections: dict[str, Mapping[str, Any]] = {}
    for event in events:
        case_id = str(event.get("case_id", ""))
        if not case_id:
            continue
        payload = _event_payload(event, "MEMORY_RETRIEVAL_COMPLETED")
        if payload is not None:
            retrievals[case_id] = payload
        payload = _event_payload(event, "MEMORY_SELECTION_COMPLETED")
        if payload is not None:
            selections[case_id] = payload

    rows: dict[str, dict[str, Any]] = {}
    for case_id, payload in retrievals.items():
        ranked = payload.get("ranked_results")
        if not isinstance(ranked, list):
            continue
        scores: list[float] = []
        for item in ranked:
            if not isinstance(item, Mapping) or not isinstance(item.get("score"), (int, float)):
                scores = []
                break
            scores.append(float(item["score"]))
        if not scores:
            continue
        selection = selections.get(case_id, {})
        selected = selection.get("selected_memory_ids")
        if not isinstance(selected, list) or not selected:
            continue
        selected_count = len(selected)
        s1 = scores[0]
        s2 = scores[1] if len(scores) >= 2 else None
        s3 = scores[2] if len(scores) >= 3 else None
        rows[case_id] = {
            "case_id": case_id,
            "selected_count": selected_count,
            "s1": s1,
            "s2": s2,
            "s3": s3,
            "min_selected_score": min(scores),
            "top1_top2_gap": s1 - s2 if s2 is not None else None,
        }
    return rows


def extract_v1_correctness(case_ledger: Iterable[Mapping[str, Any]]) -> dict[str, bool]:
    """Read frozen V1 outcomes for labels; outcomes are never policy features."""

    result: dict[str, bool] = {}
    for row in case_ledger:
        case_id = str(row.get("case_id", ""))
        if case_id:
            result[case_id] = row.get("v1_correct") is True
    return result


def extract_partitions(case_ledger: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in case_ledger:
        case_id = str(row.get("case_id", ""))
        partition = row.get("partition")
        if case_id and isinstance(partition, str):
            result[case_id] = partition
    return result


def extract_attribution(
    case_ids: Iterable[str], attribution: Iterable[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    by_case = {str(row.get("case_id")): dict(row) for row in attribution}
    return {case_id: by_case[case_id] for case_id in case_ids if case_id in by_case}


def policy_feature_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return only runtime/derived policy inputs; reject gold or outcome leakage."""

    forbidden = set(row) - FEATURE_COLUMNS
    if forbidden:
        raise ValueError(f"policy feature leakage: {sorted(forbidden)}")
    return {name: row.get(name) for name in FEATURE_COLUMNS}


def _require_numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def evaluate_policy(
    policy_id: str,
    features: Mapping[str, Any],
    parameters: Mapping[str, float] | None = None,
) -> bool:
    """Simulate memory admission from reference-blind features only."""

    policy_feature_view(features)
    params = parameters or {}
    if policy_id == "P0_CURRENT":
        return int(features.get("selected_count") or 0) > 0
    if policy_id == "P1_TOP1_MIN_SCORE":
        score = _require_numeric(features.get("s1"))
        threshold = _require_numeric(params.get("tau"))
        return score is not None and threshold is not None and score >= threshold
    if policy_id == "P2_MIN_SELECTED_SCORE":
        minimum = _require_numeric(features.get("min_selected_score"))
        threshold = _require_numeric(params.get("tau"))
        return minimum is not None and threshold is not None and minimum >= threshold
    if policy_id == "P3_TOP1_PLUS_MARGIN":
        score = _require_numeric(features.get("s1"))
        gap = _require_numeric(features.get("top1_top2_gap"))
        threshold = _require_numeric(params.get("tau"))
        margin = _require_numeric(params.get("delta"))
        return (
            score is not None
            and gap is not None
            and threshold is not None
            and margin is not None
            and score >= threshold
            and gap >= margin
        )
    raise ValueError(f"unknown policy family: {policy_id}")


def threshold_candidates(values: Iterable[float]) -> list[float]:
    """Return deterministic observed-value/midpoint candidates."""

    unique = sorted({float(value) for value in values})
    if not unique:
        return []
    candidates = list(unique)
    candidates.extend(
        (left + right) / 2 for left, right in zip(unique, unique[1:], strict=False)
    )
    return sorted(set(candidates))


def _read_frozen_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ledger = load_jsonl(M104S_RESULT_ROOT / "case_ledger.jsonl")
    events = load_jsonl(M104S_RESULT_ROOT / "runtime_provenance.jsonl")
    attribution_document = load_json(M104S_RESULT_ROOT / "automated_causal_attribution.json")
    if not isinstance(attribution_document, Mapping):
        raise ValueError("frozen attribution must be an object")
    attribution = attribution_document.get("records")
    if not isinstance(attribution, list):
        raise ValueError("frozen attribution records must be a list")
    return ledger, events, attribution


def build_label_manifest() -> dict[str, Any]:
    """Build labels from frozen attribution without inventing compatibility evidence."""

    ledger, events, attribution = _read_frozen_rows()
    memory_case_ids = {
        str(row.get("case_id"))
        for row in ledger
        if row.get("memory_used") is True
    }
    features = {
        case_id: row
        for case_id, row in extract_memory_features(events).items()
        if case_id in memory_case_ids
    }
    correctness = extract_v1_correctness(ledger)
    partitions = extract_partitions(ledger)
    attributed = extract_attribution(features, attribution)
    rows: list[dict[str, Any]] = []
    for case_id in sorted(features):
        record = attributed.get(case_id, {})
        mechanism = record.get("primary_causal_mechanism")
        is_primary = mechanism == "MEMORY_RETRIEVAL_SELECTIVITY_FAILURE"
        is_overtransfer = mechanism == "MEMORY_OVERTRANSFER_EVIDENCE"
        # The frozen M10.4S audit established no compatible selected-memory
        # cases.  All remaining memory-use cases therefore stay unknown; a
        # correct outcome is not upgraded into compatibility evidence.
        compatibility = "INCOMPATIBLE" if is_primary or is_overtransfer else "COMPATIBILITY_UNKNOWN"
        rows.append(
            {
                "case_id": case_id,
                "partition": partitions.get(case_id),
                "primary_mechanism": mechanism,
                "compatibility_class": compatibility,
                "compatibility_evidence_source": (
                    "frozen_m104s_memory_rule"
                    if compatibility == "INCOMPATIBLE"
                    else "none_available"
                ),
                "v1_correct": correctness.get(case_id),
                "secondary_phenotypes": record.get("secondary_phenotypes", []),
                "included_primary_positive": is_primary,
                "included_compatible_control": False,
                "included_correct_compatible_control": False,
                "included_compatible_but_wrong_control": False,
                "included_overtransfer_secondary": is_overtransfer,
                "included_unknown": compatibility == "COMPATIBILITY_UNKNOWN",
            }
        )
    return {
        "artifact": "M110_COMPATIBILITY_LABEL_MANIFEST",
        "version": "decision-sql-m110-compatibility-labels-v1",
        "source": "frozen M10.4S automated causal attribution and runtime provenance",
        "memory_use_case_count": len(rows),
        "rows": rows,
        "counts": {
            "primary_selectivity_positive": sum(row["included_primary_positive"] for row in rows),
            "diag_a_primary_positive": sum(
                row["included_primary_positive"] and row["partition"] == "DIAG_A" for row in rows
            ),
            "diag_b_primary_positive": sum(
                row["included_primary_positive"] and row["partition"] == "DIAG_B" for row in rows
            ),
            "overtransfer_secondary": sum(row["included_overtransfer_secondary"] for row in rows),
            "compatible_controls": sum(row["included_compatible_control"] for row in rows),
            "correct_compatible_controls": sum(
                row["included_correct_compatible_control"] for row in rows
            ),
            "compatible_but_wrong_controls": sum(
                row["included_compatible_but_wrong_control"] for row in rows
            ),
            "compatibility_unknown": sum(row["included_unknown"] for row in rows),
        },
        "control_status": "BLOCKED_NO_DETERMINISTIC_COMPATIBLE_CONTROLS",
        "counterfactual": "not_run",
    }


def build_feature_rows() -> list[dict[str, Any]]:
    """Return one safe feature row per actually injected historical context."""

    ledger, events, _ = _read_frozen_rows()
    memory_case_ids = {
        str(row.get("case_id"))
        for row in ledger
        if row.get("memory_used") is True
    }
    partitions = extract_partitions(ledger)
    features = extract_memory_features(events)
    rows: list[dict[str, Any]] = []
    for case_id in sorted(memory_case_ids & features.keys()):
        row = features[case_id]
        rows.append(
            {
                "case_id": case_id,
                "partition": partitions.get(case_id),
                "features": {name: row[name] for name in sorted(FEATURE_COLUMNS)},
            }
        )
    return rows


def descriptive_statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    """Calculate presentation-only deterministic distribution summaries."""

    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
            "mean": None,
            "std": None,
        }

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * weight

    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": percentile(0.10),
        "p25": percentile(0.25),
        "median": percentile(0.50),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
        "std": statistics.pstdev(ordered) if len(ordered) > 1 else 0.0,
    }


def build_score_diagnostics(label_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize available scores without inventing unavailable controls."""

    labels = {row["case_id"]: row for row in label_manifest["rows"]}
    feature_rows = {row["case_id"]: row["features"] for row in build_feature_rows()}
    signal_names = ("s1", "s2", "s3", "min_selected_score", "top1_top2_gap")
    groups = {
        "primary_selectivity_failures": [
            case_id for case_id, row in labels.items() if row["included_primary_positive"]
        ],
        "compatible_controls": [
            case_id for case_id, row in labels.items() if row["included_compatible_control"]
        ],
        "correct_compatible_controls": [
            case_id for case_id, row in labels.items() if row["included_correct_compatible_control"]
        ],
        "compatible_but_wrong_controls": [
            case_id
            for case_id, row in labels.items()
            if row["included_compatible_but_wrong_control"]
        ],
        "overtransfer_secondary": [
            case_id for case_id, row in labels.items() if row["included_overtransfer_secondary"]
        ],
    }
    distributions: dict[str, Any] = {}
    for signal in signal_names:
        distributions[signal] = {
            group: descriptive_statistics(
                feature_rows[case_id][signal]
                for case_id in case_ids
                if feature_rows[case_id].get(signal) is not None
            )
            for group, case_ids in groups.items()
        }
    return {
        "artifact": "M110_SCORE_DIAGNOSTICS",
        "version": "decision-sql-m110-score-diagnostics-v1",
        "status": "DESCRIPTIVE_ONLY_COMPATIBLE_CONTROL_SET_EMPTY",
        "signals": list(signal_names),
        "score_orientation": "higher_is_better",
        "score_calibrated_probability": False,
        "distributions": distributions,
        "auroc": None,
        "auprc": None,
        "reason": (
            "AUROC/AUPRC and policy eligibility require a non-empty "
            "deterministic compatible control set"
        ),
    }


if __name__ == "__main__":
    raise SystemExit(
        "M11.0 is an offline audit library; use the focused tests or artifact builder."
    )
