# M9.5R.2S — Fresh Real-Execution Integration Fixture Construction

## Status

This milestone is **M95R2S_FIXTURE_CONSTRUCTION_BLOCKED**. The required fresh
48-case suite was not constructed, because the repository's existing M1 policy
boundary cannot support two mandatory fixture families without an M1 change.
No database connection, PostgreSQL execution, M1 `EXPLAIN`, provider call, or
SQL-generation call occurred.

## Offline schema evidence

The offline schema was reconstructed from `app/db/models.py`, the default
catalog, and the `0001_commerce_schema` migration. It contains 8 tables, 42
columns, and 9 declared relationships. The DB-free `SQLParser` and `SQLPolicy`
were used only for static policy inspection; `SqlSafetyService.plan()` was not
called.

Two fresh representative passthrough forms were parsed and rejected by the
existing policy:

| Family | Result | Rejection |
| --- | --- | --- |
| CTE passthrough | blocked | `UNKNOWN_COLUMN fr.label` |
| derived-table passthrough | blocked | `UNKNOWN_COLUMN dv.label` |

The policy resolves outer references only against base catalog aliases. It does
not expose CTE or derived-table output columns as queryable scope metadata.
Both `CTE_OR_DERIVED_PASSTHROUGH` and the required direct-positive composition
therefore cannot be frozen as later M1-compatible integration fixtures without
changing M1. That change is explicitly out of scope.

No recipe, candidate/reference corpus, oracle, binder baseline, execution
manifest, or freeze lock was created. This avoids freezing a suite already
known to fail the future M1 gate.

## Frozen components

The historical binder-v1 and validated binder-v2 were not modified. Verified
hashes remain:

- binder-v1: `aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb`
- binder-v2: `ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e`
- V2 comparator: `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97`

M9.5R.1 remains accepted evidence; its validation and adversarial recipe
hashes were verified unchanged. This milestone produced no binder-v2
validation or real-execution evidence.

M10 remains fully unconsumed and `M10_BINDING_PROTOCOL_READY` remains false.
M7 remains 99/150 = 66%; no new or adjusted Text-to-SQL accuracy was created.

The next milestone is **M9.6 — Evaluator V2 Applicability Strategy Audit**.
