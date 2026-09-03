# M2.14 — BIRD Mini-Dev PostgreSQL External Validation

M2.14 is an evaluation-only calibration milestone. It runs the existing
one-shot SQL generation path against the pinned, canonical 500-row
`mini_dev_postgresql.json` BIRD Mini-Dev subset using BIRD expert evidence and
the complete PostgreSQL schema.

The upstream package is pinned to `bird-bench/mini_dev` commit
`b3d4bcbbae9a96934ad812551eb400c7a3b23c12`. The package archive and extracted
dataset/dump hashes are recorded by `--prepare` in
`evaluation/external/bird/manifest.json`.

The PostgreSQL dump is the upstream single physical `bird` database containing
the 11 logical BIRD domains. The evaluation-only `BirdBenchmarkExecutor` uses
a dedicated read-only role on port 55433 and is not M1 or a product path.

## Run

Set `BIRD_MINI_DEV_ROOT` to the extracted package directory containing
`mini_dev_postgresql.json` and `BIRD_dev.sql`. Then run `--prepare`, perform the
gold preflight, do one predetermined smoke request, and only then run the 500
fresh Luna calls. The primary metric is BIRD execution accuracy (EX); no gold
SQL, results, or AST-derived labels enter the provider prompt.

The BIRD reference prompt contains a chain-of-thought phrase. DecisionSQL
intentionally omits that phrase and requests SQL only with `reasoning=none`, so
the result is named DecisionSQL One-Shot on BIRD Mini-Dev PostgreSQL rather
than an official leaderboard reproduction.

## Final scorecard

| Measure | Result |
| --- | ---: |
| BIRD EX | **225/500 (45.00%)** |
| Simple | **87/148 (58.78%)** |
| Moderate | **108/250 (43.20%)** |
| Challenging | **30/102 (29.41%)** |
| Ratio/division | **17/101 (16.83%)** |
| Temporal/date-time | **4/19 (21.05%)** |
| Gold Window slice | **1/5 (20.00%)** |

The Window slice contains only five gold Window questions and must not be
treated as a general Window capability score. The run parsed 499/500
generated SQL statements and executed 496/500. The gap between execution and
correctness is therefore primarily semantic, not basic SQL syntax.

The valid full run is
`evaluation/results/m214/dev/20260903T130000Z/`. The 25-question operational
control scored 11/25 (44.00%), and agreed with the full-run level. Earlier
wrong-schema and interrupted runs are excluded from the score.

M2.14 closes broad benchmark acquisition for this project. M2.12 remains the
**Internal Window Compositional Stress Suite**; its results are not averaged
with BIRD or Defog. M3 Governed Semantic Metrics was subsequently validated on
its own frozen internal benchmark; the next product milestone is M3.4 —
Feature-Flagged Governed Metric Routing & Observability.
