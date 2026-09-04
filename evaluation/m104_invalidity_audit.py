# ruff: noqa: E501

"""Record post-run protocol invalidity without modifying consumed run data."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from sqlglot import parse_one

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures"
RESULTS = ROOT / "evaluation" / "results" / "m104" / "fresh-provenance-20260904"


def main() -> None:
    historical: set[str] = set()
    for path in FIXTURES.rglob("*.json"):
        if path.name.startswith("m104_"):
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"reference_sql", "gold_sql"} and isinstance(item, str):
                        try:
                            historical.add(parse_one(item, read="postgres").sql(dialect="postgres").lower())
                        except Exception:
                            pass
                    elif isinstance(item, (dict, list)):
                        stack.append(item)
            elif isinstance(value, list):
                stack.extend(value)
    from evaluation.m4_benchmark import build_memory_corpus

    for example in build_memory_corpus():
        historical.add(parse_one(example.sql, read="postgres").sql(dialect="postgres").lower())
    corpus = json.loads((FIXTURES / "m104_corpus.json").read_text())
    overlaps = []
    for case in corpus["cases"]:
        canonical = parse_one(case["reference_sql"], read="postgres").sql(dialect="postgres").lower()
        if canonical in historical:
            overlaps.append(case["case_id"])
    report = {
        "classification": "M104_RUN_INVALID",
        "reason": "final corpus freshness gate used raw trimmed SQL rather than canonical SQL; provider execution had already begun, so repair is prohibited",
        "canonical_reference_overlaps": overlaps,
        "overlap_count": len(overlaps),
        "provider_calls_after_exposure": 277,
        "corpus_edit_after_final_freeze": False,
        "rerun_attempted": False,
        "m11_selection_permitted": False,
    }
    path = RESULTS / "protocol_integrity_failure.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    (RESULTS / "selection_decision.json").write_text(
        json.dumps(
            {
                "classification": "M104_RUN_INVALID",
                "selected_mechanism": None,
                "next_milestone": "M10.4R — Fresh Provenance-Complete Diagnostic Run Repair Audit",
                "selection_permitted": False,
                "protocol_integrity_failure": report,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(json.dumps({"hash": sha256(path.read_bytes()).hexdigest(), **report}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
