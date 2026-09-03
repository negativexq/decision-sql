# M2.8 Result-Shape Contract Ablation

## Scope and evidence status

This is a DEVELOPMENT ONLY experiment on the consumed 48-question M2 dataset.
No M2.8 holdout was created or consumed because the development screen was
neutral. The M2.7 holdout remains untouched.

The first implementation run, `evaluation/results/m28/dev/20260903T005711Z`,
was preserved but superseded because the deterministic validator did not resolve
SQL table aliases when matching physical `source_hint` values. The corrected
run is `evaluation/results/m28/dev/20260903T011500Z`; no prompt, model,
schema, evaluator, or M1 policy tuning was made.

- Dataset: `evaluation/datasets/m2_baseline.json`
- Dataset SHA-256: `5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87`
- Model: `gpt-5.6-luna`
- Reasoning: `none`
- Sampling: temperature omitted; provider default
- Context: `FULL_COMPACT`
- Generation: `ONE_SHOT`
- Endpoint: OpenAI-compatible Chat Completions
- Evaluator: `m2-result-equivalence-v2-ordinal-columns`

## Contract

`ResultShapeProposal` is intentionally narrower than the rejected M2.5
`QueryIntent`:

```text
ResultShapeProposal
├── outputs[]
│   ├── semantic_label
│   ├── kind: PHYSICAL_COLUMN | DERIVED_VALUE
│   └── source_hint: optional fully-qualified column
├── shape: ROW_FILTER | AGGREGATE | GROUPED_AGGREGATE | GROUPED_TOP_K
│         | RATIO | WINDOW | OTHER
├── explicit_limit: optional integer
└── explicit_order_direction: optional ASC | DESC
```

It contains no tables, joins, filters, formulas, business definitions, or
authorization state. It is untrusted generation guidance. The contract arm
still creates `SqlCandidate(source=LLM)` and uses only:

```text
question → ResultShapeProposal → SQL proposal → SqlCandidate
         → SqlSafetyService.plan() → QueryPlan → SqlSafetyService.execute()
```

The validator checks projection arity, physical output hints, and explicitly
requested limit/order direction after accepted M1 planning and before execution.
It is not an authorization mechanism and does not read gold SQL at runtime.

## Results

| Metric | Baseline | ResultShape contract | Delta |
|---|---:|---:|---:|
| Result equivalence | 16/48 (33.33%) | 16/48 (33.33%) | 0 (0.00 pp) |
| Parse success | 48/48 | 47/48 | -1 |
| M1 plan acceptance | 44/48 | 44/48 | 0 |
| Execution success | 44/48 | 39/48 | -5 |
| Policy rejection | 4 | 3 | -1 |
| Contract/generation-stage failures | 0 | 6 | +6 |
| Provider calls attempted | 48 | 95 | +47 |

Pairwise outcomes: BOTH_CORRECT 12, BASELINE_ONLY_CORRECT 4,
CONTRACT_ONLY_CORRECT 4, BOTH_INCORRECT 28. The development screen is neutral
on the primary metric and does not justify consuming a new holdout.

## Category result equivalence

| Category | Baseline | Contract | Delta |
|---|---:|---:|---:|
| simple_filters | 0/8 | 1/8 | +1 |
| simple_aggregation | 8/8 | 7/8 | -1 |
| group_by | 4/5 | 3/5 | -1 |
| joins | 1/6 | 3/6 | +2 |
| multi_table_joins | 0/6 | 0/6 | 0 |
| date_filtering | 3/5 | 2/5 | -1 |
| top_k | 0/4 | 0/4 | 0 |
| ratios | 0/3 | 0/3 | 0 |
| window_functions | 0/3 | 0/3 | 0 |

## Projection diagnostics

These are structural diagnostics, not the primary correctness score:

| Metric | Baseline | Contract |
|---|---:|---:|
| Exact projection arity | 41.67% | 64.58% |
| Extra projection rate | 37.50% | 0.00% |
| Missing projection rate | 43.75% | 79.17% |
| Exact physical projection match | 29.17% | 35.42% |
| Projection-only failure ceiling | 1 | 0 |

The contract reduced extra projections, but frequently generated an overly
small shape and then produced a correspondingly incomplete SQL projection.
ResultShape proposal quality was: output-arity accuracy 64.58%, required-output
recall 27.08%, and extra-output precision 64.58%. Shape proposal success was
48/48; 39/48 generated plans passed shape validation. One proposal referenced
an unavailable wildcard and five accepted proposals disagreed with generated
physical expressions.

The offline projection-only ceiling from the existing persisted M2.7 Luna
development output was 1 case. This is not a rewritten execution score.

## Failure taxonomy

The run-local conservative taxonomy for genuinely incorrect or rejected cases:

| Primary cause | Baseline | Contract |
|---|---:|---:|
| REQUIRED_OBJECT_WRONG | 24 | 20 |
| PROJECTION_OVERSELECTION | 1 | 0 |
| AGGREGATION_GRAIN_ERROR | 0 | 2 |
| FILTER_CONSTRUCTION_ERROR | 0 | 1 |
| ORDER_TOPK_ERROR | 1 | 1 |
| WINDOW_COMPOSITION_ERROR | 2 | 2 |
| SQL_COMPOSITION_ERROR | 0 | 2 |
| POLICY_REJECTION | 4 | 3 |
| PROVIDER_FAILURE | 0 | 1 |

The taxonomy is diagnostic and is not substituted for result equivalence.
The large reduction in extra projections did not yield a net correctness gain;
the contract arm also introduced five pre-execution contract rejections and one
shape visibility failure. No provider HTTP failures occurred.

## Cost and latency

| Metric | Baseline | Contract |
|---|---:|---:|
| Input tokens | 43,968 | SQL 46,895 + shape 47,184 |
| Output tokens | 2,559 | SQL 2,071 + shape 2,422 |
| Average SQL latency | 1,316 ms | 1,341 ms |
| Average shape latency | — | 1,306 ms |
| Average end-to-end generation latency | 1,316 ms | 2,618 ms |
| p50 end-to-end latency | 1,301 ms | 2,635 ms |
| p95 end-to-end latency | 1,695 ms | 3,169 ms |

No versioned pricing configuration exists, so API cost was not computed.
The contract required 48 shape calls and 47 SQL calls in addition to the
baseline arm's 48 SQL calls; one shape visibility failure stopped SQL
generation for its case.

## Decision

ResultShape improved the projection diagnostic but did not improve result
equivalence on the development data. It also increased calls and roughly
doubled end-to-end generation latency. Since this is not coherent positive
evidence, no new M2.8 holdout was created and no holdout evidence exists.

M1, the evaluator, FULL_COMPACT serializer, and the M2.7 holdout were not
changed. No semantic layer, value grounding, repair, candidate voting,
decomposition, or M3 work was started.
