# M8 — Routing Error Audit

## Executive conclusion

M8 classifies the routing redesign as `ROUTING_NOT_PRIMARY_RECOVERABLE_BOTTLENECK`.

The audit reused the 20 false-governed cases from the frozen M7 evaluation. All
20 were expected `DIRECT` but actually routed `GOVERNED`; 13 were incorrect
under the current combined path and 7 were correct. The frozen M4 direct
counterfactual recovered 6 of the 13 harmful cases and preserved all 7
currently-correct cases. That is 46.15% harmful recoverability, zero harmless
regressions, and a net observed gain of 6 cases. It does not meet the
precommitted broad redesign threshold (8/13 recovered) or any narrow-fix
threshold.

The result supports the partial-semantic-coverage hypothesis as an offline
diagnosis: 9 of the 13 harmful cases were marked `PARTIAL` governed coverage.
It does not justify changing the router. The direct counterfactual still had
7 failures, transferring primarily to query structure and related direct-path
composition failures. The next milestone is therefore:

**M9 — Direct-Path Query Structure Failure Audit**

This document is an evaluation record, not a production routing proposal.

## M7 evidence and frozen inputs

M7 measured the current accepted combined architecture (`P1`) against a direct
one-shot baseline (`P0`) on a new 150-question internal mixed workload:

| Arm | DEV | HOLDOUT | Total |
|---|---:|---:|---:|
| P0 direct one-shot | 35/100 (35%) | 16/50 (32%) | 51/150 (34%) |
| P1 current combined | 63/100 (63%) | 36/50 (72%) | 99/150 (66%) |

P1 routed 130/150 correctly at the route-label level. Expected governed cases
were 30, with 30/30 governed-path results correct. Expected direct cases were
120, with 69/120 correct. The M8 slice is derived from persisted M7 records;
M7 was not rerun.

M7 anchors remain:

- corpus: `f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043`
- DEV: `59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f`
- HOLDOUT: `740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5`
- M3 contract: `0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c`
- M4 corpus: `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`

## Current router and compiler boundary

`GovernedMetricRouteService` asks the governed-metric grounding provider for
an applicability decision, metric name, and dimensions. An applicable result
is converted to a `MetricRequest`, validated against the semantic contract,
compiled by `MetricCompiler`, and sent to M1. A non-applicable result falls
back to the direct path; with the accepted evaluation configuration that path
uses the frozen M4 runtime. The router currently does not first inspect the
whole question for operations outside the governed request contract. Metric
recognition is therefore sufficient to enter the governed attempt, subject
to the existing grounding, contract, compiler, M1, and execution checks.

The compiler currently represents catalog-owned measures and ratios, up to two
validated dimensions, built-in eligibility predicates, safe relationship-graph
paths, aggregation, and the ratio SQL shape implemented by M3. The typed
request does not represent arbitrary explicit filters or HAVING, general
ordering/top-N, window operations, custom formulas, subqueries, or arbitrary
composition. A ratio metric is supported when it is itself a governed catalog
object; that does not make every user-derived ratio governed.

This is the boundary tested by M8. It is not a new capability inventory for
runtime use.

## Frozen audit slice

The M8 corpus is `decisionsql-m8-false-governed-audit`, version
`m8-routing-audit-v1`, with 20 cases: 12 DEV and 8 HOLDOUT. Its hashes are:

- full: `e42d02cd04e31725eb38cf9da235787a410c28b3bd3b2397b35323754a1b319c`
- DEV: `030111f0f9ccf83dae7d9e0d571700bf3561f09d0ac0237a577639a7b599d400`
- HOLDOUT: `6429cfd9ece631d6e71374dd99d2bebf8d9ee89be079871154111f4134975a5a`

The pre-provider validation passed: unique IDs, exact route invariant,
13/20 harmful and 7/20 harmless cases, and split counts all matched the
persisted M7 evidence. Coverage was FULL=4, PARTIAL=12, NONE=4. Sixteen
expected-route annotations were `CONFIRMED_DIRECT`; four were
`POSSIBLY_GOVERNED`. No annotation was changed and no M7 label error was
declared.

The slice families were: `CLEAR_DIRECT_COMPLEX` 5,
`RELATIONAL_COMPOSITION` 6, `FILTER_AGGREGATION` 5, `RATIO_DERIVED` 1,
`ORDER_TOP_N` 2, and `WINDOW_COMPOSITION` 1.

## Mechanism audit

The precommitted taxonomy produced this distribution across all 20 cases:

| Mechanism | All | Harmful | Harmless |
|---|---:|---:|---:|
| R1 metric-intent overmatch | 4 | 4 | 0 |
| R2 partial semantic coverage | 1 | 0 | 1 |
| R3 unsupported filter extension | 3 | 1 | 2 |
| R4 unsupported order/top-N extension | 1 | 1 | 0 |
| R5 unsupported window extension | 2 | 2 | 0 |
| R6 unsupported derived-formula extension | 1 | 1 | 0 |
| R7 dimension/grain overmatch | 4 | 4 | 0 |
| R8 relationship-scope overmatch | 0 | 0 | 0 |
| R9 lexical/alias collision | 0 | 0 | 0 |
| R10 other | 4 | 0 | 4 |
| R11 undetermined | 0 | 0 | 0 |

The largest mechanism shares are 4/20 (20%) overall and 4/13 (30.77%) among
harmful cases, tied by R1 and R7. No single mechanism met the narrow-fix
threshold. Unsupported operation annotations across the slice included
dimension 8, relationship traversal 6, measure 6, grain 5, explicit filter 3,
order 3, HAVING 2, window 2, top-N 2, and smaller counts for running aggregate,
distinct entity count, aggregation, custom formula, and temporal boundary.

Coverage was FULL=4, PARTIAL=12, NONE=4 overall; among harmful cases it was
FULL=0, PARTIAL=9, NONE=4. Thus the partial-coverage hypothesis is supported
as a case-audit explanation, but the recoverability test below does not support
router redesign.

## Harmful versus harmless misroutes

The 7 harmless false-governed cases were not treated as evidence that the
router was correct. Four were likely representable by the frozen M3 contract
(`POSSIBLY_GOVERNED`), and three were current-governed successes where the
direct path also remained correct. The fixture did not produce a direct-path
regression among these seven.

The 13 harmful cases include metric-intent overmatch, dimension/grain
overmatch, unsupported window/order/filter extensions, and one derived-formula
extension. The exact route mechanism is an early causal diagnosis; it does not
imply that sending the same question direct will succeed.

## Counterfactual protocol

Each exact slice question was forced through the accepted M4 direct path once:

`m4-retriever-v1`, R1 lexical plus schema overlap, K=3, one SQL-generation
call, unchanged M1 planning/execution, and the frozen M7 result-equivalence
evaluator. The current P1 governed result and P0 result were reused from M7.

The run made 20 provider calls, all 20 produced valid provider results, and no
retry, repair, second candidate, judge, embedding, reranker, M3, or P0 call was
made. All 20 had a memory hit. SQL parsing succeeded for 20/20; M1 accepted
19/20, with one policy rejection and zero cost rejections. Execution succeeded
for 19/20.

| Counterfactual outcome | Count |
|---|---:|
| Direct result correct | 13/20 (65%) |
| Harmful cases recovered | 6/13 (46.15%) |
| Harmful cases not recovered | 7/13 |
| Harmless cases also correct | 7/7 |
| Harmless cases regressed | 0/7 (0%) |

Recoverability by split was 5/8 harmful DEV cases and 1/5 harmful HOLDOUT
cases. The observed exact-slice estimate is 99 + 6 - 0 = 105/150 (70%),
which is +4 percentage points over historical P1. It is an observed
counterfactual under a stochastic provider, not a guaranteed future-router
score or an upper bound.

The seven counterfactual direct failures transferred to three
`QUERY_STRUCTURE_ERROR`s, one `WINDOW_COMPOSITION_ERROR`, one
`DERIVED_METRIC_FORMULA_ERROR`, one `ORDER_LIMIT_ERROR`, and one M1 policy
rejection. This transfer is the principal reason routing is not the primary
recoverable bottleneck.

Counterfactual latency was 2,831.86 ms average, 2,738.56 ms p50, and
5,473.74 ms p95. Provider generation was 2,819.14 ms average, 2,723.93 ms
p50, and 5,457.38 ms p95. Retrieval instrumentation followed the existing
M4 path and reported 2,831.74 ms average, 2,738.50 ms p50, and 5,473.66 ms
p95. Total tokens were 22,651 input and 3,000 output. These are diagnostic
measurements, not production latency claims.

## Decision

The precommitted broad threshold required at least 8/13 harmful recoveries,
at most 2/7 harmless regressions, a coherent mechanism covering at least 8
harmful cases, and net gain of at least 6. The observed net gain met only the
last condition. The narrow threshold was also not met: R1 recovered 2/4, R3
1/1, R4 1/1, R5 1/2, R6 0/1, and R7 1/4, with no harmless regressions.

Therefore the final M8 classification is:

`ROUTING_NOT_PRIMARY_RECOVERABLE_BOTTLENECK`

The capability-based applicability direction is not supported for promotion
by this audit alone. Production routing was not changed. M7 remains frozen,
including its four possible governed-route annotation diagnostics.

The next milestone is **M9 — Direct-Path Query Structure Failure Audit**.
It outranks a routing redesign because 7/13 cases not recovered by the direct
counterfactual failed in direct query composition, while no routing mechanism
met the precommitted recovery threshold. It should remain evaluation-first and
must use new questions; the 20-case M8 slice cannot both discover and validate
a future rule.

## Scope and integrity record

- SQL-generation calls: 20 counterfactual calls only.
- Repair calls: 0.
- LLM judge calls: 0.
- Embedding calls: 0.
- Reranker calls: 0.
- New M3 grounding calls: 0.
- New P0 calls: 0.
- M1, M3, M4, and M5 were unchanged; M5 and M6 were not reopened.
- Runtime defaults and production routing were unchanged.
- Defog, BIRD, PRACTIQ, M3, M4 full, and M7 full benchmarks were not rerun.
- PostgreSQL was used read-only for frozen reference/counterfactual execution;
  no database mutations were made.
- No secrets or raw sensitive telemetry were added.

Artifacts are evaluation-only under `evaluation/results/m8/audit-20260904/`.
The implementation is limited to the M8 audit builder/runner and unit tests;
there is no production router module. No commit or push was made.
