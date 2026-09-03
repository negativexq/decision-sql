"""BIRD EX evaluator adapter kept outside the production evaluator."""

from __future__ import annotations

from evaluation.external.bird.benchmark import QueryResult, bird_execution_match, soft_f1


def evaluate(generated: QueryResult, gold: QueryResult) -> dict[str, bool | float]:
    return {
        "execution_accuracy": bird_execution_match(generated, gold),
        "soft_f1": soft_f1(generated, gold),
    }
