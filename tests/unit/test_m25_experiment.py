import inspect

from app.retrieval.context import SchemaContextMode
from app.text_to_sql.models import GenerationMode
from app.text_to_sql.service import TextToSqlService
from evaluation.run_m25 import _experiment_cells


def test_primary_experiment_is_full_context_grounding_pair() -> None:
    cells = _experiment_cells("primary", None)

    assert [cell[0] for cell in cells] == [
        "full_compact_one_shot",
        "full_compact_grounded",
    ]
    assert all(cell[1] is SchemaContextMode.FULL_COMPACT for cell in cells)
    assert [cell[2] for cell in cells] == [GenerationMode.ONE_SHOT, GenerationMode.GROUNDED]


def test_full_experiment_has_four_orthogonal_cells() -> None:
    cells = _experiment_cells("full", None)

    assert [cell[0] for cell in cells] == [
        "full_compact_one_shot",
        "full_compact_grounded",
        "retrieved_bounded_one_shot",
        "retrieved_bounded_grounded",
    ]


def test_generation_runtime_does_not_depend_on_evaluation_dataset() -> None:
    source = inspect.getsource(TextToSqlService)
    assert "evaluation.datasets" not in source
    assert "m25_holdout" not in source
