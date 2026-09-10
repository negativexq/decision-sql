# M59 intervention basis

This document was authored before M59 candidate calls. It extracts the frozen M58 evidence and defines the general surfaces tested by M59; it does not inspect M59 outcomes.

| M58 evidence | Mechanism | General intervention surface | Prompt causality |
| --- | --- | --- | --- |
| `telecom_10`, `workforce_03` | `DECISION_CALIBRATION` | Symmetric answerability test: identify a material unresolved variable before clarification, without erasing real ambiguity. | MEDIUM |
| `procurement_03` | `SCHEMA_SEMANTIC_INFERENCE` | Treat schema affordance as data shape, not business-rule authorization. | HIGH |
| `procurement_13` | `AUTHORITY_CLASSIFICATION` | Distinguish clear-but-unauthorized intent from unclear meaning. | HIGH |
| `telecom_15` | `UNAUTHORIZED_RELATION_PROPOSAL` | Require every SQL dependency to be traceable to the request-scoped authorized context. | HIGH |
| `procurement_05`, `marketplace_07`, `marketplace_10`, `healthcare_10` | `FANOUT_SEMANTICS`, `SQL_GENERATION_VARIABILITY`, `GOVERNED_PREDICATE_OMISSION` | Add a silent semantic consistency pass covering population, measure, grain, predicates, authority, projection, and temporal behavior. | MEDIUM |

## Candidate family

`CONTROL_C` is byte-for-byte Candidate C. `CANDIDATE_D` adds only the general decision/evidence hierarchy. `CANDIDATE_E` adds the same hierarchy plus a compact silent semantic consistency checklist. No case names, benchmark SQL, or evaluator truth are included.

The selection set and criteria are frozen in `m59_experiment_plan.json` before any provider call.
