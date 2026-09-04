# M5R.1 — Frozen Selective Answering Benchmark & Three-Arm Evaluation

## Status

The benchmark was authored and validated provider-free. The attempted provider
run is `M5R1_RUN_INVALID`: the first R2 response did not conform to the frozen
interpretation DTO. No retry, prompt tuning, SQL generation, or HOLDOUT
consumption was performed.

This document therefore reports a valid dataset and protocol freeze, but no
valid three-arm quality result.

## Benchmark contract

The frozen identity is:

| Field | Value |
| --- | --- |
| corpus ID | `decisionsql-selective-answering` |
| version | `m5r1-selective-answering-v1` |
| total | 150 |
| DEV | 100 |
| HOLDOUT | 50 |
| actions | 50 `ANSWER`, 50 `CLARIFY`, 50 `ABSTAIN` |
| contrast groups | 50 three-question groups |
| full hash | `066d036596e54a9effe319f4cf58761a0da5b6572f20ac82cbaec7b8ae9ec767` |
| DEV hash | `301e001e1207b646d3469aab7607bdbff4db501b1c09df1fb329cecfbd6089c9` |
| HOLDOUT hash | `087cca9818f9654c72bc0abd42c153c41152fba8ca82bec498d5944046a318ad` |

Each case is SQL-free and stores only the question, gold action, reason family,
supported interpretation metadata, server-owned references, adjudication note,
split, and contrast-group identity. Gold metadata is not passed to any
runtime-facing arm. The deterministic builder is finite and produces the same
hashes on repeated builds.

Answer subtypes are ten each: explicit governed metric, governed default,
clear direct simple, clear direct complex/M4-style, and clear filtered
analytics. Clarification subtypes are C1 metric (10), C2 filter target (10),
C3 filter criteria (10), C4 population (10), C5 top-N basis (5), and C6
dimension (5). Abstention subtypes U1 through U5 each contain 10 cases.

The 50 groups are minimal contrasts: an answerable request, an ambiguity case,
and an unavailable-data case use similar domain context while preserving
distinct wording. One answer case is split separately to achieve exactly
100/50; there is no question-ID overlap between splits. The primary cases are
marked `NATURAL`; borderline cases were not included. No exact M4 question was
reused.

Governed-default cases are intentional hard negatives. A surface phrase such
as “refund rate” is `ANSWER` when the active M3 contract provides one canonical
meaning. M4-style multi-join, grouped, threshold, and arithmetic questions are
also `ANSWER` when intent is clear; structural complexity is not ambiguity.

The deferred boundaries remain value grounding (`Florida` versus `FL`, fuzzy
values and entity resolution) and temporal grounding (`recent`, periods,
calendar and timezone semantics). They are not present as primary M5R.1
families.

## Arms and frozen protocol

R0 is a deliberately narrow deterministic baseline using the semantic contract,
schema names, relationship metadata, exact governed aliases, and conservative
lexical cues. It made zero provider calls.

R1 is one provider call per question and returns only:

```json
{"action":"ANSWER|CLARIFY|ABSTAIN","reason_code":"...","resolved_semantic_ids":[]}
```

R2 is one provider call per question and returns only a bounded list of minimal
interpretation DTOs. The local validator checks active semantic IDs, deduplicates
by stable IDs, and applies the frozen policy: zero valid interpretations is
`ABSTAIN`, one is `ANSWER`, and two or more materially different valid
interpretations is `CLARIFY`.

R1 prompt hash:
`db21af3e6065c9610ab4a0a635bb283aea81f8567283f5b3fa7f8f79578cd063`.

R2 prompt hash:
`39ad1d9b9c6afe47016a65ca12328a84c915dda5f4980761dbd41d50ac3f805a`.

R1 DTO version is `m5r1-r1-decision-v1`; R2 DTO version is
`m5r1-r2-interpretation-v1`; validator version is
`m5r1-r2-validator-v1` with hash
`c999626806aa961aaa364b34e9b0e760a50d45283796bb38679c0665962e386e`.

The precommitted provider configuration was `gpt-5.6-luna`, reasoning `none`,
with temperature omitted so the provider default applies. No SQL or
clarification text was requested. No retry, judge, ensemble, self-consistency,
or second classification call is permitted.

## Validation and run outcome

Provider-free checks passed: balanced counts, semantic-reference checks,
minimal-contrast integrity, split disjointness, deterministic R0 behavior,
R2 zero/one/many policy, stable-ID canonical deduplication, unknown-ID
rejection, and SQL-field protocol rejection. `ruff`, `mypy`, focused pytest,
and `git diff --check` passed.

R0 DEV preflight, on 100 cases, produced 73/100 accuracy, 57% answer
coverage, 56.14% ANSWER precision, 90.48% CLARIFY precision, 100% ABSTAIN
precision, and 37.88% unsafe-answer rate. It did not satisfy the frozen DEV
gate.

The real-provider run stopped after one R1 call and one R2 call. R1 returned a
parseable decision. R2 returned an object with `action`, `interpretation`, and
`canonical` fields instead of the frozen `interpretations[]` DTO. This is a
protocol failure, not a quality result. There were no SQL-generation calls,
repair calls, judge calls, embedding calls, or HOLDOUT calls.

Because the run was invalid, R1/R2 DEV metrics, provider latency/token
aggregates, arm selection, and all HOLDOUT metrics are intentionally `NOT
AVAILABLE`. The HOLDOUT remains unconsumed. No prompt or DTO changes were made
after the failure.

## Decision

Dataset classification: `SELECTIVE_ANSWERING_DATASET_FROZEN`, but the required
M5R.1 evaluation classification is `M5R1_RUN_INVALID`.

No arm is accepted, and no `GO_R0`, `GO_R1`, or `GO_R2` decision is justified
from this run. The correct next step is a new explicitly authorized run after
reviewing the DTO protocol; this milestone does not silently turn that review
into prompt tuning.

## Preserved boundaries

M1, M3, M4, and the M5 verifier were not modified. The frozen historical hashes
remain recorded in `evaluation/fixtures/m5r1_manifest.json`. Defog, BIRD, M4
DEV/HOLDOUT, M5.0, and M5.0.1 were not rerun or consumed as generation data.
No production answerability, clarification, abstention, or SQL-generation
runtime was added.
