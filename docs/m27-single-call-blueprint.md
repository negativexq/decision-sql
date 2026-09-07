# M27 — Single-Call Query Blueprint → SQL

## Objective

M27 measured whether a compact semantic blueprint in the same provider response
improves compositional SQL generation. It was a fresh, frozen 18-case DIRECT
arm, compared with the existing M25.2 full-semantic-context arm. The model was
GPT-5.6 Luna and the experiment allowed one provider request per case.

## Frozen contract

The M25.2 context was restored exactly: live structural schema, all official
column meanings, and all database KB content. M26 query-aware retrieval and
database-value probing were disabled. M1, the read-only executor, and the
official evaluator were unchanged.

The only generation intervention was the response contract:

```text
one provider response → { blueprint, sql }
```

The blueprint is bounded, untrusted descriptive data. It has no compilation,
execution, safety, or SQL-rewrite authority. Only the returned SQL is passed to
M1.

## Blueprint fields

The contract covers population, grain, joins, filters, aggregations, temporal
semantics, projection, ordering, limit, distinct, notes, and optional model
self-check booleans. Strict validation rejects malformed or oversized objects;
there is no retry or repair call.

## Provider budget and integrity

All 18 cases received exactly one request. The OpenAI-compatible client uses a
single direct HTTP request with no SDK retry loop. Responses were persisted to
the ignored protected/results journal immediately after completion. The
protected GT artifact and M25.2 outputs were not sent to the provider.

## Results

M25.2 produced 6/18 official raw correct. M27 produced 0/18: all 18 provider
responses contained a blueprint and SQL-shaped content, but none matched the
strict bounded object contract, so they were recorded as protocol failures and
no SQL was handed to M1. This is a valid measurement of the implemented M27
contract, but it is not evidence that the underlying SQL text would have scored
zero under a different parser contract.

The paired transitions were 0 old-correct/new-correct, 6 old-correct/new-wrong,
0 old-wrong/new-correct, and 12 old-wrong/new-wrong. The exact two-sided
McNemar p-value was 0.03125; with 18 cases this is descriptive only.

## Claim boundary

M27 measured a single-call blueprint-plus-SQL response contract. It did not run
the full 180-case benchmark, add a second model call, modify the production
prompt, change M1, or establish model quality beyond this frozen pilot. The
historical M25.2 score remains 6/18.
