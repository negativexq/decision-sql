# M54 — Post-M53 Residual Semantic Forensics and Deterministic Repair

## Historical preservation

The frozen M53.2 expansion result remains 77/90 governed and 52/60 Answerable Runtime TSA. No model or provider call was made during M54, and frozen response bytes were not modified.

## Forensic ledger

The model-blind M54 ledger classifies all thirteen original residuals. Benchmark defects were repaired only where question/context alignment independently required it. Genuine frozen model failures remain attributed to the model or governance decision.

| Case | Primary class | Post-replay status |
| --- | --- | --- |
| `telecom_10` | `MODEL_DECISION` | PRESERVED_MODEL_FAILURE |
| `workforce_10` | `MODEL_DECISION` | PRESERVED_MODEL_FAILURE |
| `procurement_03` | `GOVERNANCE_DECISION` | PRESERVED_GOVERNANCE_FAILURE |
| `procurement_13` | `GOVERNANCE_DECISION` | PRESERVED_GOVERNANCE_FAILURE |
| `telecom_15` | `GOVERNANCE_DECISION` | PRESERVED_MODEL_FAILURE_RUNTIME_BLOCKED |
| `telecom_14` | `BENCHMARK_DEFECT` | REPAIRED_AND_PASSING |
| `marketplace_09` | `BENCHMARK_DEFECT` | REPAIRED_AND_PASSING |
| `procurement_05` | `MODEL_SQL_SEMANTICS` | PRESERVED_MODEL_FAILURE_BENCHMARK_HARDENED |
| `workforce_02` | `MODEL_SQL_SEMANTICS` | PRESERVED_MODEL_FAILURE |
| `healthcare_10` | `MODEL_SQL_SEMANTICS` | PRESERVED_MODEL_FAILURE |
| `marketplace_04` | `RUNTIME_LIMITATION` | RUNTIME_REPAIRED_AND_PASSING |
| `procurement_14` | `BENCHMARK_DEFECT` | REPAIRED_AND_PASSING |
| `insurance_11` | `BENCHMARK_DEFECT` | REPAIRED_AND_PASSING |

## Deterministic repairs

`procurement_14` and `insurance_11` now preserve every base entity and return NULL for eventless children. `telecom_14` and `marketplace_09` are answerable under their visible governed definitions. `procurement_05` references and counterfactual coverage were hardened against duplicate approval fanout. `marketplace_04` now admits only the catalog-governed GMV derived measure; arbitrary parent fanout remains rejected.

## Complete zero-call replay

The exact frozen M51B/M53.1/M53.2 response assignment was replayed against current contracts. Response-map hash: `e3ba0e8b02ea64ce75f34df0bebdd4d54f904e33675273d143d8cc8456e4ed21`. Provider calls: **0**.

## Before / after metrics

| Metric | M53.2 | Post-M54 replay |
| --- | ---: | ---: |
| Governed Task Success | 77/90 = 85.56% | **82/90 = 91.11%** |
| Answerable Runtime TSA | 52/60 = 86.67% | **57/62 = 91.94%** |
| Authority | 13/15 | 13/15 |
| Ambiguity | 6/9 | 6/7 |
| Policy | 6/6 | 6/6 |

The changed denominators reflect the two model-blind ambiguity repairs; the delta is not model improvement. Exact attribution is recorded in `m54_before_after.json`.

## Public combined metrics

The descriptive 180-case aggregate is now **160/180 governed (88.89%)** and **108/122 Answerable Runtime TSA (88.52%)**, with authority **28/30**, ambiguity **12/16**, and policy **12/12**. This is an arithmetic aggregate of preserved legacy evidence and the repaired expansion replay, not a fresh 180-case model run.

## Residual classifications

The unchanged model failures are `telecom_10`, `workforce_10`, `procurement_03`, `procurement_13`, `telecom_15`, `procurement_05`, `workforce_02`, and `healthcare_10`. `marketplace_04` is the deterministic runtime limitation repaired by catalog-derived metric provenance. The repaired benchmark cases are `procurement_14`, `insurance_11`, `telecom_14`, and `marketplace_09`.

## telecom_15 safety replay

The frozen model decision remains `ANSWER` and its SQL hash is unchanged. M52.S rejects its unauthorized relation before any database connection, EXPLAIN, or execution. This preserves model attribution while closing unauthorized execution.

## Determinism and scope

The replay was run twice with canonical-identical results. M54 did not add retries, judges, selectors, model calls, or runtime authority weakening. No unresolved cases remain.

## Verdict

`M54_REPAIRS_AND_REPLAY_COMPLETE`
