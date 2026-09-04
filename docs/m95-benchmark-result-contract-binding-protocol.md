# M9.5 — Benchmark Result-Contract Binding Protocol

## Executive conclusion

M9.5 is **BINDING_PROTOCOL_UNSAFE**. The first frozen adversarial execution
found one genuine incorrect semantic binding: an unqualified `name` in a
self-join was bound to the customer-name identity even though the generated
projection was ambiguous. M10 remains blocked.

The protocol was conservative in several other cases, but the single false
binding is sufficient to reject it. The binder was not changed after the
validation execution and the failed result is retained as evidence.

## Scope and starting state

This was an offline evaluation-protocol experiment. It used the 150 persisted
M7 P1 generated SQL artifacts (100 historical DEV records and 50 historical
HOLDOUT records) and a new 40-case SQL-only adversarial fixture. No provider,
SQL-generation, database, evaluator, repair, judge, embedding, or reranker
call was made. M10 consumed zero cases and its blocked document was preserved:
[`docs/m10-clean-residual-rebaseline.md`](m10-clean-residual-rebaseline.md).

The frozen V2 comparator remained:

- contract: `result-equivalence-contract-v1`
- comparator: `m92-result-equivalence-candidate-v1`
- comparator source SHA-256:
  `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97`

The M9.2 and M9.3 suites were rerun after the binder experiment and remained
accepted and independently validated, respectively.

## Protocol design

The proposed protocol version was `benchmark-result-binding-v1`. It introduced
an evaluation-only `ResultBindingSpec` separate from the frozen result contract.
The binder inputs were limited to:

```
generated SQL + generated projection AST + QueryExecution columns
    + server-owned schema semantic map + predeclared binding spec
        → BoundResult
```

Reference SQL, reference rows, expected correctness, the V2 comparator, and
question-specific runtime rules were not accepted by the binder API. The
adapter attached bindings only; it did not alter rows, reorder columns,
deduplicate, aggregate, rewrite values, or search subsets.

The bounded implementation used explicit physical-column lineage and semantic
expression fingerprints. Aliases were never sufficient to bind a slot. It
covered direct columns, aggregates, arithmetic, CASE, windows, simple CTEs,
derived tables, and scalar expressions where the AST could be resolved; it
failed closed when an expression or lineage was unresolved.

The intended order was preserved:

```
generated SQL → deterministic binding → BoundResult → Evaluator V2 once
```

No evaluator-as-binding-oracle loop existed.

## Frozen inputs and hashes

The validation IDs were frozen before binder execution. The 60-case validation
selection excluded all M9.1 audit IDs and was selected deterministically from
the recoverable, resolvable historical population. The selection hash was
`103b664450784986d23f2a6b842dd4fc00b9301226db3c3691b72bef06f668b2`.

| Artifact | SHA-256 |
| --- | --- |
| validation ID selection | `103b664450784986d23f2a6b842dd4fc00b9301226db3c3691b72bef06f668b2` |
| validation oracle metadata | `3c94f690ce6037871b646d251eee77e55bc65431c7aab3b89f5d728ad1ec9cac` |
| binding-spec metadata | `6141483d2f6e85b75f29fc9796526951751b673e31998b8c1981f936086f2e32` |
| schema semantic map | `8ad6544f8f3d69705ff53665a023ce8913029835e046028d889291ac08315407` |
| binding taxonomy | `252498eeb99eaa261b03698f575ad17315503b94c0763988c3f47cfa5586c79b` |
| adversarial fixture | `5f86e47e340edd5d25de33f60478f578ea817954e36810a1dda491afab3a985d` |

The binder source hash was
`aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb` both
before and after the validation executions.

## Results

Historical development contained 90 cases and 214 generated projections. The
60-case validation contained 117 projections, of which the frozen semantic
oracle classified 109 as correct bindings and 8 as incorrect bindings. Among
52 semantically correct validation cases, 49 were fully evaluable by the
protocol (94.2%). This is a coverage failure even apart from the safety issue.

The adversarial suite produced 36/40 expected outcomes. The failures were:

| Case | Observed issue |
| --- | --- |
| `A10_COMMUTATIVE_ARITHMETIC` | conservative false negative; equivalent addition was rejected |
| `A17_SELF_JOIN_AMBIGUITY` | unsafe false positive; ambiguous unqualified `name` was bound |
| `A27_TWO_OUTPUTS_ONE_SLOT` | fail-closed ambiguity was observed, but the harness expected the broader reject label |
| `A28_ONE_OUTPUT_TWO_SLOTS` | fixture construction exposed an invalid ambiguity setup |

`A17_SELF_JOIN_AMBIGUITY` is the decisive failure. A wrong semantic binding is
more serious than an unbound output, and one such result is enough for
`BINDING_PROTOCOL_UNSAFE`.

The implementation did not use alias-only binding, value matching, reference
SQL at runtime, reference-result matching, post-hoc manual mapping, subset
search, evaluator search, LLM judgment, embeddings, case-ID rules, or
question-string rules. The failure came from insufficient relation-instance
tracking for self-join ambiguity, not from the frozen V2 comparator.

## Regression and historical boundaries

M9.2 remained `RESULT_EQUIVALENCE_CONTRACT_ACCEPTED` and M9.3 remained
`RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED`. Their comparator semantics and
fixtures were unchanged. Historical scores remain unchanged: M7 is 99/150
(66%), M8 is 105/150 observed counterfactual, and M9 is 75/120 reconstructed
direct output observations. M9.5 created no adjusted benchmark accuracy.

M9.5 did not change `app/`, runtime defaults, M1, M3, M4, routing, prompts,
Evaluator V2 semantics, or ResultEquivalenceContract V1. It did not construct
or consume the M10 200-case corpus.

## Decision and next milestone

`M10_BINDING_PROTOCOL_READY = false`.

The precommitted safety rule rejects the protocol because the independent
adversarial suite contained an incorrect semantic binding. The exact next
milestone is:

**M9.5R — Result Binding Safety Boundary Audit**

That audit must understand and repair the self-join ambiguity mechanism through
a new binder version and a new independent validation corpus. The consumed M9.5
validation set must not be reused to certify a revised binder. M10 must not
start until a later protocol version is independently safe.
