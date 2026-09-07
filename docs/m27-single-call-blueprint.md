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
self-check booleans. The blueprint is diagnostic only: the receiver now
performs bounded best-effort normalization for common string/list shape drift
and records warnings. Invalid or missing SQL is still rejected, and there is
no retry or repair call.

## Provider budget and integrity

All 18 cases received exactly one request. The OpenAI-compatible client uses a
single direct HTTP request with no SDK retry loop. Responses were persisted to
the ignored protected/results journal immediately after completion. The
protected GT artifact and M25.2 outputs were not sent to the provider.

## Results

M25.2 produced 6/18 official raw correct. The original M27 implementation
reported 0/18 because all 18 provider responses contained SQL-shaped content
but none matched the strict blueprint object contract; no SQL was handed to
M1. After the structural parser fix, the same persisted responses were
replayed without a provider call: 18/18 blueprints parsed (with bounded
warnings), 18/18 SQL values reached the pipeline, 15/18 passed M1 and
executed, and the official result was 6/18. This confirms that the original
zero was a protocol artifact, not a SQL-quality score.

The replay is a downstream diagnostic, not a fresh model run. It does not claim
that the quality pack improved model accuracy; it removes a format failure and
keeps malformed SQL subject to the unchanged M1 boundary.

The corrected paired transitions were 5 old-correct/new-correct, 1
old-correct/new-wrong, 1 old-wrong/new-correct, and 11 old-wrong/new-wrong.
The exact two-sided McNemar p-value was 1.0; with 18 cases this is descriptive
only.

## Claim boundary

M27 measured a single-call blueprint-plus-SQL response contract. It did not run
the full 180-case benchmark, add a second model call, modify the production
prompt, change M1, or establish model quality beyond this frozen pilot. The
historical M25.2 score remains 6/18.
