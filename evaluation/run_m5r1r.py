"""M5R.1R protocol smoke and frozen selective-answering rerun.

This runner is evaluation-only.  It never requests SQL and never retries a
provider call.  HOLDOUT requires an explicit DEV freeze produced by this
command after the precommitted DEV gates pass.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    MalformedProviderResponse,
    OpenAICompatibleProvider,
)
from evaluation.m5r1_benchmark import (
    CORPUS_ID,
    CORPUS_VERSION,
    SelectiveAction,
    build_benchmark,
    validate_benchmark,
)
from evaluation.selective_answering import (
    ArmResult,
    InterpretationValidator,
    metrics,
    prompt_hash,
    provider_r1,
    provider_r2,
    provider_settings,
    render_server_context,
    response_schema_hash,
)

EXPECTED_CORPUS_HASH = "066d036596e54a9effe319f4cf58761a0da5b6572f20ac82cbaec7b8ae9ec767"
EXPECTED_DEV_HASH = "301e001e1207b646d3469aab7607bdbff4db501b1c09df1fb329cecfbd6089c9"
EXPECTED_HOLDOUT_HASH = "087cca9818f9654c72bc0abd42c153c41152fba8ca82bec498d5944046a318ad"


@dataclass(frozen=True)
class _CallRecord:
    result: ArmResult | None
    status: Literal["VALID", "DTO_FAILURE", "TRANSPORT_FAILURE"]
    error_type: str | None = None


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


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
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


def _failure_payload(case_id: str, record: _CallRecord) -> dict[str, Any]:
    return {"case_id": case_id, "status": record.status, "error_type": record.error_type}


def _protocol_summary(records: list[_CallRecord]) -> dict[str, Any]:
    counts = {
        status: sum(record.status == status for record in records)
        for status in ("VALID", "DTO_FAILURE", "TRANSPORT_FAILURE")
    }
    attempted = len(records)
    return {
        "calls_attempted": attempted,
        "valid_responses": counts["VALID"],
        "dto_failures": counts["DTO_FAILURE"],
        "transport_failures": counts["TRANSPORT_FAILURE"],
        "protocol_compliance": counts["VALID"] / attempted if attempted else None,
        "counts": counts,
    }


async def _call_r1(
    provider: OpenAICompatibleProvider, case_id: str, question: str, context: str
) -> _CallRecord:
    try:
        decision, input_tokens, output_tokens, latency = await provider_r1(
            provider, question, context
        )
        return _CallRecord(
            ArmResult(
                case_id, decision.action, decision.reason_code, latency, input_tokens, output_tokens
            ),
            "VALID",
        )
    except MalformedProviderResponse as error:
        return _CallRecord(None, "DTO_FAILURE", type(error).__name__)
    except LLMProviderError as error:
        return _CallRecord(None, "TRANSPORT_FAILURE", type(error).__name__)


async def _call_r2(
    provider: OpenAICompatibleProvider,
    validator: InterpretationValidator,
    case_id: str,
    question: str,
    context: str,
) -> _CallRecord:
    try:
        response, input_tokens, output_tokens, latency = await provider_r2(
            provider, question, context
        )
        decision = validator.validate(response)
        return _CallRecord(
            ArmResult(
                case_id,
                decision.action,
                decision.reason_code,
                latency,
                input_tokens,
                output_tokens,
                decision,
            ),
            "VALID",
        )
    except MalformedProviderResponse as error:
        return _CallRecord(None, "DTO_FAILURE", type(error).__name__)
    except LLMProviderError as error:
        return _CallRecord(None, "TRANSPORT_FAILURE", type(error).__name__)


async def _run_quality(
    cases: tuple[Any, ...],
    context: str,
    arms: tuple[str, ...],
) -> tuple[dict[str, list[ArmResult]], dict[str, list[_CallRecord]]]:
    settings = provider_settings(get_settings())
    if not settings.llm_api_key:
        raise RuntimeError("M5R.1R provider credential is not configured")
    provider = OpenAICompatibleProvider(settings)
    validator = InterpretationValidator()
    results: dict[str, list[ArmResult]] = {arm: [] for arm in arms}
    records: dict[str, list[_CallRecord]] = {arm: [] for arm in arms}
    for case in cases:
        if "R1" in arms:
            record = await _call_r1(provider, case.case_id, case.question, context)
            records["R1"].append(record)
            if record.result is not None:
                results["R1"].append(record.result)
        if "R2" in arms:
            record = await _call_r2(provider, validator, case.case_id, case.question, context)
            records["R2"].append(record)
            if record.result is not None:
                results["R2"].append(record.result)
    return results, records


async def _protocol_smoke(context: str) -> dict[str, Any]:
    settings = provider_settings(get_settings())
    if not settings.llm_api_key:
        raise RuntimeError("M5R.1R provider credential is not configured")
    provider = OpenAICompatibleProvider(settings)
    validator = InterpretationValidator()
    smoke: dict[str, Any] = {"R1": {"calls": 1}, "R2": {"calls": 1}}
    try:
        decision, _, _, _ = await provider_r1(
            provider, "Protocol smoke: is completed revenue a supported concept?", context
        )
        smoke["R1"].update(
            {
                "dto_valid": True,
                "action_enum_valid": decision.action.value
                in {action.value for action in SelectiveAction},
            }
        )
    except Exception as error:  # noqa: BLE001 - smoke must report bounded failure and stop
        smoke["R1"].update({"dto_valid": False, "error_type": type(error).__name__})
    try:
        response, _, _, _ = await provider_r2(
            provider,
            "Protocol smoke: propose a supported interpretation for completed revenue.",
            context,
        )
        r2_decision = validator.validate(response)
        smoke["R2"].update(
            {
                "dto_valid": True,
                "validator_consumed": True,
                "action_field_present": False,
                "valid_interpretation_count": len(r2_decision.valid_interpretation_keys),
            }
        )
    except Exception as error:  # noqa: BLE001 - smoke must report bounded failure and stop
        smoke["R2"].update({"dto_valid": False, "error_type": type(error).__name__})
    smoke["passed"] = bool(smoke["R1"]["dto_valid"] and smoke["R2"]["dto_valid"])
    return smoke


def _subtype_false_intervention(
    cases: tuple[Any, ...], results: tuple[ArmResult, ...], subtype: str
) -> float | None:
    selected = [case for case in cases if case.subtype == subtype]
    if not selected:
        return None
    by_id = {result.case_id: result for result in results}
    if any(case.case_id not in by_id for case in selected):
        return None
    return sum(by_id[case.case_id].action is not SelectiveAction.ANSWER for case in selected) / len(
        selected
    )


def _family_metrics(cases: tuple[Any, ...], results: tuple[ArmResult, ...]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    families = sorted({case.reason_family for case in cases})
    return {
        family: metrics(
            tuple(case for case in cases if case.reason_family == family),
            tuple(result for result in results if by_id[result.case_id].reason_family == family),
        )
        for family in families
    }


def _arm_quality(cases: tuple[Any, ...], results: tuple[ArmResult, ...]) -> dict[str, Any]:
    summary = metrics(cases, results)
    summary["governed_default_false_intervention"] = _subtype_false_intervention(
        cases, results, "A2_GOVERNED_DEFAULT"
    )
    summary["m4_complexity_false_intervention"] = _subtype_false_intervention(
        cases, results, "A4_CLEAR_DIRECT_COMPLEX"
    )
    summary["family_metrics"] = _family_metrics(cases, results)
    values = (
        summary["unsafe_answer_rate"],
        summary["answer_precision"],
        summary["clarify_precision"],
        summary["abstain_precision"],
        summary["unnecessary_intervention_rate"],
        summary["governed_default_false_intervention"],
        summary["m4_complexity_false_intervention"],
    )
    summary["semantic_gate_passed"] = all(value is not None for value in values) and (
        summary["unsafe_answer_rate"] <= 0.10
        and summary["answer_precision"] >= 0.90
        and summary["clarify_precision"] >= 0.80
        and summary["abstain_precision"] >= 0.90
        and summary["unnecessary_intervention_rate"] <= 0.25
        and summary["governed_default_false_intervention"] <= 0.10
        and summary["m4_complexity_false_intervention"] <= 0.15
    )
    return summary


def _r2_analysis(cases: tuple[Any, ...], results: tuple[ArmResult, ...]) -> dict[str, Any]:
    by_id = {case.case_id: case for case in cases}
    r2_results = [result for result in results if result.r2 is not None]
    matched = 0
    gold_total = 0
    proposed = valid = invalid = dedup = 0
    distribution = {"zero_valid": 0, "one_valid": 0, "two_plus_valid": 0}
    omissions = 0
    for result in r2_results:
        assert result.r2 is not None
        info = result.r2
        case = by_id[result.case_id]
        proposed += info.proposed_interpretation_count
        valid += len(info.valid_interpretation_keys)
        invalid += info.invalid_interpretation_count
        dedup += info.canonical_dedup_count
        count = len(info.valid_interpretation_keys)
        distribution[
            "zero_valid" if count == 0 else "one_valid" if count == 1 else "two_plus_valid"
        ] += 1
        gold = set(case.supported_interpretations)
        if case.gold_action is SelectiveAction.CLARIFY:
            gold_total += len(gold)
            matched += len(gold & set(info.valid_interpretation_keys))
            if not gold <= set(info.valid_interpretation_keys):
                omissions += 1
    return {
        "questions_with_valid_dto": len(r2_results),
        "mean_interpretations_proposed": proposed / len(r2_results) if r2_results else None,
        "valid_interpretation_count": valid,
        "invalid_interpretation_count": invalid,
        "canonical_dedup_count": dedup,
        "zero_valid_count": distribution["zero_valid"],
        "one_valid_count": distribution["one_valid"],
        "two_plus_valid_count": distribution["two_plus_valid"],
        "ambiguous_interpretation_recall": matched / gold_total if gold_total else None,
        "interpretation_precision": valid / proposed if proposed else None,
        "interpretation_omission_count": omissions,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("dev", "holdout"), required=True)
    parser.add_argument("--artifact-dir", type=Path, default=Path("evaluation/results/m5r1r"))
    parser.add_argument("--dev-freeze", type=Path)
    args = parser.parse_args()

    cases = build_benchmark()
    validation = validate_benchmark(cases)
    if (validation["corpus_hash"], validation["dev_hash"], validation["holdout_hash"]) != (
        EXPECTED_CORPUS_HASH,
        EXPECTED_DEV_HASH,
        EXPECTED_HOLDOUT_HASH,
    ):
        raise RuntimeError("M5R.1 benchmark hash drift detected; refusing M5R.1R")
    context = render_server_context()
    split = "DEV" if args.phase == "dev" else "HOLDOUT"
    split_cases = tuple(case for case in cases if case.split == split)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{split.lower()}"
    artifact_dir = args.artifact_dir / run_id
    metadata = {
        "phase": split,
        "corpus_id": CORPUS_ID,
        "corpus_version": CORPUS_VERSION,
        "corpus_hash": validation["corpus_hash"],
        "split_hash": validation[f"{split.lower()}_hash"],
        "r1_dto_version": "m5r1-r1-decision-v1",
        "r1_response_schema_hash": response_schema_hash("R1"),
        "r1_semantic_prompt_hash": prompt_hash("R1", context),
        "r2_dto_version": "m5r1-r2-interpretation-v1",
        "r2_response_schema_hash": response_schema_hash("R2"),
        "r2_semantic_prompt_hash": prompt_hash("R2", context),
        "r2_validator_version": "m5r1-r2-validator-v1",
        "r2_validator_hash": "c999626806aa961aaa364b34e9b0e760a50d45283796bb38679c0665962e386e",
        "model": "gpt-5.6-luna",
        "reasoning": "none",
        "temperature": "omitted/provider-default",
        "server_context_hash": __import__("hashlib").sha256(context.encode()).hexdigest(),
        "provider_transport": "httpx OpenAI-compatible Chat Completions; no OpenAI SDK dependency",
        "historical_invalid_calls": {"R1": 1, "R2": 1},
    }
    _write_json(artifact_dir / "metadata.json", metadata)

    if args.phase == "dev":
        smoke = asyncio.run(_protocol_smoke(context))
        _write_json(artifact_dir / "protocol_smoke.json", smoke)
        if not smoke["passed"]:
            _write_json(
                artifact_dir / "final_decision.json",
                {"classification": "M5R1R_PROTOCOL_NOT_FIXED", "smoke": smoke},
            )
            raise RuntimeError("M5R.1R protocol smoke failed; DEV was not run")
        arms = ("R1", "R2")
    else:
        if args.dev_freeze is None or not args.dev_freeze.exists():
            raise RuntimeError("HOLDOUT requires an explicit DEV freeze artifact")
        freeze = json.loads(args.dev_freeze.read_text(encoding="utf-8"))
        arms = tuple(freeze.get("eligible_arms", ()))
        if not arms or any(arm not in {"R1", "R2"} for arm in arms):
            raise RuntimeError("DEV freeze contains no eligible provider arm")

    started = perf_counter()
    results, records = asyncio.run(_run_quality(split_cases, context, arms))
    metadata["quality_wall_ms"] = (perf_counter() - started) * 1000
    _write_json(artifact_dir / "metadata.json", metadata)
    summaries: dict[str, Any] = {}
    for arm in arms:
        _write_jsonl(
            artifact_dir / f"{arm.lower()}_results.jsonl",
            [_result_payload(result) for result in results[arm]],
        )
        _write_jsonl(
            artifact_dir / f"{arm.lower()}_protocol.jsonl",
            [
                _failure_payload(case.case_id, record)
                for case, record in zip(split_cases, records[arm], strict=True)
            ],
        )
        protocol = _protocol_summary(records[arm])
        quality = (
            _arm_quality(split_cases, tuple(results[arm]))
            if protocol["protocol_compliance"] == 1.0
            else {"status": "NOT_SCORED_PROTOCOL_FAILURE"}
        )
        summaries[arm] = {"protocol": protocol, "quality": quality}
        if arm == "R2" and protocol["protocol_compliance"] == 1.0:
            summaries[arm]["interpretation_analysis"] = _r2_analysis(
                split_cases, tuple(results[arm])
            )
    _write_json(artifact_dir / "summary.json", summaries)
    if args.phase == "dev":
        eligible = [
            arm
            for arm in arms
            if summaries[arm]["protocol"]["protocol_compliance"] >= 0.99
            and summaries[arm]["quality"].get("semantic_gate_passed")
        ]
        freeze = {
            "corpus_hash": validation["corpus_hash"],
            "dev_hash": validation["dev_hash"],
            "eligible_arms": eligible,
            "prompt_hashes": {"R1": prompt_hash("R1", context), "R2": prompt_hash("R2", context)},
            "response_schema_hashes": {
                "R1": response_schema_hash("R1"),
                "R2": response_schema_hash("R2"),
            },
        }
        _write_json(artifact_dir / "dev_freeze.json", freeze)
        protocol_unreliable = any(
            summaries[arm]["protocol"]["protocol_compliance"] < 0.99 for arm in arms
        )
        final = {
            "classification": (
                "DEV_GATE_PASSED"
                if eligible
                else "M5R1R_PROTOCOL_UNRELIABLE"
                if protocol_unreliable
                else "NO_GO_M5"
            ),
            "eligible_arms": eligible,
        }
    else:
        final = {"classification": "HOLDOUT_EVALUATED", "eligible_arms": list(arms)}
    _write_json(artifact_dir / "final_decision.json", final)
    print(
        json.dumps(
            {"artifact_dir": str(artifact_dir), "final": final, "summary": summaries},
            indent=2,
            default=_json_default,
        )
    )


if __name__ == "__main__":
    main()
