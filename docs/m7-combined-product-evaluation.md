# M7 — Combined Product Evaluation & Residual Failure Attribution

## Executive result

M7 is a frozen internal mixed-workload evaluation of the current accepted
DecisionSQL query-resolution architecture. It is not a production-accuracy
claim and is not comparable to Defog, BIRD, or the standalone M3/M4 scores.

The new corpus contains 150 answerable cases: 100 DEV and 50 HOLDOUT. Gold
SQL was manually authored, then passed through the existing M1 plan/execute
path before any provider call. There was no exact question reuse from M3 or
M4, no exact M4 SQL reuse, and four normalized-SQL overlaps were retained as a
separate diagnostic because they were not exact leakage.

The two evaluated arms were:

- **P0 — DIRECT_ONE_SHOT:** existing direct SQL generation, then M1 and read-only execution.
- **P1 — CURRENT_BEST_COMBINED:** existing governed applicability/grounding and compiler, with the existing M4 R1 lexical+schema retriever (K=3) for residual direct queries, then M1 and execution.

P1 improved from 51/150 for P0 to 99/150, an absolute gain of 32 percentage
points. DEV was 63/100 versus 35/100; HOLDOUT was 36/50 versus 16/50.
P1 had zero provider protocol failures, zero execution errors, and six M1
policy rejections across the 150 cases. Its remaining 51 incorrect cases were
adjudicated with one primary cause each.

The residuals are **RESIDUAL_FAILURES_CONCENTRATED** around routing and direct
query structure: `ROUTING_ERROR` 13/51 (25.5%) and
`QUERY_STRUCTURE_ERROR` 12/51 (23.5%). The strongest next evidence milestone
is therefore **M8 — Routing Error Audit**, not value grounding, temporal
grounding, memory retuning, or a new resolver.

## Frozen corpus

| Item | Value |
|---|---|
| Corpus ID | `decisionsql-combined-mixed-workload` |
| Version | `m7-combined-v1` |
| Total / DEV / HOLDOUT | 150 / 100 / 50 |
| Full hash | `f169e3641d64bdb9f009bb97ee6f82e6589608579817ed948f2109ddf8921043` |
| DEV hash | `59bd32b94939b4f1c2d77ab79cc9feaf4622358009073f9c16f7108eee3a014f` |
| HOLDOUT hash | `740ab83e8700877e3bbe7bee6bcd61611a03d92755231dafebb7270d762be0e5` |
| Naturalness | 150 NATURAL, 0 BORDERLINE |
| Expected route | 30 GOVERNED, 120 DIRECT |

Family counts are: GOVERNED_METRIC 30, CLEAR_DIRECT_SIMPLE 20,
CLEAR_DIRECT_COMPLEX 25, RELATIONAL_COMPOSITION 20, FILTER_AGGREGATION 15,
RATIO_DERIVED 10, ORDER_TOP_N 10, WINDOW_COMPOSITION 10, and
EXACT_LITERAL_VALUE_FILTER 10. The corpus is in
[evaluation/fixtures/m7_manifest.json](/Users/ofk/DecisionSQL/evaluation/fixtures/m7_manifest.json).

All 150 reference queries parsed, passed M1, and executed successfully. The
30 governed references are deterministic materializations of the explicitly
selected M3 metric/dimension targets; the 120 direct references are manually
authored SQL. Both sets were independently checked through M1 and PostgreSQL
execution before provider evaluation. The primary evaluator compares materialized result semantics with the existing
numeric tolerance, NULL normalization, duplicate preservation, and unordered
multiset behavior.

## Run configuration and accounting

The provider configuration was `gpt-5.6-luna`, reasoning `none`, and
temperature omitted (`None`) to match prior accepted evaluation convention.
M3 was explicitly enabled in the evaluation harness; M4 used
`m4-retriever-v1`, R1 lexical+schema overlap, K=3, with the frozen corpus
hash `f871bd31649af8284bde0264aa6e8032b8ed2a2163f930d28df97d8e764087ae`.
The M3 contract hash was
`0463a10ecd3dbb414d11559f8559c604748f2b131a078d66ec79c4993d70eb3c`.

Protocol smoke used three calls: one P0 direct, one P1 residual-direct, and
one P1 governed. All passed. DEV used 100 P0 calls and 100 P1 routing attempts;
P1 used 100 M3 grounding calls and 68 direct SQL generation calls. HOLDOUT
used 50 P0 calls and 50 P1 routing attempts; P1 used 50 grounding calls and
32 direct SQL generation calls. There were no retries, repairs, judge calls,
embeddings, rerankers, or SQL calls outside the intended generation paths.

## Results

| Split | P0 | P1 | Delta |
|---|---:|---:|---:|
| DEV | 35/100 (35%) | 63/100 (63%) | +28 pp |
| HOLDOUT | 16/50 (32%) | 36/50 (72%) | +40 pp |
| Combined | 51/150 (34%) | 99/150 (66%) | +32 pp |

Combined paired outcomes were BOTH_CORRECT 44, P1_ONLY 55, P0_ONLY 7, and
BOTH_INCORRECT 44. P1 route accuracy was 88% DEV and 84% HOLDOUT. Governed
recall was 100% on both splits, while governed precision was 62.5% DEV and
55.6% HOLDOUT: the observed routing weakness was false-governed routing of
direct questions.

P1 expected-governed accuracy was 20/20 DEV and 10/10 HOLDOUT. Expected-direct
accuracy was 43/80 DEV (53.8%) and 26/40 HOLDOUT (65.0%). Among P1 actual
direct paths, M4 returned a hit for 68/68 DEV cases (39 correct) and 31/32
HOLDOUT cases (22 correct); no `MEMORY_OVERTRANSFER` was observed. This does
not establish that retrieval caused every direct failure: retrieval miss was
only assigned when the frozen evidence met the taxonomy’s causal requirement.

Per-family execution-equivalence:

| Family | N | DEV P0/P1 | HOLDOUT P0/P1 |
|---|---:|---:|---:|
| GOVERNED_METRIC | 30 | 5/20 of 20 | 1/10 of 10 |
| CLEAR_DIRECT_SIMPLE | 20 | 8/10 of 13 | 3/6 of 7 |
| CLEAR_DIRECT_COMPLEX | 25 | 4/6 of 17 | 2/3 of 8 |
| RELATIONAL_COMPOSITION | 20 | 9/11 of 13 | 3/6 of 7 |
| FILTER_AGGREGATION | 15 | 8/9 of 10 | 4/2 of 5 |
| RATIO_DERIVED | 10 | 0/1 of 7 | 0/1 of 3 |
| ORDER_TOP_N | 10 | 1/5 of 7 | 0/1 of 3 |
| WINDOW_COMPOSITION | 10 | 0/1 of 7 | 2/3 of 3 |
| EXACT_LITERAL_VALUE_FILTER | 10 | 0/0 of 6 | 1/4 of 4 |

The notation above is `P0/P1 correct of N in that split`.

The exact-literal family produced no DEV successes for either arm, but this
was adjudicated as filter/query behavior, not automatically as value
grounding. M6 remains `VALUE_GROUNDING_WEAK_EVIDENCE`.

## Residual failure attribution

The failure taxonomy was frozen before HOLDOUT. Each incorrect P1 case has one
primary cause, optional secondary tags, concise deterministic evidence, route
and memory provenance, and an adjudication status. The split artifacts are
`dev_failure_attribution.json` and `holdout_failure_attribution.json` under
the M7 run directories.

Combined primary distribution over 51 P1 residual failures:

| Primary cause | Count | Share of all 150 | Share of residuals |
|---|---:|---:|---:|
| ROUTING_ERROR | 13 | 8.7% | 25.5% |
| QUERY_STRUCTURE_ERROR | 12 | 8.0% | 23.5% |
| FILTER_ERROR | 9 | 6.0% | 17.6% |
| M1_POLICY_REJECTION | 6 | 4.0% | 11.8% |
| WINDOW_COMPOSITION_ERROR | 5 | 3.3% | 9.8% |
| DERIVED_METRIC_FORMULA_ERROR | 4 | 2.7% | 7.8% |
| ORDER_LIMIT_ERROR | 2 | 1.3% | 3.9% |

Top-1 residual share is 25.5%; top-3 share is 66.7%. `ROUTING_ERROR` meets the
roadmap evidence rule of at least 10 confirmed/strongly-supported cases and at
least 20% of residual failures. The next milestone should investigate route
false positives and their interaction with direct generation. It should not
silently change routing during M7.

## Interpretation and roadmap

M3 translated its standalone semantic benefit into a clear combined-system
gain on governed cases: P1 got every expected-governed case correct in both
splits, while P0 did not. However, the route’s false-governed behavior is a
real combined-system residual because direct questions were compiled as
governed metrics and then produced the wrong result. This is earlier than SQL
composition and therefore receives primary attribution.

M4 remains useful on residual direct questions, but the mixed workload does
not justify retriever tuning: there was no overtransfer, and the remaining
direct failures are distributed across structural, filter, ratio, order, and
window families. The data is insufficient to reopen M6 or temporal work.

The combined architecture is classified **COMBINED_ARCHITECTURE_PARTIALLY_VALIDATED**:
the accepted layers materially improve the frozen internal workload, but 66%
execution-equivalence is not a basis for a broad production claim. Residuals
are classified **RESIDUAL_FAILURES_CONCENTRATED** because routing is a
coherent, measured, roadmap-actionable primary family and the top three causes
account for 66.7% of residuals.

No unsupported-question diagnostic set was created. M7 intentionally evaluates
answerable execution paths only. No natural-language answer synthesis was
added; the endpoint is executed result equivalence.

## Preserved boundaries

M1, M3, M4, M5, and M6 runtime behavior were not changed. Production defaults
were not changed. No M6.1 benchmark was created. Defog, BIRD, PRACTIQ, M3,
M4, and M5 benchmarks were not rerun. Existing frozen hashes remain historical
and unchanged. The new code is evaluation-only in
[evaluation/m7_benchmark.py](/Users/ofk/DecisionSQL/evaluation/m7_benchmark.py)
and [evaluation/run_m7.py](/Users/ofk/DecisionSQL/evaluation/run_m7.py).

The next milestone is exactly one:

**M8 — Routing Error Audit**

It outranks value grounding, temporal grounding, memory retuning, and a new
SQL resolver because false-governed routing is the largest confirmed primary
residual family and is observed consistently in DEV and HOLDOUT. M8 should be
another frozen diagnostic study before any runtime change.
