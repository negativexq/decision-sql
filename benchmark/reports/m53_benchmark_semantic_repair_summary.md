# M53 Benchmark Semantic Repair Summary

## Historical preservation

The pre-M53 benchmark and scores remain preserved: legacy 78/90 governed and 51/60 Answerable TSA; M51B-R expansion 47/90 and 22/60; combined 125/180 and 73/120. The historical M51B verdict was not rewritten.

## M53 experimental boundary

M53 is a benchmark-quality audit and repair. Provider calls, model calls, Luna calls, and LLM-assisted repairs: **0**. The model was not rerun. Benchmark edits were selected from Pass A model-blind evidence, not from response performance.

## Starting repository state

Expected and observed starting HEAD: `f05333578023a3a50a04e1dfe3f87e4c30f40b29`. `origin/main` matched and the starting tree was clean.

## Frozen benchmark integrity

The legacy 90-case corpus was not edited. The expansion remained 90 cases across six domains with 60 ANSWERABLE, 15 AUTHORITY_BLOCKED, 9 AMBIGUOUS, and 6 POLICY_BLOCKED cases. Original M51A truth hashes remain in their historical manifests.

## Expansion manifest integrity

The pre-M53 expansion manifest hash remained `d48be18622e34c11057a7ad31272cc96b1fbb7d8017b9c0f74953de5a92a2417`. The historical manifest was not overwritten.

## Truth hashes

Pre-M53 expansion: `7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9`; post-M53 expansion: `26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309`. Pre-M53 full truth: `b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173`; post-M53 full truth: `0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b`.

## System, prompt, and runtime preservation

The retained system/model/prompt was not invoked or changed. `app/`, runtime semantics, evaluator semantics, model context schema, and provider schema were not modified. M51B frozen responses were read only after the Pass A freeze.

## Model-blind semantic audit

Pass A audited 90/90 expansion cases (60 answerable, 30 non-answerable) without reading model responses. The ledger was frozen and pushed at `1364bba3f8846df5e5caf9224a6a59cee8aff5af` before Pass B. Defect counts: `{"CONTEXT_SUFFICIENCY_DEFECT": 8, "HIDDEN_NULL_SEMANTICS_DEFECT": 10, "HIDDEN_TEMPORAL_BOUNDARY_DEFECT": 1, "QUESTION_GOLD_POPULATION_AMBIGUITY": 13, "RESULT_ORDER_CONTRACT_DEFECT": 52}`.

All 60 answerable rows include question, visible context, semantic target, ResultContract, RefA/RefB presence, counterfactual presence, projection/order/grain checks, alignment status, and evidence. All 30 non-answerables were checked against their authority, ambiguity, or policy evidence. The model-blind defect IDs are:

- `CONTEXT_SUFFICIENCY_DEFECT` (8 occurrences): healthcare_01, healthcare_07, insurance_05, procurement_12, telecom_06, telecom_09, telecom_12, workforce_04
- `HIDDEN_NULL_SEMANTICS_DEFECT` (10 occurrences): healthcare_07, insurance_14, marketplace_07, marketplace_10, procurement_10, procurement_15, telecom_12, workforce_01, workforce_02, workforce_10
- `HIDDEN_TEMPORAL_BOUNDARY_DEFECT` (1 occurrences): telecom_07
- `QUESTION_GOLD_POPULATION_AMBIGUITY` (13 occurrences): insurance_02, insurance_08, marketplace_05, marketplace_06, marketplace_07, marketplace_08, marketplace_10, procurement_06, telecom_12, workforce_01, workforce_02, workforce_10, workforce_14
- `RESULT_ORDER_CONTRACT_DEFECT` (52 occurrences): healthcare_01, healthcare_02, healthcare_05, healthcare_06, healthcare_07, healthcare_09, healthcare_11, healthcare_12, healthcare_15, insurance_02, insurance_04, insurance_05, insurance_07, insurance_08, insurance_10, insurance_11, insurance_12, insurance_14, marketplace_01, marketplace_04, marketplace_05, marketplace_06, marketplace_07, marketplace_08, marketplace_10, marketplace_14, procurement_02, procurement_05, procurement_06, procurement_08, procurement_10, procurement_11, procurement_12, procurement_14, procurement_15, telecom_02, telecom_05, telecom_06, telecom_07, telecom_08, telecom_09, telecom_10, telecom_11, telecom_12, workforce_01, workforce_02, workforce_04, workforce_06, workforce_08, workforce_10, workforce_12, workforce_14

## Row-order and tie audit

The normative unordered-row rule was applied. 52 non-requested row-order requirements were repaired evaluator-only. Requested top/latest/rank cases were retained where ordering was explicit. No hidden tie-break defect was found; `procurement_14` is not a tie defect. `telecom_07` had an unsupported chronology and received a model-visible question repair.

## Repair classification

52 cases changed. Repairs were limited to objectively demonstrated specification/context defects: row-order contracts were made unordered where not requested; missing visible attributes/rules were exposed; hidden population/NULL assumptions were made explicit; and the unsupported telecom “latest” chronology was replaced with an explicit identifier ordering. No repair was selected from model performance.

| Case | Defects | Applied repair class | Model-visible changed? |
|---|---|---|---:|
| `healthcare_01` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `healthcare_02` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_05` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_06` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_07` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR, R5_QUESTION_REPAIR | YES |
| `healthcare_09` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_11` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_12` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `healthcare_15` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_02` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `insurance_04` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_05` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `insurance_07` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_08` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `insurance_10` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_11` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_12` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `insurance_14` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_01` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `marketplace_04` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `marketplace_05` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_06` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_07` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_08` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_10` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `marketplace_14` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_02` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_05` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_06` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `procurement_08` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_10` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `procurement_11` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_12` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `procurement_14` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `procurement_15` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `telecom_02` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `telecom_05` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `telecom_06` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `telecom_07` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_TEMPORAL_BOUNDARY_DEFECT | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `telecom_08` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `telecom_09` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `telecom_10` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `telecom_11` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `telecom_12` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR, R5_QUESTION_REPAIR | YES |
| `workforce_01` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `workforce_02` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `workforce_04` | RESULT_ORDER_CONTRACT_DEFECT, CONTEXT_SUFFICIENCY_DEFECT | R1_EVALUATOR_ONLY, R4_MODEL_VISIBLE_CONTEXT_REPAIR | YES |
| `workforce_06` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `workforce_08` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `workforce_10` | RESULT_ORDER_CONTRACT_DEFECT, HIDDEN_NULL_SEMANTICS_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |
| `workforce_12` | RESULT_ORDER_CONTRACT_DEFECT | R1_EVALUATOR_ONLY | NO |
| `workforce_14` | RESULT_ORDER_CONTRACT_DEFECT, QUESTION_GOLD_POPULATION_AMBIGUITY | R1_EVALUATOR_ONLY, R5_QUESTION_REPAIR | YES |

The canonical defect ledger covers all 90 expansion cases. Changed semantic files are mapped to ledger rows; no silent repair occurred. No hidden fixture-only, hidden-truth, task-reclassification, or replacement repair was used.

## Response reusability

66/90 frozen responses remain eligible for reuse. 24/90 are invalidated because model-visible question/context changed. No invalidated response was rescored and no response was modified.

Invalidated response IDs: healthcare_01, healthcare_07, insurance_02, insurance_05, insurance_08, insurance_14, marketplace_05, marketplace_06, marketplace_07, marketplace_08, marketplace_10, procurement_06, procurement_10, procurement_12, procurement_15, telecom_06, telecom_07, telecom_09, telecom_12, workforce_01, workforce_02, workforce_04, workforce_10, workforce_14.

## Model-response impact

Pass B inspected the frozen corpus only after the model-blind ledger freeze. Impact is recorded for all 90 responses. 38 cases had no defect, 28 have evaluator-only repairs pending zero-call rescore review, and 24 are explicitly invalidated. The ledger classification was not changed by model behavior.

## Post-repair validation

References: 180/180 state pairs valid (360 reference executions). Counterfactual/reference alignment: PASS. Mutants: 180/180 killed, 0 survived, 0 invalid.

## Specification compliance

Row order is required only where requested; remaining hidden tie, population, temporal, and NULL requirements are all zero. Reference A/B, BASE, and counterfactual validation passed for the repaired expansion. No invalidated response was scored against changed model-visible input.

## High-risk case adjudication

`healthcare_09` received an evaluator-only unordered-row repair and remains reusable. `healthcare_07` received a visible status-context repair; its issue was context sufficiency, not a gold group-survival correction. `procurement_14` has no hidden tie repair. `telecom_07` received an explicit identifier-based question repair because the source schema has no chronology; its response is invalidated.

## Reference and mutation quality

RefA and RefB remained byte/content-identical to their pre-M53 versions. All 180 mutants were re-executed against repaired references and counterfactual states: 180 killed, 0 survived, 0 invalid.

## Historical score status

The historical pre-M53 score remains authoritative for the old benchmark version. Because 24 model-visible inputs changed, the post-M53 official score is `POST_M53_SCORE_PENDING_FRESH_EVALUATION`; no zero-call corrected 90/90 score is published.

## Defect details

Row-order defects were audited against the normative unordered-row rule. No hidden tie-break defect was found; `procurement_14` asks for the latest timestamp and does not require an evaluator-only payload tie-break. `healthcare_07` had a visible status-context gap, not a group-survival truth defect. `healthcare_09` was repaired evaluator-only for row ordering and its frozen response remains reusable. `telecom_07` required a model-visible question repair for unsupported chronology.

## Provenance and hashes

Pre-M53 expansion truth: `7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9`. Post-M53 expansion truth: `26c662d27be3366b59f1e16c9f55e766779c63f4a2f4b137c05d91c62bfca309`. Pre-M53 full truth: `b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173`. Post-M53 full truth: `0ee815d4d46cbb7723e9d7fa07da3628420f6da7181d2b551a282e5a77f4f70b`. No README, prompt, app runtime, or evaluator semantics were changed.

## No benchmark repair driven by model failure

Required answer: **NO**. The model response corpus had no role in deciding whether Pass A defects existed. It was consulted only for impact/reusability after the freeze commit.

## Response reuse rule

Evaluator-only row-order changes preserve reuse eligibility. Question/context repairs change the model-visible hash and invalidate the old response. No invalidated response was imputed.

## Tests

The focused M53 tests passed 6/6. The full repository suite completed with 972 passed, 9 historical frozen-contract failures, and 8 skipped integration tests; new M53 regressions: 0. Ruff check, format check, and mypy report only pre-existing failures outside M53; `git diff --check` passes. The deterministic audit and PostgreSQL reference/mutation validation used zero provider/model calls.

## Determinism

The M53 audit runner is deterministic; post-repair validation is based on fixed case order, fixed fixtures, canonical hashes, and the generic retained comparator. The final audit was regenerated after the repair commit and passed the focused M53 checks.

## Repository state

M53 Pass A was frozen before model-response inspection. Final repository state is reported after the repair and validation commits.

## Final M53 verdict

`BENCHMARK_SEMANTIC_AUDIT_AND_REPAIR_COMPLETE`

## Recommended next milestone

`M53.1 — Fresh Evaluation of Invalidated Cases`

## Post-M53 score status

`POST_M53_SCORE_PENDING_FRESH_EVALUATION`. The next run should evaluate only the 24 invalidated cases first using the exact retained mainline; unaffected response reuse must not be confused with a new official benchmark score.
