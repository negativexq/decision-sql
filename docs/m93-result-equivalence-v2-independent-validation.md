# M9.3 — Versioned Result Equivalence V2 Independent Validation

## Executive conclusion

The frozen M9.2 candidate passed a new, provider-free 160-case independent
validation corpus without changing comparator semantics. It accepted all 40
contract-equivalent positives and all 30 strict controls, rejected all 80
genuine negative cases, and rejected all 10 invalid or ambiguous contracts.
The result is:

`RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED`

This validates the candidate contract semantics for this internal validation
corpus. It does not replace evaluator V1, rescore historical milestones, or
establish universal SQL equivalence.

The next milestone is exactly:

**M9.4 — Versioned Evaluator V2 Integration & Provenance**

M9.4 remains future work. No evaluator integration was performed here.

## Scope and frozen provenance

M9.3 was offline and provider-free. There were zero provider calls, SQL
generation calls, repair calls, judge calls, embedding calls, reranker calls,
database calls, and database mutations. The existing M9.2 implementation was
loaded unchanged:

| Item | Frozen value |
| --- | --- |
| Contract version | `result-equivalence-contract-v1` |
| Candidate comparator | `m92-result-equivalence-candidate-v1` |
| Comparator source SHA-256 | `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97` |
| M9.2 fixture hash | `b29bf443f4c883f31f0c14b0b5a0bfd5d2bf0acbc9ea0ff6c1810ac79b4360c7` |

The comparator source hash was identical before and after the primary run.
The contract schema and comparator behavior were not modified after the
independent corpus was frozen.

## Independent corpus

The corpus is:

`decisionsql-m93-equivalence-independent`, version
`m93-equivalence-independent-v2`.

It contains exactly 160 new deterministic cases, split into two fixed
80-case validation halves. Case IDs have zero overlap with the M9.1 audit and
the M9.2 143-case regression corpus. The fixture uses fresh deterministic
values and new result layouts; it does not copy M9.2 result fixtures.

| Category | Cases | Expected behavior |
| --- | ---: | --- |
| Contract-equivalent positives | 40 | Accept as contract-equivalent |
| Strict-equivalent controls | 30 | Accept |
| Projection negatives | 30 | Reject |
| Grain/cardinality negatives | 20 | Reject |
| Value/semantic negatives | 20 | Reject |
| Order/duplicate/shape negatives | 10 | Reject |
| Invalid or ambiguous contracts | 10 | Contract-invalid / reject |

The corpus contains optional identity, display-metadata, auxiliary, and other
declared-optional cases; optional fields occur before, between, and after
required fields. It also covers scalar, grouped, multi-dimensional/entity-list,
ordered, NULL, numeric-tolerance, multiple-reference, alias, and column-order
variants. The suite deliberately keeps semantic slot bindings in frozen
evaluation metadata rather than discovering slots from values or SQL text.

Frozen hashes:

| Artifact | SHA-256 |
| --- | --- |
| Fixture | `8865f5fa1e12cab1be33852d418ad30c397a84087b743b19da53d6c7c3c8fcd4` |
| Full corpus | `523f91f94d6c8f46db00d9cd6ec0368a0eff078f4af384fd1610cd285be483a2` |
| Validation A | `dc00d0d25d11d30938fb543b8ea09ff8ddf95b1c2a6a6d108b1907e141f295f1` |
| Validation B | `f7a1fd1072c1cdf05dc747ae9dfb5f9300517403ad7c2da165eae1a327697a8d` |
| Contract-equivalent positives | `1c7999e9a75f184ca71c471b918fc8a17aa4a63869201e51a31906e0296e646b` |
| Strict controls | `2ff74369bbf2b843afb54e925c2fa4e6b986a0fbe70f3cfd280e9ee39aa71b03` |
| Projection negatives | `b8bf190ff7c8d982161cb279c8b42588f3ec45c423862062141697f83f25bc62` |
| Grain negatives | `d3f56feabb758c0f01e776d10b1609353460bd8b36377eb3fa113c2425d91d52` |
| Value negatives | `7a6b18cd969583ed40d1b0244a078307b68972f1098f482264479c95a96c9962` |
| Order/duplicate negatives | `98438675304a0ad6d5f2aa9b04aca71921add7d0c977c364b034d57c106c58da` |
| Invalid contracts | `a7131e275d413a219fab03bd235dcfbdc18037ead5e6b90b5077a8a80cdd66c1` |

Both validation halves contain every category, so the result does not depend
on one half containing all of the positive or negative evidence.

## Candidate behavior

The primary run produced:

| Category | Cases | Accepted as expected | False accepts | False rejects |
| --- | ---: | ---: | ---: | ---: |
| Contract-equivalent positives | 40 | 40 | 0 | 0 |
| Strict-equivalent controls | 30 | 30 | 0 | 0 |
| Projection negatives | 30 | 30 rejected | 0 | 0 |
| Grain/cardinality negatives | 20 | 20 rejected | 0 | 0 |
| Value/semantic negatives | 20 | 20 rejected | 0 | 0 |
| Order/duplicate/shape negatives | 10 | 10 rejected | 0 | 0 |
| Invalid or ambiguous contracts | 10 | 10 rejected | 0 | 0 |

Thus:

- legitimate positive cases: 70/70 accepted;
- genuine negative cases: 80/80 rejected;
- false accepts: 0;
- false rejects: 0;
- overall expected outcomes: 160/160.

The outcome totals were 40 `CONTRACT_EQUIVALENT`, 30
`STRICT_EQUIVALENT`, 80 `NOT_EQUIVALENT`, and 10 `CONTRACT_INVALID`.

Validation A and B each retained their fixed category composition and had
zero false accepts. The optional-field groups each contributed 10 positives
and passed 10/10. The known independent positive dimensions also passed: five
scalar cases, ten single-dimension grouped cases, ten multi-dimensional or
entity-list cases, and five order-sensitive cases.

## Safety boundaries

The candidate only ignores output slots explicitly declared optional by the
contract. It does not use arbitrary projection-subset search, deduplicate
rows, reaggregate rows, rewrite values, infer slots from matching values, or
apply case-ID or question-string rules. Required slots, semantic row identity,
duplicate multiplicity, scalar/tabular shape, required row order, NULL behavior,
and the existing numeric tolerance remain binding.

The independent safety cases passed without false acceptance:

- undeclared extra output: 0 false accepts;
- missing required output: 0;
- wrong semantic slot: 0;
- wrong values: 0;
- extra or collapsed/expanded grain: 0;
- duplicate mismatch: 0;
- required-order mismatch: 0;
- scalar/tabular mismatch: 0;
- ambiguous or invalid binding: 0.

Metamorphic and mutation-safety checks were recorded as 12/12 and 12/12,
respectively. They preserve the same safety properties: optional companions
can be accepted only when declared and non-grain-changing, while changes to
required fields, row multiplicity, required order, or semantic bindings are
rejected.

## M9.2 regression preservation

The frozen 143-case M9.2 regression was run before and after the independent
validation. Both runs remained:

`RESULT_EQUIVALENCE_CONTRACT_ACCEPTED`

with identical semantic summaries and decisions. M9.2 fixture, historical,
synthetic, and comparator hashes were unchanged. The M9.2 suite and M9.3
suite remain separate; they must not be reported as one combined accuracy.

The V1/V2 diagnostic matrix over the independent cases was:

| V1/V2 outcome | Count |
| --- | ---: |
| V1_ACCEPT / V2_ACCEPT | 30 |
| V1_ACCEPT / V2_REJECT | 24 |
| V1_REJECT / V2_ACCEPT | 40 |
| V1_REJECT / V2_REJECT | 66 |

This is a side-by-side diagnostic only. V1 was not edited and V2 was not
integrated.

## Limitations

The semantic bindings and intended contracts are frozen evaluation-owned
annotations. They prove that the comparator semantics are coherent and safe
on genuinely new cases; they do not provide automatic runtime semantic-slot
binding. M9.3 is therefore independent validation of the comparator contract,
not universal evaluator validation and not a deployable evaluator integration.

The 70 legitimate positives and 80 negatives are deterministic result-fixture
cases, not newly generated SQL. No public benchmark was rerun. M2.8 remains
closed; M9.3 does not reintroduce an LLM-generated ResultShape or any provider
intermediate stage.

## Historical score boundary

Historical results are unchanged:

- M7 remains 99/150 = 66%.
- M8 remains 105/150 observed counterfactual.
- M9 remains 75/120 reconstructed direct outputs.

M9.3 created no adjusted benchmark accuracy and made no production change.
Future evaluator adoption, if pursued, must be versioned and side-by-side
with V1. That is the purpose of the next milestone, M9.4.
