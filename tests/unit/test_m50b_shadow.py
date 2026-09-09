from __future__ import annotations

import ast
from pathlib import Path


def test_m50b_harness_is_present_and_zero_call() -> None:
    source = Path("benchmark/m50b_shadow.py").read_text(encoding="utf-8")

    assert '"provider_calls": 0' in source
    assert '"model_calls": 0' in source
    assert "phase-a" in source
    assert "phase-b" in source


def test_m50b_core_builder_is_benchmark_agnostic() -> None:
    source = Path("app/semantics/context_availability.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert not any(module.startswith("benchmark") for module in imports)
    assert "truth_behavior" not in source
    assert "reference_implementation" not in source
    assert "is_answerable" not in source
    assert "should_answer" not in source
