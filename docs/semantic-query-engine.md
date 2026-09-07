# Canonical Semantic Query Engine

Decision-SQL now has one typed semantic execution contract for new semantic
queries.  The model proposes bounded semantic identifiers and typed
expressions; server-owned mapping and deterministic compilation produce the
executable SQL.

```text
Question
  -> SemanticQueryPlan
  -> plan validation
  -> SemanticQueryIR
  -> server mapping / RelationshipGraph
  -> SQLGlot AST
  -> SemanticConsistencyValidator
  -> M1 safety and cost gate
  -> read-only execution
```

## Semantic and physical boundaries

`SemanticEntityId`, `SemanticAttributeId`, and `SemanticRelationshipId` are
logical identifiers.  `SemanticMappingSnapshot` resolves them to live,
queryable physical tables and columns.  Relationship IDs resolve to foreign-key
metadata and the compiler constructs the join predicate; a provider cannot
submit an `ON` clause.  The identity mapping generated from `SchemaCatalog` is
the generic adapter for schemas without a separately governed ontology.

## Canonical plan and IR

`SemanticQueryPlan` is strict (`extra="forbid"`) and contains typed outputs,
population and grain contracts, joins, predicates, grouping, ordering,
limits, calculations, and typed expressions.  `plan_to_ir` creates the
SQL-free `SemanticQueryIR`.  Population and grain are explicit rather than
being implied by a descriptive string.  Ratios use typed numerator,
denominator, scale, population-alignment, and zero-denominator policy fields.

The expression tree supports attributes, typed literals, predicates, boolean
logic, arithmetic, casts, CASE, reviewed functions, aggregates, filtered
aggregates, windows, scalar subqueries, and EXISTS subqueries.  Function and
operator names are bounded enums; arbitrary SQL strings are not accepted.

## Relational expressivity

Nested relational work uses the same `SemanticQueryIR` recursively.  A
`DerivedRelation` or non-recursive `CommonTableExpression` contains nested IR
and explicitly exports its output attributes; it never contains SQL text.
`EntityRelationSource`, `DerivedRelationSource`, and `CTERelationSource` keep
query-local scope separate from physical table identity.  Parent expressions
can reference only exported nested outputs.

The compiler uses one scoped deterministic alias allocator and composes
SQLGlot ASTs directly.  It supports base relations, typed joins between
exported relations, aggregate subqueries, CTE dependencies, scalar subqueries,
filtered aggregates, typed `NOT`, bounded casts, temporal intervals, and
window expressions.  Population metadata is carried through nested queries;
relationship expansion still checks connectivity, ambiguity, and fanout.

```text
SemanticQueryIR
  ├─ base relation sources
  ├─ CTEs / derived relations (nested SemanticQueryIR)
  ├─ typed expressions, aggregates, calculations, windows
  └─ population / grain contracts
       ↓
scope resolution + RelationshipGraph + cardinality checks
       ↓
SQLGlot AST (compiler-owned aliases)
```

Non-FK relationships are accepted only through an explicit
`SCHEMA_DECLARED` or `SERVER_CONFIGURED` mapping snapshot.  The evaluation
adapter does not manufacture such mappings from reference SQL.

## RelationshipGraph

The existing metric relationship graph remains compatible with governed
metrics.  Its deterministic unique-shortest-path rule is also applied by the
semantic mapping graph.  Unknown, disconnected, cyclic, ambiguous, or unsafe
relationship choices fail closed.  Physical relationship metadata remains
server-owned.

## Deterministic compiler and validator

`SemanticQueryCompiler` lowers the IR through `ExpressionCompiler` and
SQLGlot.  `AggregationCompiler` and `CalculationCompiler` provide shared
lowering primitives for aggregates and ratio-style calculations.  Identifiers
come only from the mapping and unsupported functions/operators raise typed
semantic failures.

`SemanticConsistencyValidator` is separate from authorization.  It checks the
compiled AST against the accepted semantic contract for projection, tables,
aggregates, filters, joins, grouping, ordering, DISTINCT, and LIMIT.  It does
not repair SQL and it does not grant execution authority.

The canonical function capability registry mirrors the reviewed M1 analytical
surface, including deterministic `NULLIF`, while volatile and stateful
functions remain outside the registry.  This is a capability distinction, not
a bypass around M1.

## Runtime modes and provider boundary

`ExecutionMode.SEMANTIC` is an explicit configuration mode.  It performs one
semantic-plan provider operation, deterministic validation/lowering, semantic
consistency validation, and then the existing safety/execution path.  A
semantic failure never silently falls back to DIRECT.  `DIRECT` remains the
default legacy path for compatibility and controlled comparisons.

The semantic provider operation returns one strict semantic plan and no SQL.
It is separate from the existing raw-SQL provider operation; provider/model
selection and the production DIRECT prompt are unchanged.

## Oracle ceiling diagnostic

`evaluation/semantic_oracle_ceiling.py` is an evaluation-only harness.  Where
an already available reference SQL can be translated into the bounded semantic
contract, it measures plan validation and deterministic compilation without a
provider call.  Reference SQL is an input to that diagnostic translator only;
it cannot create runtime mappings, provider context, or benchmark-specific
production rules.  Unsupported constructs remain explicit rather than being
best-effort compiled.

## Legacy compatibility

`QueryPlanV1` lowers into the canonical plan and its compiler now uses the
canonical mapping/IR compiler.  `QueryPlanWireV2`, `ResultShape`, `TopKPlan`,
`RatioPlan`, and supported window DTOs have one-way adapters.  They remain
available for compatibility, but they are not competing execution authorities.
Governed metrics retain their public contract and reuse common expression and
aggregate lowering primitives.

## M1 boundary

M1 remains the sole SQL safety authority and is mandatory before execution.
It retains SELECT-only, single-statement, catalog/table/column, reviewed
function, EXPLAIN cost, and read-only execution boundaries.  `NULLIF` is part
of the already-reviewed null-handling capability; volatile and stateful
functions remain outside the semantic capability registry and outside M1's
allowed surface.

## Known limitations

The generic identity adapter does not infer business ontology beyond live
schema metadata.  Recursive correlated references, set operations, recursive
CTEs, and unregistered non-FK joins remain unsupported until explicitly
represented by server-owned metadata and typed contracts.  Ambiguous paths and
uncertain cardinality fail closed.  Legacy window adapters retain their
historical narrow DTO boundary while the canonical expression model provides
the common representation.

## LiveSQLBench Base-Lite execution boundary

The provider-free semantic oracle uses the official prebuilt PostgreSQL image,
not the Decision-SQL demo database and not locally reconstructed benchmark
data.  The official LiveSQLBench compose definition is the source of the
image `docker.io/shawnxxh/bird-interact-postgresql:latest` (verified locally at
digest `sha256:1ae45d7aa5d64dd8eb82e4058f56b4b9625d5035b9b9dc0d2afa0295d9d3053c`), its credentials,
and the container's PostgreSQL port:

```text
docker compose -p livesqlbench-agent \
  -f /path/to/LiveSQLBench-Agent/docker-compose.yml pull postgresql
docker compose -p livesqlbench-agent \
  -f /path/to/LiveSQLBench-Agent/docker-compose.yml up -d postgresql
```

The compose service maps host port `5432` to container port `5432`.  The
repository's normal PostgreSQL service also uses host port `5432`, so the
evaluation-only instance used in parallel development is exposed on host
port `55434` and selected via `LIVESQLBENCH_DB_PORT=55434`.

The server contains databases named `{database}_template`, with PostgreSQL
listening on container port `5432`.  For a host-port conflict with the normal
Decision-SQL PostgreSQL service, the same official image/data volume may be
exposed on an evaluation-only host port and configured through
`LIVESQLBENCH_DB_HOST`, `LIVESQLBENCH_DB_PORT`, `LIVESQLBENCH_DB_USER`,
`LIVESQLBENCH_DB_PASSWORD`, and `LIVESQLBENCH_DB_SUFFIX`; no benchmark tables
are imported into `decision_sql`.

Readiness and the complete provider-free 18-case ceiling are run with:

```text
LIVESQLBENCH_DB_PORT=55434 \
  .venv/bin/python -m evaluation.livesqlbench_oracle_execution \
  --public-root /path/to/livesqlbench-base-lite-audit \
  --protected evaluation/external/livesqlbench/protected/livesqlbench_gt_kg_testcases_0528.jsonl
```

The harness records only safe aggregate/status evidence.  Protected SQL,
test cases, and result rows remain local.  The execution ceiling is distinct
from the representation ceiling: two frozen cases remain blocked because the
official runtime metadata does not register their required non-FK relation;
they are not scored as execution failures.
