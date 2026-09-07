import json
from pathlib import Path

from evaluation.external.livesqlbench.benchmark import failure_ledger_record
from evaluation.external.livesqlbench.evaluator import LiveSqlBenchResult, soft_ex_match
from evaluation.external.livesqlbench.loader import (
    load_dataset,
    stable_json_hash,
    validate_database_assets,
)
from evaluation.external.livesqlbench.schema import (
    LiveSqlBenchColumn,
    LiveSqlBenchDatabase,
    LiveSqlBenchTable,
    render_schema_context,
)


def _row(instance_id: str, category: str = "Query") -> dict[str, object]:
    return {
        "instance_id": instance_id,
        "selected_database": "demo",
        "query": "List names",
        "preprocess_sql": [],
        "clean_up_sqls": [],
        "sol_sql": [],
        "external_knowledge": [],
        "test_cases": [],
        "category": category,
        "high_level": False,
        "conditions": {"order": False, "decimal": [], "distinct": False},
        "difficulty_tier": "Simple",
    }


def test_loader_preserves_upstream_fields_and_separates_runtime_payload(tmp_path: Path) -> None:
    dataset_path = tmp_path / "livesqlbench_data.jsonl"
    dataset_path.write_text(
        "\n".join(json.dumps(row) for row in (_row("q1"), _row("m1", "Management"))) + "\n",
        encoding="utf-8",
    )
    dataset = load_dataset(dataset_path)

    assert len(dataset.cases) == 2
    assert len(dataset.eligible_select_cases) == 1
    assert len(dataset.management_cases) == 1
    assert "sol_sql" not in dataset.cases[0].runtime_payload
    assert "test_cases" not in dataset.cases[0].runtime_payload


def test_database_asset_validation_is_fail_closed(tmp_path: Path) -> None:
    for suffix in ("schema.txt", "kb.jsonl", "column_meaning_base.json"):
        path = tmp_path / "demo" / f"demo_{suffix}"
        path.parent.mkdir(exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    assert validate_database_assets(tmp_path, ("demo",)) == []
    assert validate_database_assets(tmp_path, ("missing",))


def test_schema_rendering_is_deterministic_and_explicit() -> None:
    database = LiveSqlBenchDatabase(
        name="demo",
        server_version="14",
        primary_key_count=1,
        foreign_key_count=1,
        tables=(
            LiveSqlBenchTable(
                schema="public",
                name="orders",
                columns=(
                    LiveSqlBenchColumn(
                        schema="public",
                        table="orders",
                        name="customer_id",
                        data_type="integer",
                        nullable=False,
                        ordinal=1,
                        primary_key=False,
                        foreign_key_target=("public", "customers", "id"),
                    ),
                ),
            ),
        ),
    )
    first = render_schema_context(database)
    second = render_schema_context(database)
    assert first == second
    assert "FOREIGN KEY public.orders.customer_id REFERENCES public.customers.id" in first
    assert stable_json_hash(first) == stable_json_hash(second)


def test_query_soft_ex_known_good_and_wrong_controls() -> None:
    expected = LiveSqlBenchResult(("name",), (("Ada",), ("Linus",)))
    same_rows_different_order = LiveSqlBenchResult(("name",), (("Linus",), ("Ada",)))
    wrong = LiveSqlBenchResult(("name",), (("Ada",), ("Grace",)))

    assert soft_ex_match(expected, expected, ordered=True)
    assert soft_ex_match(same_rows_different_order, expected, ordered=False)
    assert not soft_ex_match(wrong, expected, ordered=False)


def test_failure_ledger_keeps_reference_offline_and_runtime_separate(tmp_path: Path) -> None:
    dataset_path = tmp_path / "livesqlbench_data.jsonl"
    dataset_path.write_text(json.dumps(_row("q1")) + "\n", encoding="utf-8")
    case = load_dataset(dataset_path).cases[0]
    record = failure_ledger_record(case, generated_sql="SELECT 1", m1_status="ALLOWED")

    assert record["generated_sql"] == "SELECT 1"
    assert record["reference_sql_offline"] is None
    assert "reference_sql_offline" not in case.runtime_payload
