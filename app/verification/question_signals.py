"""Conservative lexical cues used by the deterministic verifier."""

from __future__ import annotations

import re

_TOP_N_RE = re.compile(r"\b(?:top|bottom|highest|lowest|first|last)\s+(\d+)\b", re.IGNORECASE)
_GROUPING_PATTERNS = (
    re.compile(r"\bby\s+([a-z][a-z _-]*)", re.IGNORECASE),
    re.compile(r"\bper\s+([a-z][a-z _-]*)", re.IGNORECASE),
    re.compile(r"\bfor each\s+([a-z][a-z _-]*)", re.IGNORECASE),
    re.compile(r"\beach\s+([a-z][a-z _-]*)", re.IGNORECASE),
)
_GROUPING_STOP = re.compile(
    r"\b(?:with|whose|that|ordered|sorted|using|from|where|and|having|above|below)\b",
    re.IGNORECASE,
)


def normalized_question(question: str) -> str:
    return " ".join(question.lower().strip().split())


def requested_top_n(question: str) -> int | None:
    match = _TOP_N_RE.search(question)
    return int(match.group(1)) if match else None


def requested_grouping_terms(question: str) -> tuple[str, ...]:
    terms: list[str] = []
    for pattern in _GROUPING_PATTERNS:
        for match in pattern.finditer(question):
            value = _GROUPING_STOP.split(match.group(1), maxsplit=1)[0]
            value = " ".join(value.replace("-", " ").split()).strip(" .,?!:;")
            if value:
                terms.append(value)
    return tuple(dict.fromkeys(terms))


def has_aggregation_cue(question: str) -> bool:
    value = normalized_question(question)
    return bool(re.search(r"\btotal\b", value)) or any(
        term in value
        for term in ("sum", "average", "avg", "mean", "count", "how many", "number of")
    )


def has_average_cue(question: str) -> bool:
    value = normalized_question(question)
    return any(term in value for term in ("average", "avg", "mean"))


def has_total_cue(question: str) -> bool:
    value = normalized_question(question)
    return bool(re.search(r"\b(?:total|sum)\b", value))


def has_threshold_cue(question: str) -> bool:
    value = normalized_question(question)
    return any(
        term in value
        for term in ("more than", "at least", "fewer than", "greater than", "less than")
    )


def has_distinct_entity_cue(question: str) -> bool:
    value = normalized_question(question)
    return any(
        term in value
        for term in (
            "distinct",
            "unique",
            "number of customers",
            "number of orders",
            "number of products",
        )
    )


def has_explicit_ordering_cue(question: str) -> bool:
    value = normalized_question(question)
    return any(
        term in value
        for term in (
            "top",
            "bottom",
            "highest",
            "lowest",
            "largest",
            "smallest",
            "most",
            "least",
            "first",
            "last",
            "ordered",
            "sorted",
        )
    )


def explicit_entities(question: str) -> tuple[str, ...]:
    value = normalized_question(question)
    matches = []
    for term in (
        "regions",
        "representatives",
        "customers",
        "products",
        "orders",
        "payments",
        "refunds",
        "order items",
    ):
        if term in value:
            matches.append(term)
    return tuple(matches)


def explicit_status_filter(question: str) -> str | None:
    value = normalized_question(question)
    statuses = ("completed", "settled", "failed", "pending", "refunded")
    for status in statuses:
        if re.search(rf"\b(?:status|orders?|payments?)\s+{status}\b", value):
            return status
    return None
