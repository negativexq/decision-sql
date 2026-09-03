# M2.12R — Minimal Pattern-Specific Plain JSON Window IR Capability Rerun

## Decision

The development rerun does not pass the precommitted screen and the M2.12
holdout was not consumed.

The two fresh arms made 96 provider calls in total, all successful at the
provider boundary:

| Arm | Result-equivalent | Plan accepted | Executed | Provider failures |
|---|---:|---:|---:|---:|
| Baseline one-shot SQL | 4/48 (8.33%) | 38/48 | 38/48 | 0 |
| Minimal pattern JSON → Window IR → compiler | 2/48 (4.17%) | 26/48 | 26/48 | 0 |

Paired outcomes were `BOTH_CORRECT=0`, `BASELINE_ONLY_CORRECT=4`,
`WINDOW_IR_ONLY_CORRECT=2`, and `BOTH_INCORRECT=42`. The absolute delta was
`-4.17` percentage points. The transport gate also failed: only 27/48
(56.25%) model responses reached a valid compiler-facing IR, below the
precommitted 95% requirement. No holdout quality claim is therefore made.

The classification is **PROVIDER_REPRESENTATION_STILL_UNRELIABLE**. This run
does not establish semantic Window capability because most responses stopped
before a valid internal IR existed.

## Provider-facing DTOs

The provider boundary uses separate, plain-JSON, pattern-specific Pydantic
models. `LagLeadProviderDTO` serves both LAG and LEAD with a bounded function
enum. `MixedWindowProviderDTO` is a bounded special case for the existing
mixed family; it is not used for the seven simple families.

| Pattern | Provider DTO | Fields |
|---|---|---:|
| `LATEST_PER_GROUP` | `LatestPerGroupProviderDTO` | 7 |
| `TOP_N_PER_GROUP` | `TopNPerGroupProviderDTO` | 9 |
| `LAG`, `LEAD` | `LagLeadProviderDTO` | 8 |
| `RUNNING_AGGREGATE` | `RunningAggregateProviderDTO` | 7 |
| `MOVING_AGGREGATE` | `MovingAggregateProviderDTO` | 9 |
| `RANKING` | `RankingProviderDTO` | 6 |
| `SHARE_OF_TOTAL` | `ShareOfTotalProviderDTO` | 5 |
| `MIXED_MULTI_WINDOW` | `MixedWindowProviderDTO` | 3 top-level + bounded operations |

The exact schemas and hashes are persisted in
`evaluation/results/m212r/dev/20260903T141500Z/provider_field_inventory.json`
and `provider_schema_hashes.json`:

| DTO | SHA-256 |
|---|---|
| `LATEST_PER_GROUP` | `128ac60a267ea1013abdd15cea2628a018a6f8a29a0e1b0a332fa418b55b3b59` |
| `TOP_N_PER_GROUP` | `0308b4f3ef5192cecdb451411155885b915bf5d3187f226b7433a55eb071d798` |
| `LAG` / `LEAD` | `449589ccb7574e3dd775616db9504e115ea9c12c9320fc2c5a77fc05ff53414d` |
| `RUNNING_AGGREGATE` | `274bf5578c9d04f93914855ed4fca866e5f3b5d7476aceaa97d1c3dce9a5ba9c` |
| `MOVING_AGGREGATE` | `7c535c8788f4d5eb3136d3385ea8d1e1655bd52e9bed76cae4d0ec24611aadac` |
| `RANKING` | `82ea491d1e7797e7a419067ce4e1191a55aede5bc829706b2fefbdd348401a5c` |
| `SHARE_OF_TOTAL` | `84f9da4608b843cc030572e2469bfe184f211b24b36a05439a45871e8bf645c1` |
| `MIXED_MULTI_WINDOW` | `e7ca49dff390ca84abd3d11e3412623a840cac88556dd1971844a728cb4b8adb` |

Compared with the M2.12.1 generic DTO, the simple DTOs remove the large
nullable operation field set, nested order objects, provider aliases, generic
frame fields, `ranking_function`, and all raw SQL/SQL-fragment escape hatches.
The model chooses only pattern-relevant semantic slots. The adapter owns
compiler aliases and maps moving-window shorthand (`preceding` plus
`include_current`) to the strict typed frame.

An unqualified identifier is qualified only against the selected `source`
table, only when that table is queryable and the column is queryable and
uniquely present. Qualified identifiers must name that same source table. No
fuzzy matching, cross-table search, missing-field inference, or semantic repair
is performed.

## Development data and validation

The frozen M2.12 development set contains 48 questions and has SHA-256
`bfb04992e860d7e10add010db350e94357fcedb20037540eaf9a0dfc17704ac3`.
The frozen M2.12 holdout contains 32 questions and has SHA-256
`e2cf624233937374e9aeff91cbcae239b6e65151f6ecbb449126b7fc25339fc4`.

Gold compiler validation remained 100% before provider calls:

| Dataset | IR validation | Compiler/M1 plan | Execution | Result equivalence |
|---|---:|---:|---:|---:|
| Development | 48/48 | 48/48 | 48/48 | 48/48 |
| Holdout | 32/32 | 32/32 | 32/32 | 32/32 |

The holdout was not consumed. It was not modified.

Development pattern results:

| Pattern | Baseline | Minimal JSON IR/compiler | Delta |
|---|---:|---:|---:|
| `LATEST_PER_GROUP` | 2/6 | 1/6 | -1 |
| `TOP_N_PER_GROUP` | 1/6 | 1/6 | 0 |
| `LAG` / `LEAD` | 0/6 | 0/6 | 0 |
| `RUNNING_AGGREGATE` | 0/6 | 0/6 | 0 |
| `MOVING_AGGREGATE` | 1/6 | 0/6 | -1 |
| `RANKING` | 0/6 | 0/6 | 0 |
| `SHARE_OF_TOTAL` | 0/6 | 0/6 | 0 |
| `MIXED_MULTI_WINDOW` | 0/6 | 0/6 | 0 |

Transport and downstream metrics for the IR arm:

| Stage | Count |
|---|---:|
| JSON parse | 48/48 |
| DTO validation | 47/48 |
| Conservative catalog qualification checkpoint | 27/48 |
| Adapter conversion | 27/48 |
| Internal IR validation | 27/48 |
| M1 plan accepted | 26/48 |
| Execution | 26/48 |
| Result-equivalent | 2/48 |

The transport failure taxonomy was: `COLUMN_RESOLUTION_FAILED=16`,
`ADAPTER_FAILED=4`, and `DTO_VALIDATION_FAILED=1`. Among transported records,
the semantic/downstream diagnostics were `OUTPUT_GROUNDING_ERROR=19`,
`ORDER_GROUNDING_ERROR=3`, `RESULT_MISMATCH_AFTER_CORRECT_IR=2`, and
`POLICY_REJECTION=1`. There were no compiler errors.

The original run's diagnostic matcher reported three model IR matches after
ignoring aliases and treating physical outputs as an unordered set. The
offline M2.12R.0 audit corrected that diagnostic: only one case matches the
gold IR when physical output order is retained. The two ranking cases also
have the first two physical output values in the opposite ordinal order, so
they are not alias-only mismatches. The canonical evaluator correctly rejects
them, and the raw records remain unchanged in `window_ir.json`.

For the 27 transported IRs, slot diagnostics were:

| Slot | Accuracy |
|---|---:|
| Source relation | 27/27 (100.00%) |
| Physical-output precision | 64/67 (95.52%) |
| Physical-output recall | 64/86 (74.42%) |
| Target | 9/11 (81.82%) |
| Partition | 27/28 (96.43%) |
| Order column | 21/21 (100.00%) |
| Direction | 17/21 (80.95%) |
| Offset | 1/1 (100.00%) |
| Top-N | 5/5 (100.00%) |
| Aggregate | 3/3 (100.00%) |
| Ranking function | 6/6 (100.00%) |
| Moving frame mode | 2/2 (100.00%) |
| Moving frame bounds | 1/2 (50.00%) |
| Scale | 4/7 (57.14%) |

These are diagnostics on transported records, not a valid full semantic
accuracy estimate because transport coverage was only 56.25%.

## Cost and latency

Both arms used one Luna provider call per question. There were 48 baseline
calls and 48 IR calls; all 96 succeeded and there were no provider failures.
No retry, repair, SQL fallback, second LLM call, or holdout call was made.

| Arm | Input tokens | Output tokens | Avg latency | p50 | p95 |
|---|---:|---:|---:|---:|---:|
| Baseline | 44,351 | 3,982 | 1,874.6 ms | 1,640.6 ms | 3,028.5 ms |
| Minimal JSON IR | 63,977 | 2,634 | 1,469.7 ms | 1,423.3 ms | 1,826.0 ms |

The IR input-token ratio was `1.443x`; output-token ratio was `0.661x`.
No versioned pricing configuration was used, so no monetary cost was
invented.

## Screening and interpretation

The precommitted screen failed:

- gold compiler ceiling: passed, 48/48 and 32/32;
- transport ≥95%: failed, 27/48 (56.25%);
- +10 percentage-point result-equivalence improvement: failed, -4.17 pp;
- IR-only wins greater than baseline-only wins: failed, 2 versus 4;
- improvement in at least four pattern families: failed;
- provider/infrastructure failure: none.

Accordingly, the M2.12 holdout remains unconsumed and there is no holdout
baseline/IR result, delta, pairwise result, or pattern breakdown to report.
The current evidence supports **PROVIDER_REPRESENTATION_STILL_UNRELIABLE**;
it does not support `SEMANTIC_SLOT_GROUNDING_BOTTLENECK` yet.

## Scope and quality gates

- M1 unchanged; compiler output still passed `SqlSafetyService.plan()` before
  execution. No rejected SQL bypassed M1.
- Evaluator, baseline FULL_COMPACT, and baseline SQL prompt unchanged.
- Luna and `reasoning=none` used in both arms.
- Internal `WindowQueryIR` and existing deterministic compiler reused; no
  provider call occurs inside the adapter or compiler.
- Provider DTOs contain no raw SQL. No semantic repair is performed.
- No role-aware schema, ResultShape, QueryIntent, retrieval, values, examples,
  pass@K, or M3 work was used.
- M2.7, M2.11, and M2.12 holdouts remain untouched; their recorded SHA values
  are unchanged.
- Provider candidates were independent, and gold information was used only
  for offline validation/scoring.

Quality gates for the implementation completed successfully:

| Gate | Result |
|---|---|
| pytest | 160 passed, 1 skipped |
| PostgreSQL-backed integration | passed as part of pytest; 1 integration skip in the environment |
| Ruff | passed |
| mypy | passed for app/evaluation |
| `git diff --check` | passed |

The raw run and corrected derived summaries are under
`evaluation/results/m212r/dev/20260903T141500Z/`. The corrected summaries fix
only report-field selection; generated SQL, model I/O, and raw per-call
records were preserved.

## Exactly one next recommendation

**M2.12R.1 — Provider-Representation Interface Audit:** keep the compiler and
M1 frozen and test one still simpler, pattern-specific transport boundary with
a precommitted ≥95% transport gate before making any semantic-quality claim.
Do not consume the M2.12 holdout until that gate is met.
