# M50B — Typed Context-Availability Primitives Shadow Contract

## Historical preservation

Historical hash mismatches: `0`.

## M50B scope

Provider calls: **0**; model calls: **0**; prompt changes: **0**; runtime decision changes: **0**.

## Parent M50A evidence

M50A verdict: `TYPED_AVAILABILITY_PRIMITIVES_FEASIBLE`; full answerability and uniqueness remain outside scope.

## Supported primitive families

Schema, authorized relationships, semantic definitions, temporal definitions, and policy.

## Unsupported capabilities

Required-fact identification, required-fact completeness, uniqueness, final answerability, and model decisions are `NOT_COMPUTED`.

## Shadow contract design

`context-availability-shadow-contract-1`; immutable typed models, five primitive inventories, explicit negative capabilities, and no runtime integration.

## Contract version and hashes

Contract schema: `9b7cf6054f819c2aa7697740c811f339ec66e5e8c8f39fbb439c9b13bb87c805`; builder: `4ff0b22ad4eb667c9c2c95094848579dcd5c9f7972e8cd125ab89f047e5146b2`; snapshot corpus: `759d5056cf8edf8477bf1cae2d7028ab9e3da63c37d20bf6f7dfb0d7319dfb4f`.

## Canonicalization

Mappings, records, primitive IDs, and family inventories are canonically ordered; case IDs are outside snapshot hashes.

## Provenance model

Every emitted primitive carries source ID, source version, source hash, derivation rule, and `inference=NONE`.

## Synthetic contract validation

Populated, empty, missing, duplicate, conflicting, permuted, UNKNOWN, and consumer-safety checks passed.

## 90-case shadow replay

90/90 snapshots produced with zero calls.

## M50 paired snapshot parity

90/90 factual snapshot parity.

## M50A primitive parity

90/90 semantic parity across all five supported families.

## Primitive family coverage

Coverage and primitive counts are recorded in `m50b_primitive_family_coverage.json`.

## Negative-capability validation

90/90 snapshots retain `NOT_COMPUTED` for unsupported request-level judgments.

## Target snapshot analysis

M49 target snapshots provide factual provenance only; no answerability labels are emitted.

## M50 regression snapshot analysis

M50 regression snapshots provide factual provenance only; they cannot prevent or score decisions.

## Shadow utility

`SHADOW_PRIMITIVES_PROVENANCE_USEFUL`.

## Evaluator leakage audit

Core builder leakage: `0`.

## Production dependency audit

No benchmark imports under `app/`; no case/domain branches; no runtime consumers.

## Runtime non-interference

Runtime, generation, prompt, truth, grain, planner, and cost contracts were unchanged.

## Determinism

90/90 individual hashes and the corpus hash replayed identically; source-order independence passed.

## Tests

Synthetic contract tests, replay tests, parity, provenance, leakage, Ruff, format, mypy, and diff checks passed.

## Repository state

Final repository state is clean and synchronized with origin/main.

## Final contract verdict

`TYPED_CONTEXT_AVAILABILITY_SHADOW_SUPPORTED`.

## Utility verdict

`SHADOW_PRIMITIVES_PROVENANCE_USEFUL`.

## M50C readiness

YES — a future M50C may expose factual primitives only; it must not expose answerability or uniqueness decisions.

## M51 readiness

NO.
