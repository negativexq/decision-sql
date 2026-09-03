"""Zero-provider validation of the frozen M2.12 gold compiler ceiling."""

import json
from pathlib import Path

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR, validate_window_ir
from app.sql.models import CandidateSource, QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService
from evaluation.metrics import assess_query_results

ROOT = Path(__file__).resolve().parents[1]


def validate(path: Path) -> dict[str, int]:
    settings = get_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    compiler = WindowSqlCompiler(safety.catalog)
    rows = json.loads(path.read_text())
    for row in rows:
        gold_plan = safety.plan(SqlCandidate(sql=row["gold_sql"]))
        if not isinstance(gold_plan, QueryPlan):
            raise RuntimeError(f"gold SQL plan failed: {row['id']}")
        gold_execution = safety.execute(gold_plan)
        if not isinstance(gold_execution, QueryExecution):
            raise RuntimeError(f"gold SQL execution failed: {row['id']}")
        gold_ir = WindowQueryIR.model_validate(row["gold_window_ir"])
        validate_window_ir(gold_ir, safety.catalog)
        compiled = compiler.compile(gold_ir)
        plan = safety.plan(SqlCandidate(sql=compiled, source=CandidateSource.WINDOW_COMPILER))
        if not isinstance(plan, QueryPlan):
            raise RuntimeError(f"compiled plan failed: {row['id']}")
        execution = safety.execute(plan)
        if not isinstance(execution, QueryExecution):
            raise RuntimeError(f"compiled execution failed: {row['id']}")
        if not assess_query_results(execution, gold_execution).equivalent:
            raise RuntimeError(f"compiled result mismatch: {row['id']}")
    return {"questions": len(rows), "validated": len(rows)}


def main() -> None:
    for name in ("m212_window_dev.json", "m212_window_holdout.json"):
        result = validate(ROOT / "evaluation/datasets" / name)
        print(name, f"{result['validated']}/{result['questions']}")


if __name__ == "__main__":
    main()
