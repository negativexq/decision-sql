# M2.10 — Hard-Slice Pass@K Capability Audit

This is a development-only oracle diagnostic, not production candidate
selection. It used the existing M2 development dataset and generated eight
independent candidates per question. Gold results were used only after all
provider calls for a question completed.

## Frozen run

- Run: `evaluation/results/m210/20260903T014725Z`
- Corrected derived diagnostics: `evaluation/results/m210/20260903T014725Z/recomputed-v2/`
- Source dataset SHA-256: `5cf5a80366debff4efd6e33e5ea6ee1f668aa870f770d2982a1f0396d014cf87`
- Hard-slice manifest: `evaluation/datasets/m210_hard_slice_manifest.json`
- Questions: 10 — top_k (4), ratios (3), window_functions (3)
- Model: `gpt-5.6-luna`; reasoning `none`; temperature omitted/provider default
- Context/generation: `FULL_COMPACT` + `ONE_SHOT`
- Provider calls: 80 attempted, 80 succeeded, 0 failed
- No M2.7 holdout was used; its SHA remained
  `a540298e2776842c6b345f9984b21941c4d0f964447518cbaa12f98f34b8074c`.

The raw `candidates.jsonl` is preserved. A post-run reporting correction uses
the persisted `generated_signature` field when computing structural diversity;
it did not regenerate or alter any SQL.

## Oracle pass@K

| Metric | Correct questions | Rate | Marginal gain |
|---|---:|---:|---:|
| pass@1 | 0/10 | 0.00% | — |
| pass@2 | 0/10 | 0.00% | 0.00 pp |
| pass@4 | 0/10 | 0.00% | 0.00 pp |
| pass@8 | 0/10 | 0.00% | 0.00 pp |

Selection headroom at K=8 is **0.00 percentage points**. The first-correct
histogram is 1:0, 2:0, 3:0, 4:0, 5:0, 6:0, 7:0, 8:0, NONE:10.

This means that, on this hard slice, no independently sampled candidate
produced an execution-equivalent result. It is evidence against a near-term
candidate-selection intervention for these categories, not a claim about
production accuracy outside this diagnostic set.

## Category pass@K

| Category | pass@1 | pass@2 | pass@4 | pass@8 |
|---|---:|---:|---:|---:|
| top_k | 0/4 | 0/4 | 0/4 | 0/4 |
| ratios | 0/3 | 0/3 | 0/3 | 0/3 |
| window_functions | 0/3 | 0/3 | 0/3 | 0/3 |

An optional multi-table-join secondary slice was not run.

## Diversity and failure persistence

Across eight candidates per question:

- average unique raw SQL: 4.3
- average unique normalized SQL: 3.6
- average unique structural signatures: 2.5
- `LOW_DIVERSITY_FAILURE`: 3 questions
- `HIGH_DIVERSITY_NO_CORRECT`: 7 questions
- `HIGH_DIVERSITY_WITH_CORRECT`: 0 questions
- `LOW_DIVERSITY_WITH_CORRECT`: 0 questions

The structural signatures are bounded sqlglot diagnostics and are not a proof
of semantic program diversity. Nonetheless, the run shows both repeated
failure (especially ratio and some window cases) and meaningful structural
variation without a correct candidate. Failure-class transitions occurred for
2 questions; 8 questions retained the same primary failure class across all
eight candidates.

## Failure mechanisms

Primary candidate-level causes (80 candidates; no correct candidates):

| Primary cause | Count | Share |
|---|---:|---:|
| POLICY_REJECTION | 24 | 30.0% |
| OBJECT_SELECTION_ERROR | 24 | 30.0% |
| ORDER_TOPK_ERROR | 19 | 23.75% |
| BUSINESS_SEMANTIC_INFORMATION_MISSING | 8 | 10.0% |
| FILTER_CONSTRUCTION_ERROR | 5 | 6.25% |

Category detail:

- `top_k` (32 candidates): ORDER_TOPK_ERROR 19, FILTER_CONSTRUCTION_ERROR 5,
  BUSINESS_SEMANTIC_INFORMATION_MISSING 8.
- `ratios` (24 candidates): all 24 were rejected by the existing M1
  `FORBIDDEN_FUNCTION` policy for `NULLIF`.
- `window_functions` (24 candidates): OBJECT_SELECTION_ERROR 24.

No parse failures or execution failures occurred. The 24 policy rejects were
classified offline as `SAFE_CAPABILITY_NOT_ALLOWED` because they were parsed
read-only/catalog-safe queries whose only recorded rejection was the function
allowlist. `GENUINELY_UNDESIRABLE` policy rejections: 0. LOWER-related
rejections: 0. This classification did not change M1 or permit execution.

The resulting policy-allowed empirical oracle pass@8 is 0/10. A candidate
rejected by M1 cannot be execution-scored in this run, so this metric does not
claim that any rejected candidate would have been correct if allowed.

## Cost and latency

| Metric | K=1 | K=2 | K=4 | K=8 |
|---|---:|---:|---:|---:|
| Input tokens | 9,164 | 18,328 | 36,656 | 73,312 |
| Output tokens | 693 | 1,347 | 2,769 | 5,556 |
| Sequential latency/question | 1,741 ms | 3,256 ms | 6,288 ms | 12,196 ms |
| Input-token multiplier | 1.0x | 2.0x | 4.0x | 8.0x |

Per provider call: input tokens averaged 916.4 (p50 915, p95 920), output
tokens averaged 69.45 (p50 74, p95 106), and latency averaged 1,524.5 ms
(p50 1,476.3 ms, p95 1,941.8 ms). No versioned pricing snapshot is configured,
so API cost was not computed.

## Safety and experimental controls

- Every generated candidate became `SqlCandidate(source=LLM)`.
- Every executable candidate passed `SqlSafetyService.plan()` and produced a
  `QueryPlan` before `SqlSafetyService.execute()`.
- 56/80 candidates were plan-accepted and executable; 24/80 were policy
  rejected. There were no parse or execution failures.
- All eight candidates for each question had identical question, schema,
  prompt-template, and complete-input hashes.
- Candidates were independent sequential provider calls. No candidate output,
  execution result, or gold result was supplied to a later call.
- No selector, voting, repair, feedback loop, grounding, retrieval, role-aware
  schema, ResultShape, or QueryIntent path was used.
- No hidden chain-of-thought was requested or persisted.
- No new holdout was created or consumed.

## Decision

Final classification: **GENERATOR_CAPABILITY_BOTTLENECK** for this measured
hard slice. The pass@8 curve has no gain over pass@1, and the seven
high-diversity questions still had no correct candidate. This does not support
M2.11 candidate selection as the next experiment.

Exactly one recommended next experiment is **narrow SQL-shape decomposition**
focused on ratios, top-k, and windows. It is recommended because repeated
sampling produced no correct program, while several candidates still varied
structurally; an oracle selector would have no correct candidate to select.
This recommendation is not implemented by M2.10.
