# M2.6 Reasoning Capacity Ablation

## Experimental configuration

- Model: `gpt-5.6-luna`
- Endpoint: OpenAI-compatible Chat Completions
- Sampling: temperature omitted; provider default
- Response format: `json_object`
- Architecture: `FULL_COMPACT + ONE_SHOT`
- QueryIntent, retrieval, repair, and retries: disabled
- Evaluator: `m2-result-equivalence-v2-ordinal-columns`

The prior `temperature=0` LOW development run remains preserved as
`INVALID_REASONING_COMPARISON`; the provider rejected all LOW requests before
generation. It is not included in the results below.

The LOW/default smoke request succeeded with one attempt and one structured
response. Smoke artifact:
`evaluation/results/m26/provider-smoke-low-default-20260903/metadata.json`.

## Development screening — diagnostic only

Persisted at `evaluation/results/m26/dev/20260903T003000Z/` using the existing
48-question M2 development dataset.

| Metric | NONE | LOW | Delta |
|---|---:|---:|---:|
| Result equivalence | 14/48 (29.17%) | 16/48 (33.33%) | +2 (+4.17 pp) |
| Parse success | 48/48 | 48/48 | 0 |
| M1 plan acceptance | 42/48 | 41/48 | -1 |
| Execution success | 42/48 | 41/48 | -1 |
| Provider calls | 48 | 48 | 0 |
| Input tokens | 43,968 | 43,968 | 0 |
| Output tokens | 2,731 | 4,771 | +2,040 |
| Reasoning tokens | 0 | 1,901 | +1,901 |
| Average latency | 1,374.5 ms | 2,016.0 ms | +641.5 ms |
| P50 / P95 latency | 1,372.1 / 1,860.2 ms | 1,805.3 / 3,776.6 ms | — |

Pairwise outcomes: 14 BOTH_CORRECT, 2 LOW_ONLY_CORRECT, 0 NONE_ONLY_CORRECT,
32 BOTH_INCORRECT. The positive development signal was modest and concentrated
in simple aggregation and date filtering; joins, multi-table joins, top-k,
ratios, and windows did not improve.

## Frozen holdout — primary evidence

Persisted at `evaluation/results/m26/holdout/20260903T010000Z/`.

The holdout contains 54 questions, six per category, and SHA-256
`c96473d64100370df6cfb86ac6afa53edba5d4af82772413b856bfe1579ba354`. Gold
validation was 54/54 M1 plan success and 54/54 execution success.

| Metric | NONE | LOW | Delta |
|---|---:|---:|---:|
| Result equivalence | 24/54 (44.44%) | 23/54 (42.59%) | -1 (-1.85 pp) |
| Parse success | 54/54 | 54/54 | 0 |
| M1 plan acceptance | 46/54 | 49/54 | +3 |
| Execution success | 46/54 | 49/54 | +3 |
| Provider calls | 54 | 54 | 0 |
| Input tokens | 49,569 | 49,569 | 0 |
| Output tokens | 2,999 | 5,834 | +2,835 (+94.5%) |
| Reasoning tokens | 0 | 2,378 | +2,378 |
| Average latency | 1,479.3 ms | 2,135.3 ms | +656.0 ms (+44.3%) |
| P50 / P95 latency | 1,466.2 / 1,926.4 ms | 2,024.5 / 3,292.5 ms | — |

Pairwise outcomes: 22 BOTH_CORRECT, 2 NONE_ONLY_CORRECT, 1 LOW_ONLY_CORRECT,
29 BOTH_INCORRECT.

### Category results

| Category | NONE | LOW | Delta |
|---|---:|---:|---:|
| simple_filters | 0/6 | 0/6 | 0 |
| aggregation | 6/6 | 5/6 | -1 |
| group_by | 4/6 | 4/6 | 0 |
| joins | 2/6 | 2/6 | 0 |
| multi_table_joins | 4/6 | 3/6 | -1 |
| date_filtering | 4/6 | 5/6 | +1 |
| top_k | 3/6 | 3/6 | 0 |
| ratios | 1/6 | 1/6 | 0 |
| window_functions | 0/6 | 0/6 | 0 |

Failure taxonomy was dominated by `WRONG_COLUMN` (NONE 18, LOW 22) and
`WRONG_TABLE` (4, 4). LOW also had 5 policy rejections and NONE had 6. There
were no provider failures or query-cost rejections. The holdout did not show a
reduction in `WRONG_COLUMN`, and LOW added one more incorrect result overall.

No pricing configuration is available; API cost was not computed.

## Decision

LOW is rejected as the preferred reasoning setting for this milestone. On the
frozen holdout it was 1/54 worse while using 94.5% more output tokens and 44.3%
more average generation latency. M2.6 is closed without changing the default
architecture or starting M3.

All generated SQL remained untrusted `SqlCandidate` data and could execute only
through M1 `plan()` and `execute()`. M1 was unchanged.
