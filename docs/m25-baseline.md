# M2.5 Holdout Baseline

## Run history

- `20260902T224049Z` is an `INVALID_PROVIDER_RUN`; it is preserved unchanged
  and is not a 0% accuracy result or valid M2.5 evidence.
- The provider smoke test succeeded with `gpt-5.6-luna`, structured JSON output,
  and reasoning effort `none`.
- `20260902T225639Z` is the first frozen primary empirical run. It used only
  `FULL_COMPACT + ONE_SHOT` and `FULL_COMPACT + GROUNDED`.

This report is generated from the frozen M2.5 holdout. No repair loop, semantic layer, value profiling, or golden-example retrieval is used.

- Run ID: `20260902T225639Z`
- Dataset SHA-256: `739c2c057617a7b841f1b002f4c91e18378459b3608bfa8c1f31aefcfd1f2499`
- Model: `gpt-5.6-luna`
- Experiment: `primary`
- Cells: `full_compact_one_shot, full_compact_grounded`

## Strategy comparison

| Metric | M2_ONE_SHOT | M25_GROUNDED | Delta |
|---|---:|---:|---:|
| Result equivalence count | 24 | 19 | -5 |
| Result equivalence rate | 0.4444444444444444 | 0.35185185185185186 | -0.09259259259259256 |
| Parse success | 54 | 44 | -10 |
| Plan acceptance | 47 | 39 | -8 |
| Execution success | 47 | 39 | -8 |
| Average input tokens | 917.425925925926 | 1857.734693877551 | 940.3087679516251 |
| Average output tokens | 56.370370370370374 | 129.85714285714286 | 73.48677248677248 |
| Average latency (ms) | 1470.038222833224 | 3043.5816586731544 | 1573.5434358399305 |
| P50 latency (ms) | 1452.4537505003536 | 2992.54562400165 | 1540.0918735012965 |
| P95 latency (ms) | 1914.9529224985595 | 4395.845319001814 | 2480.892396503254 |
| Provider calls attempted | 54 | 98 | 44 |

Provider calls succeeded were 54 and 93 respectively; grounded had 5 provider
failures and 5 successful intents rejected by schema-visibility validation
before SQL generation. No retries were used. The extra grounded call increased
average generation latency by 1573.54 ms and average per-question input/output
tokens by 940.31/73.49. Pricing is not configured, so API cost is not computed.

## Primary result

One-shot result equivalence was 24/54 (44.44%). Grounded result equivalence was
19/54 (35.19%), a raw delta of -5 questions (-9.26 percentage points). This is
an observed holdout result, not a significance claim. Grounding did not improve
this run's correctness and reduced operational completion because of provider
and intent-validation failures.

Pairwise outcomes: BOTH_CORRECT 19, ONE_SHOT_ONLY_CORRECT 5,
GROUNDED_ONLY_CORRECT 0, BOTH_INCORRECT 30.

## Category result equivalence

| Category | One-shot | Grounded | Delta |
|---|---:|---:|---:|
| aggregation | 1.0000 | 1.0000 | 0.0000 |
| date_filtering | 0.8333 | 0.8333 | 0.0000 |
| group_by | 1.0000 | 0.6667 | -0.3333 |
| joins | 0.3333 | 0.1667 | -0.1667 |
| multi_table_joins | 0.5000 | 0.3333 | -0.1667 |
| ratios | 0.1667 | 0.1667 | 0.0000 |
| simple_filters | 0.1667 | 0.0000 | -0.1667 |
| top_k | 0.0000 | 0.0000 | 0.0000 |
| window_functions | 0.0000 | 0.0000 | 0.0000 |

## full_compact_one_shot diagnostics

Failure taxonomy: `{"WRONG_COLUMN": 20, "WRONG_TABLE": 3}`
Grounding metrics: `{"intent_success_count": 0}`

## full_compact_grounded diagnostics

Failure taxonomy: `{"WRONG_COLUMN": 18, "WRONG_ORDERING": 1, "WRONG_TABLE": 1}`
Grounding metrics: `{"aggregation_agreement_count": 21, "diagnostic_count": 44, "grouping_agreement_count": 32, "intent_success_count": 44, "join_agreement_count": 43, "limit_agreement_count": 44, "ordering_agreement_count": 39, "requested_columns_present_count": 44, "requested_tables_present_count": 44, "sql_intent_column_agreement_count": 15, "sql_intent_table_agreement_count": 42, "window_agreement_count": 42}`

## Operational outcomes

One-shot parse success was 54/54, M1 plan acceptance 47/54, and execution
success 47/54; the seven non-executed cases were policy rejections. Grounded
parse success was 44/54, M1 plan acceptance 39/54, and execution success 39/54;
there were five provider failures, five schema-linking/visibility failures, and
five policy rejections. No query-cost or database execution failures occurred.

The persisted machine-readable artifacts are under
`evaluation/results/m25/20260902T225639Z/`. Provider error details are bounded
by the adapter; the run artifact records the failure stages but does not expose
credentials, headers, prompts, or result rows.

## Limitations

This is a 54-question frozen ordinary-SQL holdout. It is not the future M8 benchmark. No causal claim is made beyond this controlled ablation, and no generation setting was tuned against the holdout.
