# M50A — Typed Answerability Boundary Feasibility Audit

## Historical preservation

M50.1, M50, M49, M48B.2, and README hashes are unchanged.

## M50A scope

Provider calls: **0**
Model calls: **0**
Runtime changes: **0**
Prompt changes: **0**

## Parent evidence

Parent: `M50.1` at `7e1137ced6087e8adf8a9da5ef419b0fc749f30c`; M50.1 conclusion remains `PARTIAL_DISCRIMINANT_EVIDENCE`.

## Core feasibility question

The audit separates factual/catalog availability from request-specific semantic sufficiency and uniqueness. It does not implement a runtime gate or expose new model context.

## Source allowlist

Phase A used the 90-case order, model-case files, `benchmark/context.py`, and public authority JSON only.

## Source denylist

Truth behavior, references, result contracts, fixtures, M49 labels, M50 transitions, and case IDs as logic were excluded from feature construction.

## Existing server-owned metadata inventory

The scoped inventory is complete: schema/entities, attributes, authorized relationships, metrics, business rules, temporal rules, and policy are rendered by the existing governed-context builder. Existing semantic catalog/relationship types were inventoried but not revived or changed.

## Required-fact identification feasibility

`PARTIALLY_DERIVABLE`: there is no typed required-fact inventory in the generation request. Mapping arbitrary question text to required facts would require free-form interpretation.

## Candidate primitive features

Deterministic metadata primitives are available for public schema, authorized-relationship catalog, semantic-definition catalog, temporal-definition catalog, and policy catalog. Request-scoped features return explicit `UNKNOWN`; uniqueness and composite answerability return `UNRESOLVED`.

## Primitive feature feasibility

Five metadata primitive families are fully supported. Eight request-scoped families are partial or unresolved; UNKNOWN is preserved rather than coerced to false.

## Uniqueness-of-interpretation feasibility

`UNIQUENESS_NOT_DETERMINISTICALLY_DERIVABLE`: current metadata has no typed interpretation inventory or ambiguity graph. Determining uniqueness for arbitrary natural-language requests would require free-form model reasoning.

## Blind feature derivation

Derived 90/90 rows before label join. The blind matrix hash is `ea240e623fd41c60aa69813038d4d8fac5bb4973abfc47b2d9f746e46946ef4a` and replay hash is `ea240e623fd41c60aa69813038d4d8fac5bb4973abfc47b2d9f746e46946ef4a`.

## Blind feature matrix hash

`ea240e623fd41c60aa69813038d4d8fac5bb4973abfc47b2d9f746e46946ef4a`

## Evaluator leakage audit

Phase A truth/reference/contract/fixture/M49/M50 access: **NO**. Leakage count: **0**.

## Full 90-case evaluation

Truth distribution: {'ANSWERABLE': 60, 'AMBIGUOUS': 9, 'AUTHORITY_BLOCKED': 15, 'POLICY_BLOCKED': 6}.

## ANSWERABLE analysis

All 60 cases retain UNKNOWN request-scoped availability and UNRESOLVED uniqueness; no composite answerability label was emitted.

## AMBIGUOUS analysis

All 9 cases retain UNKNOWN request-scoped availability and UNRESOLVED uniqueness. No case was deterministically marked safe-to-answer.

## AUTHORITY_BLOCKED analysis

All 15 cases retain separate authorized-relationship metadata and unresolved request mapping; authority was not collapsed into answerability.

## POLICY_BLOCKED analysis

All 6 cases retain separate policy metadata; policy was not used as an answerability oracle.

## M49 target analysis

The target rows were evaluated only after the blind matrix was frozen; their public metadata did not yield a deterministic request-specific uniqueness state.

## M50 transition analysis

M50 labels were joined after freeze for evaluation only and did not affect feature values.

## subscription_18 canary

Candidate state: `UNKNOWN`. Public catalogs are present, but the blind contract cannot determine that the competing time interpretations are unresolved.

## warehouse_13 canary

Candidate state: `UNKNOWN`. Public authorized relationships and definitions are present, but request-to-fact mapping is not typed.

## subscription_06 canary

Candidate state: `UNKNOWN`. The same boundary remains UNKNOWN rather than being inferred from the historical target label.

## warehouse_08 canary

Candidate state: `UNKNOWN`. Its public metadata does not establish request-specific answerability without evaluator semantics.

## Full-boundary feasibility

**NO**. Required-fact identification and uniqueness gates fail.

## Primitive-only feasibility

**YES**: a shadow contract for factual/catalog primitives is feasible if it preserves UNKNOWN and does not make the final decision.

## Architecture candidacy

Worth investigating as a zero-call shadow/diagnostic contract; not implemented here.

## Model-context candidacy

Safe to investigate only for provenance-preserving factual primitives. Not ready for a final answerability field or decision instruction.

## Determinism

Two blind derivations were identical; provider/model calls remained zero.

## Tests

The M50A unit tests cover 90-case replay, denylist-field absence, explicit UNKNOWN/UNRESOLVED states, and zero-call integrity.

## Repository state

Historical hash mismatches: `0`.

## Final verdict

`TYPED_AVAILABILITY_PRIMITIVES_FEASIBLE`

## Next milestone readiness

M50.2: **NO**. M51: **NO**. Recommended next milestone: `M50B — Typed Context-Availability Primitives Shadow Contract`.
