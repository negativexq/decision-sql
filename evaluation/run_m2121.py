"""M2.12.1 provider-boundary compatibility audit.

This module measures transport reliability only.  It never sends gold IR and
never falls back to SQL generation.
"""

import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.config import get_settings
from app.generation.provider import (
    LLMProviderError,
    MalformedProviderResponse,
    OpenAICompatibleProvider,
)
from app.generation.window_ir import WindowQueryIR, validate_window_ir
from app.generation.window_provider_adapter import ProviderWindowIRAdapter
from app.generation.window_provider_dto import (
    ProviderWindowIRDTO,
)
from app.retrieval.context import SchemaContextMode, SchemaContextResolver, serialize_schema_context

ROOT = Path(__file__).resolve().parents[1]
DEV_PATH = ROOT / "evaluation/datasets/m212_window_dev.json"
MANIFEST_PATH = ROOT / "evaluation/datasets/m2121_provider_audit_manifest.json"


class LadderPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Literal["LAG", "RANKING"]


class LadderSource(LadderPattern):
    source_relation: str


class LadderOutputs(LadderSource):
    physical_outputs: tuple[str, ...]


class FlatLag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Literal["LAG"]
    source_relation: str
    physical_outputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    target: str
    partition_by: tuple[str, ...] = ()
    order_column: str
    order_direction: Literal["ASC", "DESC"]
    offset: int = Field(ge=1, le=100)
    alias: str


class FlatRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: Literal["RANKING"]
    source_relation: str
    physical_outputs: tuple[str, ...] = Field(min_length=1, max_length=16)
    partition_by: tuple[str, ...] = ()
    order_column: str
    order_direction: Literal["ASC", "DESC"]
    ranking_function: Literal["ROW_NUMBER", "RANK", "DENSE_RANK"]
    alias: str


LADDER_SCHEMAS: dict[int, type[BaseModel]] = {
    0: LadderPattern,
    1: LadderSource,
    2: LadderOutputs,
    3: FlatLag,
    4: FlatRanking,
    5: ProviderWindowIRDTO,
}
L6_ADAPTER: TypeAdapter[FlatLag | FlatRanking] = TypeAdapter(
    # The small discriminated union is intentionally tested only after flat levels.
    Annotated[FlatLag | FlatRanking, Field(discriminator="pattern")]
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _load_rows() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], json.loads(DEV_PATH.read_text()))


def _load_audit_rows() -> list[dict[str, Any]]:
    rows = {row["id"]: row for row in _load_rows()}
    manifest = json.loads(MANIFEST_PATH.read_text())
    return [rows[row_id] for row_id in manifest["question_ids"]]


def _settings() -> Any:
    return get_settings().model_copy(
        update={
            "llm_model": "gpt-5.6-luna",
            "llm_reasoning_effort": "none",
            "llm_temperature": None,
            "eval_capture_model_io": True,
        }
    )


def _schema_inventory() -> dict[str, Any]:
    from app.generation.window_ir import WindowQueryIR

    schemas: dict[str, Any] = {
        "WindowQueryIR": WindowQueryIR.model_json_schema(),
        "ProviderWindowIRDTO": ProviderWindowIRDTO.model_json_schema(),
    }
    for level, schema_type in LADDER_SCHEMAS.items():
        schemas[f"level_{level}"] = schema_type.model_json_schema()
    schemas["level_6_union"] = L6_ADAPTER.json_schema()
    features: dict[str, Any] = {}
    for name, schema in schemas.items():
        serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
        features[name] = {
            "sha256": _sha(serialized),
            "bytes": len(serialized),
            "oneOf": _contains_key(schema, "oneOf"),
            "anyOf": _contains_key(schema, "anyOf"),
            "discriminators": _contains_key(schema, "discriminator"),
            "nested_unions": _contains_nested_union(schema),
            "union_arrays": _contains_union_array(schema),
            "nullable": _contains_key(schema, "nullable"),
            "optional_fields": _optional_field_count(schema),
            "nested_enums": _nested_enum_count(schema),
            "additional_properties_constraints": _contains_key(schema, "additionalProperties"),
            "$defs": len(schema.get("$defs", {})) if isinstance(schema, dict) else 0,
            "schema": schema,
        }
    return {"schemas": features}


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _contains_nested_union(schema: Any) -> bool:
    return bool(isinstance(schema, dict) and schema.get("$defs") and _contains_key(schema, "oneOf"))


def _contains_union_array(schema: Any) -> bool:
    if isinstance(schema, dict):
        items = schema.get("items")
        if isinstance(items, dict) and ("oneOf" in items or "anyOf" in items):
            return True
        return any(_contains_union_array(value) for value in schema.values())
    if isinstance(schema, list):
        return any(_contains_union_array(value) for value in schema)
    return False


def _optional_field_count(schema: Any) -> int:
    if not isinstance(schema, dict):
        return 0
    count = len(schema.get("properties", {})) - len(schema.get("required", []))
    return max(0, count) + sum(_optional_field_count(value) for value in schema.values())


def _nested_enum_count(schema: Any) -> int:
    if isinstance(schema, dict):
        own = int("enum" in schema)
        return own + sum(_nested_enum_count(value) for value in schema.values())
    if isinstance(schema, list):
        return sum(_nested_enum_count(value) for value in schema)
    return 0


def _schema_prompt(name: str, schema: dict[str, Any], representation: str) -> str:
    return (
        f"Map the question to exactly one {name} object. Representation: {representation}. "
        "Return only the object, no markdown, no explanation, no SQL and no SQL fragments. "
        f"The allowed JSON schema is:\n{json.dumps(schema, sort_keys=True)}"
    )


def _classify_error(error: Exception, content: str | None) -> str:
    if isinstance(error, LLMProviderError):
        if error.detail is not None:
            if error.detail.status_code == 400 and error.detail.error_code in {
                "unsupported_value",
                "invalid_json_schema",
            }:
                return "REQUEST_SCHEMA_REJECTED"
            return "PROVIDER_HTTP_ERROR"
        if isinstance(error, MalformedProviderResponse):
            if content is None:
                return "OTHER_SANITIZED_PROVIDER_ERROR"
            try:
                json.loads(content)
            except json.JSONDecodeError:
                return "PROVIDER_RETURNED_NON_JSON"
            return "SCHEMA_VALIDATION_FAILED"
        return "OTHER_SANITIZED_PROVIDER_ERROR"
    return "OTHER_SANITIZED_PROVIDER_ERROR"


def _capture(provider: OpenAICompatibleProvider) -> dict[str, Any] | None:
    capture = provider.consume_model_io()
    if capture is None:
        return None
    value = capture.model_dump(mode="json")
    if isinstance(value.get("raw_assistant_content"), str):
        value["raw_assistant_content"] = _sanitize_content(value["raw_assistant_content"])
    return value


def _sanitize_content(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", value)
    value = re.sub(r"(?i)(api[_ -]?key|authorization)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    return value[:12000]


def _transport_record(
    row: dict[str, Any], name: str, attempt: int, capture: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "question_id": row["id"],
        "category": row["category"],
        "gold_pattern": row["pattern"],
        "representation": name,
        "attempt": attempt,
        "question_hash": _sha(row["question"]),
        "capture": capture,
        "status": "failed",
        "failure_class": None,
        "raw_content": capture.get("raw_assistant_content") if capture else None,
        "parsed": None,
        "internal_ir": None,
        "semantic_match": None,
    }


def _parse_json(content: str | None) -> dict[str, Any]:
    if not content:
        raise ValueError("provider returned empty content")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("provider returned a non-object JSON value")
    return value


def _parse_level(level: int, content: str | None) -> Any:
    data = _parse_json(content)
    if level == 6:
        return L6_ADAPTER.validate_python(data)
    return LADDER_SCHEMAS[level].model_validate(data)


def _flat_to_dto(value: Any) -> ProviderWindowIRDTO:
    if isinstance(value, (FlatLag, FlatRanking)):
        data = value.model_dump(mode="json")
        data["order_by"] = [
            {"column": data.pop("order_column"), "direction": data.pop("order_direction")}
        ]
        return ProviderWindowIRDTO.model_validate(data)
    return cast(ProviderWindowIRDTO, value)


def _semantic_matches(candidate: WindowQueryIR, gold: WindowQueryIR) -> dict[str, bool]:
    computation = candidate.computations[0]
    expected = gold.computations[0]
    result = {
        "pattern": candidate.pattern == gold.pattern,
        "source_relation": candidate.source_relation == gold.source_relation,
        "physical_outputs": set(candidate.physical_outputs) == set(gold.physical_outputs),
    }
    for name in (
        "target",
        "partition_by",
        "order_by",
        "offset",
        "n",
        "frame",
        "function",
        "aggregate",
        "scale",
        "alias",
    ):
        if hasattr(expected, name):
            left = getattr(computation, name, None)
            right = getattr(expected, name, None)
            result[name] = left == right
    return result


async def _call_transport(
    provider: OpenAICompatibleProvider,
    row: dict[str, Any],
    schema: dict[str, Any],
    name: str,
    instruction: str,
    response_format: dict[str, str] | None,
    attempt: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    capture = None
    record = _transport_record(row, name, attempt, None)
    try:
        proposal = await provider.propose_window_transport(
            row["question"],
            serialize_schema_context(
                SchemaContextResolver(provider_settings_catalog()).resolve(
                    row["question"], SchemaContextMode.FULL_COMPACT
                )
            ),
            _schema_prompt(name, schema, instruction),
            response_format=response_format,
            operation=name,
        )
        capture = _capture(provider)
        record["capture"] = capture
        record["raw_content"] = _sanitize_content(proposal.content or "")
        record["provider_succeeded"] = True
        return record, capture
    except Exception as error:
        capture = _capture(provider)
        record["capture"] = capture
        record["failure_class"] = _classify_error(
            error, capture.get("raw_assistant_content") if capture else None
        )
        record["error"] = type(error).__name__
        if isinstance(error, LLMProviderError) and error.detail is not None:
            record["provider_error"] = error.detail.model_dump(mode="json")
        return record, capture


def provider_settings_catalog() -> Any:
    # The provider owns settings, while catalog construction remains server-owned.
    from app.catalog.default import build_default_catalog
    from app.db.models import Base

    return build_default_catalog(Base.metadata)


async def _run_audit(run_dir: Path) -> None:
    rows = _load_audit_rows()
    settings = _settings()
    provider = OpenAICompatibleProvider(settings)
    schema_context = serialize_schema_context(
        SchemaContextResolver(provider_settings_catalog()).resolve(
            rows[0]["question"], SchemaContextMode.FULL_COMPACT
        )
    )
    inventory = _schema_inventory()
    _write(run_dir / "schema_feature_inventory.json", inventory)
    _write(
        run_dir / "schema_hashes.json",
        {name: details["sha256"] for name, details in inventory["schemas"].items()},
    )
    current_native: list[dict[str, Any]] = []
    flat: list[dict[str, Any]] = []
    plain: list[dict[str, Any]] = []
    dsl: list[dict[str, Any]] = []
    ladder: list[dict[str, Any]] = []
    io_records: list[Any] = []

    native_rows = [rows[2], rows[7], rows[5]]
    for index, row in enumerate(native_rows, 1):
        record = _transport_record(row, "CURRENT_NATIVE_WINDOW_IR", index, None)
        try:
            started = perf_counter()
            proposal = await provider.propose_window_ir(row["question"], schema_context)
            record["provider_succeeded"] = True
            record["parsed"] = proposal.ir.model_dump(mode="json")
            validate_window_ir(proposal.ir, provider_settings_catalog())
            record["internal_ir"] = proposal.ir.model_dump(mode="json")
            record["semantic_match"] = _semantic_matches(
                proposal.ir, WindowQueryIR.model_validate(row["gold_window_ir"])
            )
            record["status"] = "transport_success"
            record["latency_ms_local_wrapper"] = (perf_counter() - started) * 1000
        except Exception as error:
            record["failure_class"] = _classify_error(error, None)
            record["error"] = type(error).__name__
            if isinstance(error, LLMProviderError) and error.detail is not None:
                record["provider_error"] = error.detail.model_dump(mode="json")
        capture = _capture(provider)
        record["capture"] = capture
        if capture is not None:
            io_records.append(capture)
        current_native.append(record)

    level_rows: dict[int, list[dict[str, Any]]] = {
        0: [rows[2], rows[7], rows[5]],
        1: [rows[2], rows[7], rows[5]],
        2: [rows[2], rows[7], rows[5]],
        3: [rows[2], rows[8], rows[10]],
        4: [rows[7], rows[8], rows[10]],
        5: [rows[2], rows[7], rows[5]],
        6: [rows[2], rows[7], rows[8]],
    }
    for level, selected in level_rows.items():
        schema = (
            inventory["schemas"][f"level_{level}"]["schema"]
            if level < 6
            else inventory["schemas"]["level_6_union"]["schema"]
        )
        for index, row in enumerate(selected, 1):
            record, capture = await _call_transport(
                provider,
                row,
                schema,
                f"ladder_level_{level}",
                "Use the exact fields in this compact ladder schema.",
                {"type": "json_object"},
                index,
            )
            try:
                value = _parse_level(level, record.get("raw_content"))
                record["parsed"] = value.model_dump(mode="json")
                if level >= 5:
                    dto = _flat_to_dto(value)
                    adapter = ProviderWindowIRAdapter(provider_settings_catalog())
                    ir = adapter.convert(dto)
                    record["internal_ir"] = ir.model_dump(mode="json")
                    record["semantic_match"] = _semantic_matches(
                        ir, WindowQueryIR.model_validate(row["gold_window_ir"])
                    )
                    record["status"] = "transport_success"
                else:
                    record["status"] = "schema_parse_success"
            except Exception as error:
                record["failure_class"] = (
                    "SCHEMA_VALIDATION_FAILED"
                    if record.get("raw_content")
                    else "PROVIDER_RETURNED_NON_JSON"
                )
                record["error"] = type(error).__name__
            ladder.append(record)
            if capture is not None:
                io_records.append(capture)

    flat_schema = inventory["schemas"]["ProviderWindowIRDTO"]["schema"]
    for index, row in enumerate(rows, 1):
        record, capture = await _call_transport(
            provider,
            row,
            flat_schema,
            "FLAT_PROVIDER_DTO",
            "Use the flat ProviderWindowIRDTO fields.",
            {"type": "json_object"},
            index,
        )
        try:
            dto = ProviderWindowIRDTO.model_validate(_parse_json(record.get("raw_content")))
            record["parsed"] = dto.model_dump(mode="json")
            ir = ProviderWindowIRAdapter(provider_settings_catalog()).convert(dto)
            record["internal_ir"] = ir.model_dump(mode="json")
            record["semantic_match"] = _semantic_matches(
                ir, WindowQueryIR.model_validate(row["gold_window_ir"])
            )
            record["status"] = "transport_success"
        except Exception as error:
            record["failure_class"] = "SCHEMA_VALIDATION_FAILED"
            record["error"] = type(error).__name__
        flat.append(record)
        if capture is not None:
            io_records.append(capture)

    for name, response_format, target in (
        ("PLAIN_JSON_TEXT", None, plain),
        ("WINDOW_DSL", None, dsl),
    ):
        for index, row in enumerate(rows, 1):
            schema = (
                flat_schema
                if name == "PLAIN_JSON_TEXT"
                else {
                    "grammar": (
                        "WINDOW\\nkey=value lines; keys are pattern, source, outputs, "
                        "partition, order, target, offset, n, tie_policy, aggregate, "
                        "ranking_function, frame_mode, frame_start_kind, "
                        "frame_start_value, frame_end_kind, frame_end_value, scale, alias"
                    )
                }
            )
            instruction = (
                "Return one JSON object matching the flat DTO."
                if name == "PLAIN_JSON_TEXT"
                else "Return exactly one WINDOW DSL block using key=value lines and no explanation."
            )
            record, capture = await _call_transport(
                provider, row, schema, name, instruction, response_format, index
            )
            try:
                if name == "PLAIN_JSON_TEXT":
                    dto = ProviderWindowIRDTO.model_validate(_parse_json(record.get("raw_content")))
                else:
                    from app.generation.window_dsl import parse_window_dsl

                    dto = parse_window_dsl(record.get("raw_content") or "")
                record["parsed"] = dto.model_dump(mode="json")
                ir = ProviderWindowIRAdapter(provider_settings_catalog()).convert(dto)
                record["internal_ir"] = ir.model_dump(mode="json")
                record["semantic_match"] = _semantic_matches(
                    ir, WindowQueryIR.model_validate(row["gold_window_ir"])
                )
                record["status"] = "transport_success"
            except Exception as error:
                record["failure_class"] = "SCHEMA_VALIDATION_FAILED"
                record["error"] = type(error).__name__
            target.append(record)
            if capture is not None:
                io_records.append(capture)

    _write(run_dir / "native_structured_results.json", current_native)
    _write(run_dir / "schema_ladder.json", ladder)
    _write(run_dir / "flat_dto_results.json", flat)
    _write(run_dir / "plain_json_results.json", plain)
    _write(run_dir / "dsl_results.json", dsl)
    _write(
        run_dir / "provider_errors.json",
        _provider_errors(current_native + ladder + flat + plain + dsl),
    )
    _write(
        run_dir / "transport_comparison.json",
        _transport_summary(current_native, ladder, flat, plain, dsl),
    )
    _write(run_dir / "semantic_diagnostic.json", _semantic_summary(ladder + flat + plain + dsl))
    _write_jsonl(run_dir / "model_io.jsonl", io_records)
    _write(
        run_dir / "metadata.json",
        {
            "milestone": "M2.12.1",
            "run_id": run_dir.name,
            "source_dataset_sha256": _sha(DEV_PATH.read_text()),
            "question_ids": [row["id"] for row in rows],
            "model": "gpt-5.6-luna",
            "reasoning_effort": "none",
            "temperature": None,
            "sampling_mode": "provider_default",
            "endpoint_family": "chat_completions",
            "total_provider_calls": 60,
            "holdout_used": False,
            "m212_holdout_sha256": (
                "e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4"
            ),
        },
    )
    print(json.dumps(_transport_summary(current_native, ladder, flat, plain, dsl), indent=2))


def _provider_errors(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "question_id": record["question_id"],
            "representation": record["representation"],
            "failure_class": record.get("failure_class"),
            "provider_error": record.get("provider_error"),
            "error": record.get("error"),
        }
        for record in records
        if record.get("failure_class") or record.get("provider_error")
    ]


def _transport_summary(*groups: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for group in groups:
        if not group:
            continue
        name = group[0]["representation"]
        result[name] = {
            "attempted": len(group),
            "provider_accepted": sum(record.get("provider_succeeded") is True for record in group),
            "responses_parsed": sum(
                record.get("status") in {"schema_parse_success", "transport_success"}
                for record in group
            ),
            "internal_ir_validation": sum(
                record.get("status") == "transport_success" for record in group
            ),
            "transport_success": sum(
                record.get("status") == "transport_success" for record in group
            ),
            "failure_classes": dict(
                Counter(
                    record.get("failure_class") for record in group if record.get("failure_class")
                )
            ),
        }
    return result


def _semantic_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record.get("status") == "transport_success"]
    if not successful:
        return {"transported_records": 0, "field_accuracy": {}}
    fields = (
        "pattern",
        "source_relation",
        "physical_outputs",
        "target",
        "partition_by",
        "order_by",
        "offset",
        "n",
        "frame",
        "function",
        "aggregate",
        "scale",
        "alias",
    )
    return {
        "transported_records": len(successful),
        "field_accuracy": {
            field: sum(bool(record.get("semantic_match", {}).get(field)) for record in successful)
            / len(successful)
            for field in fields
            if any(field in record.get("semantic_match", {}) for record in successful)
        },
    }


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def _write_jsonl(path: Path, values: list[Any]) -> None:
    path.write_text("".join(json.dumps(value, default=str) + "\n" for value in values))


def classify_historical() -> dict[str, Any]:
    path = ROOT / "evaluation/results/m212/dev/20260903T025751Z/window_ir.json"
    rows = json.loads(path.read_text())
    return {
        "source": str(path.relative_to(ROOT)),
        "records": len(rows),
        "classification": "UNDETERMINABLE_FROM_PERSISTED_ARTIFACT",
        "counts": {"UNDETERMINABLE_FROM_PERSISTED_ARTIFACT": len(rows)},
        "reason": (
            "The historical capture contains neither raw assistant content nor "
            "sanitized provider error detail for the pre-parse failures."
        ),
        "request_rejected_before_inference": "not determinable",
        "inference_occurred_but_output_unparsed": "not determinable",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--historical-only", action="store_true")
    args = parser.parse_args()
    if args.historical_only:
        print(json.dumps(classify_historical(), indent=2))
        return
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "evaluation/results/m2121" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write(run_dir / "historical_failure_classification.json", classify_historical())
    asyncio.run(_run_audit(run_dir))


if __name__ == "__main__":
    main()
