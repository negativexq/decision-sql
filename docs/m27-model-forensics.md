# M2.7 Model Capacity and I/O Forensics

This is the corrected, development-only M2.7 forensic conclusion. Detailed
case-level model-visible inputs, SQL, structural signatures, and diffs are in
[`m27-model-forensics-corrected.md`](m27-model-forensics-corrected.md).

## Experiment

- Dataset: M2 development/diagnostic set, 48 questions
- Models: `gpt-5.6-luna` vs `gpt-5.6-terra`
- Architecture: `FULL_COMPACT + ONE_SHOT`
- Reasoning: `none`
- Sampling: temperature omitted; provider default
- QueryIntent, retrieval, repair, and candidate voting: disabled
- Model I/O capture: evaluation-only; no hidden reasoning requested or stored

The paired model-visible prompt and schema hashes matched for every question.
The forensic replay corrected only offline signature normalization; generated
SQL and persisted provider I/O were not changed.

## Development-only result

| Metric | Luna | Terra | Delta (Terra − Luna) |
|---|---:|---:|---:|
| Result equivalence | 15/48 (31.25%) | 15/48 (31.25%) | 0 (0.00 pp) |
| Parse success | 48/48 | 48/48 | 0 |
| M1 plan acceptance | 42/48 | 44/48 | +2 |
| Execution success | 42/48 | 44/48 | +2 |
| Input tokens | 43,968 | 43,968 | 0 |
| Output tokens | 2,628 | 3,207 | +579 |
| Average latency | 1,479.3 ms | 1,579.2 ms | +99.9 ms |
| P95 latency | 1,993.1 ms | 2,063.7 ms | +70.6 ms |

Pairwise outcomes: BOTH_CORRECT 14, BOTH_INCORRECT 32,
LUNA_ONLY_CORRECT 1, TERRA_ONLY_CORRECT 1.

Corrected visibility audit for both arms: gold tables 100%, gold columns 100%,
gold relationships 100%. This development result provides no coherent Terra
capacity signal: the headline score is tied and each model has one exclusive
correct answer, while Terra uses more output tokens and latency.

## Corrected forensic causes

These are diagnostic classifications, not authorization decisions. The
`EVALUATOR_OR_FIXTURE_WARNING` entries are result-equivalent cases with
structural divergence and therefore do not represent incorrect-result counts.

| Cause | Luna | Terra |
|---|---:|---:|
| ORDER_LIMIT_REASONING_ERROR | 19 | 22 |
| EVALUATOR_OR_FIXTURE_WARNING | 6 | 12 |
| POLICY_REJECTION | 6 | 4 |
| WINDOW_REASONING_ERROR | 3 | 3 |
| SCHEMA_SELECTION_ERROR | 2 | 3 |
| AGGREGATION_GRAIN_ERROR | 1 | 1 |
| SQL_COMPOSITION_ERROR | 2 | 0 |
| INPUT_SCHEMA_INSUFFICIENT | 0 | 0 |

Existing result-failure taxonomy counts were WRONG_COLUMN 25 vs 26 and
WRONG_TABLE 2 vs 3 (Luna vs Terra). The full structural diff and column
confusion summaries are persisted in
`evaluation/results/m27/dev/20260903T023000Z/recomputed-v2/`.

## Decision

Terra did not pass development screening. The M2.7 holdout was not consumed,
because the development comparison was neutral and Terra was slower and more
verbose. The frozen holdout remains available for a separately authorized
experiment.

The evidence points away from insufficient schema visibility and does not
demonstrate a model-capacity benefit. The dominant observed issue is mixed,
with order/limit and SQL-composition reasoning prominent; future work should
test an independently designed SQL-composition/decomposition hypothesis.
No tuning was performed from this forensic analysis.
