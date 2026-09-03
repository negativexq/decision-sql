"""Offline analysis for a completed M2.12.1 provider-boundary audit."""

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from app.catalog.default import build_default_catalog
from app.db.models import Base
from app.generation.window_dsl import parse_window_dsl
from app.generation.window_provider_adapter import ProviderWindowIRAdapter
from app.generation.window_provider_dto import ProviderWindowIRDTO

ROOT = Path(__file__).resolve().parents[1]


def _load(run_dir: Path, name: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads((run_dir / name).read_text()))


def _json_object(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _native_classification(record: dict[str, Any]) -> str:
    capture = record.get("capture") or {}
    usage = capture.get("usage") or {}
    content = capture.get("raw_assistant_content")
    if content:
        if _json_object(content) is not None:
            return "SCHEMA_VALIDATION_FAILED"
        return "PROVIDER_RETURNED_NON_JSON"
    if (
        capture.get("response_model")
        and usage.get("prompt_tokens") is not None
        and usage.get("completion_tokens") is not None
        and not content
    ):
        return "PROVIDER_STRUCTURED_OUTPUT_UNSUPPORTED"
    return "OTHER_SANITIZED_PROVIDER_ERROR"


def _representation_metrics(
    records: list[dict[str, Any]],
    *,
    parse_dto: bool,
    parse_dsl: bool = False,
    union_level: bool = False,
) -> dict[str, Any]:
    if union_level:
        parsed = sum(record.get("parsed") is not None for record in records)
        converted = sum(record.get("status") == "transport_success" for record in records)
        return {
            "requests_attempted": len(records),
            "provider_accepted": sum(
                record.get("provider_succeeded") is True for record in records
            ),
            "responses_received": sum(record.get("capture") is not None for record in records),
            "syntax_parsed": parsed,
            "provider_dto_validated": parsed,
            "adapter_conversion_succeeded": converted,
            "internal_ir_validated": converted,
            "transport_success": converted,
            "failure_classes": dict(
                Counter(
                    record.get("failure_class") or "ADAPTER_MAPPING_FAILED"
                    for record in records
                    if record.get("status") != "transport_success"
                )
            ),
        }
    adapter = ProviderWindowIRAdapter(build_default_catalog(Base.metadata))
    provider_accepted = sum(record.get("provider_succeeded") is True for record in records)
    syntax_parsed = 0
    dto_validated = 0
    adapter_converted = 0
    internal_validated = 0
    failures: Counter[str] = Counter()
    for record in records:
        content = record.get("raw_content")
        if content is None:
            failures[record.get("failure_class") or "NO_RESPONSE_CONTENT"] += 1
            continue
        if parse_dsl:
            try:
                dto = parse_window_dsl(content)
                syntax_parsed += 1
                dto_validated += 1
            except Exception:
                failures["DSL_PARSE_FAILED"] += 1
                continue
        else:
            data = _json_object(content)
            if data is None:
                failures["PROVIDER_RETURNED_NON_JSON"] += 1
                continue
            syntax_parsed += 1
            if not parse_dto:
                continue
            try:
                dto = ProviderWindowIRDTO.model_validate(data)
                dto_validated += 1
            except ValidationError:
                failures["SCHEMA_VALIDATION_FAILED"] += 1
                continue
        try:
            adapter.convert(dto)
            adapter_converted += 1
            internal_validated += 1
        except Exception:
            failures["ADAPTER_MAPPING_FAILED"] += 1
    return {
        "requests_attempted": len(records),
        "provider_accepted": provider_accepted,
        "responses_received": sum(record.get("capture") is not None for record in records),
        "syntax_parsed": syntax_parsed,
        "provider_dto_validated": dto_validated,
        "adapter_conversion_succeeded": adapter_converted,
        "internal_ir_validated": internal_validated,
        "transport_success": internal_validated,
        "failure_classes": dict(failures),
    }


def analyze(run_dir: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    native = _load(run_dir, "native_structured_results.json")
    ladder = _load(run_dir, "schema_ladder.json")
    flat = _load(run_dir, "flat_dto_results.json")
    plain = _load(run_dir, "plain_json_results.json")
    dsl = _load(run_dir, "dsl_results.json")
    native_classes = Counter(_native_classification(record) for record in native)
    for record in native:
        capture = record.get("capture") or {}
        record["raw_content"] = capture.get("raw_assistant_content")
        record["failure_class"] = _native_classification(record)
        record["provider_succeeded"] = bool(capture.get("response_model"))
    (run_dir / "native_structured_results.json").write_text(json.dumps(native, indent=2) + "\n")
    ladder_by_level = {
        str(level): _representation_metrics(
            [record for record in ladder if record["representation"] == f"ladder_level_{level}"],
            parse_dto=level >= 5,
            union_level=level == 6,
        )
        for level in range(7)
    }
    result = {
        "run_dir": str(run_dir.relative_to(ROOT)),
        "historical_native_classification": {
            "classification": "UNDETERMINABLE_FROM_PERSISTED_ARTIFACT",
            "count": 48,
            "request_rejected_before_inference": "not determinable",
            "inference_occurred_but_output_unparsed": "not determinable",
        },
        "current_native": {
            "attempted": len(native),
            "provider_accepted_or_inference_evidenced": sum(
                bool(record.get("capture", {}).get("response_model")) for record in native
            ),
            "classifications": dict(native_classes),
        },
        "ladder": ladder_by_level,
        "flat_dto": _representation_metrics(flat, parse_dto=True),
        "plain_json": _representation_metrics(plain, parse_dto=True),
        "dsl": _representation_metrics(dsl, parse_dto=False, parse_dsl=True),
        "semantic_diagnostics": json.loads((run_dir / "semantic_diagnostic.json").read_text()),
        "provider_calls": sum(len(group) for group in (native, ladder, flat, plain, dsl)),
    }
    (run_dir / "transport_comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["provider_calls_by_arm"] = {
        "CURRENT_NATIVE_WINDOW_IR": len(native),
        "SCHEMA_LADDER": len(ladder),
        "FLAT_PROVIDER_DTO": len(flat),
        "PLAIN_JSON_TEXT": len(plain),
        "WINDOW_DSL": len(dsl),
    }
    metadata["provider_calls_attempted"] = result["provider_calls"]
    metadata["provider_calls_succeeded"] = sum(
        bool(record.get("capture"))
        for group in (native, ladder, flat, plain, dsl)
        for record in group
    )
    metadata["provider_calls_failed"] = (
        metadata["provider_calls_attempted"] - metadata["provider_calls_succeeded"]
    )
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    (run_dir / "provider_errors.json").write_text(
        json.dumps(
            [
                {
                    "question_id": record["question_id"],
                    "representation": record["representation"],
                    "failure_class": _native_classification(record),
                }
                for record in native
            ]
            + [
                {
                    "question_id": record["question_id"],
                    "representation": record["representation"],
                    "failure_class": record.get("failure_class"),
                    "error": record.get("error"),
                }
                for record in ladder + flat + plain + dsl
                if record.get("failure_class")
            ],
            indent=2,
        )
        + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze(args.run_dir), indent=2))


if __name__ == "__main__":
    main()
