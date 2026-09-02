# Evaluation plan

Evaluation is a first-class subsystem, not a collection of ad hoc examples. M1 adds safety-boundary tests, but it does not claim Text-to-SQL quality measurements.

The future frozen benchmark should contain roughly 200–300 questions with expected semantic outcomes, relevant tables/columns, policy context, and expected clarification/abstention behavior. Exact SQL string match will not be the primary criterion.

Planned metrics include:

- parse and execution success;
- result/execution equivalence;
- table and column selection;
- governed semantic metric correctness;
- policy compliance, unauthorized execution, cross-tenant leakage, and write execution;
- latency, repair rate, and query execution cost;
- answer correctness and clarification/abstention behavior.

Every case should retain a typed failure stage such as `SQL_PARSE_ERROR`, `POLICY_REJECTION`, `QUERY_COST_REJECTION`, or `ANSWER_SYNTHESIS_ERROR`. No benchmark or security number is reported until it has actually been measured.

M1 execution outcomes are typed as `ALLOWED`, `SQL_PARSE_ERROR`, `POLICY_REJECTION`, `QUERY_COST_REJECTION`, or `EXECUTION_ERROR`. Adversarial integration tests verify that rejected SQL stops before EXPLAIN, and that accepted SQL reaches execution only after EXPLAIN acceptance.

The trust lifecycle is also tested explicitly: successful planning produces a `QueryPlan`, execution produces a `QueryExecution`, and raw SQL or copied plans cannot bypass `plan()`. Planning is the current M1 dry-plan primitive and does not return database rows.
