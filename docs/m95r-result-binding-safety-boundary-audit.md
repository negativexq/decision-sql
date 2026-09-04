# M9.5R — Result Binding Safety Boundary Audit

## Executive conclusion

M9.5R classification: **BINDING_SAFETY_BOUNDARY_IDENTIFIED**.

The consumed `benchmark-result-binding-v1` evidence has a coherent safety
failure. All eight independent incorrect projection bindings share one
over-binding rule: an expression whose fingerprint matches no predeclared
contract slot is nevertheless returned as `BOUND` with an `expression:<hash>`
identity. The independent projection audit therefore found 8/8 unsafe
over-bindings, in addition to the controlled A17 self-join false binding.

The most important separate failure is `A17_SELF_JOIN_AMBIGUITY`. An
unqualified `name` in a join with two `customers` relation instances was bound
to `physical:customers.name`. The binder collapsed aliases `a` and `b` to one
base-relation key before resolving candidates. A unique-provenance rule would
have refused this output.

An audit-only Boundary B—unique lineage plus bounded expression fingerprints,
exactly one declared slot, no alias-only proof, and fail-closed ambiguity—
eliminates all eight known independent over-bindings and all four adversarial
failure conditions under normalized fail-closed outcomes. It retains 109/109
known-correct projection bindings and 49/52 semantically-correct cases as
potentially fully evaluable on the consumed evidence. This justifies a new
versioned binder experiment, not acceptance of a binder-v2.

`M10_BINDING_PROTOCOL_READY = false`. M9.5's 60-case validation population is
consumed and cannot certify a revised binder.

## Scope and provenance

The audit began at `9fb6413811661d3320db83507f737feecff46657`, with `main`
aligned to `origin/main`. The pre-existing blocked M10 document was preserved:
[`docs/m10-clean-residual-rebaseline.md`](m10-clean-residual-rebaseline.md).
M9.5's eight independent incorrect projections, four adversarial failures, 33
development support cases, and a deterministic 30-case correct-control sample
are frozen in
[`evaluation/fixtures/m95r_failure_manifest.json`](../evaluation/fixtures/m95r_failure_manifest.json).

This was provider-free and database-free: provider calls, SQL-generation calls,
M3 calls, and database calls were all zero. No M10 corpus was created or
consumed. No historical benchmark score was produced.

Frozen source checks remained unchanged:

| Artifact | SHA-256 |
| --- | --- |
| M9.5 binder v1, before/after | `aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb` |
| M9.2 comparator, before/after | `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97` |
| M9.5 validation selection | `103b664450784986d23f2a6b842dd4fc00b9301226db3c3691b72bef06f668b2` |
| M9.5 validation oracle | `3c94f690ce6037871b646d251eee77e55bc65431c7aab3b89f5d728ad1ec9cac` |
| schema semantic map | `8ad6544f8f3d69705ff53665a023ce8913029835e046028d889291ac08315407` |
| M9.5 binding-spec metadata | `6141483d2f6e85b75f29fc9796526951751b673e31998b8c1981f936086f2e32` |

## Actual data flow and information audit

M9.5's binder API is generated-side only:

```text
generated SQL + QueryExecution columns + predeclared binding spec
    + server-owned schema semantic map
        -> BoundResult + binding report
```

It does not accept reference SQL, reference results, values, an evaluator,
expected correctness, or a question-specific runtime rule. The persisted M7
P1 artifacts do contain case-level governed `semantic_provenance`, but M9.5's
SQL-only harness did not pass that provenance into the binder. That explains
the reported `GOVERNED_PROVENANCE = 0` and is a coverage/integration finding,
not evidence that M3 lacks provenance.

Likewise, `DIRECT_COLUMN_LINEAGE = 0` in the M9.5 reports is not proof that
direct lineage was impossible. The harness constructed every binding spec as
`BindingKind.EXPRESSION`, so all 117 validation projections were reported via
`SEMANTIC_EXPRESSION_FINGERPRINT`. Separating direct-lineage proof from
expression proof is a requirement for a future version.

## Frozen forensic taxonomy

The primary taxonomy was frozen before the counterfactual calculations:

`B1_SCOPE_AMBIGUITY`, `B2_SELF_JOIN_LINEAGE_AMBIGUITY`,
`B3_UNQUALIFIED_COLUMN_AMBIGUITY`, `B4_CTE_DERIVED_LINEAGE_COLLISION`,
`B5_EXPRESSION_FINGERPRINT_COLLISION`, `B6_SEMANTIC_LEAF_COLLISION`,
`B7_ALIAS_OVERWEIGHTING`, `B8_INSUFFICIENT_SOURCE_QUALIFICATION`,
`B9_MULTI_CANDIDATE_TIE_BROKEN_UNSAFELY`,
`B10_UNSUPPORTED_EXPRESSION_OVERBOUND`, `B11_GOVERNED_PROVENANCE_LOSS`,
`B12_SCHEMA_MAP_IDENTITY_COLLISION`, `B13_OTHER_OVERBINDING`, and
`B14_UNDETERMINED_OVERBINDING`.

The eight independent incorrect projections were all adjudicated
`B13_OTHER_OVERBINDING`, `SPEC_SUFFICIENT`, and
`S1_POTENTIAL_FALSE_ACCEPT`. There are eight projection records across seven
cases:

| Case / projection | Generated projection | V1 result | Forensic finding |
| --- | --- | --- | --- |
| `m7-clear_direct_complex-007 / 0` | `customers.name AS dimension_0` | `BOUND`, undeclared expression identity | customer column is not the reference region slot; unmatched output was over-bound |
| `m7-clear_direct_complex-012 / 0` | `SUM(orders.total_amount)` | `BOUND`, undeclared expression identity | aggregate is not the requested running-window output |
| `m7-clear_direct_simple-013 / 1` | `COUNT(DISTINCT c.id)` | `BOUND`, undeclared expression identity | DISTINCT count is not the declared plain count slot |
| `m7-filter_aggregation-003 / 0` | `COALESCE(SUM(r.amount), 0)` | `BOUND`, undeclared expression identity | wrapper changes the declared SUM expression |
| `m7-filter_aggregation-010 / 0` | `o.order_number` | `BOUND`, undeclared expression identity | extra entity column is not the reference measure slot |
| `m7-filter_aggregation-010 / 1` | `COALESCE(SUM(p.amount), 0)` | `BOUND`, undeclared expression identity | wrapper is not the declared payment SUM slot |
| `m7-ratio_derived-006 / 0` | CASE-wrapped refund/revenue ratio | `BOUND`, undeclared expression identity | generated ratio/projection does not match the declared regional ratio slots |
| `m7-relational_composition-000 / 1` | `COUNT(DISTINCT o.id)` | `BOUND`, undeclared expression identity | DISTINCT count is not the declared plain order count slot |

The shared unsafe decision is visible in the V1 source: when a resolved
fingerprint has no declared match, it is assigned
`expression:<fingerprint_hash>` and `BindingStatus.BOUND` instead of failing
closed. This is not alias-only or value-based inference. It is still unsafe
because a generated semantic identity has been asserted without a declared
contract slot.

### A17 reconstruction

The generated SQL was:

```sql
SELECT name AS name FROM customers a JOIN customers b ON a.id=b.id
```

The SQL AST retains an unqualified `Column` (`table == ""`) and retains both
table instances: `a -> customers` and `b -> customers`. Both instances expose
`customers.name`, so relation-instance-aware resolution has two lineage
candidates:

```text
a -> physical:customers.name
b -> physical:customers.name
```

V1's `_table_aliases` instead produces one base-relation mapping for
`customers`, and its unqualified lookup deduplicates the two aliases. The
collapsed candidate count is therefore one, and V1 returns `BOUND` with
`physical:customers.name`'s fingerprint instead of `AMBIGUOUS`. The earliest
loss is relation-instance identity, so the primary cause is
`B2_SELF_JOIN_LINEAGE_AMBIGUITY`, not a later fingerprint issue. A general
candidate-count-equals-one rule, applied before semantic binding, prevents the
failure. Safe future lineage must preserve base relation identity plus
scope/relation-instance identity; a repeated base column can have different
semantic roles in a self-join.

### The other three adversarial failures

| Case | Root cause | Safety consequence | Boundary B result |
| --- | --- | --- | --- |
| `A10_COMMUTATIVE_ARITHMETIC` | `B13_OTHER_OVERBINDING` / conservative normalization gap | safe equivalent addition was rejected; no false accept | BOUND using a predeclared safe ADD normalization |
| `A27_TWO_OUTPUTS_ONE_SLOT` | `B13_OTHER_OVERBINDING` / harness expected `REJECT` while V1 correctly returned `AMBIGUOUS` | no unsafe accept; fail-closed behavior was present | REJECT-equivalent |
| `A28_ONE_OUTPUT_TWO_SLOTS` | `B13_OTHER_OVERBINDING` / invalid fixture setup: `slot_1` was absent from the contract | no unsafe accept; invalid setup was rejected | reject before binding |

The only observed adversarial false accept was `A17_SELF_JOIN_AMBIGUITY` (one
case). The eight historical incorrect bindings are potential false-accept
risk; raw historical result rows were unavailable, so an exact historical
false-accept rate cannot be claimed.

## Counterfactual boundaries

These are audit-only simulations, not binder-v2. Definitions and hashes were
frozen before calculation.

Boundary A, `UNIQUE_DIRECT_LINEAGE_ONLY`, accepts only uniquely resolvable
direct columns and deterministic passthroughs. On the SQL-only historical
inputs it retains 57/109 known-correct projections (52.3%) and 13/52 fully
evaluable correct cases (25.0%). It leaves zero known incorrect bindings, but
its coverage is too low. Correct projection retention by actual route is 46
direct and 11 governed; the 11 governed results reflect the SQL-only input,
not the native governed provenance available in M7 artifacts.

Boundary B, `UNIQUE_LINEAGE_PLUS_BOUNDED_EXPRESSIONS`, additionally permits
bounded fingerprints for aggregates, arithmetic, CASE, windows, and simple
passthrough lineage. Every semantic leaf must resolve uniquely; an expression
must match exactly one predeclared slot; aliases cannot reduce candidate
count; duplicate-slot matches, unsupported expressions, ambiguous scopes, and
undeclared expressions fail closed. It retains:

| Boundary B measure | Result |
| --- | ---: |
| known incorrect bindings remaining | 0/8 |
| known-correct projections retained | 109/109 (100.0%) |
| semantically-correct cases fully evaluable | 49/52 (94.2%) |
| governed correct projections retained | 46/46 |
| direct correct projections retained | 63/63 |
| adversarial safety failures remaining | 0 |

Boundary B's 49/52 case result is a potential-evaluability estimate on
consumed evidence, not independent validation. Its three blocked correct cases
are retained as evidence that V2 coverage needs to be measured explicitly;
they are not silently dropped.

Boundary A and B both prevent the eight historical over-bindings. Boundary B
also handles the A10 conservative case through a narrowly declared arithmetic
normalization and refuses A17 through relation-instance ambiguity. No full
join-role inference, predicate verifier, query planner, reference matching,
theorem prover, LLM, embedding, or evaluator search is required for this
projection-identity boundary.

## Normalization and schema findings

The six audited V1 normalization/scope rules are:

| Rule | Classification |
| --- | --- |
| alias removal | safe syntax normalization |
| parenthesis removal | safe syntax normalization |
| qualified name to semantic leaf | safe only after unique resolution |
| cast preservation | safe syntax normalization |
| commutative operand sorting | potentially semantic; current implementation does not robustly reorder ADD/MUL operands |
| scope alias flattening | unsafe; loses self-join relation-instance identity |

No independent failure was an alias-only binding, a value match, a reference
SQL match, a schema-map identity collision, or a fingerprint collision. The
42-entry schema map's frozen hash is unchanged. The consumed specs were
sufficient for the eight records; the issue was binder overreach rather than
spec ambiguity. Correct-control review used the frozen deterministic sample of
30 cases in addition to the 109 automatically identified correct projection
bindings.

## Future protocol shape and M10 implication

A new versioned experiment is justified as
`benchmark-result-binding-v2`, with these invariants:

- use governed compiler-owned semantic provenance whenever it is available;
- preserve query-scope relation-instance identity;
- separate direct lineage proof from expression-fingerprint proof;
- bind only when exactly one semantic identity is proven;
- treat aliases as corroboration only;
- make multiple candidates `AMBIGUOUS` and unsupported expressions `UNSUPPORTED`;
- never use reference SQL, reference values, evaluator outcomes, subset search,
  question strings, case IDs, LLMs, or embeddings.

The consumed M9.5 validation set cannot validate this new version. A future
independent validation needs new case IDs, new generated-SQL fixtures or an
independently sourced historical population, a new frozen oracle, and a new
adversarial suite. The most practical provider-free option is to first use
previously unused historical generated SQL, supplemented by independently
authored SQL-only semantic fixtures; provider-generated cases would be a
separate milestone.

Future M10 must not remove unbindable generated outputs after seeing them. To
avoid selection bias, M10 should guarantee binding coverage before provider
consumption, or retain every generated case and report binding-protocol failure
as a separate outcome rather than silently excluding it. Until a new binder
passes new independent validation, `M10_BINDING_PROTOCOL_READY` stays false.

## Quality and frozen research debt

The M9.5 source and fixtures were not modified to make their consumed evidence
look clean. The known M9.5 quality debt remains:

- project Ruff still reports fixture formatting/line-length issues and the
  frozen binder import-order issue;
- project mypy still reports four frozen binder typing diagnostics: optional
  dictionary indexing, a `values` redefinition, tuple/list extension typing,
  and an `Any` return.

These are recorded as non-semantic research-code quality debt unless a future
investigation proves a runtime safety connection. New M9.5R code is kept
separate and type/lint clean; no blanket suppression or V1 edit was made.

M9.2 remains accepted, M9.3 remains independently validated, and M9.4
integration remains accepted. Historical scores remain frozen: M7 is 99/150
(66%), M8 is 105/150 observed counterfactual, and M9 is 75/120 reconstructed
direct outputs. M9.5R produced no new benchmark accuracy and did not rerun
Defog or BIRD.

## Final decision

**BINDING_SAFETY_BOUNDARY_IDENTIFIED**

The next milestone is exactly:

**M9.5R.1 — Fail-Closed Result Binder V2 & New Independent Validation**

It should implement the frozen Boundary B as a new version, preserve V1, and
validate it on new cases. It must not reuse the consumed 60-case M9.5
validation as independent evidence. M10 remains unconsumed and must wait for
that new validation.
