# M2.13 — Defog PostgreSQL external calibration

M2.13 calibrates the existing one-shot DecisionSQL SQL-generation path on the
pinned Defog SQL-Eval PostgreSQL datasets. It is an evaluation-only benchmark
adapter; it does not route external SQL through DecisionSQL M1 and does not
change the internal evaluator, schema serializer, Window IR, or compiler.

The pinned sources are recorded in
`evaluation/external/defog/manifest.json`. The current run is
`evaluation/results/m213/dev/20260903T120700Z/`.

The selected data files are:

- Classic: `questions_gen_postgres.csv`, 210 questions.
- Advanced: `instruct_advanced_postgres.csv`, 64 questions.

Advanced prompts use the upstream `instructions` field with the same sentence
line-break formatting used by the pinned Defog runner. `full_instructions` is
not used and K-shot prompting is zero.

The benchmark databases run in the isolated `decision-sql-defog-postgres`
container on host port 55432. Queries are executed as the dedicated
`defog_reader` role in read-only transactions with a statement timeout. The
executor accepts only one PostgreSQL SELECT/SELECT-CTE statement and rejects
write, DDL, transaction, COPY, and lock operations. It is not the production
M1 safety service.

The model configuration is Luna (`gpt-5.6-luna`), reasoning `none`, provider
default temperature, and one fresh generation per benchmark question. The
provider returns the existing structured SQL proposal; only whitespace and
Markdown SQL fences are removed locally.

Gold validation completed before quality evaluation: 274/274 questions and
446/446 canonical gold variants executed successfully. The run's primary
results are evaluated by the pinned Defog `compare_df` and `subset_df`
semantics. The detailed scorecard is in `difficulty_calibration.json`, with
category and window reports in `category_analysis.json` and
`window_analysis.json`.

M2.12 is described as the **Internal Window Compositional Stress Suite**. Its
results are reported separately from this external calibration and are never
averaged with Defog scores.

## Final scorecard

| Dataset | Questions | Exact | Subset |
| --- | ---: | ---: | ---: |
| Classic PostgreSQL | 210 | **142/210 (67.62%)** | **158/210 (75.24%)** |
| Advanced PostgreSQL | 64 | **48/64 (75.00%)** | **45/64 (70.31%)** |
| Advanced `instructions_cte_window` | 8 | **6/8 (75.00%)** | **6/8 (75.00%)** |
| Advanced AST-derived HAS_WINDOW | 10 | **7/10 (70.00%)** | **7/10 (70.00%)** |

The AST-derived slice is evaluation-only and is not sent to the provider. The
Classic ratio category scored 12/35 exact (34.29%). Advanced date-join
questions were also a weaker category than the other Advanced groups. These
results support semantic/compositional follow-up work, while the public
Window slice is materially healthier than the internal M2.12 stress suite.

The immutable primary run is
`evaluation/results/m213/dev/20260903T120700Z/`; raw run outputs are retained
locally and excluded from source control by repository policy.
