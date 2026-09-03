# DecisionSQL architecture

## North-star question

DecisionSQL is designed to answer: how can we prove that a natural-language question used the correct business data, respected authorization, could not trigger dangerous or expensive SQL, produced an answer supported by the result, and can be measured by attributable pipeline stage?

## Authority boundary

The model is a proposal generator. It does not receive database credentials, authorization authority, or an execution tool. A future request coordinator will pass a server-owned `UserContext` through deterministic stages. SQL will be parsed with `sqlglot`, validated, policy-checked, cost-gated with `EXPLAIN`, and executed only through a restricted PostgreSQL reader.

## Request paths

```text
question -> server UserContext -> semantic resolution -> schema retrieval
 -> LLM proposal -> AST parse -> deterministic validation -> policy
 -> EXPLAIN cost gate -> read-only execution -> result validation
 -> answer synthesis -> answer, SQL, and provenance
```

M1 adds a deterministic `SqlSafetyService` for direct candidate SQL supplied by internal/test code. It performs parsing, AST policy validation, EXPLAIN cost gating, and reader-only execution. There is still deliberately no public Text-to-SQL endpoint.

For catalog-covered business metrics, the validated M3 path is:

```text
question -> public metric glossary -> metric/dimension grounding
 -> server-owned semantic catalog -> deterministic metric compiler
 -> SqlCandidate -> M1 -> EXPLAIN -> read-only execution
```

This governed route is explicit and default-off after M3.4 feature-flagged
routing and observability validation. M3.5 adds stable semantic contract
identity and execution provenance without changing route selection. Arbitrary
analytics continue to use the direct SQL proposal path above.

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

## M2 context-aware baseline

M2 adds the first natural-language proposal path above the frozen M1 control plane:

```text
Natural-language question
        -> SchemaContextResolver
        -> bounded SchemaContext
        -> OpenAICompatibleProvider
        -> SqlProposal
        -> SqlCandidate(source=LLM)
        -> SqlSafetyService.plan()
        -> QueryPlan
        -> SqlSafetyService.execute()
        -> QueryExecution
```

`SchemaContextResolver` performs deterministic lexical matching over table names, aliases, descriptions, and columns. The default relationship depth is 2, which is justified by the commerce bridge pattern `products -> order_items -> orders`; expansion adds only shortest paths between selected seed tables, with bounded depth and table count. Retrieved mode preserves primary/foreign keys, then selects lexically relevant queryable columns within the per-table bound. Full ablation mode includes all queryable columns from the finite demo catalog. Context serialization includes metadata and relationships only: no sample values, business metrics, golden queries, or hidden columns. Retrieval scores are diagnostics, never authorization signals.

The M2 coordinator owns sequencing only. It does not contain SQL policy logic and cannot use a database engine. The provider has model/API configuration and returns structured `SqlProposal` data; it has no database credentials or execution interface. Every proposal is converted to `SqlCandidate(source=LLM)` before entering M1.

The trust order is fixed in code:

```text
parse -> AST validation -> object/function policy -> reader identity validation
-> EXPLAIN -> cost policy -> QueryPlan -> QueryExecution
```

Planning performs parse, validation, authorization, EXPLAIN, and cost evaluation without fetching result rows. Execution requires an accepted `QueryPlan` and only then returns a bounded `QueryExecution`.

M2 supports `execute=False` for an explicit plan-only request. This is not a second dry-run subsystem: it exposes the existing M1 planning/EXPLAIN primitive through the coordinator.

## M2.5 schema grounding

M2.5 uses two orthogonal dimensions: `FULL_COMPACT` versus
`RETRIEVED_BOUNDED` schema context, and `ONE_SHOT` versus `GROUNDED` generation.
The resulting four cells are:

```text
                         ONE_SHOT                         GROUNDED
FULL_COMPACT       SQL -> SqlCandidate -> M1       Intent -> SQL -> M1
RETRIEVED_BOUNDED  SQL -> SqlCandidate -> M1       Intent -> SQL -> M1
```

The primary comparison is the two full-context cells. The demo schema is small,
and M2's measured full-schema result was higher than its retrieved result, so
retrieval is kept out of the primary grounding comparison. Retrieved context is
still evaluated as the scalability/cost secondary analysis.

The generation cells are:

```text
FULL_COMPACT + ONE_SHOT:
Question -> full compact SchemaContext -> SqlProposal -> SqlCandidate -> M1

FULL_COMPACT + GROUNDED:
Question -> the same full compact SchemaContext -> QueryIntent -> SqlProposal
          -> SqlCandidate -> M1

RETRIEVED_BOUNDED + ONE_SHOT:
Question -> retrieved bounded SchemaContext -> SqlProposal -> SqlCandidate -> M1

RETRIEVED_BOUNDED + GROUNDED:
Question -> the same retrieved bounded SchemaContext -> QueryIntent -> SqlProposal
          -> SqlCandidate -> M1
```

`QueryIntent` is a compact, untrusted structural interpretation containing
tables, fully-qualified columns where available, joins, filters, aggregations,
grouping, ordering, limit, and window-operation hints. The server verifies that
explicitly named tables, columns, and join endpoints are visible in the bounded
context, but this is a context-validity check rather than authorization.
Generated SQL is compared to the intent for table, column, join, aggregation,
grouping, ordering, limit, and window diagnostics; disagreement does not bypass
or replace M1.

One-shot intentionally has one SQL-generation call. Grounded has one grounding
call and one SQL-generation call. There is no repair or retry loop. The M2.5
holdout is frozen before these generation changes and is separate from the
development M2 dataset. Runtime generation modules do not load evaluation
questions or gold SQL.

The EXPLAIN and execution stages share a transaction configured with `READ ONLY` and the configured statement timeout. Queries without a LIMIT are not rewritten; the executor fetches at most `max_result_rows + 1`, returns at most `max_result_rows`, and marks the result as truncated. The cost gate is the protection against unbounded work, while the row cap bounds returned data.

## Boundaries

- `app/models`: typed contracts shared between stages.
- `app/catalog` and `app/retrieval`: server-owned catalog and deterministic relevant-schema retrieval.
- `app/semantics`: governed business metrics, relationship graph, and typed
  deterministic metric compilation.
- `app/generation`: provider-neutral proposal interface.
- `app/sql/parser.py`: PostgreSQL parsing and one-statement enforcement.
- `app/sql/policy.py`: deterministic statement, object, column, catalog, and function policy.
- `app/sql/service.py`: explicit M1 trust-order coordinator.
- `app/execution`: EXPLAIN cost gate and reader-only execution.
- `app/synthesis`: future result-grounded answer generation.
- `evaluation`: small M2 baseline, result-equivalence runner, and future frozen benchmark/security suites.

The current demo schema is maintained in Alembic and mirrored by typed SQLAlchemy ORM models. Seed generation is deterministic and owned by `demo/seed`.
