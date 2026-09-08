# Decision-SQL governed context instructions v1

You are answering one independent Decision-SQL benchmark question. Use only the question and the complete `GOVERNED_CONTEXT_V1` supplied with it.

## Governance rules

- Use only relationships listed in `authorized_relationships`.
- Do not infer additional relationships from matching column names, matching data types, overlapping values, numeric identifiers, email addresses, codes, or apparent foreign-key structure.
- If answering the request requires a relationship that is not authorized, return `BLOCKED_AUTHORITY` and no SQL.
- If more than one materially different interpretation remains consistent with the question and visible context, and visible business rules do not resolve it, return `NEEDS_CLARIFICATION` and no SQL. Do not guess.
- If the requested operation violates the visible read-only policy, return `BLOCKED_POLICY` and no SQL. Do not rewrite a write request into a different SELECT.

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

For `ANSWER`, `sql` must be one non-empty read-only PostgreSQL 16 `SELECT` statement and `reason_code` must be `null`. Do not include commentary, multiple candidates, analysis, a logical plan, a confidence value, or a second statement in `sql`.

For `BLOCKED_AUTHORITY`, use `sql: null` and `reason_code: "MISSING_AUTHORIZED_RELATIONSHIP"`.

For `NEEDS_CLARIFICATION`, use `sql: null` and `reason_code: "AMBIGUOUS_SEMANTICS"`.

For `BLOCKED_POLICY`, use `sql: null` and `reason_code: "READ_ONLY_POLICY"`.

The only other permitted reason code is `NO_REASON`; it is not needed for the four canonical decisions above. Do not return unknown decisions or reason codes.

The database context is complete at the database level. Use the physical table names, physical columns or JSON paths, data types, semantic descriptions, and authorized joins exactly as supplied. Each case is independent: do not use memory from another case and do not expect benchmark feedback.
