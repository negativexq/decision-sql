from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "evaluation" / "postp3_intervention_review.py"


def _module():
    spec = importlib.util.spec_from_file_location("postp3_review", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_p3_matrix_and_stability() -> None:
    evidence = _module().frozen_evidence()["p3"]
    assert (evidence["A"], evidence["B"], evidence["C"], evidence["D"]) == (36, 4, 9, 56)
    assert sum(evidence[key] for key in ("A", "B", "C", "D")) == 105
    assert evidence["same_correctness_state"] == 92
    assert evidence["D"] / 105 == 56 / 105


def test_candidate_matrix_has_exactly_one_selected_direction() -> None:
    decisions = [row["decision"] for row in _module().candidate_matrix()]
    assert decisions.count("SELECTED_NEXT_RESEARCH_DIRECTION") == 1
    assert _module().selection_decision()["selected_candidate"] == (
        "DIRECT_GENERATION_RESIDUAL_REBASELINE"
    )


def test_review_has_no_provider_db_retriever_or_app_import() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not imported & {"app", "psycopg", "sqlalchemy", "openai"}
    source = SOURCE.read_text(encoding="utf-8")
    assert '"provider_calls": 0' in source
    assert '"retrieval_reruns": 0' in source


def test_next_milestone_is_diagnostic_56_case_population() -> None:
    contract = _module().next_milestone_contract()
    assert contract["next_milestone"] == "M11.2 — Direct Generation Residual Rebaseline"
    assert contract["population_source"] == "P3 primary matrix D cell OFF_WRONG_ON_WRONG"
    assert contract["population_n"] == 56
    assert contract["review_performed_case_level_taxonomy"] is False


def test_p3_intervention_effect_is_not_relabelled() -> None:
    summary = _module().frozen_evidence()["p3"]
    assert summary["classification"] == "M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE"
    assert summary["net_percentage_points"] < 5.0
    assert summary["p_value"] > 0.05
