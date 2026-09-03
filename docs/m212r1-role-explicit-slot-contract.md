# M2.12R.1 — Role-Explicit Semantic Slot Contract Audit

## Result

Phase A passed and Phase B ran on the frozen 48-question development set. No
holdout was consumed.

The role-explicit contract fixed the targeted transport problem, but it did
not improve end-to-end correctness. The remaining dominant issue was output
grounding and semantic slot selection, not provider transport.

## Contract

The provider DTOs are separate from the strict internal `WindowQueryIR`:

| DTO | Fields |
|---|---|
| `LatestPerGroupRoleDTO` | `source_table`, `passthrough_columns`, `partition_columns`, `order_column`, `order_direction`, `tie_breaker_column`, `tie_breaker_direction` |
| `TopNPerGroupRoleDTO` | previous physical-role fields plus `top_n`, `tie_policy` |
| `LagLeadRoleDTO` | `source_table`, `passthrough_columns`, `window_direction`, `input_value_column`, `partition_columns`, `order_column`, `order_direction`, `offset_rows` |
| `RunningAggregateRoleDTO` | `source_table`, `passthrough_columns`, `input_value_column`, `aggregate_function`, `partition_columns`, `order_column`, `order_direction` |
| `MovingAggregateRoleDTO` | running fields plus `preceding_rows`, `include_current_row` |
| `RankingRoleDTO` | `source_table`, `passthrough_columns`, `ranking_function`, `partition_columns`, `order_column`, `order_direction` |
| `ShareOfTotalRoleDTO` | `source_table`, `passthrough_columns`, `input_value_column`, `partition_columns`, `scale_factor` |
| `MixedWindowRoleDTO` | `source_table`, `passthrough_columns`, bounded two-operation list |

Field counts are respectively 7, 9, 8, 7, 9, 6, 5, and 3 top-level fields.
The old ambiguous names (`source`, `outputs`, `target`, `partition_by`,
`order_by`, `direction`, `offset`, `n`, `aggregate`, `preceding`,
`include_current`, `scale`) are replaced by explicit semantic-role names.
Computed aliases are compiler-owned and are not provider fields.

Every `*_column`/`*_columns` value must be an existing queryable physical
column of `source_table`. Unqualified names are qualified only by a unique
source-local catalog lookup. There is no fuzzy matching, cross-table search,
or semantic repair.

## Phase A

Manifest:
`evaluation/datasets/m212r1_role_slot_diagnostic_manifest.json`

- source SHA: `bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3`
- 15 targeted computed-alias cases and 5 transported controls
- 20 one-call provider attempts; 20/20 provider successes
- JSON parse, DTO validation, column resolution, adapter, and internal IR
  validation: 20/20 each
- full transport: 20/20; targeted: 15/15; controls: 5/5
- computed-alias-as-physical-column: 0/15
- Phase A gate: passed

## Phase B

Artifact: `evaluation/results/m212r1/dev/20260903T172000Z/`

Gold compiler ceiling remained 100%: development 48/48 and holdout gold
validation 32/32.

| Metric | Baseline | Role-explicit IR |
|---|---:|---:|
| Result equivalence | 3/48 (6.25%) | 1/48 (2.08%) |
| Provider calls | 48 | 48 |
| Input tokens | 44,351 | 66,311 |
| Output tokens | 4,044 | 3,166 |
| Provider latency avg | 1,850.9 ms | 1,551.7 ms |
| Provider latency p50 | 1,719.0 ms | 1,523.8 ms |
| Provider latency p95 | 2,417.0 ms | 2,044.5 ms |

Pairwise outcomes: `BOTH_CORRECT=0`, `BASELINE_ONLY_CORRECT=3`,
`WINDOW_IR_ONLY_CORRECT=1`, `BOTH_INCORRECT=44`.

Role IR transport was 48/48 (100%), above the 95% threshold. The +10
percentage-point result criterion failed: delta was -2/48 (-4.17 pp). Pattern
results were:

| Pattern | Baseline | IR/compiler |
|---|---:|---:|
| Latest per group | 2/6 | 1/6 |
| Top-N per group | 1/6 | 0/6 |
| LAG/LEAD | 0/6 | 0/6 |
| Running aggregate | 0/6 | 0/6 |
| Moving aggregate | 0/6 | 0/6 |
| Ranking | 0/6 | 0/6 |
| Share of total | 0/6 | 0/6 |
| Mixed multi-window | 0/6 | 0/6 |

`MODEL_IR_MATCHES_GOLD=1`; `RESULT_MISMATCH_AFTER_CORRECT_IR=0`.
Passthrough precision was 78.19% and recall 92.01%; 29 cases had extra
outputs and 10 had missing outputs. Slot diagnostics: source 48/48, target
24/24, partition 41/48, primary order 35/48, direction 42/48, offset 6/6,
top-N 6/6, aggregate 12/12, ranking 6/6, moving frame 4/6, scale 4/6.

Role-confusion counts among transported DTOs:
`COMPUTED_ALIAS_AS_INPUT_VALUE=0`, `COMPUTED_ALIAS_AS_PASSTHROUGH=0`,
`COMPUTED_ALIAS_AS_ORDER_COLUMN=0`, `COMPUTED_ALIAS_AS_PARTITION_COLUMN=0`,
`NONEXISTENT_PHYSICAL_COLUMN=0`, `WRONG_PHYSICAL_INPUT_COLUMN=0`.

## Decision

The role contract passed the targeted Phase A test but failed the frozen Phase
B quality screen. It is not supported as a default architecture. The final
classification is **`SEMANTIC_SLOT_GROUNDING_BOTTLENECK`**: explicit role names
removed the computed-alias transport failure, but output, ordering, frame, and
other semantic choices remained unreliable.

The M2.12 holdout SHA is
`e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4`; it was
not consumed. The single next experiment is **verified structural example
grounding** for Window questions. No further field-renaming transport variant
should be created.

The credential-blocked run `evaluation/results/m212r1/diagnostic/20260903T170000Z/`
is operational history only and contains no model evidence. The valid Phase A
and Phase B runs are `20260903T171500Z` and `20260903T172000Z` respectively.

## Preservation and quality gates

M1, the evaluator, `FULL_COMPACT`, `WindowQueryIR`, and the Window compiler
remain unchanged. Both arms used one provider call per question; there was no
SQL fallback, semantic repair, retry, or second LLM call. M2.7 and M2.11
holdouts remain untouched.

- Pytest: 168 passed, 1 skipped
- PostgreSQL integration: 37 passed, 1 skipped
- Ruff: passed
- Mypy: passed
- `git diff --check`: passed
