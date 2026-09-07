# M24 — M1 Analytical SQL Coverage Repair

## Motivation

The protected LiveSQLBench Base-Lite PostgreSQL preflight contained 180
official SELECT solutions. Before M24, only 21 passed the unchanged M1
boundary: 155 were rejected and four were multi-statement planning errors.
The dominant first failure was `FORBIDDEN_FUNCTION` (142 cases), which showed
that the original analytical allowlist was narrower than the read-only SQL
surface Decision-SQL is intended to support. This is a safety-layer coverage
finding, not a model-accuracy result.

## Previous policy limitation

The prior policy used a small flat allowlist. That preserved a conservative
fail-closed posture, but it also rejected reviewed pure analytical operations
such as numeric transforms, JSON construction/extraction, array operations,
ordered aggregates, and string aggregation. M24 expands the explicitly
reviewed surface by semantic family; it does not infer safety from benchmark
presence.

## Security model

M1 remains an explicit allowlist and the sole SQL safety authority. Newly
reviewed functions are read-only analytical operations whose resource use is
still subject to the existing EXPLAIN cost gate, statement timeout, row bound,
reader role, and read-only transaction. Nondeterministic functions remain a
determinism boundary, stateful/dangerous functions remain denied, and unknown
functions fail closed. SELECT-only, single-statement, catalog, table, column,
and physical wildcard protections are unchanged.

## Function families

The policy now organizes reviewed functions as aggregate, datetime, null
handling, numeric, string, array, JSON, window, subquery, and cast families.
The full new surface is recorded in the safe aggregate M24 fixture. Names in
that fixture are general PostgreSQL/SQLGlot policy vocabulary; no protected
case-to-function mapping is committed.

## Scope resolution

M1 now uses SQLGlot scope traversal. Each relation in a SELECT scope is
resolved independently. CTEs, derived tables, nested scopes, lateral
relations, table-valued array expansion, correlated outer references, explicit
CTE/derived column lists, row-valued relation references, and projection aliases
in `ORDER BY`/`GROUP BY` derive their visible output columns from the relation
projection. Base tables are still checked against the physical queryable
catalog, and invalid physical or derived columns remain rejected.

## Preserved boundaries

The four multi-statement official solutions remain outside the generated-query
contract. `CURRENT_DATE` and related current-time functions remain denied for
determinism. The M1 cost threshold that rejected one official query was not
relaxed. No provider prompt, model, routing, ResultShape, QueryPlan, memory,
retrieval, or evaluator behavior changed.

## Protected benchmark evidence

The local protected GT artifact was used only for provider-free replay. It is
gitignored and untracked. The committed M24 fixture contains aggregate counts,
general policy vocabulary, and hashes only; it contains no protected SQL,
questions, external knowledge, test cases, or case-specific mappings.

## Before/after replay

The unchanged protected preflight runner was replayed against all 180 official
SELECT solutions with zero provider calls. Before M24: 21 accepted, 155
rejected, and four planning/multi-statement outcomes. After M24: 166 accepted,
10 rejected, and four planning/multi-statement outcomes. The remaining policy
outcomes are nine determinism-boundary `CURRENT_DATE` cases and one general
cost rejection. All 166 newly accepted reference solutions executed
successfully and passed the existing evaluator sanity check.

The scope repair reduced `UNKNOWN_COLUMN` from 13 to zero through generic
scope logic. The first-failure attribution was 132 function-policy-only
recoveries and 13 scope-only recoveries. The four multi-statement cases and
the remaining determinism/cost cases were not made executable by policy
special cases.

## Historical regression

The frozen 774-case BIRD/Defog provider corpus was not replayed in this
environment because its external database roots were not provisioned. The
M24 runner records this limitation explicitly. Existing M1 unit and available
PostgreSQL integration suites were run; no old accepted-case regression was
observed in the available protected replay.

## Claim boundary

M24 validates general M1 analytical SQL coverage and scope handling. It makes
no claim about LiveSQLBench model accuracy and does not run the pending
18-case provider pilot. A future model run must still use the unchanged
provider path, M1, read-only executor, and evaluator.
