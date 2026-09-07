# Query quality hardening checkpoint

## Scope

This checkpoint hardens the shared generation path used by the three external
benchmark surfaces (Defog Classic/Advanced and BIRD) and the internal/M27
diagnostic paths. No Luna API call or new benchmark generation was performed.

## Structural changes

1. The one-call blueprint parser now treats the blueprint as non-authoritative
   diagnostics. Common provider shape drift is normalized with bounded limits:
   string predicates, aggregations, joins, temporal fields, ordering, scalar
   fields, and non-object self-checks no longer discard an otherwise valid SQL
   value. Every normalization is retained as a parse warning.
2. Missing/extra blueprint metadata no longer blocks SQL extraction. Missing or
   invalid SQL still fails closed. SQL is never rewritten and still enters the
   unchanged M1 policy, EXPLAIN, read-only execution, and result evaluator path.
3. A shared query-quality pack is included in direct and blueprint prompts. It
   explicitly targets the observed failure modes: over-projection, unnecessary
   joins/aggregation/filters, incorrect top-N order/limit, and invented
   physical mappings or business rules.
4. The default provider temperature is now omitted. An explicit value remains
   opt-in, while provider-default sampling avoids the known rejection seen when
   reasoning models receive `temperature=0`.
5. The replay harness now recovers raw assistant content from failed protocol
   journal rows. This prevents a formatting failure from being reported as if
   no SQL had been generated.

## Offline validation

Existing persisted M27 responses were replayed locally; no provider request was
made:

| Stage | Before parser fix | After parser fix |
|---|---:|---:|
| Blueprint parsed | 0/18 | 18/18 |
| SQL available to pipeline | 0/18 | 18/18 |
| M1 accepted | 0/18 | 15/18 |
| Execution success | 0/18 | 15/18 |
| Official result equivalence | 0/18 (protocol artifact) | 6/18 |

The corrected 6/18 is equal to the frozen M25.2 pilot on this paired sample;
it is not a fresh model-quality gain. It establishes that the previous 0/18
was not a valid SQL-quality measurement. The remaining failures are real
downstream SQL/M1 outcomes and remain visible in the protected local case
ledger, which now stores expected SQL, generated SQL, expected result shape,
and the M1/execution verdict per case.

The full Defog, BIRD, and internal benchmark scores remain unchanged historical
measurements until a separately authorized provider run is performed. No
evaluator relaxation, denominator change, gold-aware routing, or benchmark
specific rule was added.
