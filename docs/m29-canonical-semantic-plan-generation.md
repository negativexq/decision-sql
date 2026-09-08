# M29 — Canonical Semantic Plan Generation

## Objective

M29 measures whether the existing model can populate the frozen canonical
`SemanticQueryPlan` contract. It uses the same 18-case LiveSQLBench slice and
the same model/provider settings as the comparable direct arm. The semantic
arm makes one provider request per case and does not fall back to raw SQL.

The deterministic semantic engine was checkpointed first at
`c79b5c4bc6e4897a7f0112ce35b9a154152ac26a`. Its provider-free oracle ceiling
remains the architecture reference: 16 cases are representable, 16 compile,
pass semantic validation, M1, and execute; 15 are officially correct and one
has the known empty-reference-result evaluator limitation. Two cases remain
blocked by missing legitimate server-owned relationships.

## Protocol

```text
Question + full M25.2 semantic context + server-owned semantic vocabulary
        ↓ one gpt-5.6-luna request
strict SemanticQueryPlan JSON
        ↓
canonical validation → IR → SQLGlot compiler → semantic validator → M1
```

M26 query-aware grounding and database value probing were disabled. The
provider saw no oracle plan, gold SQL, reference result, evaluator outcome, or
historical generated SQL. The canonical plan has no raw SQL field and cannot
provide join predicates or physical SQL expressions.

## Frozen primary result

All 18 requests were attempted exactly once and all 18 received a provider
response. None of the 18 responses satisfied the canonical strict plan schema;
therefore no case reached canonical IR production, compilation, semantic
validation, M1, execution, or official evaluation. Four response captures were
bounded/truncated at the diagnostic capture limit, so their JSON validity is
reported as unverified; the provider request and response hash remain recorded.

The observed failure is `PLAN_SCHEMA_FAILURE`, not a deterministic engine
failure. Visible invalid responses used a different generic semantic JSON shape
(for example alternate population/output keys and untyped expression fields),
which is rejected fail-closed by the canonical contract. No retry or prompt
adjustment was made after the primary run.

The persisted safe result is
`evaluation/fixtures/m29_livesqlbench_semantic_plan_result.json`. Detailed
question, context, and response evidence remains under the ignored protected
results directory.

## Paired control

The control is the persisted M25.2 full-semantic-context DIRECT arm: 6/18
officially correct. For the 15 binary-comparable cases (excluding two metadata
blocked cases and one known evaluator limitation), the paired counts are:

```text
both correct: 0
DIRECT only:  6
SEMANTIC only: 0
both wrong:   9
```

The exact two-sided McNemar p-value is 0.03125 for this small diagnostic slice.
It is reported descriptively and is not treated as a general performance claim.

## Contract acquisition versus semantic correctness

Contract acquisition was 0/18 canonical-valid plans. Consequently, semantic
plan exact-match and component-level semantic accuracy have no applicable
denominator in this run; they are not scored as semantic reasoning errors.
End-to-end semantic-arm correctness is likewise 0/18 reached cases, with all
18 stopping at plan schema validation.

## Reproducibility and claim boundary

The experiment manifest records the checkpoint, frozen case order, model
configuration, prompt/schema hashes, Base-Lite image digest, and one-call
budget. The primary run is frozen. This result measures the current model's
ability to acquire the canonical semantic contract; it does not measure the
deterministic engine ceiling again and it does not justify changing the model,
prompt, retrieval, or runtime architecture within M29.
