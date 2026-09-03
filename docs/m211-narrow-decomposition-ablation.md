# M2.11 — Narrow SQL-Shape Decomposition Ablation

## Scope and holdout

This was a development/diagnostic experiment over the consumed M2 hard slice,
not unbiased final evidence. The slice contains 10 questions: 4 `top_k`, 3
`ratios`, and 3 `window_functions`. Its source dataset SHA-256 is
`5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87`.

A new 30-question holdout was created and frozen before provider evaluation:

- file: `evaluation/datasets/m211_holdout.json`
- SHA-256: `668f7ff7e58b6e7038f916bb542cb47399e0cefc9a2d85d125dfc3125aa6d090`
- distribution: 10 `top_k`, 10 `ratios`, 10 `window_functions`
- gold validation: 30/30 M1 plan success and 30/30 execution success
- status: **not consumed**, because development screening was negative

The M2.7 holdout remains untouched; its SHA-256 remains
`a540298e2776842c6b345f9984b21941c4d0f964447518cbaa12f98f34b8074c`.

## Plans

The decomposed arm uses three separate, untrusted Pydantic objects rather than
a generic plan:

- `TopKPlan`: `entity_outputs`, `measure` (`semantic_label`, `aggregation`,
  `components`), `group_by`, `order_direction`, and `limit`.
- `RatioPlan`: `numerator` and `denominator` components (each with
  `semantic_label`, `source_columns`, `aggregation`, `distinct`), `grain`, and
  optional `scale`.
- `WindowPlan`: `requested_outputs`, `window_function`, `partition_by`,
  `order_by`, and `order_direction`.

None contains SQL, a join graph, `FROM`/`WHERE`, authorization, business
formulas, or hidden reasoning. Each plan only guides the second SQL-generation
call. Generated SQL still becomes `SqlCandidate(source=LLM)` and goes through
M1 `plan()` and `execute()`.

## Development run

Run: `20260903T030000Z`; corrected persisted view:
`evaluation/results/m211/dev/20260903T030000Z/corrected-v2/`.

| Arm | Result equivalence | Calls | Provider failures | Plan accepted | Execution success |
|---|---:|---:|---:|---:|---:|
| Baseline | 0/10 (0%) | 10 SQL | 0 | 7/10 | 7/10 |
| Decomposed | 0/10 (0%) | 10 plan + 7 SQL = 17 | 3 plan | 5/10 | 5/10 |

Delta: 0 questions / 0 percentage points. Pairwise outcomes: `BOTH_INCORRECT`
10, with no baseline-only or decomposed-only correct cases.

Category results were 0/4 vs 0/4 for top-k, 0/3 vs 0/3 for ratios, and 0/3 vs
0/3 for windows. The policy-unconfounded top-k + window subset was also 0/7
for baseline and 0/4 for decomposed executable cases; three decomposed window
cases failed at plan generation.

## Plan quality and attribution

Offline plan-component diagnostics against gold structure were:

| Category | Component accuracy |
|---|---|
| Top-k | entity 2/4, measure 4/4, aggregation 3/4, grouping 2/4, direction 4/4, limit 1/4 |
| Ratios | numerator 0/2, denominator 0/2, aggregation 2/2, grain 2/2, scale 2/2; 1/3 plan calls invalid |
| Windows | function 1/1, partition 1/1, order 1/1, direction 1/1, outputs 0/1; 2/3 plan calls invalid |

Plan attribution counts were: `PLAN_CORRECT_SQL_CORRECT` 0,
`PLAN_CORRECT_SQL_WRONG` 0, `PLAN_WRONG_SQL_CORRECT` 0,
`PLAN_WRONG_SQL_WRONG` 7, `PLAN_INVALID` 3, and
`POLICY_BLOCKED_AFTER_CORRECT_PLAN` 0. These are diagnostics, not correctness
metrics.

Failure taxonomy counts were:

| Primary cause | Baseline | Decomposed |
|---|---:|---:|
| OBJECT_SELECTION_ERROR | 3 | 1 |
| FILTER_CONSTRUCTION_ERROR | 1 | 0 |
| AGGREGATION_GRAIN_ERROR | 0 | 0 |
| ORDER_TOPK_ERROR | 2 | 4 |
| WINDOW_COMPOSITION_ERROR | 0 | 0 |
| BUSINESS_SEMANTIC_INFORMATION_MISSING | 1 | 0 |
| POLICY_REJECTION | 3 | 2 |
| PROVIDER_FAILURE | 0 | 3 |

The three baseline ratio queries and two decomposed ratio queries were
rejected by the unchanged M1 policy because they used `NULLIF`; all five are
reported separately from model correctness. The policy-shadow diagnostic
performed no execution of rejected SQL.

## Cost and latency

Baseline used 10 calls, 9,164 input tokens, and 751 output tokens. Its average
SQL-call latency was 1,553 ms (p50 1,392 ms, p95 2,591 ms).

Decomposed used 10 plan calls and 7 SQL calls: 17 attempts total, 14
successes, and 3 provider failures. Successful plan calls used 6,674 input and
395 output tokens; successful SQL calls used 7,105 input and 437 output
tokens. Thus successful-call totals were 13,779 input and 832 output tokens,
without imputing tokens for failed calls. Average plan latency was 1,549 ms;
average successful SQL latency was 1,526 ms; average paired plan+SQL latency
for the seven cases reaching SQL was 3,075 ms.

No versioned pricing snapshot is configured, so API cost was not computed.

## Decision

The development screen failed: final result equivalence did not improve, no
decomposed-only correct case appeared, and plan failures added an extra failure
surface. The M2.11 holdout was therefore not consumed. Narrow decomposition is
rejected as a demonstrated improvement on this evidence; no selector,
candidate voting, M1 policy change, NULLIF allowlist change, semantic layer,
or M3 work was started.
