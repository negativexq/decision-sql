"""Run the fixed M3.4 routing validation corpus."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from app.config import GovernedMetricsMode, Settings, get_settings
from app.db.session import build_reader_engine
from app.generation.provider import (
    GovernedMetricGroundingProposal,
    LLMProvider,
    OpenAICompatibleProvider,
)
from app.models.domain import TextToSqlRequest
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.semantics.catalog import build_m3_catalog
from app.semantics.routing import GovernedMetricRouteService
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.m34_models import M34RoutingCase


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode", choices=[mode.value for mode in GovernedMetricsMode], default="on"
    )
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument(
        "--fixture", type=Path, default=Path("evaluation/fixtures/m34_routing.json")
    )
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"llm_temperature": None})
    if not settings.llm_api_key:
        raise SystemExit("M3.4 requires a provider credential; no artifact was created")
    cases = load_cases(args.fixture)
    if len(cases) != 40:
        raise SystemExit(f"M3.4 routing corpus must contain 40 cases, found {len(cases)}")
    artifact_dir = args.artifact_dir or _new_artifact_dir()
    artifact_dir.mkdir(parents=True, exist_ok=False)
    asyncio.run(run(settings, cases, GovernedMetricsMode(args.mode), args.fixture, artifact_dir))


def load_cases(path: Path) -> tuple[M34RoutingCase, ...]:
    raw = json.loads(path.read_text())
    cases = tuple(M34RoutingCase.model_validate(item) for item in raw)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("M3.4 routing case IDs must be unique")
    governed = sum(case.expected_path == "GOVERNED_METRIC" for case in cases)
    direct = sum(case.expected_path == "DIRECT_SQL" for case in cases)
    if governed != 20 or direct != 20:
        raise ValueError("M3.4 routing corpus must contain 20 governed and 20 direct cases")
    return cases


class RecordingGroundingProvider:
    def __init__(self, delegate: LLMProvider) -> None:
        self.delegate = delegate
        self.proposals: list[GovernedMetricGroundingProposal] = []

    async def propose_metric_grounding(
        self, question: str, glossary: str
    ) -> GovernedMetricGroundingProposal:
        proposal = await self.delegate.propose_metric_grounding(question, glossary)
        self.proposals.append(proposal)
        return proposal


async def run(
    settings: Settings,
    cases: tuple[M34RoutingCase, ...],
    mode: GovernedMetricsMode,
    fixture: Path,
    artifact_dir: Path,
) -> None:
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    direct_provider = OpenAICompatibleProvider(settings)
    grounding_provider = RecordingGroundingProvider(OpenAICompatibleProvider(settings))
    direct_service = TextToSqlService(
        SchemaContextResolver(
            safety.catalog,
            top_k=settings.schema_top_k,
            max_tables=settings.max_context_tables,
            max_columns_per_table=settings.max_columns_per_table,
            relationship_depth=settings.relationship_depth,
        ),
        direct_provider,
        safety,
        context_mode=SchemaContextMode.FULL_COMPACT,
    )
    route = GovernedMetricRouteService(
        direct_service,
        grounding_provider,
        safety,
        catalog=build_m3_catalog(safety.catalog),
        mode=mode,
        shadow_execute=settings.governed_metrics_shadow_execute,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        started = perf_counter()
        proposal_count_before = len(grounding_provider.proposals)
        decision = await route.run(TextToSqlRequest(question=case.question))
        grounding = (
            grounding_provider.proposals[-1]
            if len(grounding_provider.proposals) > proposal_count_before
            else None
        )
        direct_calls = (
            decision.user_result.provider_calls_attempted
            if decision.path.value != "GOVERNED_METRIC"
            else 0
        )
        rows.append(
            {
                "case_id": case.case_id,
                "expected_path": case.expected_path,
                "actual_path": decision.path.value,
                "route_status": decision.status.value,
                "route_correct": decision.path.value == case.expected_path,
                "expected_metric": case.metric_name,
                "actual_metric": decision.metric_name,
                "expected_dimensions": list(case.dimensions),
                "actual_dimensions": list(decision.dimensions),
                "metric_correct": decision.metric_name == case.metric_name
                if case.expected_path == "GOVERNED_METRIC"
                else None,
                "dimensions_correct": decision.dimensions == case.dimensions
                if case.expected_path == "GOVERNED_METRIC"
                else None,
                "fallback_reason": decision.fallback_reason.value
                if decision.fallback_reason
                else None,
                "grounding_applicable": grounding.grounding.applicable if grounding else None,
                "grounding_input_tokens": grounding.prompt_tokens if grounding else None,
                "grounding_output_tokens": grounding.completion_tokens if grounding else None,
                "grounding_latency_ms": grounding.latency_ms if grounding else None,
                "direct_provider_calls": direct_calls,
                "governed_provider_calls": 1,
                "total_latency_ms": (perf_counter() - started) * 1000,
                "shadow_comparison": decision.shadow_comparison.value
                if decision.shadow_comparison
                else None,
            }
        )
    write_artifacts(artifact_dir, fixture, settings, mode, cases, rows)
    print(json.dumps(summarize(rows), indent=2))


def write_artifacts(
    artifact_dir: Path,
    fixture: Path,
    settings: Settings,
    mode: GovernedMetricsMode,
    cases: tuple[M34RoutingCase, ...],
    rows: list[dict[str, Any]],
) -> None:
    fixture_hash = sha256(fixture.read_bytes()).hexdigest()
    metadata = {
        "milestone": "M3.4",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": mode.value,
        "shadow_execute": settings.governed_metrics_shadow_execute,
        "model": settings.llm_model,
        "reasoning": settings.llm_reasoning_effort,
        "temperature": settings.llm_temperature,
        "credential_present": bool(settings.llm_api_key),
        "provider_calls": sum(
            row["governed_provider_calls"] + row["direct_provider_calls"] for row in rows
        ),
        "routing_corpus_count": len(cases),
        "routing_corpus_sha256": fixture_hash,
    }
    (artifact_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (artifact_dir / "routing_manifest.json").write_text(
        json.dumps(
            {
                "fixture": str(fixture),
                "fixture_sha256": fixture_hash,
                "governed_expected": 20,
                "direct_expected": 20,
            },
            indent=2,
        )
        + "\n"
    )
    with (artifact_dir / "routing_results.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    (artifact_dir / "routing_summary.json").write_text(
        json.dumps(summarize(rows), indent=2) + "\n"
    )


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    governed = [row for row in rows if row["expected_path"] == "GOVERNED_METRIC"]
    direct = [row for row in rows if row["expected_path"] == "DIRECT_SQL"]
    true_positive = sum(row["actual_path"] == "GOVERNED_METRIC" for row in governed)
    false_positive = sum(
        row["actual_path"] == "GOVERNED_METRIC" for row in direct
    )
    actual_governed = sum(row["actual_path"] == "GOVERNED_METRIC" for row in rows)
    metric_correct = sum(row["metric_correct"] is True for row in governed)
    dimensions_correct = sum(row["dimensions_correct"] is True for row in governed)
    return {
        "total": len(rows),
        "route_correct": sum(row["route_correct"] for row in rows),
        "route_accuracy": sum(row["route_correct"] for row in rows) / len(rows),
        "governed_expected": len(governed),
        "direct_expected": len(direct),
        "actual_governed": actual_governed,
        "governed_precision": true_positive / actual_governed if actual_governed else 1.0,
        "governed_recall": true_positive / len(governed) if governed else 1.0,
        "metric_accuracy": metric_correct / len(governed) if governed else 1.0,
        "dimension_accuracy": dimensions_correct / len(governed) if governed else 1.0,
        "false_governed_route": false_positive,
        "false_direct_route": len(governed) - true_positive,
        "status_counts": _counts(rows, "route_status"),
        "fallback_counts": _counts(rows, "fallback_reason"),
        "path_counts": _counts(rows, "actual_path"),
        "shadow_comparison_counts": _counts(rows, "shadow_comparison"),
        "grounding_input_tokens": sum(row["grounding_input_tokens"] or 0 for row in rows),
        "grounding_output_tokens": sum(row["grounding_output_tokens"] or 0 for row in rows),
    }


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row[key] or "NONE"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _new_artifact_dir() -> Path:
    return Path("evaluation/results/m34") / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    main()
