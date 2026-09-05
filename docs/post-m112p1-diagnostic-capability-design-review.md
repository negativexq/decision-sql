# POST-M11.2P1 Diagnostic Capability Design Review

This additive review starts at `a074f202ca73290323f6158d545668565e502531`.
It preserves the predecessor result: `POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT`,
with `STATIC_SEMANTIC_PROOF_LIMIT` at 24/46 and materially narrowed future
architecture-selection authority.

## Decision

The review selects D2, `COUNTEREXAMPLE_GUIDED_DIFFERENTIAL_EXECUTION`, as the
single primary experimental evidence-acquisition direction eligible for an
independently validated prototype. It is not a primary failure-attribution
solution. D7, `INDEPENDENT_ADVERSARIAL_SEMANTIC_FIXTURES`, is required
validation scaffolding. This is not production approval and does not select a
generation intervention.

The D2 direct-static count of 24/24 means that bounded one-sided evidence
acquisition can be attempted for all 24 static design cases. It does not mean
that D2 resolves all 24 or attributes a failure boundary in any of them. The
Level-1 evidence opportunity is 24/24; Level-2 bounded relation resolution is
up to 22/24 only when `NON_EQUIVALENCE_WITNESSED` is actually produced; and
Level-3 failure-boundary attribution is 0/24. For the two
`MULTIPLE_EVIDENCE_TYPES_REQUIRED` cases, D2 is necessary but not sufficient.

D2 authority is narrow: case applicability is
`SUPPORTED_WITH_NARROW_SCOPE`; non-equivalence witness authority is
`SUPPORTED_WITH_NARROW_SCOPE` only after independent validation; equivalence
proof and failure-boundary attribution are `NOT_SUPPORTED`; and primary
experiment-direction authority is `SUPPORTED_WITH_NARROW_SCOPE`.

M11.2P2 therefore means D2B,
`INDEPENDENT_ADVERSARIAL_FIXTURE_COUNTEREXAMPLE_SEARCH`. D2A replay on the
original database is `REDUNDANT_WITH_P3`; independently authored adversarial
fixture search has `MATERIAL_INCREMENTAL_VALUE`. `NO_COUNTEREXAMPLE_FOUND` is
abstention and is never equivalence.

D2 can add `NON_EQUIVALENCE_WITNESSED` evidence from an isolated diagnostic
fixture. It cannot prove universal SQL equivalence, identify a causal component
by itself, or turn `NO_COUNTEREXAMPLE_FOUND` into equivalence. It must abstain
on inconclusive, invalid, or non-executable diagnostic inputs.

D3 remains a potential extension for compound divergence, but component
isolation must abstain when changed components interact. D1 is retained as a
narrow secondary possibility. D5 and D6 are narrow contract directions. D4
requires independently grounded authorship and must not encode reference SQL
syntax. D8 is non-authoritative because model-family contamination,
reproducibility, calibration, and independence remain unresolved. D9 remains
the safe baseline if independent validation fails.

## Evidence boundary

The 24 static and 15 compound cases are consumed design evidence, not an
independent validation set. Structural profiles describe observable SQL
differences; they are not hidden generation-failure labels. The largest
`ORDER_SIGNATURE_CHANGE + PROJECTION_CHANGE` profile is therefore not proof of
ordering or projection failure. CORE output masking and all P0 adjudications
remain immutable.

Future validation must contain independently authored known-equivalent and
known-non-equivalent pairs, adversarial coverage for literal case, OFFSET,
`JOIN USING`, NULLs, duplicates, joins, aggregates, formulas, windows, order,
limits, set operations, and projection entitlement. Decision-grade positive
evidence requires zero observed false positives on that validation set, with
coverage and abstention reported separately. This is an empirical precision
gate, not a statistical zero-risk claim.

The authority ladder is: design selected, experimental implementation,
independent validation, shadow re-attribution, authority review, and only then
possible architecture-selection evidence. No stage after design selection has
started in this review. No provider, DB, retrieval, execution, fixture
generation, candidate generation, evaluator/runtime, P0/P1, or benchmark work
occurred. Public Defog/BIRD reruns remain closed.
