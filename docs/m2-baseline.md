# M2 Baseline Evidence

- Run ID: `m2-real-20260903`
- Evaluation timestamp: `2026-09-02T21:19:58.680337+00:00`
- Provider: `openai-compatible`
- Model: `gpt-4o-mini`
- Dataset: `evaluation/datasets/m2_baseline.json`
- Dataset SHA-256: `5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87`

## Methodology

The same 48-question deterministic dataset, model, prompt contract, generation settings, M1 policy, and PostgreSQL reader were used in both runs. The only intended difference was full queryable schema context versus retrieved/bounded context. Primary correctness is result equivalence against the gold query result; SQL text equality is not used.

Generation settings: `temperature=0`, structured JSON response format, PostgreSQL chat-completions contract.

## Evaluator correction and recomputation

The original M2 run reported result equivalence of **16.67% (8/48)** for full-schema context and **12.50% (6/48)** for retrieved context. A subsequent forensic audit found that the historical evaluator required identical output column labels, incorrectly treating harmless alias differences as failures.

The results were recomputed as `m2-result-equivalence-v2-ordinal-columns`. The corrected contract validates equal output arity, compares values by ordinal position, preserves duplicate multiplicity, supports ordered or unordered comparison according to the case, normalizes NULL/date/numeric values, and retains numeric tolerance. Structural divergence is diagnostic only and does not override execution equivalence.

Corrected results are persisted separately under `evaluation/results/m2/m2-real-20260903/recomputed/`. The original run artifacts remain unchanged. The recomputation used only the persisted 96 generated SQL statements and gold SQL; it made zero new LLM calls and did not change prompts, retrieval, model settings, gold SQL, or M1 policy.

## Primary and pipeline results

| Metric | Full Schema | Retrieved Context | Delta (retrieved - full) |
|---|---:|---:|---:|
| Result equivalence | 0.1667 | 0.1250 | -0.0417 |
| Parse success | 1.0000 | 1.0000 | 0.0000 |
| Plan acceptance | 1.0000 | 1.0000 | 0.0000 |
| Execution success | 1.0000 | 1.0000 | 0.0000 |
| Average context tables | 8.0000 | 3.0208 | -4.9792 |
| Average context columns | 41.0000 | 17.7708 | -23.2292 |
| Average prompt tokens | 766.0000 | 349.5625 | -416.4375 |
| Average generation latency (ms) | 1044.9143 | 1100.6204 | 55.7061 |

## Counts and failure attribution

| Metric | Full Schema | Retrieved Context |
|---|---:|---:|
| Total questions | 48 | 48 |
| Result-equivalent | 8 | 6 |
| Parse success | 48 | 48 |
| Plan accepted | 48 | 48 |
| Execution success | 48 | 48 |
| Query cost rejection | 0 | 0 |
| Policy rejection | 0 | 0 |
| Generation failure | 0 | 0 |
| Retrieval miss (diagnostic) | 0 | 1 |

Failure attribution is assigned to the earliest observed pipeline stage. An executed but non-equivalent result is `RESULT_INCORRECT`; retrieval hit is reported separately and is not automatically treated as the cause of an incorrect result.

### Failure modes

| Attribution | Full Schema | Retrieved Context |
|---|---:|---:|
| RESULT_INCORRECT | 40 | 42 |

## Retrieval and context bounds

Retrieved-context hit rate: **0.9792**. The configured retrieved-mode bounds were recorded in metadata; selected context sizes are reported above. Full mode includes all queryable catalog objects and is the ablation control.

## Token and latency observations

Full schema tokens: prompt total=36768, completion total=1852, p50 latency=955.9496 ms, p95 latency=1599.8210 ms.
Retrieved context tokens: prompt total=16779, completion total=1904, p50 latency=1107.9731 ms, p95 latency=1481.1627 ms.

## Category breakdown

| Category | Full Schema | Retrieved Context |
|---|---:|---:|
| date_filtering | 0.2000 (1/5) | 0.0000 (0/5) |
| group_by | 0.4000 (2/5) | 0.6000 (3/5) |
| joins | 0.1667 (1/6) | 0.0000 (0/6) |
| multi_table_joins | 0.0000 (0/6) | 0.0000 (0/6) |
| ratios | 0.0000 (0/3) | 0.0000 (0/3) |
| simple_aggregation | 0.2500 (2/8) | 0.2500 (2/8) |
| simple_filters | 0.2500 (2/8) | 0.1250 (1/8) |
| top_k | 0.0000 (0/4) | 0.0000 (0/4) |
| window_functions | 0.0000 (0/3) | 0.0000 (0/3) |

## Limitations

This is a 48-question M2 baseline over the synthetic commerce database, not the future M8 benchmark. No statistical significance is claimed. No retrieval or prompt tuning was performed after observing these results. No semantic metrics, value profiling, repair, clarification, answer synthesis, tenant/RLS, or UI behavior is measured here.

M1 remains the sole SQL planning and execution authority; LLM output is always an untrusted `SqlCandidate`.
