"""Deterministic lexical/schema/heuristic retrieval for verified examples."""

from __future__ import annotations

import math
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.memory.models import VerifiedQueryExample


class RetrieverVariant(StrEnum):
    QUESTION_LEXICAL = "R0_QUESTION_LEXICAL"
    QUESTION_LEXICAL_SCHEMA = "R1_QUESTION_LEXICAL_SCHEMA"
    QUESTION_LEXICAL_SCHEMA_HEURISTIC = "R2_QUESTION_LEXICAL_SCHEMA_HEURISTIC"


class RetrieverConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = "m4-retriever-v1"
    variant: RetrieverVariant
    k: int = Field(ge=1, le=3)
    lexical_weight: float = Field(ge=0)
    schema_weight: float = Field(ge=0)
    structural_weight: float = Field(ge=0)


class RetrievedExample(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    example: VerifiedQueryExample
    lexical_score: float
    schema_score: float
    structural_bonus: float
    final_score: float


class VerifiedQueryRetriever:
    """Finite in-memory retriever with stable score and ID ordering."""

    def __init__(self, examples: tuple[VerifiedQueryExample, ...]) -> None:
        self.examples = tuple(
            sorted(
                (example for example in examples if example.lifecycle.value == "ACTIVE"),
                key=lambda x: x.example_id,
            )
        )

    def retrieve(
        self,
        question: str,
        schema_objects: tuple[str, ...] = (),
        config: RetrieverConfig | None = None,
    ) -> tuple[RetrievedExample, ...]:
        config = config or RetrieverConfig(
            variant=RetrieverVariant.QUESTION_LEXICAL_SCHEMA_HEURISTIC,
            k=3,
            lexical_weight=0.65,
            schema_weight=0.20,
            structural_weight=0.15,
        )
        query_tokens = _tokens(question)
        query_schema = {value.lower() for value in schema_objects}
        query_features = _question_features(question)
        scored = []
        for example in self.examples:
            lexical = _cosine_overlap(query_tokens, _tokens(example.question))
            schema = _jaccard(query_schema, {value.lower() for value in example.schema_objects})
            structural = _jaccard(query_features, _signature_features(example))
            score = (
                config.lexical_weight * lexical
                + config.schema_weight * schema
                + config.structural_weight * structural
            )
            scored.append(
                RetrievedExample(
                    example=example,
                    lexical_score=lexical,
                    schema_score=schema,
                    structural_bonus=structural,
                    final_score=score,
                )
            )
        scored.sort(key=lambda item: (-item.final_score, item.example.example_id))
        return tuple(scored[: config.k])


_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOPWORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "show", "the", "to", "with"}


def _tokens(value: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for token in _TOKEN_RE.findall(value.lower()):
        if token in _STOPWORDS:
            continue
        result[token] = result.get(token, 0) + 1
    return result


def _cosine_overlap(left: dict[str, int], right: dict[str, int]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(
        left.get(token, 0) * right.get(token, 0) for token in left.keys() & right.keys()
    )
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def _signature_features(example: VerifiedQueryExample) -> set[str]:
    signature = example.structural_signature
    features = {f"TABLES_{signature.tables_count}"}
    if signature.join_count:
        features.add("JOIN")
    if signature.multi_join:
        features.add("MULTI_JOIN")
    if signature.aggregation_functions:
        features.add("AGGREGATION")
    for flag, name in (
        (signature.group_by, "GROUP_BY"),
        (signature.having, "HAVING"),
        (signature.subquery, "SUBQUERY"),
        (signature.cte, "CTE"),
        (signature.case, "CASE"),
        (signature.arithmetic, "ARITHMETIC"),
        (signature.division, "DIVISION"),
        (signature.order_by, "ORDER_LIMIT"),
        (signature.window, "WINDOW"),
        (signature.distinct, "DISTINCT"),
        (signature.set_operation, "SET_OPERATION"),
    ):
        if flag:
            features.add(name)
    return features


def _question_features(question: str) -> set[str]:
    value = question.lower()
    features: set[str] = set()
    if any(
        word in value
        for word in ("sum", "total", "average", "avg", "mean", "count", "amount", "quantity")
    ):
        features.add("AGGREGATION")
    if "join" in value or any(
        word in value for word in ("customer", "product", "payment", "refund", "order item")
    ):
        features.add("JOIN")
    if any(word in value for word in ("each", "by ", "per ", "group")):
        features.add("GROUP_BY")
    if any(word in value for word in ("more than", "at least", "fewer than", "only those")):
        features.add("HAVING")
    if any(word in value for word in ("top", "highest", "largest", "most", "lowest", "smallest")):
        features.add("ORDER_LIMIT")
    if any(word in value for word in ("distinct", "unique", "different")):
        features.add("DISTINCT")
    if any(word in value for word in ("classify", "bucket", "segment", "category")):
        features.add("CASE")
    if any(
        word in value for word in ("percentage", "percent", "difference", "margin", "per order")
    ):
        features.add("ARITHMETIC")
    if "average" in value and any(word in value for word in ("above", "below", "higher", "lower")):
        features.add("SUBQUERY")
    if any(word in value for word in ("sort", "ordered", "largest", "highest", "top")):
        features.add("ORDER_LIMIT")
    return features
