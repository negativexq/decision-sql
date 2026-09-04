# M9.6 — Evaluator V2 Applicability Strategy Audit

Protocol: `decisionsql-m96-evaluator-applicability-strategy-audit`

Version: `m96-evaluator-applicability-v1`

## Decision

Final classification: `V1_AUTHORITATIVE_V2_SHADOW_JUSTIFIED`

The recommended M10 protocol is:

- `decision-result-evaluator-v1` is authoritative for every executable M10
  case.
- `decision-result-evaluator-v2` is a reference-blind diagnostic shadow when
  a valid deterministic binding exists.
- V2 unavailability is recorded as `V2_BINDING_UNAVAILABLE`; it does not remove
  the case, mark the candidate incorrect, or change the V1 result.
- V1/V2 disagreement is diagnostic only. There is no best-of, evaluator
  shopping, post-hoc case removal, or historical score rewriting.
- M1 rejection, generation failure, provider failure, invalid reference data,
  and evaluator protocol failure remain separate protocol outcomes.

The future score should be named:
`DecisionSQL M10 Clean Rebaseline — Evaluator V1 Authoritative`.

This is a stable full-denominator residual baseline, not a claim that V1 is the
most permissive semantic evaluator. V2 remains useful for contract-aware
diagnostics and later evaluator modernization.

## Frozen evidence

The audit used only repository-owned frozen artifacts. The invalid M9.5R.2
reconnaissance, the M9.5R.2R clean-selection block, and the M9.5R.2S M1
applicability blocker were treated as historical evidence only. No database,
provider, SQL-generation, M10, or public-benchmark work was performed.

Frozen component hashes:

| Component | Hash |
| --- | --- |
| binder-v1 | `aabf907b57f244f793ed61f842a99c0e3240f9907817391e3271a343681d0fdb` |
| binder-v2 | `ff997c50f025643e4a95fd22fd9ec4bbe0bbfb8fc14c50bf8d27ecda3b78fb8e` |
| V2 comparator | `677bf77c4ebd1cdea4b80a7f1125801389905d03e8dbc7e036fc996823ab3f97` |
| Boundary B | `5ae911c84d0e0641493ad7e1cb0babecef9a54056fff54adc8e12d9f1d885f0f` |

M9.4's frozen dual-shadow population had 150 valid cases:

- 96 V1/V2 agreements.
- 54 disagreements.
- 40 `CLEAR_V1_FALSE_NEGATIVE`: V1 rejected declared optional-output
  contract equivalence that V2 accepted.
- 14 `CLEAR_V2_CONTRACT_RECOVERY`: V1 accepted raw-value matches that V2
  rejected because required contract slots were missing.
- 0 true semantic disagreements.
- 0 binding-dependent disagreements.
- 0 unresolved/insufficient-evidence disagreements.

These counts describe disagreement causes; they do not rewrite M7, M8, M9, or
any historical score.

## Strategy matrix

The exact frozen definitions and dimension ratings are in
[`m96_strategy_definitions.json`](../evaluation/fixtures/m96_strategy_definitions.json).

| Strategy | Primary | Diagnostic | Decision |
| --- | --- | --- | --- |
| S1 V2 universal | No | No | Not justified or feasible under current M1/binder boundaries. |
| S2 V1 authoritative + V2 shadow | Yes | Yes | Recommended. |
| S3 predeclared case-level evaluator | No | No | Mixed semantics reduce route and cross-model interpretability. |
| S4 generated-side applicability | No | Yes | Candidate SQL can change the authoritative evaluator; diagnostic only. |
| S5 strict core + contract diagnostic | Yes | Yes | Validates the same practical protocol pattern as S2. |

Hashes:

- Strategy definitions: `d09f85b4ef2dd28ed47422e7bd6d5d8a06fd9f29e092e15da938266a6fc92257`
- Strategy matrix: `b5e6149de311b46ce0ea61d612220aef3ef7e5aa56e54c0fb81e2b59bb23caa3`
- Evidence manifest: `dc6dbadb94bb0086e6fe35087b3e78e90c1665a9ade572bb8f37a32376f1a588`
- M9.4 disagreement audit: `49ed8f0fa30cb55f77696e3bf05e8b4c603e08c66743895539805425687f7ffd`

### Applicability conclusions

Universal V2 is not necessary for M10's residual-diagnosis purpose. Current
evidence does not establish universal real-execution binding, and M9.5R.2S
identified a separate M1 capability boundary: some CTE/derived output aliases
are rejected as `UNKNOWN_COLUMN` under the frozen policy. That is an M1
product-capability question, not evidence that binder-v2 or V2 is unsafe.

Predeclared case-level hybrid evaluation avoids candidate leakage but combines
different evaluator semantics and can couple route metadata to scoring.
Generated-side applicability is deterministic and reference-blind, but two
models can receive different authoritative evaluators for the same case based
on SQL shape. This is a material cross-model fairness risk, so it is not
recommended for primary scoring.

S2/S5 preserve evaluator invariance: the authoritative evaluator is fixed
before candidate output and independent of route, candidate SQL, reference
results, and evaluator outcomes. V2 availability is a diagnostic fact, not a
scoring choice.

## Future M10 protocol requirements

Before provider execution, M10 must freeze its corpus, contracts, references,
failure taxonomy, and evaluator protocol. Every case remains in the denominator.
The protocol must report at least:

- V1 result and primary status.
- V2 applicability and V2 result when available.
- V1/V2 disagreement.
- `V2_BINDING_UNAVAILABLE` separately from generation and M1 failures.
- `M1_REJECTED`, `GENERATION_FAILURE`, `EVALUATOR_PROTOCOL_FAILURE`,
  `REFERENCE_INVALID`, and `RUN_INVALID` separately.

The protocol must never compute `max(V1, V2)`, a union score, or a best-evaluator
score. V1 is authoritative even when V2 would accept a case that V1 rejects.

Readiness terminology proposed by this audit:

- `M10_PRIMARY_EVALUATION_PROTOCOL_READY = true`.
- `M10_V2_SHADOW_PROTOCOL_READY = true`.
- The legacy universal `M10_BINDING_PROTOCOL_READY` is not the primary gate.

No M10 corpus, provider output, M10 hash, or M10 score was created or consumed.
M7 remains `99/150 = 66%`; no adjusted benchmark accuracy was produced.

## Quality and scope

Focused M9.6 tests and DB-free evaluator/binder regressions pass. New M9.6
Ruff and mypy diagnostics are zero. The known frozen binder-v1 static debt is
not modified. M1, M3, M4, routing, prompts, runtime, app, both evaluators,
both binders, and ResultContract V1 are unchanged.

The exact next step is:

`CHECKPOINT M9.5R.1 + M9.6 research state`, then
`M10 — Clean Residual Rebaseline`.

Do not start M10 in M9.6.
