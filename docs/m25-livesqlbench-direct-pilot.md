# M25 — LiveSQLBench DIRECT pilot

## Objective

M25 measured the unchanged one-call DIRECT Text-to-SQL path on the frozen
18-case LiveSQLBench Base-Lite PostgreSQL pilot. The run used `gpt-5.6-luna`
with no prompt, M1, evaluator, or production-default changes.

## Frozen population

The pilot retained the existing one-case-per-database population and order from
the final LiveSQLBench manifest. All 18 cases had protected gold coverage,
external-knowledge coverage, constructible schema context, M1-compatible gold,
and successful reference execution before provider exposure. The protected
artifact remained local-only and ignored.

## Runtime contract

Each case received the same current schema context plus its legitimate official
external knowledge, then followed:

```text
question + knowledge + schema
  → one existing DIRECT SQL-generation call
  → unchanged M1
  → existing bounded read-only execution
  → official-compatible evaluator
```

ResultShape, QueryPlan, repair, judge, routing, and additional model calls were
not used. The evaluation harness invokes the existing direct `_run` path without
constructing the unrelated governed-metric route, because arbitrary external
benchmark catalogs are not the fixed governed catalog. This does not alter the
production service or its runtime behavior.

## Provider budget

The preflight froze a maximum of 18 provider requests. The sequential run made
exactly 18 requests: one per case, with no automatic retries, repairs, judges,
planning calls, ResultShape calls, or other model calls. A crash-safe local
journal records each completed attempt and prevents duplicate resume attempts.

## Benchmark integrity caveat

One pilot reference case has a confirmed `CURRENT_DATE` temporal-drift issue:
the current wall clock produces an empty result against the historical database
snapshot, and the upstream evaluator rejects empty expected results, including
empty-vs-empty comparisons. The official denominator remains 18 and no manual
correctness override was applied. A secondary 17-case reference-valid diagnostic
is reported separately.

## Results

The official raw result was **1/18 (5.56%)**. The reference-valid diagnostic was
**1/17 (5.88%)** and is not the official score.

The pipeline funnel was:

```text
18 provider successes
18 protocol successes / SQL outputs
13 M1 accepted
13 executions successful
1 official PASS
```

The five M1 rejections were three `FORBIDDEN_FUNCTION` cases, one
`EXECUTION_ERROR` policy-stage rejection, and one `SQL_PARSE_ERROR`. Of the 13
executed candidates, 11 were ordinary official result mismatches and one was
the known temporal-reference-integrity limitation; the remaining one passed.

Provider latency had a median of approximately 2.96 seconds and P95 of
approximately 3.61 seconds. End-to-end latency had a median of approximately
2.99 seconds and P95 of approximately 3.64 seconds. Usage metadata was
available for all 18 calls: 48,327 input tokens and 3,436 output tokens total.

## Claim boundary

This is an 18-case pilot, not the full 180-case benchmark. It measures the
current DIRECT system and does not justify prompt tuning, a new capability, or
production enablement. The low score is valid pilot evidence because the
population, evaluator, runtime context, model configuration, and call budget
were frozen before provider exposure.

## Classification

`M25_LIVESQLBENCH_DIRECT_PILOT_COMPLETED`

## Next milestone

The frozen 18-case DIRECT pilot is complete. Review its accuracy and failure
distribution before deciding whether the next step is a full 180-case
LiveSQLBench run or targeted semantic analysis.
