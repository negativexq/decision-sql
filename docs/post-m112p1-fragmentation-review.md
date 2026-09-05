# POST-M11.2P1 Fragmentation Review

This additive review starts from checkpoint
`cc7e9c81a0effa1358f579c8ed557ab4ed4db138` and consumes the frozen M11.2P1
result only. M11.2P1 reported `NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE` for
53 contract-eligible P3 D-cell residual cases: 23 FULL, 30 CORE, with three
P0-quarantined cases excluded.

The review asks why bounded attribution did not prove a material failure family
for 46 of the 53 eligible OFF/ON pairs. It does not assign new generation
failure families, inspect the unresolved cases for hidden causes, or implement
an evaluator. Structural SQL profiles are descriptive evidence, not semantic
failure labels.

## Frozen diagnostic boundary

The 46 neither-arm-proven pairs are the primary review population. The other
seven eligible pairs are controls showing what the frozen M11.2P1 attribution
paths can resolve. P0 tiers and the three quarantined cases remain immutable.

The review distinguishes a failure mechanism from the reason a mechanism could
not be proven. A taxonomy entry is not evidence that the frozen implementation
can adjudicate that family. Zero observed support therefore has authority only
where a positive attribution path existed; otherwise it is limited or not
evaluable evidence.

The pre-exposure phase freezes the blocker taxonomy, structural profile
contract, family reachability audit, controls, and decision thresholds before
case-level exposure. Phase B reads frozen P1 artifacts only and writes additive
diagnostic evidence.

## Required interpretation

`NO_REPEATED_BOUNDED_BOUNDARY_CANDIDATE` is not automatically evidence of
genuine residual fragmentation. It may reflect heterogeneous observable
residuals, attribution-capability limits, both, or insufficient evidence. The
review does not use P1 semantic/adaptability labels as authority; those remain
quarantined pending P1R2.

No provider, database, SQL execution, retrieval rerun, candidate regeneration,
runtime change, historical mutation, README edit, or benchmark rerun is part
of this review. No new evaluator or intervention is selected.

## Artifacts

The implementation is `evaluation/post_m112p1_fragmentation_review.py`.
Run `--prepare` to freeze Phase A and its pre-exposure manifest, then `--analyze`
to produce the additive blocker ledger, structural-profile summaries,
reachability matrix, decision evidence, and final summary. The focused tests
are in `tests/unit/test_post_m112p1_fragmentation_review.py`.

The final scientific result must be selected only by the frozen rules:
`POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT`,
`POSTM112P1_FRAGMENTED_RESIDUAL_CONFIRMED`,
`POSTM112P1_MIXED_FRAGMENTATION_AND_ATTRIBUTION_LIMIT`, or
`POSTM112P1_INSUFFICIENT_EVIDENCE`. Any future diagnostic enhancement must be
independently validated before it can support an architecture intervention.

## Result recorded in this review

The frozen result is `POSTM112P1_ATTRIBUTION_CAPABILITY_BOUNDARY_DOMINANT`:
`STATIC_SEMANTIC_PROOF_LIMIT` is the largest primary blocker at 24/46, with
systematic source evidence that the predecessor exposes bounded feature
detectors but no general semantic-equivalence proof path. This is not a claim
that the 46 cases are correct, fragmented, or free of generation problems.

The remaining primary blockers are compound divergence (15), value-domain
semantics limits (4), schema/relationship semantics limits (2), and the P0
output-mask boundary (1). The structural profile summary has nine distinct
profiles, one singleton, a largest profile of 21, top-three coverage of 32,
and top-five coverage of 39; these are observable profiles, not failure
families. The seven controls resolved through frozen P1 paths: four through M1
metadata and three through the window-specific rule.

The next research step is a checkpoint followed by `POST-M11.2P1 Diagnostic
Capability Design Review`. No evaluator enhancement or intervention is chosen
here. P1/P0 history, M1, references, runtime, and README remain unchanged.
