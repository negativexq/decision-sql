"""Provider-free M2.12 gold IR/compiler/SQL/M1 validation."""

import json
from pathlib import Path

from app.config import get_settings
from app.db.session import build_reader_engine
from app.generation.window_compiler import WindowSqlCompiler
from app.generation.window_ir import WindowQueryIR, validate_window_ir
from app.sql.models import QueryExecution, QueryPlan, SqlCandidate
from app.sql.service import SqlSafetyService

ROOT = Path(__file__).resolve().parents[1]


def validate_dataset(path: Path, safety: SqlSafetyService) -> dict[str, int | str]:
    rows = json.loads(path.read_text())
    compiler = WindowSqlCompiler(safety.catalog)
    ir_valid = compiled = plan_ok = execution_ok = equivalent = 0
    for row in rows:
        try:
            ir = WindowQueryIR.model_validate(row["gold_window_ir"])
            validate_window_ir(ir, safety.catalog)
            compiled_sql = compiler.compile(ir)
            ir_valid += 1
            if compiled_sql == row["gold_sql"]:
                compiled += 1
            plan = safety.plan(SqlCandidate(sql=compiled_sql))
            if not isinstance(plan, QueryPlan):
                continue
            plan_ok += 1
            result = safety.execute(plan)
            if not isinstance(result, QueryExecution):
                continue
            execution_ok += 1
            gold_plan = safety.plan(SqlCandidate(sql=row["gold_sql"]))
            if isinstance(gold_plan, QueryPlan):
                gold_result = safety.execute(gold_plan)
                if isinstance(gold_result, QueryExecution):
                    equivalent += int(
                        result.columns == gold_result.columns and result.rows == gold_result.rows
                    )
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "dataset": path.name,
        "cases": len(rows),
        "ir_validation_success": ir_valid,
        "compiler_success": compiled,
        "m1_plan_success": plan_ok,
        "execution_success": execution_ok,
        "compiled_result_equivalence": equivalent,
    }


def main() -> None:
    settings = get_settings()
    engine = build_reader_engine(settings)
    safety = SqlSafetyService(engine, settings=settings)
    results = [
        validate_dataset(ROOT / "evaluation/datasets/m212_window_dev.json", safety),
        validate_dataset(ROOT / "evaluation/datasets/m212_window_holdout.json", safety),
    ]
    print(json.dumps({"dev": results[0], "holdout": results[1]}, indent=2))
    if any(
        result[key] != result["cases"]
        for result in results
        for key in (
            "ir_validation_success",
            "compiler_success",
            "m1_plan_success",
            "execution_success",
            "compiled_result_equivalence",
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
