# M15 — Current General Text-to-SQL Benchmark Re-Evaluation

M15 reran the existing direct SQL benchmark paths at the M14 checkpoint using
GPT-5.6 Luna (`gpt-5.6-luna`), with the pinned Defog and BIRD datasets,
evaluators, schema contexts, zero-shot prompts, reasoning disabled, and
provider-default temperature. The benchmark path did not use the governed M3
metric compiler, semantic-plan generation, memory, or retrieval.

The complete result is frozen in
`evaluation/fixtures/m15_general_benchmark_result.json`. The raw current runs
remain local under the ignored `evaluation/results/` directory.

## Current versus historical

| Benchmark | Historical | Current Luna | Delta |
| --- | ---: | ---: | ---: |
| Defog Classic exact | 142/210 (67.62%) | 141/210 (67.14%) | −1 case, −0.48 pp |
| Defog Classic subset | 158/210 (75.24%) | 156/210 (74.29%) | −2 cases, −0.95 pp |
| Defog Advanced exact | 48/64 (75.00%) | 47/64 (73.44%) | −1 case, −1.56 pp |
| Defog Advanced subset | 45/64 (70.31%) | 45/64 (70.31%) | 0 |
| BIRD Mini-Dev EX | 225/500 (45.00%) | 231/500 (46.20%) | +6 cases, +1.20 pp |

BIRD improved by 3/148 simple cases (+2.03 pp), 1/250 moderate case (+0.40
pp), and 2/102 challenging cases (+1.96 pp). Ratio increased from 17/101 to
20/101, Temporal from 4/19 to 5/19, and Window was unchanged at 1/5.

M1 rejection counts are zero because these external benchmark runners use their
dedicated benchmark read-only executors rather than the production M1 service;
this is not a safety-regression claim. Provider failures, judge calls, repair
calls, and technical retries were all zero. The observable current failure
stages were Defog execution errors (3), Defog result mismatches (55 total),
BIRD SQL extraction failure (1), BIRD execution errors (6), and BIRD result
mismatches (262).

## Conclusion

Classification: `M15_GENERAL_BENCHMARK_MIXED`.

The general DIRECT Text-to-SQL capability did not materially improve relative
to the frozen historical checkpoint: Defog was slightly down while BIRD was
slightly up. This is sharply different from the bounded M12R result, where the
internal governed metric slice improved from 45/109 direct to 109/109 governed
(+58.716 percentage points). That M12R result does not transfer automatically
to the general benchmark path because Defog and BIRD were intentionally kept on
DIRECT.

M15 is a benchmark checkpoint, not a prompt-tuning or attribution exercise.
Fresh implementation experiments replace residual-mining reviews; benchmark
outcomes produce measured capability evidence rather than another mechanism
selection review. The next general-generation direction is a fresh structured
QueryPlan capability line, with this result retained as its baseline.
