# M11.0 — Verified Query Memory Selectivity Intervention Design Audit

Status: `M110_DESIGN_AUDIT_BLOCKED`

This offline audit used only the consumed, valid M10.4S development evidence.
It did not call a provider or database, rerun retrieval, create a corpus,
modify runtime semantics, or implement M11.

## Frozen question and signal boundary

M10.4S identified `MEMORY_RETRIEVAL_SELECTIVITY_FAILURE` as the sole mechanism
passing the precommitted M11 gate: 61 E2-attributed failures, split 31 in
DIAG_A and 30 in DIAG_B. This is an attribution of observed incompatible
selected context. It is not a claim that memory-OFF would recover 61 cases;
no memory-OFF counterfactual was run.

The frozen M4 implementation is `app/memory/retrieval.py`:
`VerifiedQueryRetriever.retrieve`. Its ranking score is the existing weighted
similarity:

`final_score = 0.75 * lexical_score + 0.25 * schema_score + 0.0 * structural_bonus`

Higher scores rank first, with `example_id` as the deterministic tie-break.
The frozen runtime provenance preserves ranked `final_score`, rank, selected
IDs, selected count, and related identities. It does not preserve separate
lexical, schema, or structural components. The preserved score is a retrieval
similarity score, not a calibrated probability.

Reference-blind policy features frozen before label analysis were `s1`, `s2`,
`s3`, selected count, minimum selected score, and the derived `s1 - s2` gap.
Gold, reference SQL, correctness, mechanism, family, route, and partition are
labels or analysis metadata only and cannot enter a policy decision.

## Precommitted policy families

The audit froze exactly four families before outcome-separated score analysis:

- `P0_CURRENT`: admit the currently selected context.
- `P1_TOP1_MIN_SCORE`: admit when `s1 >= tau`.
- `P2_MIN_SELECTED_SCORE`: admit when the selected-set minimum is at least `tau`.
- `P3_TOP1_PLUS_MARGIN`: admit when `s1 >= tau` and `s1 - s2 >= delta`.

Thresholds would have been selected independently in DIAG_A→DIAG_B and
DIAG_B→DIAG_A using only training-fold values and adjacent midpoints. The
held-fold gates require at least 50% primary block recall, 80% compatible
retention, 70% blocked-set precision, and 80% correct-compatible retention
when that safeguard has at least five controls. No numeric threshold was
frozen.

## Control sufficiency result

The frozen M10.4S causal artifact contains 105 memory-use cases. It establishes
61 primary selectivity positives and 21 separate overtransfer cases. It
establishes **zero** deterministically compatible selected-memory controls.
The remaining 23 memory-use cases are `COMPATIBILITY_UNKNOWN`; correctness
alone was not promoted to compatibility evidence.

Consequently, compatible retention and blocked-set precision have no valid
denominator. P1/P2/P3 are `NOT_EVALUABLE`, and P0 is retained as a reference
only. No score policy can be selected under the frozen cross-fit protocol.

The correct result is therefore `M110_DESIGN_AUDIT_BLOCKED`, not a claim that
score/rank signals are sufficient or insufficient. This audit also does not
design a new server-owned compatibility signal. That question remains open
until a valid deterministic compatible-control evidence basis exists.

## Scientific boundary

M10.4S remains valid fresh development evidence for selecting the memory
selectivity target. M11.0 is development analysis, not independent validation.
It performed no downstream accuracy or provider counterfactual, made no
recovery claim, and made no M11 implementation change. The M10.4S corpus is
consumed evidence and cannot become M11 validation data. Public benchmarks
remain deferred.

Artifacts:

- `m110_feature_manifest.json` and `m110_policy_family_manifest.json` freeze
  the reference-blind signal and candidate-family boundaries.
- `m110_compatibility_labels.json` records the frozen label limitation.
- `m110_crossfit_protocol.json` records the untouched-fold procedure.
- `m110_policy_results.json`, `m110_design_decision.json`, and
  `m110_evidence_manifest.json` record the blocked decision and provenance.
