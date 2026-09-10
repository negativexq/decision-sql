# dbt ACME benchmark provenance

This directory records the first-party source identity used for M61. The
external checkout itself is intentionally not copied into the Decision-SQL
repository.

- Benchmark repository: [`dbt-labs/dbt-llm-sl-bench`](https://github.com/dbt-labs/dbt-llm-sl-bench)
- Pinned commit: `a29f2429b1bb38cee9d591892299e96f1e197ff9`
- Related dbt project, `main`: `ac20c9292fa88ec44d30aeeae61ee41a51e31748`
- Raw schema: `ACME_small.ddl`
- Question source: `benchmark_questions.ttl` plus the official
  `BaseConfig.selected_challenges` list
- Official result comparator: `src/llm_bench/services/comparison.py`

The exact question and SQL-gold identifiers are frozen in
[`m61_question_set.json`](../../audits/m61/m61_question_set.json). Gold SQL is
evaluator-only and is not part of a provider request.

M61 did not reach live acquisition because the dbt Semantic Layer execution
credentials were unavailable. No benchmark score is claimed.
