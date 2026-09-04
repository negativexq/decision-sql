# M9.5R.1 — Fail-Closed Result Binder V2 & New Independent Validation

M9.5R.1 implemented and independently exercised a new
`benchmark-result-binding-v2` protocol. The historical
`benchmark-result-binding-v1` remains frozen, unsafe research evidence; it was
not edited or reused as the new binder.

## Protocol boundary

V2 binds a generated SELECT projection only after deterministic, generated-side
proof is complete:

1. preserve query-scope relation-instance identity;
2. resolve every physical lineage candidate without semantic deduplication;
3. fail closed when a candidate is ambiguous or unsupported;
4. build a bounded expression fingerprint only after all semantic leaves are
   unique; and
5. require exactly one compatible predeclared semantic slot.

The proof paths are separate: governed compiler provenance, unique direct
lineage, CTE/derived passthrough lineage, and bounded expression fingerprints.
Aliases corroborate a unique proof but never create uniqueness. The binder API
has no reference SQL, reference rows, reference values, oracle, evaluator,
question-string, or case-ID inputs. It does not transform result rows and does
not call Evaluator V2 while deciding bindings.

The V2 semantic source was frozen before independent execution:

| Item | Value |
| --- | --- |
| protocol | `benchmark-result-binding-v2` |
| pre-validation source hash | `ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e` |
| post-validation source hash | `ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27fd9ecda3b78fb8e` |
| V2 comparator | `m92-result-equivalence-candidate-v1` |
| comparator source hash | `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97` |
| validation recipe hash | `dbb8f972c9fc81415ec4e1f3b4e4e12e9de19a5fc57cf8f837c7edd64e0edf55` |
| adversarial recipe hash | `d50aa1b0866ccde7325d550156429298588875cb2c1f039c540b758c786aeea8` |
| independent corpus hash | `2c523687856cad163ae07f13de692f8bf910730181092465e7e9e75af0f31532` |
| positive subset hash | `c09a70e14df4fd02bb080f7f3a1e4e77c2da99bbd792b5c8b1853ab3b203e041` |
| wrong-negative subset hash | `161800677214879fa791be15c49cb798270f00eb0c9904f2c2794dcaaf36e2b3` |
| fail-closed subset hash | `70d87ceb32185184c27ba4cc070a9699acc3e7418d58aec9acb13d0cb2a2a95a` |
| governed-positive subset hash | `58bc78c3d64ab7184b103277711abb19bec41882688423ef431276f46d8dd87e` |
| direct-positive subset hash | `70355699ff5ab3669d13c22c57b21d45645373058cd198f38cf8cb73eea7d3a2` |
| independent oracle hash | `4f423f691d4398152d998affb512458044fcd4d7d31302b16229497cfa1cc721` |
| candidate SQL fixture hash | `6a8f34af21ffc3febdf56260146ed53903845dbe5c17bc01f5430242fab39ac6` |
| reference SQL fixture hash | `dc8bdeca3bf766b0ab2a455291039ce0dfa87ea90562f8dd9b5e3b4f10c89cbb` |
| contract/spec fixture hash | `a72161938c6a5f7a91eeafb8e7b5db779fd9b19b333fb212e7018ce326d47b1a` |

## New independent corpus

The corpus is `decisionsql-m95r1-binding-v2-independent`, version
`m95r1-binding-v2-independent-v1`, with 120 new case IDs. It contains 60
semantically-correct positives (20 governed-provenance and 40 direct), 40
semantically-wrong executable candidates, and 20 ambiguity/unsupported
fail-closed cases. The recipe was fixed before implementation and the corpus
was not selected from V2 results. The adversarial corpus is
`decisionsql-m95r1-binding-v2-adversarial`, version
`m95r1-binding-v2-adversarial-v1`, with 60 new cases.

The provider-free harness uses deterministic `QueryExecution` fixtures for
binding/evaluator validation. It made zero provider, generation, grounding,
embedding, reranker, or database calls. The SQL fixtures parse deterministically;
this is binding-protocol validation, not M1/database execution and not a
Text-to-SQL accuracy benchmark.

Results:

| Measure | Result |
| --- | ---: |
| independent positives fully bound/evaluable/accepted | 60/60/60 |
| governed positives correctly bound | 20/20 |
| direct positives correctly bound | 40/40 |
| wrong-negative V2 false accepts | 0/40 |
| expected fail-closed cases | 20/20 |
| independent incorrect semantic bindings | 0 |
| new adversarial expectations | 60/60 |
| relation-instance ambiguous cases incorrectly bound | 0 |
| alias-only bindings | 0 |
| value/reference/evaluator search | 0 |

The qualified self-join controls bind when one relation instance is uniquely
qualified. Unqualified self-join projections remain `AMBIGUOUS`, including
when both candidates map to the same static semantic identity. Duplicate
projections competing for one slot fail closed. Distinct aggregates, ratios,
window partition/order/frame semantics, CTE passthrough, and derived-table
passthrough remain distinguishable within the bounded support set.

## Classification and M10 status

Final classification: **FAIL_CLOSED_BINDER_V2_INDEPENDENTLY_VALIDATED**.

This means V2 passed this frozen M9.5R.1 protocol; it is not a claim of
universal binder correctness. The consumed M9.5 validation population is not
independent evidence for V2 and may only be used for regression. No new
Text-to-SQL accuracy score was produced.

`M10_BINDING_PROTOCOL_READY` is true for the binding gate, but M10 was not
started here. No M10 corpus was constructed or consumed, and no provider call
was made. Before M10, this checkpoint must be reviewed and the accepted V2
research must be checkpointed. Future M10 must retain every unbindable output as
an explicit evaluation-protocol failure rather than dropping cases after
generation.

Historical product evidence remains unchanged: M7 combined DecisionSQL is
99/150 = 66%; M8 remains 105/150 observed counterfactual; M9 remains 75/120
reconstructed direct outputs. No adjusted accuracy was created.
