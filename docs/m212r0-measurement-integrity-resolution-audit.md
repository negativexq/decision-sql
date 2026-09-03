# M2.12R.0 — Offline Measurement Integrity & Resolution Audit

## Result

The audit used only the persisted M2.12R development run
`20260903T141500Z`, with zero provider calls and no holdout access. The
original M2.12R north-star result is confirmed:

| Metric | Original M2.12R | Corrected offline replay |
|---|---:|---:|
| Baseline result equivalence | 4/48 (8.33%) | 4/48 (8.33%) |
| Window IR result equivalence | 2/48 (4.17%) | 2/48 (4.17%) |
| Delta | -4.17 pp | -4.17 pp |
| Window IR transport | 27/48 (56.25%) | 27/48 (56.25%) |

No evaluator alias false negative was found. The canonical evaluator already
ignores output names and compares values by ordinal position. The error was in
the M2.12R diagnostic matcher: it compared physical output names as an
unordered set, causing two ranking IRs to be reported as gold matches even
though their output order was wrong.

Classification: **ORIGINAL_M2.12R_RESULT_CONFIRMED**.

## Evaluator path and integrity

The exact persisted-run paths are:

- Baseline: `evaluation/run_m212r.py::_run` → `TextToSqlService.run` →
  `_compare` → `evaluation.metrics.assess_query_results`.
- Window compiler: `evaluation/run_m212r.py::_run` →
  `WindowSqlCompiler.compile` → `SqlSafetyService.plan()` →
  `SqlSafetyService.execute()` → `_compare` → the same
  `evaluation.metrics.assess_query_results`.

Both arms therefore use the same result-equivalence implementation. Its
contract is:

- equal arity;
- ordinal column/value comparison;
- aliases ignored;
- duplicates preserved;
- unordered benchmark rows compared as sorted multisets;
- NULL normalization and numeric tolerance retained;
- order-sensitive cases retain row order.

The canonical evaluator was not changed. The diagnostic matcher in
`evaluation/run_m212r.py` was corrected from `set(physical_outputs)` to an
ordinal list comparison; this does not alter scoring.

## Three reported gold-matching IR cases

The detailed replay is in
`evaluation/results/m212r0/20260903T164500Z/gold_matching_ir_replay.json`.

| Question | Original verdict | Recomputed verdict | Finding |
|---|---:|---:|---|
| `m212-dev-latest-005` | true | true | Same columns and same ordinal values |
| `m212-dev-ranking-001` | false | false | `id` and `customer_id` swapped in output order; alias also differs |
| `m212-dev-ranking-004` | false | false | `id` and `order_id` swapped in output order; alias also differs |

Alias-only false negatives: **0**. The two ranking cases are real ordinal
projection mismatches, not alias-only mismatches. After the corrected matcher,
`MODEL_IR_MATCHES_GOLD` is 1 rather than the originally reported 3, and
`RESULT_MISMATCH_AFTER_CORRECT_IR` is 0 rather than 2.

The alias invariance tests cover physical aliases, computed-expression
aliases, compiler aliases, CTE/internal aliases, quoted/unquoted names, no
alias versus explicit alias, and preserve failure for ordinal value changes.

## Column-resolution audit

All 16 `COLUMN_RESOLUTION_FAILED` records were audited individually. The full
case table is in
`evaluation/results/m212r0/20260903T164500Z/column_resolution_cases.json`.

| Primary class | Count |
|---|---:|
| `TRUE_SEMANTIC_COLUMN_ERROR` | 0 |
| `REPRESENTATION_ONLY_COLUMN_ERROR` | 0 |
| `AMBIGUOUS_COLUMN_REFERENCE` | 0 |
| `NONEXISTENT_COLUMN` | 1 |
| `WRONG_SOURCE_RELATION` | 0 |
| `COMPUTED_ALIAS_USED_AS_PHYSICAL_COLUMN` | 15 |
| `QUALIFICATION_FORMAT_ERROR` | 0 |
| `ADAPTER_BUG` | 0 |
| `OTHER` | 0 |

The one `NONEXISTENT_COLUMN` case is `m212-dev-topn-005`, where the model
returned `customer_id` for `refunds`; the valid semantic key is `order_id`.
The 15 computed-alias cases are:

`m212-dev-laglead-001`, `m212-dev-laglead-002`,
`m212-dev-laglead-003`, `m212-dev-laglead-004`,
`m212-dev-laglead-005`, `m212-dev-laglead-006`,
`m212-dev-running-001`, `m212-dev-running-002`,
`m212-dev-running-003`, `m212-dev-running-005`,
`m212-dev-running-006`, `m212-dev-moving-001`,
`m212-dev-moving-002`, `m212-dev-moving-003`, and
`m212-dev-moving-006`.

These values were generated-result names such as
`preceding_order_amount`, `cumulative_total_amount`, and
`moving_average_total_amount`, but were supplied in physical `target`
positions. Recovering the intended base target requires semantic inference;
it is not a safe qualification normalization. The `customer_id` case is both
nonexistent in the selected table and semantically wrong, but is counted once
under the mutually exclusive primary class `NONEXISTENT_COLUMN`.

Deterministic-resolution-recoverable count: **0**. No case was an exact
source-prefix, quoting, or canonical qualification variation. Consequently:

- adapter normalization was not changed;
- no fuzzy matching was added;
- no cross-table search was added;
- no semantic repair was added;
- no adapter bug was found.

## Output-grounding audit

Among the 27 transported IRs there were 22 missing physical outputs and three
extra physical outputs:

- missing-output taxonomy: `SEMANTIC_OMISSION=22`;
- extra-output taxonomy: `SEMANTIC_EXTRA_OUTPUT=3`;
- computed-output alias mismatch: 0;
- evaluator-only alias issue: 0;
- unsupported DTO field: 0.

Physical output precision and recall are unchanged at **64/67 = 95.52%** and
**64/86 = 74.42%**, respectively. These are conditional on the 27 transported
cases and do not represent full-dataset capability.

## Corrected replay

The immutable replay is in
`evaluation/results/m212r0/20260903T164500Z/corrected_replay.json`.

| Metric | Baseline | Window IR |
|---|---:|---:|
| Result equivalence | 4/48 | 2/48 |
| M1 plan acceptance | 38/48 | 26/48 |
| Execution success | 38/48 | 26/48 |

Corrected pairwise outcomes remain:

- `BOTH_CORRECT=0`;
- `BASELINE_ONLY_CORRECT=4`;
- `WINDOW_IR_ONLY_CORRECT=2`;
- `BOTH_INCORRECT=42`.

Corrected pattern results remain:

| Pattern | Baseline | Window IR |
|---|---:|---:|
| `LAG` | 0/3 | 0/3 |
| `LEAD` | 0/3 | 0/3 |
| `LATEST_PER_GROUP` | 2/6 | 1/6 |
| `TOP_N_PER_GROUP` | 1/6 | 1/6 |
| `RUNNING_AGGREGATE` | 0/6 | 0/6 |
| `MOVING_AGGREGATE` | 1/6 | 0/6 |
| `RANKING` | 0/6 | 0/6 |
| `SHARE_OF_TOTAL` | 0/6 | 0/6 |
| `MIXED_MULTI_WINDOW` | 0/6 | 0/6 |

## Frozen evidence and scope

Source artifact hashes and code hashes are in
`evaluation/results/m212r0/20260903T164500Z/metadata.json`.
The source M2.12R dataset SHA is
`bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3`.

Gold compiler validation remains 48/48 for development and 32/32 for the
M2.12 holdout. The M2.12 holdout SHA remains
`e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4`; it was
not consumed. M2.7 and M2.11 holdouts also remain untouched with their frozen
SHAs.

M2.12R.0 made **zero provider calls**. It used no new DTO, no provider
representation, no fallback, no retry, no repair, no M1 change, no evaluator
change, no compiler change, and no holdout.

Quality gates:

- pytest: **162 passed, 1 skipped**;
- PostgreSQL integration: **37 passed, 1 skipped**;
- Ruff: **passed**;
- mypy: **passed**;
- `git diff --check`: **passed**.

## Final decision

The evaluator did not create an alias false negative. The only measurement
defect was a diagnostic IR matcher that ignored output order; correcting it
does not change either arm’s result-equivalence score. The resolution audit
found no deterministic normalization opportunity. The original M2.12R
transport and quality conclusions therefore stand.

Exactly one next recommendation: **do not spend another provider token yet**;
first approve a new, separately specified provider-boundary experiment only if
it can state a deterministic transport contract and preserve the existing
ordinal result contract. No M2.12R.1 quality run or holdout consumption is
justified by this audit alone.
