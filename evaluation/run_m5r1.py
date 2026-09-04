"""Run the frozen M5R.1 selective-answering evaluation.

The command is intentionally explicit about its phase.  ``validate`` and R0
are provider-free.  ``dev`` is the only phase that may make quality calls
without an existing DEV freeze; ``holdout`` requires a persisted DEV gate.
No phase generates SQL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import get_settings
from app.generation.provider import OpenAICompatibleProvider
from evaluation.m5r1_benchmark import CORPUS_ID, CORPUS_VERSION, build_benchmark, validate_benchmark
from evaluation.selective_answering import (
    ArmResult,
    InterpretationValidator,
    deterministic_r0,
    metrics,
    prompt_hash,
    provider_r1,
    provider_r2,
    provider_settings,
    render_server_context,
)

M4_DEV_HASH = "34ff68f3b0fe632645d5e1013c2498fd4ae0c97a1e8287c308fe3c82b134c2b0"
M4_HOLDOUT_HASH = "910bb52de4d58e2963c695748e15064ef8d73b1c83c8dd6b74147c35c5e176fd"
M4_CORPUS_HASH = "f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae"
M3_CONTRACT_HASH = "0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c"
M5_RULESET_HASH = "4b3c91168832f1d3ca4370972d33abfebc7158712ea7ac04c8ea687d2253b494"
M501_CORPUS_HASH = "7cee1b1ca6ccd856a46a1fd17c0c9a1f7765fdec060bfbeef7b149a51e1e60c7"


def _json_default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: tuple[dict[str, Any], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n" for row in rows),
        encoding="utf-8",
    )


def _result_payload(result: ArmResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "case_id": result.case_id,
        "action": result.action.value,
        "reason_code": result.reason_code,
        "latency_ms": round(result.latency_ms, 3),
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
    }
    if result.r2 is not None:
        payload["r2"] = result.r2.model_dump(mode="json")
    return payload


def _family_metrics(cases: tuple[Any, ...], results: tuple[ArmResult, ...]) -> dict[str, Any]:
    grouped: dict[str, list[Any]] = {}
    by_id = {case.case_id: case for case in cases}
    for result in results:
        grouped.setdefault(by_id[result.case_id].reason_family, []).append(result)
    output: dict[str, Any] = {}
    for family, family_results in sorted(grouped.items()):
        output[family] = metrics(
            tuple(by_id[result.case_id] for result in family_results), tuple(family_results)
        )
    return output


def _gate(summary: dict[str, Any]) -> bool:
    values = (
        summary.get("unsafe_answer_rate"),
        summary.get("answer_precision"),
        summary.get("clarify_precision"),
        summary.get("abstain_precision"),
        summary.get("unnecessary_intervention_rate"),
    )
    if any(value is None for value in values):
        return False
    return bool(
        summary["unsafe_answer_rate"] <= 0.10
        and summary["answer_precision"] >= 0.90
        and summary["clarify_precision"] >= 0.80
        and summary["abstain_precision"] >= 0.90
        and summary["unnecessary_intervention_rate"] <= 0.25
    )


async def _run_provider_arms(
    cases: tuple[Any, ...], context: str
) -> tuple[tuple[ArmResult, ...], tuple[ArmResult, ...]]:
    settings = provider_settings(get_settings())
    if not settings.llm_api_key:
        raise RuntimeError("M5R.1 provider credential is not configured")
    provider = OpenAICompatibleProvider(settings)
    validator = InterpretationValidator()
    r1_results: list[ArmResult] = []
    r2_results: list[ArmResult] = []
    for case in cases:
        decision, input_tokens, output_tokens, latency = await provider_r1(
            provider, case.question, context
        )
        r1_results.append(
            ArmResult(
                case_id=case.case_id,
                action=decision.action,
                reason_code=decision.reason_code,
                latency_ms=latency,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
        response, input_tokens, output_tokens, latency = await provider_r2(
            provider, case.question, context
        )
        r2_decision = validator.validate(response)
        r2_results.append(
            ArmResult(
                case_id=case.case_id,
                action=r2_decision.action,
                reason_code=r2_decision.reason_code,
                latency_ms=latency,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                r2=r2_decision,
            )
        )
    return tuple(r1_results), tuple(r2_results)


def _run_r0(cases: tuple[Any, ...]) -> tuple[ArmResult, ...]:
    results: list[ArmResult] = []
    for case in cases:
        started = perf_counter()
        decision = deterministic_r0(case.question)
        results.append(
            ArmResult(
                case_id=case.case_id,
                action=decision.action,
                reason_code=decision.reason_code,
                latency_ms=(perf_counter() - started) * 1000,
            )
        )
    return tuple(results)


def _manifest(cases: tuple[Any, ...], validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "validation": validation,
        "frozen_sources": {
            "m3_contract_hash": M3_CONTRACT_HASH,
            "m4_corpus_hash": M4_CORPUS_HASH,
            "m4_dev_hash": M4_DEV_HASH,
            "m4_holdout_hash": M4_HOLDOUT_HASH,
            "m5_0_1_corpus_hash": M501_CORPUS_HASH,
        },
        "case_index": [
            {
                "case_id": case.case_id,
                "split": case.split,
                "gold_action": case.gold_action.value,
                "reason_family": case.reason_family,
                "subtype": case.subtype,
                "contrast_group_id": case.contrast_group_id,
            }
            for case in cases
        ],
    }


def _summarize_arm(cases: tuple[Any, ...], results: tuple[ArmResult, ...]) -> dict[str, Any]:
    summary = metrics(cases, results)
    summary["family_metrics"] = _family_metrics(cases, results)
    summary["gate_passed"] = _gate(summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("validate", "dev", "holdout"), default="validate")
    parser.add_argument("--artifact-dir", type=Path, default=Path("evaluation/results/m5r1"))
    parser.add_argument("--r0-only", action="store_true")
    args = parser.parse_args()

    cases = build_benchmark()
    validation = validate_benchmark(cases)
    _write_json(Path("evaluation/fixtures/m5r1_manifest.json"), _manifest(cases, validation))
    if args.phase == "validate":
        print(json.dumps(validation, indent=2, default=_json_default))
        return

    split = "DEV" if args.phase == "dev" else "HOLDOUT"
    split_cases = tuple(case for case in cases if case.split == split)
    context = render_server_context()
    artifact_dir = (
        args.artifact_dir / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{split.lower()}"
    )
    metadata = {
        "phase": split,
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "corpus_hash": validation["corpus_hash"],
        "split_hash": validation[f"{split.lower()}_hash"],
        "r1_prompt_hash": prompt_hash("R1", context),
        "r2_prompt_hash": prompt_hash("R2", context),
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": "omitted/provider-default",
        "provider_calls_before_phase_a": 0,
        "sql_generation_calls": 0,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    _write_json(artifact_dir / "metadata.json", metadata)
    r0_results = _run_r0(split_cases)
    _write_jsonl(artifact_dir / "r0_results.jsonl", tuple(_result_payload(x) for x in r0_results))
    summaries: dict[str, Any] = {"R0": _summarize_arm(split_cases, r0_results)}
    if args.r0_only:
        _write_json(artifact_dir / "summary.json", summaries)
        print(json.dumps(summaries, indent=2, default=_json_default))
        return
    if args.phase == "holdout":
        raise RuntimeError(
            "holdout requires an explicitly reviewed DEV freeze; no automatic consumption"
        )
    if not get_settings().llm_api_key:
        _write_json(artifact_dir / "summary.json", summaries)
        raise RuntimeError("provider credential absent; R1/R2 were not called")
    r1_results, r2_results = asyncio.run(_run_provider_arms(split_cases, context))
    for name, results in (("R1", r1_results), ("R2", r2_results)):
        _write_jsonl(
            artifact_dir / f"{name.lower()}_results.jsonl",
            tuple(_result_payload(x) for x in results),
        )
        summaries[name] = _summarize_arm(split_cases, results)
    _write_json(artifact_dir / "summary.json", summaries)
    print(json.dumps(summaries, indent=2, default=_json_default))


if __name__ == "__main__":
    main()
