# Evaluation plan

Evaluation is a first-class subsystem, not a collection of ad hoc examples.
Internal experiments and two external PostgreSQL calibrations are complete;
broad benchmark acquisition is now frozen.

The future frozen benchmark should contain roughly 200–300 questions with expected semantic outcomes, relevant tables/columns, policy context, and expected clarification/abstention behavior. Exact SQL string match will not be the primary criterion.

Planned metrics include:

- parse and execution success;
- result/execution equivalence;
- table and column selection;
- governed semantic metric correctness;
- policy compliance, unauthorized execution, cross-tenant leakage, and write execution;
- latency, repair rate, and query execution cost;
- answer correctness and clarification/abstention behavior.

M2.12 is now explicitly labelled the **Internal Window Compositional Stress
Suite**. M2.13 is the external Defog SQL-Eval PostgreSQL calibration and is
reported separately; scores from the two datasets are not averaged.

The current evaluation roadmap is:

- M2.12: Internal Window Compositional Stress Suite (completed).
- M2.13: Defog PostgreSQL External Calibration (completed).
- M2.14: BIRD Mini-Dev PostgreSQL External Validation (completed).
- M2.14.1: Cross-Benchmark Semantic Failure Mechanism Audit (completed).
- M3: Governed Semantic Metrics (accepted for catalog-covered metrics).
- M3.4: Feature-Flagged Governed Metric Routing & Observability (completed;
  production default remains off).
- M3.5: Semantic Contract Hardening (completed; stable identity, lifecycle,
  versioning, and governed execution provenance).

The external benchmark adapters and their isolated executors remain under
`evaluation/external/`; they do not replace or bypass the production M1 SQL
safety service. The detailed experiment record is in
`docs/evaluation-research-history.md`.

Every case should retain a typed failure stage such as `SQL_PARSE_ERROR`, `POLICY_REJECTION`, `QUERY_COST_REJECTION`, or `ANSWER_SYNTHESIS_ERROR`. No benchmark or security number is reported until it has actually been measured.

M1 execution outcomes are typed as `ALLOWED`, `SQL_PARSE_ERROR`, `POLICY_REJECTION`, `QUERY_COST_REJECTION`, or `EXECUTION_ERROR`. Adversarial integration tests verify that rejected SQL stops before EXPLAIN, and that accepted SQL reaches execution only after EXPLAIN acceptance.

The trust lifecycle is also tested explicitly: successful planning produces a `QueryPlan`, execution produces a `QueryExecution`, and raw SQL or copied plans cannot bypass `plan()`. Planning is the current M1 dry-plan primitive and does not return database rows.

## M2 baseline

`evaluation/datasets/m2_baseline.json` contains 48 deterministic questions for the synthetic commerce schema. Categories are simple filters, simple aggregations, joins, multi-table joins, date filtering, grouping, top-k, ratios, and window functions. It intentionally excludes semantic business metrics, entity/value questions, ambiguity, repair, and answer synthesis.

`python -m evaluation.run_m2 --mode retrieved` runs the explicit real-provider baseline when `DECISION_SQL_LLM_API_KEY` is configured. `--mode full` supplies all queryable catalog tables and columns from the finite demo schema, while `--mode ablation` runs both modes for comparison. The normal pytest suite uses deterministic static providers and does not require network access or an API key.

The evaluator reports retrieval hit rate, parse success, M1 plan acceptance, execution success, result equivalence, context size, and generation latency when available. Result equivalence compares bounded result values with numeric tolerance and optional order sensitivity; exact SQL string equality is not used as the primary metric. No real-provider quality result is claimed until the explicit command is run.

## M2.5 holdout

M2.5 uses the frozen 54-question `evaluation/datasets/m25_holdout.json`, with
six questions in each ordinary-SQL category: simple filters, aggregation,
group by, joins, multi-table joins, date filtering, top-k, ratios, and window
functions. All gold queries were validated through M1 before generation code
changed. The holdout must not be tuned against.

The holdout is evaluation-only and was frozen before M2.5 generation changes.
The experiment is factorial:

| Context | ONE_SHOT | GROUNDED |
|---|---|---|
| `FULL_COMPACT` | primary A | primary B |
| `RETRIEVED_BOUNDED` | secondary C | secondary D |

The primary comparison is `FULL_COMPACT + ONE_SHOT` versus
`FULL_COMPACT + GROUNDED`, keeping the exact compact structural schema, model,
settings, fixture, evaluator, and M1 policy constant. The secondary pair
measures grounding under retrieval. Separate comparisons measure retrieval
without and with grounding. The four cells are available with:

```bash
DECISION_SQL_LLM_API_KEY=... python -m evaluation.run_m25 --experiment primary
DECISION_SQL_LLM_API_KEY=... python -m evaluation.run_m25 --experiment full
```

The grounded path reports intent validity, SQL/intent agreement for tables,
columns, joins, aggregations, grouping, ordering, limits, and windows, provider
calls, tokens, and separate call latency. Intent disagreement is diagnostic only
and does not reject SQL. One-shot uses one provider call per question; grounded
uses two. If no API key is configured, the runner exits before creating result
artifacts and reports that empirical closure is pending.
