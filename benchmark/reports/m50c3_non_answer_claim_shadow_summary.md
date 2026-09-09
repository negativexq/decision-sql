# M50C.3 Minimal Non-Answer Semantic Claim Shadow Integration

## Historical preservation

Starting HEAD `d286646f999f2f870366ecd14c8ed2573efb0dee`. M48B.2 through M50C.2P, README, truth, references, prompts, model context, runtime contracts, and official scores are unchanged.

## Scope and zero-call accounting

Provider calls: **0**. Model calls: **0**. Prompt/context/current-output-schema/runtime changes: **0**. No decision override, retry, repair, judge, or selector.

## Parent evidence

M50C.2's frozen checker is reused unchanged. M50C.2P population semantics remain out of scope.

## Contract narrowing rationale

The candidate addresses false abstentions only: one primary stable-ID blocker claim for NON-ANSWER decisions. ANSWER submissions carry no new semantic fields.

## Minimal wire contract

`semantic-submission-shadow-1` with `decision`, optional `sql`, and at most one `blocking_claim` containing `family`, `object_id`, and `assertion`.

## Supported decisions

`ANSWER`, `NEEDS_CLARIFICATION`, `BLOCKED_AUTHORITY`, `BLOCKED_POLICY`.

## Blocking claim schema

NON-ANSWER requires exactly one claim; ANSWER forbids one. Candidate validation is separate from the current provider schema and is not wired into generation.

## Claim families

Schema object, relationship, semantic definition, temporal definition, status definition, and policy. Population semantics is excluded.

## Assertions

Supported: `MISSING`, `UNAUTHORIZED`, `UNDEFINED` with family-specific compatibility. Policy `DISALLOWED` is excluded because the frozen checker does not represent policy applicability/state separately.

## Family/assertion compatibility

See `m50c3_claim_family_assertion_matrix.json`; invalid pairings are rejected before catalog checking.

## Stable-ID requirements

Stable colon-delimited IDs only. Free-text references are rejected; no fuzzy matching is present.

## Negative capabilities

The shadow checker does not compute answerability, correctness, required facts, uniqueness, or decision policy.

## Checker semantics

The checker delegates to M50C.2 and returns only `VERIFIED`, `CONTRADICTED`, `UNRESOLVED`, `INVALID_CLAIM`, or `NOT_APPLICABLE`.

## Shadow integration

Synthetic path only: wire validation → frozen checker → audit result. Decision, SQL, runtime path, QueryPlan, and execution remain unchanged.

## Provenance integration

The shadow result exposes a bounded, hashed, truth-free provenance payload. Existing provenance stage/event enums were left unchanged so historical completeness contracts remain untouched; a future integration point can record this payload without granting it decision authority.

## Unified trace compatibility

No M50C.1 trace version was mutated. Current historical traces remain claim-absent and reproducible.

## Synthetic wire tests

Valid and invalid synthetic wire cases are recorded in the audit artifacts; invalid family/assertion pairs, free-text IDs, ANSWER claims, and missing NON-ANSWER claims are rejected.

## Checker property tests

Existing-object contradictions, complete-catalog absence verification, relationship authority contradictions, semantic/temporal checks, policy presence, and incomplete-catalog `UNRESOLVED` behavior pass.

## Mutation tests

Mutation results are deterministic and recorded; no historical claims are fabricated.

## UNKNOWN preservation

Incomplete catalog absence remains `UNRESOLVED`.

## Shadow non-interference

Synthetic shadow-on/off comparison: **100% identical** decisions, SQL, runtime, QueryPlan, and execution outputs.

## Historical false-abstention representability

**7/7** historical false-abstention mechanisms are representable by one supported stable-ID blocker claim. This is representability only; historical claims were not observed or imputed.

## Correct non-answer future safety specification

Future evaluation gates cover claim parsing/presence/validity, authority, policy, ambiguity, correct non-answer retention, and false-abstention recovery. Historical responses were not retrofitted with claims.

## M50C.4 precommitted gates

Frozen before any future model call: parse ≥98%, NON-ANSWER claim presence ≥98%, stable-ID validity ≥95%, zero authority/policy/non-answer-to-ANSWER regressions, ≥4/7 false-abstention recoveries, positive net governed correctness, zero non-target answerable regression, median output overhead ≤40 tokens, p90 ≤70 tokens, and zero input-context growth.

## Output-size analysis

Compact sidecar estimate: median **28.5** tokens; p90 **30**; both gates pass.

## Oracle-dependency audit

App truth/reference/ResultContract/required-context dependencies: 0. Question dependency: 0. Case/domain branches: 0. Benchmark imports under app: 0.

## Case/domain independence

Generic stable-ID validation only; no case IDs, domain names, or question keyword rules.

## M48B.2 regression

Frozen historical scores remain Governed **78/90** and Answerable Runtime TSA **51/60**.

## Determinism

Contract, checker, and synthetic shadow hashes replay deterministically.

## Tests

M50C.3 tests, M50C.2 checker tests, M50C.2P analyzer tests, provenance tests, lint, formatting, and mypy pass. Full-suite historical failures are reported separately.

## Repository state

Final commit and clean-tree state are reported below.

## M50C.3 verdict

`MINIMAL_NON_ANSWER_CLAIM_SHADOW_SUPPORTED`.

## M50C.4 readiness

`YES`, with gates frozen and no model call made.

## Recommended next milestone

`M50C.4 — Fresh Minimal Non-Answer Claim Evaluation`, using unchanged input context, one-shot output-side claims only, and the frozen gates.

## M51 readiness

`NO`.
