# M50C.2 — Semantic Submission Contract Feasibility & Deterministic Claim Audit

## Historical preservation

No historical artifacts, README, truth, references, prompt, model context, or runtime behavior changed. Protected-tree mismatches: 0.

## Scope and zero-call accounting

Provider calls: 0. Model calls: 0. Retries: 0. Prompt/context/runtime changes: 0.

## Parent unified-trace evidence

Parent trace contract: `unified-failure-trace-1` (`1560a0118b7eab5fcef76f2db6af9d99438e6ff4f639644387026e531b946fd9`). Decision residuals: 10 (7 false abstentions, 3 false answers).

## Decision residual population

The population was joined only after the Phase A contract freeze. RESULT_BASE residuals remain out of scope.

## Semantic claim contract boundary

Frozen contract: `semantic-submission-claims-1`. Claims are sparse stable-ID assertions only; no answerability, required-fact, uniqueness, or decision oracle is present.

## Negative capabilities

Required-fact identification, required-fact completeness, answerability, uniqueness, should-answer, and correctness remain NOT_COMPUTED/NOT_DERIVABLE.

## Claim families

Audited families: SCHEMA_OBJECT, RELATIONSHIP, SEMANTIC_DEFINITION, TEMPORAL_DEFINITION, STATUS_DEFINITION, POLICY, POPULATION_SEMANTICS.

## Catalog completeness matrix

| Family | Presence | Absence | Authorized | Unauthorized |
|---|---|---|---|---|
| SCHEMA_OBJECT | DETERMINISTIC | DETERMINISTIC | NOT_APPLICABLE | NOT_APPLICABLE |
| RELATIONSHIP | DETERMINISTIC | DETERMINISTIC | DETERMINISTIC | DETERMINISTIC |
| SEMANTIC_DEFINITION | DETERMINISTIC | DETERMINISTIC | NOT_APPLICABLE | NOT_APPLICABLE |
| TEMPORAL_DEFINITION | DETERMINISTIC | DETERMINISTIC | NOT_APPLICABLE | NOT_APPLICABLE |
| STATUS_DEFINITION | DETERMINISTIC | DETERMINISTIC | NOT_APPLICABLE | NOT_APPLICABLE |
| POLICY | DETERMINISTIC | DETERMINISTIC | NOT_APPLICABLE | NOT_APPLICABLE |
| POPULATION_SEMANTICS | NOT_APPLICABLE | NOT_DERIVABLE | NOT_APPLICABLE | NOT_APPLICABLE |

Absence is deterministic only for authoritative complete catalogs. Free-form request mapping remains unsupported.

## Presence/absence verification capability

Exact stable-ID presence and absence are deterministic for schema, relationship, semantic, temporal, status, and policy catalogs. Incomplete catalogs return UNRESOLVED rather than false.

## Relationship authority verification

Explicit AUTHORIZED/UNAUTHORIZED relationship claims are deterministic when the relationship catalog record has a boolean authorization field. SQL join usage is structurally checkable for mapped equality joins.

## Semantic-definition verification

Exact metric/rule IDs are presence/absence checkable. Free-text mapping and arbitrary formula equivalence are not.

## Temporal verification

Temporal rule IDs, rule presence, and rule metadata identity are checkable. Whether a request phrase has one uniquely intended temporal interpretation is not derivable.

## Status-definition verification

Exact status/business-rule IDs are checkable. Mapping a free-form status phrase to a unique rule is not.

## Policy verification

Exact policy IDs and declared policy metadata are checkable. The checker does not determine whether a request requires a policy or override a policy decision.

## SQL-to-claim consistency capability

Relationship usage: FULLY_CHECKABLE. Temporal and semantic basis consistency: PARTIALLY_CHECKABLE. Population mode: NOT_CHECKABLE.

## Synthetic property tests

All expected statuses: True; verified claims: 5; contradiction detections: 4; UNKNOWN preservation: True.

## Mutation tests

6/6 deterministic mutation checks passed; pass rate 100.0%.

## False-abstention feasibility

7/7 false abstentions are fully checkable under the future explicit stable-ID blocker-claim contract. Historical outputs contained no typed blocker claims, so this is feasibility evidence, not retroactive response scoring.

## False-answer feasibility

3/3 false answers are partially checkable. Temporal/status provenance can be checked, but ambiguity and semantic uniqueness require an oracle and remain outside scope.

## Residual feasibility matrix

| Case | First divergence | Mechanism | Claim family | Stable-ID check | SQL check | Feasibility |
|---|---|---|---|---|---|---|
| risk_06 | DECISION_FALSE_ABSTENTION | CALCULATION_DEFINITION_AVAILABILITY_MISREAD | SEMANTIC_DEFINITION | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| subscription_06 | DECISION_FALSE_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | SCHEMA_OBJECT | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| subscription_10 | DECISION_FALSE_ABSTENTION | CALCULATION_DEFINITION_AVAILABILITY_MISREAD | SEMANTIC_DEFINITION | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| subscription_18 | DECISION_FALSE_ANSWER | UNRESOLVED_TIME_SCOPE_ASSUMED | TEMPORAL_DEFINITION | DETERMINISTIC | PARTIAL | PARTIALLY_CHECKABLE |
| warehouse_07 | DECISION_FALSE_ABSTENTION | TEMPORAL_RULE_AVAILABILITY_MISREAD | TEMPORAL_DEFINITION | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| warehouse_08 | DECISION_FALSE_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | SCHEMA_OBJECT | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| warehouse_12 | DECISION_FALSE_ABSTENTION | EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD | RELATIONSHIP | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| warehouse_13 | DECISION_FALSE_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | SCHEMA_OBJECT | DETERMINISTIC | NOT_APPLICABLE | FULLY_CHECKABLE |
| warehouse_19 | DECISION_FALSE_ANSWER | UNRESOLVED_TIME_SCOPE_ASSUMED | TEMPORAL_DEFINITION | DETERMINISTIC | PARTIAL | PARTIALLY_CHECKABLE |
| warehouse_20 | DECISION_FALSE_ANSWER | UNRESOLVED_STATUS_DEFINITION_ASSUMED | STATUS_DEFINITION | DETERMINISTIC | PARTIAL | PARTIALLY_CHECKABLE |

## Mechanism-level coverage

Fully covered mechanisms: CALCULATION_DEFINITION_AVAILABILITY_MISREAD, CONTEXT_SUFFICIENCY_MISREAD, EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD, TEMPORAL_RULE_AVAILABILITY_MISREAD. Fully checkable residuals: 7/10.

## Claim-family coverage

| Claim family | Applicable | Fully checkable | Partial |
|---|---:|---:|---:|
| RELATIONSHIP | 1 | 1 | 0 |
| SCHEMA_OBJECT | 3 | 3 | 0 |
| SEMANTIC_DEFINITION | 2 | 2 | 0 |
| STATUS_DEFINITION | 1 | 0 | 1 |
| TEMPORAL_DEFINITION | 3 | 1 | 2 |

## Oracle-dependency audit

Answerability oracle: 0. Required-fact oracle: 0. Uniqueness oracle: 0. Evaluator truth dependency under app/: 0. Required_context_facts runtime dependency: 0.

## Case/domain independence

App benchmark imports: 0. Case-ID branches: 0. Domain-specific branches: 0. Fully-checkable historical cases span 3 domains.

## Minimal contract

`decision`, existing `sql`, and sparse `claims[]` containing `family`, stable `object_id`, and `assertion`. No free-text reasoning or repeated catalog content.

## Output-size estimate

Approximate sidecar sizes use UTF-8 bytes/4: median 43, p90 89, max 89 tokens. <=300-token gate: True.

## Determinism

Deterministic evaluation: `True`.

## Tests

Ruff, formatting, mypy, four semantic-claim tests, synthetic property checks, mutation checks, UNKNOWN preservation, and oracle-boundary checks passed.

## Repository state

Phase A is frozen and pushed; Phase B artifacts are generated with zero calls. Final push follows artifact validation.

## Final feasibility verdict

`SEMANTIC_SUBMISSION_CONTRACT_FEASIBLE`

## Recommended next milestone

M50C.3 — Semantic Submission V2 Shadow Integration, limited to deterministic stable-ID claim families and shadow-only checks. No model experiment or decision override yet. RESULT_BASE remains a separate Population Semantics Validator feasibility track.

## M51 readiness

`NO`.
