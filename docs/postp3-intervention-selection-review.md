# Post-P3 intervention-selection review

Classification: `POSTP3_DIRECT_GENERATION_RESIDUAL_REBASELINE_SELECTED`.

This is an offline synthesis of the frozen P0–P3 evidence chain. It creates no
new outcome evidence: provider calls, database calls, retrieval reruns,
MEMORY-OFF/ON runs, candidate regeneration, and public benchmark reruns are
all zero.

## Frozen evidence

P2 established 5/105 targets with a `PROVEN_ADAPTABLE` M4 example, 100/105
without one, and historical retrieval of such an example for 4/5 targets. This
is a real coverage observation, not proof that corpus expansion improves
correctness.

P3 compared the same 105 frozen cases under the current MEMORY OFF and current
MEMORY ON conditions. Its matrix was:

| cell | outcome | count |
| --- | --- | ---: |
| A | OFF correct / ON correct | 36 |
| B | OFF correct / ON wrong | 4 |
| C | OFF wrong / ON correct | 9 |
| D | OFF wrong / ON wrong | 56 |

Thus OFF was 40/105 correct and ON was 45/105 correct. The descriptive point
estimate favored ON by 5 cases, or 4.761904761904762 percentage points, but the
precommitted 5.0 percentage-point practical threshold failed and the exact
two-sided discordant-pair p-value was 0.266845703125, above 0.05. The frozen
classification therefore remains
`M111P3_MEMORY_EFFECT_MIXED_OR_INCONCLUSIVE`, not net helpful or net harmful.

The 4 harm discordances had zero bounded memory-motif-transfer findings and
four ON divergences without transfer. The 9 help discordances contained 2
adaptable-memory-supported, 6 non-adaptable-context, and 1 unknown-context
cases. These are pathway observations, not a post-hoc admission rule. P1
adaptability remains distinct from correctness.

The largest unresolved validated population is D: 56/105 (53.333333333333336%)
were wrong in both observed arms. A+D = 92/105 had the same correctness state;
this does not mean memory could not have changed their SQL, only that it did
not cross the correctness boundary in the observed run. No case-level analysis
of the 56 cases was performed here.

## Candidate comparison

The review uses an evidence hierarchy rather than an architecture score:

1. fresh paired causal correctness evidence;
2. fresh mediator/pathway evidence;
3. validated deterministic structural evidence;
4. historical or revalidated associational evidence;
5. architectural plausibility.

| candidate | status | decision boundary |
| --- | --- | --- |
| `DIRECT_GENERATION_RESIDUAL_REBASELINE` | **SELECTED_NEXT_RESEARCH_DIRECTION** | Why does the largest unresolved D population remain wrong in both conditions? |
| `MEMORY_ADMISSION_BOUNDARY_AUDIT` | `REQUIRES_FURTHER_EVIDENCE` | Four harms exist, but nine helps exist, motif transfer is 0/4, and no discriminator separates them. |
| `VERIFIED_DEMONSTRATION_CORPUS_EXPANSION_AUDIT` | `REQUIRES_FURTHER_EVIDENCE` | Sparse coverage is not a proven correctness lever; only 2/9 helps were adaptability-supported. |
| `RETRIEVAL_INTERVENTION_AUDIT` | `REQUIRES_FURTHER_EVIDENCE` | The historical 1/5 miss is too small and no alternative retriever was tested. |
| `MEMORY_REPRESENTATION_OR_ADAPTABILITY_CONTRACT_AUDIT` | `REQUIRES_FURTHER_EVIDENCE` | Six non-adaptable-context helps do not establish a repeated P1 blind spot. |
| `MEMORY_REMOVAL_AUDIT` | `NOT_SUPPORTED` | ON had the higher point estimate and harm gates failed. |
| `NO_INTERVENTION_DIRECTION_YET` | `NOT_SUPPORTED` | A diagnostic residual question is justified even though no implementation is. |

The selected direction outranks the memory alternatives because it addresses
the largest validated unresolved residual, is not answered by P0–P3, and can
produce high-information diagnostic evidence without assuming a desired
technology. It does not prove that direct generation is one mechanism, and it
does not authorize prompt, model, memory, retrieval, corpus, admission, or
runtime changes.

## Next research question

The next milestone is exactly:

`M11.2 — Direct Generation Residual Rebaseline`

Its frozen source population is the P3 `OFF_WRONG_ON_WRONG` cell, N=56. The
review did not perform a case-level failure taxonomy. M11.2 must first freeze
its diagnostic protocol and preserve M1 and the P0 typed evaluator. Its output
would determine whether the 56 cases contain a repeated decision-boundary
family or fragmented residuals. The consumed P3 population is not fresh
validation; any later intervention requires a new independent validation
population.

## Boundaries

Selected non-adaptable context is associational evidence, not causal proof.
Structural transfer is a pathway diagnostic, not the primary causal estimand.
The P3 result is controlled on the frozen population and protocol, but the
provider had no shared deterministic seed and supplied no fingerprint, so each
arm remains a one-shot stochastic realization. The P3 experiment did not test
an alternative retriever, an admission arm, an expanded corpus, or a new
representation.

P0/P1/P2/P3 and historical M10/M11 artifacts remain immutable. M1, M4, the
retriever, prompt/runtime semantics, evaluator, and semantic atoms remain
unchanged. No runtime intervention is selected. Public Defog/BIRD reruns remain
blocked pending a validated generation or architecture intervention.

The complete bounded JSON evidence package and raw SHA-256 identities are in
`evaluation/fixtures/postp3_*.json`.
