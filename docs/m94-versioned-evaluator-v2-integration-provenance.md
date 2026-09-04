# M9.4 — Versioned Evaluator V2 Integration & Provenance

## Executive conclusion

M9.4 integrated the independently validated M9.2 result-equivalence candidate
behind an explicit evaluation-only version selector. The integration preserves
the historical evaluator as V1, defaults to V1, requires an explicit valid
contract for V2, and records bounded provenance for every new result.

Classification: `VERSIONED_EVALUATOR_V2_INTEGRATION_ACCEPTED`.

The next milestone is exactly **M10 — Clean Residual Rebaseline**. A checkpoint
before M10 is recommended so the rebaseline has a stable provenance anchor.
M10 has not started.

## Starting checkpoint and frozen evidence

M9.4 started at `f6d00ffeb227e50dab55588c517b5f54a07cac14`, with `main` aligned
to `origin/main` and a clean working tree. The frozen V2 comparator remained:

| Item | Value |
| --- | --- |
| Contract | `result-equivalence-contract-v1` |
| Comparator | `m92-result-equivalence-candidate-v1` |
| Comparator source SHA-256 | `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97` |
| Provenance schema | `evaluation-provenance-v1` |

The source hash was identical before and after integration. M7, M8, M9,
M9.1, M9.2, M9.3, M3, and M4 frozen hashes were verified unchanged. No
historical artifacts were rewritten and no historical score was recalculated.

## Existing evaluator architecture

Before M9.4, the legacy V1 path was `evaluation.metrics.assess_query_results`,
called directly by historical runners such as `evaluation.runner`, `run_m7`,
and the M4/M3 evaluation paths. It compares result columns by ordinal
position, preserves duplicate multiplicity, optionally ignores row order, and
uses the existing NULL and numeric-tolerance behavior.

The M9.2 candidate was separate in
`evaluation.result_equivalence_contract.compare_contract`. It consumes
evaluation-owned `BoundResult` objects with explicit semantic bindings and a
`ResultEquivalenceContract`. M9.4 wraps both boundaries; it does not replace
either implementation.

## Version selection

The new evaluation-only API is `EvaluationComparisonRequest` and
`evaluate()` in `evaluation.versioned_result_evaluator`.

```text
                    Evaluation Request
                           |
                    Evaluator Mode
                 /        |          \
               V1       V2 explicit   DUAL_SHADOW
               |           |              |
        Legacy evaluator   |       V1 authoritative
                           |       V2 diagnostic shadow
                     Contract required
                           |
                   Frozen V2 comparator
                           |
                  Versioned provenance
```

Supported modes are `V1`, `V2`, and `DUAL_SHADOW`. Omitting a mode selects V1.
Explicit V2 is authoritative for that request. Dual mode always makes V1
authoritative and preserves the V2 result separately; it never chooses the
better result.

## Fail-closed contract behavior

V2 and DUAL_SHADOW require an explicit valid
`ResultEquivalenceContract` with the frozen semantic bindings. Missing or
invalid contracts raise typed `EvaluationConfigurationError` values with
`EVALUATOR_CONTRACT_REQUIRED` or `EVALUATOR_CONTRACT_INVALID`. There is no V1
fallback, contract inference, value matching, SQL inspection, or LLM call.

When V1 and V2 disagree, both outcomes remain available. The bounded dual
status distinguishes `V1_ONLY_EQUIVALENT` and `V2_ONLY_EQUIVALENT` from the two
agreement states. This is diagnostic data, not score aggregation.

## Provenance

Each new result records `EvaluatorProvenance` with:

- evaluator ID/version and selected mode;
- whether that result is authoritative;
- provenance schema and result-semantics version;
- contract ID, version, and deterministic contract-instance hash for V2;
- frozen comparator ID, version, and comparator-source hash for V2;
- semantic-binding version;
- bounded reference-variant and optional-slot counts;
- matched reference-variant index when one matches.

V1 deliberately has null V2-only fields. Provenance does not contain raw SQL,
raw rows, questions, prompts, credentials, authorization headers, or API keys.
Contract hashes canonicalize non-semantic serialization order while preserving
semantic policy fields. Provenance semantic fields are deterministic; run IDs
and timestamps are not part of those hashes.

## Integration parity

The frozen V2 comparator and the integrated V2 adapter matched exactly on:

- all 143 M9.2 regression cases;
- all 160 M9.3 independent-validation cases.

The V1 adapter matched the existing V1 evaluator on 100 representative frozen
cases. Default-mode parity matched explicit V1. Dual mode preserved explicit V1
as authoritative and explicit V2 as the diagnostic result, including known
V1/V2 disagreement cases.

The M9.2 direct regression remained
`RESULT_EQUIVALENCE_CONTRACT_ACCEPTED`, and the M9.3 direct validation remained
`RESULT_EQUIVALENCE_V2_INDEPENDENTLY_VALIDATED`.

## Compatibility and claim boundary

Historical M7/M8/M9 artifacts remain unchanged and are not retrofitted with
new metadata. Any future artifact produced through this boundary declares its
evaluator semantics explicitly. M10 may use benchmark-owned contracts and
explicitly select V2; M9.4 does not solve automatic question-to-contract
inference.

This milestone created no new benchmark accuracy. M7 remains 99/150 = 66%, M8
remains 105/150 observed counterfactual, and M9 remains 75/120 reconstructed
direct outputs. Defog and BIRD were not rerun.

M2.8 remains rejected, M5 remains closed, and M6 remains parked. No production
runtime, M1, M3, M4, routing, prompt, or evaluator V1 behavior changed.

## Validation and next step

M9.4 was provider-free and database-free. It added only evaluation-layer
integration, tests, and this documentation. The acceptance gates covered
source-hash locking, V1/V2 parity, default V1 behavior, fail-closed contracts,
dual-shadow authority, no-best-of behavior, deterministic contract hashing,
bounded provenance, and preservation of the frozen M9.2/M9.3 results.

The next step is **M10 — Clean Residual Rebaseline**, a new evaluation identity
using explicit V2 provenance. It must not be presented as a corrected M7 run.
