# M24.3 — LiveSQLBench temporal reference integrity

## Why M24.3 exists

M24.2 made stable temporal expressions a reviewed, read-only M1 capability. The
provider-free replay then accepted and executed 175 of the 180 official SELECT
solutions. All 175 executions succeeded, but five reference self-comparisons
failed because the result was empty. M24.3 determines whether those empty
results are temporal snapshot drift, an evaluator limitation, or both. It does
not change M1, SQL generation, or official scoring.

## Official evaluator behavior

The adapter preserves the upstream Query Soft-EX behavior at evaluator commit
`e15cd221267e06fabfaf6a3d4a69308280ce9a7c`. The upstream implementation
normalizes result rows and returns failure when either result list is empty,
before ordered or unordered equality is tested. Thus empty-versus-empty is a
failure under the official raw evaluator. The corresponding upstream source is
[`evaluation/src/test_utils.py` at the frozen commit](https://raw.githubusercontent.com/bird-bench/livesqlbench/e15cd221267e06fabfaf6a3d4a69308280ce9a7c/evaluation/src/test_utils.py).

The official evaluator was not changed and no empty-empty special case was
added.

## Current-clock semantics

The five accepted reference queries that produced empty results all contain
`CURRENT_DATE`. They execute in the existing M24.2 provenance context: the
replay date was 2026-09-07 and the PostgreSQL session timezone was
`Asia/Hong_Kong`.

For every affected query, the relevant temporal data horizon was 565 days
behind the execution date. The relevant maximum date was in the 2025-02-19
snapshot horizon. The query predicates or derived temporal expressions use
relative windows from `CURRENT_DATE`; at the current clock those windows no
longer intersect the fixed benchmark data. Replacing `CURRENT_DATE` in an
AST-only diagnostic copy with the latest relevant database date caused all five
queries to produce non-empty results. This is diagnostic evidence, not an
official benchmark-date reconstruction.

## Temporal drift evidence

| classification | count |
|---|---:|
| empty accepted references | 5 |
| with `CURRENT_DATE` | 5 |
| temporal drift confirmed | 5 |
| temporal drift plausible | 0 |
| non-temporal | 0 |
| unresolved | 0 |

The five failures therefore have two layers: current-clock temporal drift
causes the reference execution to be empty, and the unchanged official
evaluator rejects that empty self-comparison. The local latest-data-date
substitution is an evaluation-only proxy. No authoritative benchmark
reference date was found in the public release, protected metadata, release
metadata, or database metadata, so an official frozen-time reproduction is not
justified.

## Official versus diagnostic scoring

The official raw evaluator remains authoritative and unchanged. The diagnostic
annotation records:

- reference execution was successful;
- result was empty;
- temporal dependency was present;
- latest relevant database date was used only as a non-official diagnostic
  proxy.

The diagnostic annotation never replaces the raw benchmark score and never
enters runtime provider input.

## Reference execution and pilot integrity

The full accepted reference population remains 175/180: 175 M1-compatible,
175 executed successfully, 170 non-empty, and 5 empty. The five empty results
explain all five official evaluator failures. The frozen 18-case pilot remains
unchanged: 18/18 are covered, M1-compatible, and executable; 17/18 pass the
official reference self-evaluator, with one empty temporal-drift case.

This is sufficient for infrastructure readiness of the pilot, provided future
reporting keeps the official raw outcome and temporal-integrity annotation
separate.

## Claim boundary

M24.3 evaluates benchmark temporal integrity only. It does not measure model
quality, alter stable temporal SQL semantics, change the evaluator, rewrite
gold SQL, or introduce a time-aware prompt/runtime component. Provider calls
and fresh generation were zero.
