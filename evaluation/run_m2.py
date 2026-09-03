import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from app.catalog.default import build_default_catalog
from app.config import get_settings
from app.db.models import Base
from app.db.session import build_reader_engine
from app.generation.provider import OpenAICompatibleProvider
from app.retrieval.context import SchemaContextMode, SchemaContextResolver
from app.sql.service import SqlSafetyService
from app.text_to_sql.service import TextToSqlService
from evaluation.persist import persist_m2_run
from evaluation.runner import evaluate_ablation, evaluate_baseline, load_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the explicit M2 baseline evaluation")
    parser.add_argument(
        "--mode",
        choices=("retrieved", "full", "ablation"),
        default="retrieved",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/m2_baseline.json"),
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=Path("evaluation/results/m2"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/m2-baseline.md"),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    cases = load_baseline(args.dataset)
    catalog = build_default_catalog(Base.metadata)

    def build_service(mode: SchemaContextMode) -> TextToSqlService:
        resolver = SchemaContextResolver(
            catalog,
            top_k=settings.schema_top_k,
            max_tables=settings.max_context_tables,
            max_columns_per_table=settings.max_columns_per_table,
            relationship_depth=settings.relationship_depth,
        )
        safety = SqlSafetyService(build_reader_engine(settings), settings=settings, catalog=catalog)
        return TextToSqlService(
            resolver,
            OpenAICompatibleProvider(settings),
            safety,
            context_mode=mode,
        )

    if args.mode == "ablation":
        reports = asyncio.run(evaluate_ablation(cases, build_service))
        run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        metadata = {
            "run_id": run_id,
            "evaluation_timestamp": datetime.now(UTC).isoformat(),
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "generation_settings": {
                "temperature": 0,
                "response_format": "json_object",
                "dialect": "postgres",
            },
            "dataset_path": str(args.dataset),
            "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "question_count": len(cases),
            "real_provider_call_count": sum(report.total_cases for report in reports.values()),
            "context_settings": {
                "schema_top_k": settings.schema_top_k,
                "max_context_tables": settings.max_context_tables,
                "max_columns_per_table": settings.max_columns_per_table,
                "relationship_depth": settings.relationship_depth,
            },
            "persistence_note": "No credentials or result rows are stored in this report.",
        }
        output_dir = args.results_root / run_id
        persist_m2_run(output_dir, metadata, reports, args.report)
        payload = {
            "run_id": run_id,
            "output_dir": str(output_dir),
            "report": str(args.report),
            "reports": {
                key: value.model_dump(mode="json") for key, value in reports.items()
            },
        }
        if args.quiet:
            print(json.dumps({key: payload[key] for key in ("run_id", "output_dir", "report")}))
        else:
            print(json.dumps(payload, indent=2))
        return
    report = asyncio.run(evaluate_baseline(cases, build_service(SchemaContextMode(args.mode))))
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
