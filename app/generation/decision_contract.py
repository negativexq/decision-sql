# ruff: noqa: E501, I001

"""Canonical production decision contract.

The prompt bytes are intentionally kept identical to the accepted Candidate C
contract.  Benchmark adapters may import this module, but product code never
depends on the benchmark package.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


PRODUCTION_DECISION_CONTRACT_VERSION = "CANDIDATE_C"
PRODUCTION_DECISION_CASE_ID = "operator"
PRODUCTION_DECISION_CONTRACT_HASH = (
    "3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587"
)


# This is the exact accepted Candidate C prompt.  Keep the source in the
# product package so the production dependency direction is unambiguous.
_CANDIDATE_C_PROMPT = r"""# Decision-SQL governed context instructions v1

You are answering one independent Decision-SQL benchmark question. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.

## Typed JSON scalar semantics

- When the governed context documents a JSON-derived attribute with a semantic SQL type such as `NUMERIC`, use that documented semantic type when constructing SQL.
- PostgreSQL text-returning JSON extraction operators or expressions, including `->>` and `#>>`, produce text. If the documented attribute is numeric, explicitly coerce the extracted scalar to an appropriate numeric SQL type before numeric comparison, arithmetic, aggregation, numeric ordering, or returning it where the requested result is semantically numeric.
- Do not infer a JSON path or type that is not explicitly documented in the governed context.
- Use the exact documented JSON path and documented semantic type.

## Decisions and output

Allowed decisions are exactly:

- `ANSWER`: the question is sufficiently specified and can be answered using only the visible schema, attributes, authorized relationships, metrics, business rules, temporal rules, and policy.
- `BLOCKED_AUTHORITY`: the intent is clear, but an authority or relationship required by the request is absent from the authorized context.
- `NEEDS_CLARIFICATION`: materially different interpretations remain unresolved.
- `BLOCKED_POLICY`: the requested operation violates the visible read-only policy.

Return one JSON object with exactly these four fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY",
  "sql": "one PostgreSQL SELECT statement or null",
  "reason_code": null
}
```

Copy the supplied Case ID exactly into `case_id`. Do not invent, modify, or infer a different identifier.

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

The final SQL projection is part of the answer. Return only the fields requested by the question and visible task semantics. Do not add descriptive, diagnostic, intermediate, helper, grouping, ordering, or qualification columns unless the question explicitly requests them. Fields used only to filter, join, group, order, qualify, or calculate an intermediate value must not appear automatically. When the question names output fields in order, return them in that order. SQL aliases do not change semantic identity, and row ordering is separate from column ordering.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The database context is complete at the database level. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each case is independent: do not use memory from another case and do not expect benchmark feedback.


## Final contract check before emitting a decision

1. Meaning: what exact population, grouping, measure, filters, time range, and projection does the question request? Clarify only if one of these material choices is still unresolved in the visible governed context.
2. Authority: is the meaning clear, but a needed relation or path absent from `authorized_relationships`? If so, return `BLOCKED_AUTHORITY`. Do not use a technically visible table or a matching column as authorization.
3. Semantics: does each aggregate operate at its native grain? Use existence or key-preserving logic when a qualifying child could duplicate a parent measure. Use the governed duration formula rather than counting events, and retain required governed status or decision filters.

Do not turn a plausible schema interpretation into a business rule. Do not omit a governed predicate or replace a governed measure with a convenient proxy merely because the BASE data does not expose the difference.
"""


class DecisionType(StrEnum):
    ANSWER = "ANSWER"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED_POLICY = "BLOCKED_POLICY"


class DecisionReasonCode(StrEnum):
    MISSING_AUTHORIZED_RELATIONSHIP = "MISSING_AUTHORIZED_RELATIONSHIP"
    AMBIGUOUS_SEMANTICS = "AMBIGUOUS_SEMANTICS"
    READ_ONLY_POLICY = "READ_ONLY_POLICY"
    # Retained for compatibility with the historical Candidate C wire enum.
    # It is intentionally not valid for any canonical decision below.
    NO_REASON = "NO_REASON"


class ProductionDecision(BaseModel):
    """Untrusted, single-response model decision with optional SQL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    decision: DecisionType
    sql: str | None = None
    reason_code: DecisionReasonCode | None = None

    @model_validator(mode="after")
    def validate_contract(self) -> ProductionDecision:
        expected: dict[DecisionType, DecisionReasonCode | None] = {
            DecisionType.ANSWER: None,
            DecisionType.BLOCKED_AUTHORITY: DecisionReasonCode.MISSING_AUTHORIZED_RELATIONSHIP,
            DecisionType.NEEDS_CLARIFICATION: DecisionReasonCode.AMBIGUOUS_SEMANTICS,
            DecisionType.BLOCKED_POLICY: DecisionReasonCode.READ_ONLY_POLICY,
        }
        if self.decision is DecisionType.ANSWER:
            if not self.sql or not self.sql.strip() or self.reason_code is not None:
                raise ValueError("ANSWER requires non-empty sql and no reason_code")
        elif self.sql is not None or self.reason_code is not expected[self.decision]:
            raise ValueError(f"{self.decision.value} requires its canonical reason_code and no sql")
        return self


def production_decision_prompt() -> str:
    actual = hashlib.sha256(_CANDIDATE_C_PROMPT.encode("utf-8")).hexdigest()
    if actual != PRODUCTION_DECISION_CONTRACT_HASH:
        raise RuntimeError(f"PRODUCTION_DECISION_CONTRACT_HASH_MISMATCH:{actual}")
    return _CANDIDATE_C_PROMPT


def production_decision_schema() -> dict[str, Any]:
    """Return the flat Candidate C wire schema used by the provider boundary."""
    # Keep this provider-compatible schema flat.  Pydantic remains the
    # semantic validator after admission; this shape is also the historical
    # benchmark wire protocol, now owned by the product module.
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Decision-SQL provider-safe submission",
        "type": "object",
        "required": ["case_id", "decision", "sql", "reason_code"],
        "additionalProperties": False,
        "properties": {
            "case_id": {"type": "string"},
            "decision": {
                "type": "string",
                "enum": [item.value for item in DecisionType],
            },
            "sql": {"type": ["string", "null"]},
            "reason_code": {
                "type": ["string", "null"],
                "enum": [item.value for item in DecisionReasonCode] + [None],
            },
        },
    }


def production_decision_contract_hash() -> str:
    return hashlib.sha256(production_decision_prompt().encode("utf-8")).hexdigest()
