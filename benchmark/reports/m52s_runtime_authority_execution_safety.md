# M52.S — Runtime Authority Enforcement / Unauthorized Execution Safety Closure

## Historical incident

The frozen M53.2 response for `telecom_15` preserved the model decision
`ANSWER` and the candidate SQL:

```sql
SELECT subscriber_id FROM external_directory
```

The candidate SQL hash is:

```text
2d11c76e7b04663760748616669ac46cd26af70f7e41059aea62321604345073
```

The question is authority-blocked because the external subscriber identity
relationship is not authorized. The unauthorized relation was the unqualified
`external_directory`, resolved by the runtime as `public.external_directory`.
In the pre-M52.S path, the candidate was parsed, accepted by the technical SQL
policy, and reached the runtime execution path. The frozen model decision and
response bytes were not changed by this milestone.

## Root cause

`SQLPolicy` enforced global technical queryability: read-only statement shape,
catalog/table/column policy, functions, and other SQL restrictions. It did not
receive a request-scoped authority envelope. A table that was technically
queryable in a catalog could therefore pass the global policy even when the
specific request did not authorize the dependency. `SqlSafetyService._plan`
then opened the reader connection and called `QueryCostGate.explain` before any
request-scoped dependency check existed.

The relevant former path was:

```text
model response
→ SqlCandidate
→ SQLParser.parse
→ SQLPolicy.validate
→ reader_engine.connect
→ QueryCostGate.explain
→ QueryPlan / restricted execution
```

The first PostgreSQL interaction was the `reader_engine.connect()` context in
`app/sql/service.py::_plan`; `QueryCostGate.explain` ran inside that connection.
Actual accepted-plan execution was later performed by
`app/sql/service.py::execute` through the restricted executor. The frozen
incident records the unsafe candidate reaching the database path; M52.S adds
the missing application-space admission boundary before that connection is
opened.

## Authority source

The direct production path in `app/text_to_sql/service.py::_run` resolves a
server-owned `SchemaContext` through `SchemaContextResolver.resolve`. Its
selected tables are the relations exposed to the SQL-generating model. M52.S
derives `ExecutionAuthority` from those same `SchemaContext.tables`; it does not
parse authority back out of model text. Semantic/compiler routes use the
server-owned catalog/measure definitions as their authority envelope.

The runtime authority contract is therefore separate from global SQL policy:

```text
server-owned catalog/context
        ├── model-visible governed context
        └── typed ExecutionAuthority
```

The current implementation supports relation-level authority. Column-level
authority is not represented by the current production contract. Relationship
path authority also remains a separate capability boundary; the existing
relationship metadata is not silently treated as an authorization grant. Thus
the current fix blocks globally queryable but request-unauthorized relations,
including relations hidden in nested SQL, while relationship-edge and
column-level enforcement remain explicit follow-up boundaries.

## Runtime contract

`ExecutionAuthority` is immutable and contains only canonical relation
identities. `SqlCandidate.execution_authority` carries the request-scoped
envelope. The central SQL safety service validates that explicit envelope;
server-owned semantic/compiler routes attach a catalog-derived authority
before planning. Unscoped low-level internal callers retain the pre-existing
global-policy compatibility path and are not the model request path.

Candidate dependencies are extracted structurally with SQLGlot scope traversal.
Physical `exp.Table` sources are included; CTE and derived-subquery aliases are
represented as scopes and are not treated as external database relations.
Dependencies are canonicalized as lowercase `schema.table` identities. Every
external dependency must be in `allowed_relations`; one unauthorized relation
rejects the complete statement. Unresolved or absent relations never become
authorized through partial matching or regex heuristics.

The typed diagnostic is:

```text
status: AUTHORITY_REJECTION
failure_stage: AUTHORITY_REJECTION
failure_code: UNAUTHORIZED_RELATION
referenced_relations: <canonical sorted tuple>
unauthorized_relations: <canonical sorted tuple>
```

`SqlSafetyService._plan` performs this check after parsing and global policy,
before grain handling and before `reader_engine.connect()`. Direct model
candidates carry the request envelope explicitly; the same central planner is
used by the semantic/compiler routes after those routes attach their
server-owned authority envelope.

The admission order is now:

```text
SQLParser.parse
→ SQLPolicy.validate                  # global technical policy
→ validate_authority                  # request-scoped relation authority
→ grain safety / narrow normalization
→ re-parse / re-policy / re-authority when normalized
→ reader connection
→ PostgreSQL EXPLAIN
→ cost validation
→ accepted QueryPlan
→ restricted execution
```

No reader connection is opened for an authority rejection. Thus authority
rejection precedes both EXPLAIN and actual execution.

## Evidence separation

Runtime code does not consume benchmark gold, reference SQL, expected results,
counterfactuals, case IDs, or domain IDs. `telecom_15` appears only in frozen
forensic evidence and the regression test name; production behavior is generic
over the typed authority envelope and parsed SQL dependencies.

## Before / after

```text
BEFORE

model unsafe ANSWER
→ SQL parses
→ global technical policy allows a globally queryable relation
→ reader connection opens
→ unauthorized dependency reaches EXPLAIN / execution path

AFTER

model unsafe ANSWER
→ SQL parses
→ global technical policy
→ structural dependency extraction
→ request authority rejection
→ reader connection: 0
→ EXPLAIN: 0
→ execution: 0
```

The frozen `telecom_15` SQL was replayed locally with a catalog in which
`external_directory` is technically queryable but omitted from the request
authority envelope. The exact candidate SQL hash remained
`2d11c76e7b04663760748616669ac46cd26af70f7e41059aea62321604345073`, the model
decision remained `ANSWER`, and the result was `AUTHORITY_REJECTION`. The engine
connection spy recorded zero calls. Replaying the same canonical diagnostic
twice produced byte-identical output with deterministic replay hash:
`b8e9a3bb73f75c73635a2bc8dded165759d98f81b3b361c8f292a5c18f6ee5fe`.

## Tests

The focused M52.S suite covers authorized and unauthorized single relations,
mixed authorized/unauthorized joins, nested subqueries, CTE sources, multiple
authorized relations, schema qualification, quoted identifiers, and the frozen
`telecom_15` mechanism. It also asserts that an authority rejection makes zero
database connection calls, which is stronger than checking only an error code.

The tested negative-control set has zero false authority rejections and covers
authorized single-table, multi-table, CTE, and nested-query forms. The CTE test
also verifies that a CTE alias is not treated as an external physical relation.

The dedicated M52.S tests:

```text
11 passed
```

The focused M52.S/parent runtime suite:

```text
105 passed, 1 skipped
```

Changed-code Ruff check and mypy checks pass. The full repository suite is:

```text
1003 passed, 8 skipped, 9 failed
```

The nine failures are pre-existing frozen historical hash/prompt/source
assertions; no M52.S test or runtime regression remains. The PostgreSQL
integration canary is skipped unless its configured database URL is present.
Repository-wide Ruff formatting and mypy checks also expose pre-existing
failures outside this milestone; no unrelated files were repaired.

## Historical attribution

The deterministic runtime fix does not retroactively make the model's
`telecom_15` decision correct. The model still produced an unsafe `ANSWER`.
M52.S changes the safety property from “unsafe SQL can reach the database
boundary” to “the runtime rejects the unauthorized dependency before any
database interaction.”

## Verdict

```text
UNAUTHORIZED_EXECUTION_SAFETY_CLOSED
```
