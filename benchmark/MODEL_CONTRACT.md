# Decision-SQL Bench model-facing contract

This document defines the public contract for a benchmark participant. It contains no pilot answers or case labels.

## Input

Each request is independent and has this form:

```text
SYSTEM:
<governed_context_v1 instructions>

USER:
Case ID:
<the exact persisted case identifier>

Question:
<the exact persisted question>

Governed context:
<the complete canonical GOVERNED_CONTEXT_V1 JSON for that database>
```

The question is sent byte-for-byte from the persisted benchmark case. The context is the full database-level package: schema catalog, attributes, authorized relationships, metrics, business rules, temporal rules, and visible read-only policy. No case-specific retrieval or hidden target filtering is used.

The supplied `Case ID:` is an envelope identifier. Copy it exactly into the output `case_id`; do not invent, modify, or infer another identifier. The evaluator rejects any output whose `case_id` differs from the request case.

## Governance behavior

Use only relationships listed in `authorized_relationships`. Do not infer relationships from similar fields, types, values, numeric identifiers, email addresses, codes, or apparent foreign keys. If the requested relationship is absent, return `BLOCKED_AUTHORITY` with no SQL.

Return `NEEDS_CLARIFICATION` with no SQL when visible information leaves materially different interpretations unresolved. Return `BLOCKED_POLICY` with no SQL when the requested operation violates the visible read-only policy.

An `ANSWER` is allowed only when the question is sufficiently specified and answerable from the visible context. It must contain exactly one read-only PostgreSQL 16 `SELECT` statement.

The final projection is part of the answer contract. Return only the output fields requested by the question and visible task semantics. Do not include additional descriptive, diagnostic, intermediate, helper, grouping, ordering, or qualification columns unless the question explicitly requests them. A field used only for filtering, joining, grouping, ordering, qualification, or an intermediate calculation must not appear automatically in the final result. SQL aliases are non-semantic; when the question names fields in an order, preserve that column order. Row order is separate and is significant only when the question requests it.

## Output schema

Return exactly one JSON object with exactly these fields:

```json
{
  "case_id": "the supplied case identifier",
  "decision": "ANSWER | BLOCKED_AUTHORITY | NEEDS_CLARIFICATION | BLOCKED_POLICY",
  "sql": "one read-only SELECT statement or null",
  "reason_code": null
}
```

The decision/reason mapping is fixed:

| Decision | SQL | Reason code |
|---|---|---|
| `ANSWER` | non-empty one-statement SELECT | `null` |
| `BLOCKED_AUTHORITY` | `null` | `MISSING_AUTHORIZED_RELATIONSHIP` |
| `NEEDS_CLARIFICATION` | `null` | `AMBIGUOUS_SEMANTICS` |
| `BLOCKED_POLICY` | `null` | `READ_ONLY_POLICY` |

Unknown decisions, unknown reason codes, contradictory SQL fields, multiple statements, writes, DDL, locking reads, commentary, analysis, candidate lists, confidence, and reasoning traces are invalid. The only additional bounded reason code is `NO_REASON`, although the canonical mappings above are preferred.

## What is hidden

The participant does not receive task-type gold labels, semantic targets, reference SQL, expected results, fixtures, mutants, evaluator feedback, ambiguity interpretations, missing-authority descriptions, or model scores. The generic governance rules above are public and apply uniformly to every case.

## Evaluation overview

The benchmark evaluator checks the decision contract first. For `ANSWER`, it admits and executes the one read-only query on the base database and every isolated counterfactual fixture, then compares typed results under the declared result contract. For governed non-answer decisions, the decision and required null SQL/reason code are checked. The evaluator does not use an LLM judge, semantic repair, query repair, selector, or pass@K.

## M35 result categories

Each future provider result has one deterministic category: `TRANSPORT_FAILURE`, `PROVIDER_SCHEMA_FAILURE`, `INVALID_SUBMISSION`, `WRONG_GOVERNED_DECISION`, `SQL_ADMISSION_FAILURE`, `EXECUTION_FAILURE`, `RESULT_MISMATCH`, or `CORRECT`. The harness refuses a future run against a changed benchmark, prompt, submission schema, serializer, evaluator, validator, or case-order hash.
