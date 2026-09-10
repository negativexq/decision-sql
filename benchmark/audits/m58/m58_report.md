# M58 — Stable Contract Promotion & Residual Regression Forensics

## Scope and verdict

M58 used zero provider/model calls and consumed only immutable M54, M56R, and M57 evidence.
Candidate C wording was unchanged. The evidence supports `PROMOTE_CANDIDATE_C_WITH_LIMITATIONS`:
the two valid runs improve on the M54 floor, preserve authority/policy invariants, and expose
persistent model limitations and run-to-run SQL variability that remain documented below.

## Stable metric policy

| Evidence | Governed | Answerable TSA |
| --- | ---: | ---: |
| M54 baseline | 82/90 | 57/62 |
| M56R single run | 83/90 | 59/62 |
| M57 single run | 84/90 | 60/62 |
| Best observed | 84/90 | 60/62 |
| Reproduced lower envelope / stable claim | **83/90** | **59/62** |

The stable claim uses the preregistered two-run lower envelope, not the best observed run.
Conservative descriptive public arithmetic is `161/180` Governed and `110/122` Answerable TSA.
Best-observed descriptive arithmetic is `162/180` and `111/122`; it is not the stable claim.
The 180-case figures combine separately acquired controlled evidence and are not one same-time
fresh 180-request run.

## Contract promotion

- Contract: `CANDIDATE_C` (`3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`).
- Canonical Candidate C fingerprint equivalence after promotion: `90/90`.
- Production default: `CANDIDATE_C`.
- Candidate C wording and benchmark/runtime semantics were unchanged.
- Reproduced Candidate C category values: authority 13/15, ambiguity 5/7, policy 6/6.

## Cross-run stability

Decision stability was `90/90`.
The only verdict changes were SQL-side: two FAIL→PASS recoveries and one PASS→FAIL.
Among 64 cases answered in both runs, exact SQL matched for 45/64 and deterministic
normalized SQL matched for 51/64; both runs governed-passed for 58/64.

| Cross-run category | Cases |
| --- | ---: |
| Same decision + same SQL + same verdict | 71 |
| Same decision + different SQL + same verdict | 16 |
| Same decision + different SQL + changed verdict | 3 |
| Different decision + changed verdict | 0 |

## Focused case findings

| Case | Stability | Mechanism | M54 → M56R → M57 |
| --- | --- | --- | --- |
| `telecom_10` | `PERSISTENT_FAILURE` | `DECISION_CALIBRATION` | NEEDS_CLARIFICATION → NEEDS_CLARIFICATION → NEEDS_CLARIFICATION |
| `procurement_03` | `PERSISTENT_FAILURE` | `SCHEMA_SEMANTIC_INFERENCE` | ANSWER → ANSWER → ANSWER |
| `procurement_13` | `PERSISTENT_FAILURE` | `AUTHORITY_CLASSIFICATION` | NEEDS_CLARIFICATION → NEEDS_CLARIFICATION → NEEDS_CLARIFICATION |
| `telecom_15` | `PERSISTENT_FAILURE` | `UNAUTHORIZED_RELATION_PROPOSAL` | ANSWER → ANSWER → ANSWER |
| `workforce_03` | `PERSISTENT_REGRESSION` | `DECISION_CALIBRATION` | NEEDS_CLARIFICATION → ANSWER → ANSWER |
| `marketplace_07` | `RUN_UNSTABLE` | `SQL_GENERATION_VARIABILITY` | ANSWER → ANSWER → ANSWER |
| `marketplace_10` | `RECOVERED_UNSTABLE` | `SQL_GENERATION_VARIABILITY` | ANSWER → ANSWER → ANSWER |
| `healthcare_10` | `RECOVERED_UNSTABLE` | `GOVERNED_PREDICATE_OMISSION` | ANSWER → ANSWER → ANSWER |
| `workforce_10` | `RECOVERED_STABLE` | `DECISION_CALIBRATION` | NEEDS_CLARIFICATION → ANSWER → ANSWER |
| `procurement_05` | `RECOVERED_STABLE` | `FANOUT_SEMANTICS` | ANSWER → ANSWER → ANSWER |
| `workforce_02` | `RECOVERED_STABLE` | `TEMPORAL_SEMANTICS` | ANSWER → ANSWER → ANSWER |

### workforce_03

This is a persistent Candidate C regression: M54 passed with `NEEDS_CLARIFICATION`; both
Candidate C runs answered with identical status-based SQL and failed the ambiguity contract.
The observed cause is decision calibration under a still-material semantic ambiguity; prompt
causality is an inference, not a directly observed internal model cause.

### marketplace_07, marketplace_10, and healthcare_10

`marketplace_07` is a PASS→FAIL SQL-generation-variability case. `marketplace_10` and
`healthcare_10` are recovered in M57 but not stable across the two runs. In healthcare_10,
the M57 SQL explicitly includes the required `c.status = 'posted'` predicate; that is a
real observed recovery, not a reproduced stable fix.

### Reproduced fixes

`workforce_10`, `procurement_05`, and `workforce_02` passed in both Candidate C runs.
Their likely associations are answerability calibration, fanout-safe aggregation, and
literal temporal measure translation, respectively.

### Persistent original residuals

`telecom_10`, `procurement_03`, `procurement_13`, and `telecom_15` remained failures in
both valid Candidate C runs. Their mechanisms remain decision calibration, schema semantic
inference, authority classification, and unauthorized relation proposal respectively.

## telecom_15 runtime safety

Historical model decision: `ANSWER`; runtime: `AUTHORITY_REJECTION / UNAUTHORIZED_RELATION`.
EXPLAIN calls: `0`; DB connection calls: `0`;
execution calls: `0`. Model governance remains incorrect; M52.S
relation-level runtime unauthorized-execution safety remains closed.

## Repeated mechanisms and next step

The 11 focused observations reduce to three stable engineering themes: answerability/decision
calibration, governance classification and semantic authority hierarchy, and SQL semantic
stability for grain/predicate-sensitive queries. The persistent regression and three unstable
cases justify `YES_TARGETED_M59`, but M59 should test general principles rather than case-specific
prompt rules. No M59 changes were made here.

## Historical test state

The repository's 11 frozen-expectation failures remain historical and were not rewritten. They
are distinct from M58: this milestone added no model calls, benchmark changes, or runtime semantic changes.

## Final verdict

`PROMOTE_CANDIDATE_C_WITH_LIMITATIONS`
