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

M0 implements only the application/database foundation, the health check, typed domain contracts, and the LLM provider boundary. There is deliberately no query endpoint.

## Boundaries

- `app/models`: typed contracts shared between stages.
- `app/catalog` and `app/retrieval`: future catalog storage and relevant-schema retrieval.
- `app/semantic`: future governed business metrics and terminology.
- `app/generation`: provider-neutral proposal interface.
- `app/sql`: future AST parsing and deterministic SQL policy.
- `app/execution`: future EXPLAIN gate and read-only execution.
- `app/synthesis`: future result-grounded answer generation.
- `evaluation`: future frozen benchmark, runners, and security suites.

The current demo schema is maintained in Alembic and mirrored by typed SQLAlchemy ORM models. Seed generation is deterministic and owned by `demo/seed`.
