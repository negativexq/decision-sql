# DecisionSQL architecture

## North-star question

DecisionSQL is designed to answer: how can we prove that a natural-language question used the correct business data, respected authorization, could not trigger dangerous or expensive SQL, produced an answer supported by the result, and can be measured by attributable pipeline stage?

## Authority boundary

The model is a proposal generator. It does not receive database credentials, authorization authority, or an execution tool. A future request coordinator will pass a server-owned `UserContext` through deterministic stages. SQL will be parsed with `sqlglot`, validated, policy-checked, cost-gated with `EXPLAIN`, and executed only through a restricted PostgreSQL reader.

## Planned request path

```text
question -> server UserContext -> semantic resolution -> schema retrieval
 -> LLM proposal -> AST parse -> deterministic validation -> policy
 -> EXPLAIN cost gate -> read-only execution -> result validation
 -> answer synthesis -> answer, SQL, and provenance
```

M1 adds a deterministic `SqlSafetyService` for direct candidate SQL supplied by internal/test code. It performs parsing, AST policy validation, EXPLAIN cost gating, and reader-only execution. There is still deliberately no public Text-to-SQL endpoint.

## Trust lifecycle

The M1 domain model keeps proposal, authorization evidence, and execution outcome distinct:

```text
SqlCandidate
    |  untrusted SQL proposal; no authorization state
    v
QueryPlan
    |  issued only after parse, AST/object/function policy, reader validation,
    |  EXPLAIN, and cost acceptance; contains bounded planning evidence
    v
QueryExecution
       bounded result from the restricted reader connection
```

The service API is `plan(candidate: SqlCandidate) -> QueryPlan | SqlPlanFailure` and `execute(plan: QueryPlan) -> QueryExecution | SqlExecutionError`. The service retains the accepted plan identity, so a raw string, a rejected plan, or a copied/foreign plan cannot enter execution. Planning is the M1 dry-plan/dry-run primitive: it returns no result rows.

The trust order is fixed in code:

```text
parse -> AST validation -> object/function policy -> reader identity validation
-> EXPLAIN -> cost policy -> QueryPlan -> QueryExecution
```

Planning performs parse, validation, authorization, EXPLAIN, and cost evaluation without fetching result rows. Execution requires an accepted `QueryPlan` and only then returns a bounded `QueryExecution`.

The EXPLAIN and execution stages share a transaction configured with `READ ONLY` and the configured statement timeout. Queries without a LIMIT are not rewritten; the executor fetches at most `max_result_rows + 1`, returns at most `max_result_rows`, and marks the result as truncated. The cost gate is the protection against unbounded work, while the row cap bounds returned data.

## Boundaries

- `app/models`: typed contracts shared between stages.
- `app/catalog` and `app/retrieval`: future catalog storage and relevant-schema retrieval.
- `app/semantic`: future governed business metrics and terminology.
- `app/generation`: provider-neutral proposal interface.
- `app/sql/parser.py`: PostgreSQL parsing and one-statement enforcement.
- `app/sql/policy.py`: deterministic statement, object, column, catalog, and function policy.
- `app/sql/service.py`: explicit M1 trust-order coordinator.
- `app/execution`: EXPLAIN cost gate and reader-only execution.
- `app/synthesis`: future result-grounded answer generation.
- `evaluation`: future frozen benchmark, runners, and security suites.

The current demo schema is maintained in Alembic and mirrored by typed SQLAlchemy ORM models. Seed generation is deterministic and owned by `demo/seed`.
