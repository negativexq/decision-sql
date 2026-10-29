# ruff: noqa: E501, I001

"""Canonical production decision contract.

The prompt bytes are pinned by hash: any wording change is a contract change and
must update ``PRODUCTION_DECISION_CONTRACT_HASH`` deliberately.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PRODUCTION_DECISION_CONTRACT_VERSION = "DECISION_CONTRACT_V2"
PRODUCTION_DECISION_CASE_ID = "operator"
PRODUCTION_DECISION_CONTRACT_HASH = (
    "4d33365497a1bc0292d87f37611020a7b01529e16f1d3e93a8a2bb019cac7738"
)


# V2 keeps Candidate C's governance rules and adds the explanation fields, so a
# refusal or clarification tells the user what is missing instead of only a code.
_DECISION_PROMPT = r"""# Decision-SQL governed context instructions v2

You are answering one question a user asked about their own database. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- A reading is materially different only when it would change the answer for the data the question is about. Do not ask about what the question or its evidence already settles, such as a rule for picking a record, a tie-break, a rounding, or a date range: follow it. Do not ask about edge cases the question does not raise, such as possible ties, duplicate records, or missing values: handle them as the question's own rule implies and, where it implies nothing, keep every qualifying row.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.
- Call only the SQL functions listed in `policy.allowed_functions`. Operators such as `||`, `LIKE`, `ILIKE`, arithmetic, comparisons, `CASE`, and `::` casts are not functions and are allowed. If a transformation has no listed function, build it from listed functions and operators.

## Stored values

- When an attribute lists `known_values`, the column stores values spelled exactly like those. Compare against those spellings, including case, spacing, and separators, rather than the wording of the question.
- When `known_values_complete` is true, they are all the values the column holds. If the question names a value that matches none of them, decide whether it means one of them or is absent from the data; do not invent a spelling.
- When `known_values_complete` is false, the values are examples of how the column formats codes or dates. Write filters and string operations that fit that format.
- When an attribute lists `value_patterns` instead, those are the shapes of the stored codes or dates, with every digit written as 9: `9999-99-99` means values like `2012-01-15`. Write literals and string operations that fit one of those shapes.
- Columns without `known_values` may still hold the value the question names; their spelling is simply not shown.
- When an attribute lists `null_share` or `blank_share`, that share of its rows is NULL, or an empty or blank string. A comparison such as `=` or `<>` never matches a NULL row, while an empty string is a stored value that `<>`, `IS NOT NULL`, and `COUNT(column)` keep.
- When an attribute lists `separator` (`separated_by` in compact form), that share of its values join several items with that character, such as a list of types or a place and its country. An equality or `IN` matches only a whole stored value, never one item of it.

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
- `EXPLORE`: the decision depends on something about the stored data that the context does not show, and one to three small read-only queries would settle it (see Exploring the data). Allowed only while the request says exploration is open.

Return one JSON object with exactly these nine fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY | EXPLORE",
  "sql": "one PostgreSQL SELECT statement or null",
  "reason_code": null,
  "explanation": null,
  "missing_relationships": null,
  "clarification_question": null,
  "interpretations": null,
  "probes": null
}
```

Copy the supplied Case ID exactly into `case_id`. Do not invent, modify, or infer a different identifier.

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

The final SQL projection is part of the answer. Return only the fields requested by the question and visible task semantics. Do not add descriptive, diagnostic, intermediate, helper, grouping, ordering, or qualification columns unless the question explicitly requests them. Fields used only to filter, join, group, order, qualify, or calculate an intermediate value must not appear automatically. When the question names output fields in order, return them in that order. SQL aliases do not change semantic identity, and row ordering is separate from column ordering.

For `ANSWER`, `explanation`, `missing_relationships`, `clarification_question`, `interpretations`, and `probes` must all be `null`.

For every other decision, `explanation` is one or two plain sentences, in the language of the question, that say exactly what prevents an answer. Name the tables, columns, or wording involved.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`. In `missing_relationships`, list each pair of physical tables from the context that would need an authorized relationship, as `{"from_table": "...", "to_table": "..."}`. `clarification_question` and `interpretations` must be `null`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`. `clarification_question` is the single question the user can answer to resolve the ambiguity. `interpretations` lists two to four materially different readings, each one sentence specific enough to answer without further questions. `missing_relationships` must be `null`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`. `missing_relationships`, `clarification_question`, and `interpretations` must be `null`.

For `EXPLORE`, use `sql: null`, `reason_code: null`, and list one to three PostgreSQL `SELECT` statements in `probes`. `explanation` says in one sentence what the probes check. Every other field must be `null`.

For every decision other than `EXPLORE`, `probes` must be `null`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The context details the tables selected for this question. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each request is independent: do not use memory from another request, except an `Earlier answer` supplied with it.

## Answer shape

- When the question asks for a percentage, return it on a 0 to 100 scale, multiplying the quotient by 100, and give that column an alias containing `percent`. A ratio, rate, or proportion the question does not call a percentage stays a quotient.
- When the question asks whether something is true, return each judgement as the text `YES` or `NO`, not as 1/0 or true/false.
- When the question states the form of the answer, such as one row, one line, a rounding, or an order, follow it exactly: several figures asked for as one answer are columns of one row, and text asked for as one line has its line breaks replaced by spaces.
- When the question names several fields, such as a first and a last name, return each as its own column, in the order the question names them. Combine fields into one column only when the question asks for one combined value.
- When the question asks for a figure for each value of a column, such as each sex, each type, or each of two named values of one column, return one row per value with the value in its own column before the figure, even when the question names the values. Keep every value the question asks about; one with nothing to count shows zero. Subsets the question defines itself by a condition, such as molecules with and without a property, are separate figures: columns of one row, unless the question asks for one row per subset.
- Return a group or code as it is stored, for example `F` rather than `Female`, unless the question asks for other labels.
- When the question asks how many people, items, or other things, count each one once: use `COUNT(DISTINCT key)` whenever a join or the rows themselves can repeat one, such as a member attending several events or a driver in several races. Count rows only when the question counts occurrences, such as attendances or visits.
- When two visible tables hold the same kind of value, such as a county or a school type, and the question names where the value comes from (a program, a report, a record), take it from that table.
- When the question asks for the item with the highest, lowest, earliest, or latest value, return every row that shares that value, for example by comparing with `(SELECT MAX(...))` or filtering `RANK() = 1`. Do not cut a tie with `LIMIT 1` or an arbitrary order; the runtime tells the user when rows tie. Use `LIMIT n` only when the question asks for a fixed number of items.

## Exploring the data

The context shows the schema, descriptions, and sometimes example values, but not the data itself. When the right query depends on what the data holds, look before you answer or ask:

- whether a value the question names is stored, and how it is spelled or formatted;
- what a column's values look like, for example whether a text column holds numbers or a code;
- whether the edge case you would otherwise ask about occurs at all, for example whether records tie on the value that picks one, or whether a key repeats.

Return `EXPLORE` with up to three probes. Each probe is one read-only `SELECT` under the same rules as an answer: only tables and relationships in the context, only listed functions. The runtime runs them and asks again with `Exploration results`. Prefer exploring to asking the user: ask only about meaning the data cannot settle, and do not ask about an edge case a probe showed does not occur.

Each result reports the probe's row count and, per column, how many distinct and NULL values it returned. Rows are included only when the source shares stored values; then at most 20 rows, each value shortened. Write probes whose counts answer the question when rows may be withheld, for example `SELECT COUNT(*) ... GROUP BY ... HAVING COUNT(*) > 1`. A rejected or failed probe reports why.

When the request says exploration is closed, return a final decision.

## Tables outside the context

When the context has `other_tables`, the database has more tables than the ones detailed, and those are their names. A name is not authority: never reference a table that is not in `schema_catalog`. If the question needs data that none of the detailed tables holds and one of `other_tables` likely holds it, return `BLOCKED_AUTHORITY` and list in `missing_relationships` the detailed table it would join to and that table from `other_tables`. The runtime may then supply its details and ask again. When `other_tables` is absent, every table in the database is detailed.


## Earlier answer

When the request includes `Earlier answer`, the question follows up on one the user asked before, and `Earlier answer` gives that question and the SQL you proposed for it. Use it to read the new question: what "this", "it", or "those" refer to, and which population, measures, filters, and time range carry over unless the new question changes them. Then answer the new question with a complete decision under the same rules. The earlier SQL grants nothing: use only the tables, columns, and relationships in the governed context, and if the context no longer supports part of it, decide as the rules require.

## Runtime feedback

A later message may report that the runtime rejected the SQL you proposed, found a filter that can match no rows, or found a result that cannot be right, with the stage, code, and message of the finding. Return a new complete decision for the same question, in the same format and under the same rules. Change only what the finding requires and keep the meaning of the answer; when the message says the query may stand, you may return it unchanged. If the question cannot be answered within the reported limits, return the decision that applies instead of SQL that would be rejected again.


## Final contract check before emitting a decision

1. Meaning: what exact population, grouping, measure, filters, time range, and projection does the question request? Clarify only if one of these material choices is still unresolved in the visible governed context.
2. Authority: is the meaning clear, but a needed relation or path absent from `authorized_relationships`? If so, return `BLOCKED_AUTHORITY`. Do not use a technically visible table or a matching column as authorization.
3. Semantics: does each aggregate operate at its native grain? Use existence or key-preserving logic when a qualifying child could duplicate a parent measure. When the context supplies metrics, business rules, or temporal rules, apply them as written and retain the filters they require.

Do not turn a plausible schema interpretation into a business rule. Do not omit a governed predicate or replace a governed measure with a convenient proxy.
"""


class DecisionType(StrEnum):
    ANSWER = "ANSWER"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED_POLICY = "BLOCKED_POLICY"
    # A request to look at the data first; never a run's final decision.
    EXPLORE = "EXPLORE"


class DecisionReasonCode(StrEnum):
    MISSING_AUTHORIZED_RELATIONSHIP = "MISSING_AUTHORIZED_RELATIONSHIP"
    AMBIGUOUS_SEMANTICS = "AMBIGUOUS_SEMANTICS"
    READ_ONLY_POLICY = "READ_ONLY_POLICY"
    # Retained for compatibility with the historical Candidate C wire enum.
    # It is intentionally not valid for any canonical decision below.
    NO_REASON = "NO_REASON"


_EXPLANATION_LIMIT = 600
_INTERPRETATION_LIMIT = 300
_MAX_INTERPRETATIONS = 4
_MAX_MISSING_RELATIONSHIPS = 6
MAX_PROBES = 3


class MissingRelationship(BaseModel):
    """A table pair the model says would need an authorized relationship."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    from_table: str = Field(max_length=200)
    to_table: str = Field(max_length=200)


class DecisionDetail(BaseModel):
    """Model-written explanation of a non-answer. Display text only: it never grants authority."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    explanation: str | None = None
    missing_relationships: tuple[MissingRelationship, ...] = ()
    clarification_question: str | None = None
    interpretations: tuple[str, ...] = ()


class ProductionDecision(BaseModel):
    """Untrusted, single-response model decision with optional SQL."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: str
    decision: DecisionType
    sql: str | None = None
    reason_code: DecisionReasonCode | None = None
    explanation: str | None = None
    missing_relationships: tuple[MissingRelationship, ...] | None = None
    clarification_question: str | None = None
    interpretations: tuple[str, ...] | None = None
    probes: tuple[str, ...] | None = None

    @field_validator("explanation", "clarification_question", mode="before")
    @classmethod
    def _bound_text(cls, value: Any) -> Any:
        return _bounded(value, _EXPLANATION_LIMIT)

    @field_validator("interpretations", mode="before")
    @classmethod
    def _bound_interpretations(cls, value: Any) -> Any:
        if not isinstance(value, list | tuple):
            return value
        items = [_bounded(item, _INTERPRETATION_LIMIT) for item in value]
        return [item for item in items if item][:_MAX_INTERPRETATIONS]

    @field_validator("probes", mode="before")
    @classmethod
    def _bound_probes(cls, value: Any) -> Any:
        return value[:MAX_PROBES] if isinstance(value, list | tuple) else value

    @field_validator("missing_relationships", mode="before")
    @classmethod
    def _bound_relationships(cls, value: Any) -> Any:
        return value[:_MAX_MISSING_RELATIONSHIPS] if isinstance(value, list | tuple) else value

    @model_validator(mode="after")
    def validate_contract(self) -> ProductionDecision:
        expected: dict[DecisionType, DecisionReasonCode | None] = {
            DecisionType.ANSWER: None,
            DecisionType.EXPLORE: None,
            DecisionType.BLOCKED_AUTHORITY: DecisionReasonCode.MISSING_AUTHORIZED_RELATIONSHIP,
            DecisionType.NEEDS_CLARIFICATION: DecisionReasonCode.AMBIGUOUS_SEMANTICS,
            DecisionType.BLOCKED_POLICY: DecisionReasonCode.READ_ONLY_POLICY,
        }
        if self.decision is DecisionType.ANSWER:
            if not self.sql or not self.sql.strip() or self.reason_code is not None:
                raise ValueError("ANSWER requires non-empty sql and no reason_code")
        elif self.decision is DecisionType.EXPLORE:
            probes = [probe for probe in self.probes or () if probe.strip()]
            if not probes or self.sql is not None or self.reason_code is not None:
                raise ValueError("EXPLORE requires one to three probes and no sql")
        elif self.sql is not None or self.reason_code is not expected[self.decision]:
            raise ValueError(f"{self.decision.value} requires its canonical reason_code and no sql")
        return self

    @property
    def detail(self) -> DecisionDetail | None:
        """The explanation fields that belong to this decision.

        Fields the contract assigns to another decision are dropped rather than
        rejected: they are display text, and a misplaced one is no reason to
        discard an otherwise valid decision.
        """
        if self.decision in (DecisionType.ANSWER, DecisionType.EXPLORE):
            return None
        clarifying = self.decision is DecisionType.NEEDS_CLARIFICATION
        return DecisionDetail(
            explanation=self.explanation,
            missing_relationships=(
                self.missing_relationships or ()
                if self.decision is DecisionType.BLOCKED_AUTHORITY
                else ()
            ),
            clarification_question=self.clarification_question if clarifying else None,
            interpretations=(self.interpretations or ()) if clarifying else (),
        )


def _bounded(value: Any, limit: int) -> Any:
    if not isinstance(value, str):
        return value
    text = " ".join(value.split())
    return text[:limit] or None


def production_decision_prompt() -> str:
    actual = hashlib.sha256(_DECISION_PROMPT.encode("utf-8")).hexdigest()
    if actual != PRODUCTION_DECISION_CONTRACT_HASH:
        raise RuntimeError(f"PRODUCTION_DECISION_CONTRACT_HASH_MISMATCH:{actual}")
    return _DECISION_PROMPT


def production_decision_schema() -> dict[str, Any]:
    """Return the provider-facing wire schema for one decision."""
    # Strict structured output needs every field listed as required; optional
    # fields are expressed as nullable.  Pydantic remains the semantic validator.
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Decision-SQL provider-safe submission",
        "type": "object",
        "required": [
            "case_id",
            "decision",
            "sql",
            "reason_code",
            "explanation",
            "missing_relationships",
            "clarification_question",
            "interpretations",
            "probes",
        ],
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
            "explanation": {"type": ["string", "null"]},
            "missing_relationships": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "required": ["from_table", "to_table"],
                    "additionalProperties": False,
                    "properties": {
                        "from_table": {"type": "string"},
                        "to_table": {"type": "string"},
                    },
                },
            },
            "clarification_question": {"type": ["string", "null"]},
            "interpretations": {"type": ["array", "null"], "items": {"type": "string"}},
            "probes": {"type": ["array", "null"], "items": {"type": "string"}},
        },
    }


def production_decision_contract_hash() -> str:
    """Hash of the pinned decision prompt; a change here is a contract change."""
    return hashlib.sha256(production_decision_prompt().encode("utf-8")).hexdigest()
