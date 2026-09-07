from dataclasses import dataclass

from evaluation.m23_result_shape_ab_experiment import (
    KNOWN_DEV,
    _proposal_quality,
    _semantic_drift,
    select_primary,
)


@dataclass(frozen=True)
class FakeQuestion:
    index: int
    question: str
    evidence: str = ""
    db_id: str = "bird_db"
    dataset: str = "classic"
    db_name: str = "classic_db"
    instructions: str = ""
    gold_sql: str = "SELECT id FROM t"


def _datasets() -> dict[str, list[FakeQuestion]]:
    return {
        "bird": [
            FakeQuestion(index=i, question=f"List the name and value for item {i}")
            for i in range(40)
        ],
        "defog_classic": [
            FakeQuestion(
                index=i,
                question=f"What is the total number and name for item {i}",
                dataset="classic",
            )
            for i in range(40)
        ],
    }


def test_primary_selection_is_deterministic_and_excludes_discovery_cases() -> None:
    datasets = _datasets()
    first = select_primary(datasets)
    second = select_primary(datasets)

    assert first == second
    assert len(first) == 30
    assert sum(row["label"] == "bird" for row in first) == 15
    assert sum(row["label"] == "defog_classic" for row in first) == 15
    assert not {row["case_id"] for row in first} & KNOWN_DEV


def test_result_shape_quality_is_not_based_on_validator_acceptance_only() -> None:
    class Output:
        kind = type("Kind", (), {"value": "PHYSICAL_COLUMN"})()
        source_hint = "t.id"

    class Proposal:
        outputs = (Output(),)

    assert _proposal_quality(Proposal(), "SELECT t.id FROM t") == "PROPOSAL_CORRECT"


def test_non_projection_drift_is_deterministic() -> None:
    first = _semantic_drift("SELECT id FROM items", "SELECT name FROM items")
    second = _semantic_drift("SELECT id FROM items", "SELECT name FROM items")
    assert first == second
    assert first["table"] is False
