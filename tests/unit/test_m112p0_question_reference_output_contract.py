from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "evaluation" / "m112p0_question_reference_output_contract.py"


def _module():
    spec = importlib.util.spec_from_file_location("m112p0_contract", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projection_comparator_design_controls_pass() -> None:
    result = _module().projection_design_controls()
    assert result["all_pass"] is True


def test_projection_comparator_preserves_literal_and_offset_semantics() -> None:
    module = _module()
    assert (
        module.compare_projection(
            "SELECT id FROM t WHERE status = 'COMPLETED'",
            "SELECT id FROM t WHERE status = 'completed'",
        )["state"]
        == "NON_PROJECTION_DIFFERENCE_PRESENT"
    )
    assert (
        module.compare_projection(
            "SELECT id FROM t LIMIT 10 OFFSET 0",
            "SELECT id FROM t LIMIT 10 OFFSET 100",
        )["state"]
        == "NON_PROJECTION_DIFFERENCE_PRESENT"
    )


def test_question_output_and_reference_formula_controls() -> None:
    module = _module()
    assert (
        module._question_output_status("Return customer id and customer name only.")
        == "OUTPUT_CONTRACT_EXPLICIT"
    )
    result = module._reference_semantics(
        "Show each line and its share of quantity within its order.",
        "SELECT order_id, SUM(quantity) OVER (PARTITION BY order_id) FROM order_items",
    )
    assert result["status"] == "REFERENCE_SEMANTIC_DEFECT"


def test_p1_adversarial_reproduction_is_recorded_as_historical_defect() -> None:
    result = _module().p1_adversarial_reproduction()
    assert result["all_expected_reproductions"] is True
    assert all(
        result[name]["counterexample_reproduced"]
        for name in ("literal_case", "offset", "join_using")
    )


def test_p1_labels_are_not_used_and_source_has_no_app_import() -> None:
    module = _module()
    assert module.protocol()["p1_labels_used_for_adjudication"] is False
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
    assert "app" not in imported
