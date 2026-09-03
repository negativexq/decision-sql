# M2.9 — Role-Aware Schema Representation Ablation

M2.9 tested whether concise, deterministic role, grain, and meaning metadata
improves the unchanged Luna one-shot Text-to-SQL path. Both arms used
`FULL_COMPACT`, reasoning `none`, provider-default sampling, the same Chat
Completions endpoint, evaluator, database fixture, and M1 safety boundary.

No business formulas, semantic metrics, database values, few-shot examples,
ResultShape, QueryIntent, retrieval, repair, or candidate voting were added.

## Role-aware representation

The server-owned registry contains:

- `RoleAwareTableMetadata(entity, grain, purpose)`
- `RoleAwareColumnMetadata(role, meaning, grain_scope, additive, event)`
- `ColumnRole`: `PRIMARY_KEY`, `FOREIGN_KEY`, `IDENTIFIER`,
  `DISPLAY_DIMENSION`, `CATEGORICAL_DIMENSION`, `TRANSACTION_MEASURE`,
  `ENTITY_MEASURE`, `MONETARY_MEASURE`, `EVENT_TIMESTAMP`, `STATUS`, and
  `QUANTITY`
- `Additivity`: `true`, `false`, or `unknown`

The role-aware serializer preserves all 8 queryable application tables, 41
queryable columns, and 9 relationships. It excludes
`customers.external_key`. Metadata is static, structural, deterministic, and
question-independent.

The current schema ambiguity inventory contains 9 repeated-name groups, 7
repeated-type groups, and 11 role groups. Coverage is 100% for table metadata
and queryable column role metadata. The audit found no values, formulas, or
benchmark answers in the serialized role-aware context.

Schema size:

| Representation | Characters | Estimated tokens (characters / 4) |
|---|---:|---:|
| Baseline `FULL_COMPACT` | 3,652 | 913 |
| Role-aware | 7,308 | 1,827 |
| Delta / ratio | +3,656 / 2.00x | +914 / 2.00x |

## Development screening — consumed diagnostic set

Dataset: existing `m2_baseline.json`, 48 questions. This is development-only
evidence, not unbiased holdout evidence.

| Metric | Baseline | Role-aware | Delta |
|---|---:|---:|---:|
| Result equivalence | 16/48 (33.33%) | 17/48 (35.42%) | +1 (+2.08 pp) |
| Parse success | 48/48 | 48/48 | 0 |
| M1 plan acceptance | 44/48 | 42/48 | -2 |
| Execution success | 44/48 | 42/48 | -2 |
| Provider calls | 48 | 48 | 0 |
| Input tokens | 43,968 | 83,040 | +39,072 (+88.9%) |
| Output tokens | 2,656 | 2,585 | -71 |
| Average generation latency | 1,441 ms | 1,394 ms | -47 ms |

Pairwise outcomes were `BOTH_CORRECT=16`,
`ROLE_AWARE_ONLY_CORRECT=1`, `BASELINE_ONLY_CORRECT=0`, and
`BOTH_INCORRECT=31`.

Development category result-equivalence counts:

| Category | Baseline | Role-aware |
|---|---:|---:|
| simple_filters | 0/8 | 1/8 |
| simple_aggregation | 8/8 | 8/8 |
| joins | 1/6 | 1/6 |
| multi_table_joins | 0/6 | 0/6 |
| date_filtering | 3/5 | 3/5 |
| group_by | 4/5 | 4/5 |
| top_k | 0/4 | 0/4 |
| ratios | 0/3 | 0/3 |
| window_functions | 0/3 | 0/3 |

The development screen was positive only in the protocol's qualitative sense:
one role-aware-only correction, no baseline-only regression, and object
selection root causes reduced from 24 to 21. It was not a large effect, and
role-aware metadata increased input context substantially and caused two more
policy rejections in this sample.

## Frozen M2.9 holdout

The holdout was created after the positive development screen and frozen before
quality evaluation:

- 54 questions, 6 in each of 9 categories
- SHA-256: `27eed0939171273fdf79132c3f4a0a4eb0a1f4413b3f53201856f60b1d760bb3`
- Gold validation: 54/54 M1 plan success and 54/54 execution success
- Run: `evaluation/results/m29/holdout/20260903T012530Z`
- No adaptive tuning was performed between arms

Holdout results:

| Category | Baseline | Role-aware | Delta |
|---|---:|---:|---:|
| simple_filters | 6/6 | 6/6 | 0 |
| aggregation | 6/6 | 6/6 | 0 |
| group_by | 6/6 | 6/6 | 0 |
| joins | 6/6 | 6/6 | 0 |
| multi_table_joins | 5/6 | 6/6 | +1 |
| date_filtering | 5/6 | 5/6 | 0 |
| top_k | 3/6 | 3/6 | 0 |
| ratios | 1/6 | 1/6 | 0 |
| window_functions | 0/6 | 0/6 | 0 |

| Metric | Baseline | Role-aware | Delta |
|---|---:|---:|---:|
| Result equivalence | 38/54 (70.37%) | 39/54 (72.22%) | +1 (+1.85 pp) |
| Parse success | 54/54 | 54/54 | 0 |
| M1 plan acceptance | 49/54 | 49/54 | 0 |
| Execution success | 49/54 | 49/54 | 0 |
| Provider calls | 54 | 54 | 0 |
| Provider failures | 0 | 0 | 0 |
| Policy rejections | 5 | 5 | 0 |
| Input tokens | 49,688 | 93,644 | +43,956 (+88.5%) |
| Output tokens | 2,531 | 2,625 | +94 (+3.7%) |
| Average latency | 1,436 ms | 1,559 ms | +123 ms (+8.6%) |
| p50 latency | 1,413 ms | 1,414 ms | +0.2 ms |
| p95 latency | 1,950 ms | 2,770 ms | +820 ms |

Pairwise outcomes were `BOTH_CORRECT=38`,
`ROLE_AWARE_ONLY_CORRECT=1`, `BASELINE_ONLY_CORRECT=0`, and
`BOTH_INCORRECT=15`.

On the corrected root-cause diagnostics, object-selection cases decreased from
8 to 7. Within that diagnostic, wrong dimension column cases decreased 5→4;
wrong measure column remained 3→3. No policy, aggregation, or filter root
cause improvement accompanied the one result-equivalence gain. Projection
extra rate decreased 1.85%→0%, but missing projection increased 42.59%→55.56%.

## Interpretation

Role-aware metadata produced a consistent but very small correctness signal:
`+1` on development and `+1` on the frozen holdout, with no baseline-only
regressions. It did not materially improve ratios or windows, and the holdout
still had 0/6 window equivalence in both arms. The schema became approximately
2x larger while using one provider call per question in each arm.

The evidence supports role metadata as a useful candidate representation, but
does not justify claiming it as a materially better default architecture. The
remaining bottleneck is mixed, with projection/SQL-shape behavior and ratio/
window composition more prominent than missing schema visibility. The one
recommended next experiment is a narrowly scoped **SQL-shape decomposition
ablation focused on window and ratio composition**, keeping the role-aware
schema fixed and comparing it with an explicitly defined decomposition arm.
This should be designed as a new experiment; it was not implemented in M2.9.

No versioned pricing configuration exists, so API cost was not computed. Token
usage and latency are persisted in the run artifacts. No hidden chain of
thought was requested or persisted. All generated SQL remained untrusted
`SqlCandidate(source=LLM)` input and could execute only through M1
`plan()` → `QueryPlan` → `execute()`.
