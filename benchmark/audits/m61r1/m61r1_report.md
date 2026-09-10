# M61R.1 — dbt ACME Applicability & Authority Failure Audit

## Verdict

`M61R1_FORENSICS_COMPLETE`

This is a provider-free forensic audit of the immutable M61R local first-party reproduction. It does not change Candidate C, the ACME adapter, runtime authority, benchmark semantics, or the 220-observation corpus.

## Frozen evidence

- Observations: **220**; correct: **82**; failures: **138**.
- Decisions: `ANSWER` 89, `BLOCKED_AUTHORITY` 118, `NEEDS_CLARIFICATION` 13.
- Conditional correctness among ANSWER observations: **82/89 = 92.13%**; diagnostic only because ANSWER is model-selected.
- Candidate C: `3fb439e68cc158db6da5ea4bea31085d00776eb9573e5d743522922f74fb5587`; canonical builder: `ff695c5a4f9b26ffe9d88f30ee9c4a917c90e670e72b70a7b3739a9ed41b8a23`.
- Provider/model calls during M61R.1: **0**.

## Authority finding

All 118 authority blocks use the frozen `MISSING_AUTHORIZED_RELATIONSHIP` reason. Their deterministic root causes are:

| Root cause | Observations |
| --- | ---: |
| Raw schema requires implicit join inference | 54 |
| Business semantics not governed | 62 |
| Model false authority block | 2 |

The 54 implicit-inference observations are not classified as adapter loss: the needed physical tables/columns exist, but the required join is not an explicit provider-authorized DDL edge. The 62 business-semantic observations require mappings such as `PH`/`AG` role codes that were not in the provider-visible context. Two policy-number premium blocks have fully explicit gold paths and are classified as model false authority blocks.

## Clarification and ANSWER findings

The 13 clarifications split into 10 `FALSE_AMBIGUITY` observations and 3 `MISSING_BUSINESS_SEMANTICS_MASQUERADING_AS_AMBIGUITY` observations. The seven incorrect ANSWER observations are all `WRONG_JOIN_PATH` failures for average policy size; no SQL was generated for the other 131 observations.

## Relational graph and adapter audit

The pinned ACME DDL contains 13 table definitions and 28 scalar FK edges. The first-party data contains 29 CSV relations. The independent FK parser matches the M61R parser; all 18 resolvable DDL edges are exposed by the adapter. Therefore adapter relationship loss is **0**. Five DDL targets have no CSV relation: `claim_offer`, `claim_payment`, `claim_reserve`, `coverage`, and `policy_coverage_part`.

Across the 23 join predicates extracted from the 11 gold queries: 14 are explicitly provider-authorized, 9 require raw-schema/column inference beyond a DDL FK, 0 are DDL-declared edges lost by the adapter, and 0 require a semantic mapping as a join-edge classification. Semantic mappings still affect four questions at the concept level.

The DDL and CSVs share the pinned source commit, but the published `ACME_small.ddl` is structurally partial relative to the 29 source relations. This is recorded as `PARTIAL_VERSION_DRIFT`, not as evidence that two Git revisions were mixed.

## Five 0/20 questions

| Question | Why no SQL | Gold path / context finding | Root cause |
| --- | --- | --- | --- |
| Average claim settlement time | Claim→coverage→policy path ends in a policy join not declared in provider authority; open/close duration has no governed rule. | Partially authorized; raw columns exist. | `C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE` |
| Total premiums by holder | Requires PH role-code semantics and the holder-to-policy path; neither role mapping nor direct gold join is governed. | Partially authorized; PH mapping is gold-only. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |
| Policies sold by agent | Requires AG role-code semantics absent from provider context. | No fully explicit role-semantic contract. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |
| Loss payment + reserve | Loaded loss relations exist, but the DDL points them at absent `claim_payment`/`claim_reserve`; gold relies on shared identifiers to `claim_amount`. | Partially authorized; indirect unresolved-target impact. | `C_RAW_SCHEMA_REQUIRES_IMPLICIT_JOIN_INFERENCE` |
| Policies per holder | Same PH role-code and holder path issue as holder-premium questions. | Partially authorized; PH mapping is gold-only. | `D_BUSINESS_SEMANTICS_NOT_GOVERNED` |

## All-question applicability

The machine-readable question profile records score, decision counts, wrong-answer count, gold-path status, semantic support, and applicability for all 11 questions. The zero/zero count on five questions is predominantly authority/context refusal, not 100 wrong SQL generations.

## Root-cause accounting

| Root-cause family | Failures |
| --- | ---: |
| Missing business semantics | 65 |
| Benchmark expects ungoverned schema inference | 54 |
| False authority block | 2 |
| False ambiguity | 10 |
| Actual SQL semantic error | 7 |
| Adapter defect | 0 |
| True authority absence | 0 |
| Unresolved | 0 |
| **Total** | **138** |

Attribution roll-up: 7 model SQL-generation failures, 12 model decision-calibration failures, 119 governance/context mismatches, 0 adapter defects, and 0 unresolved observations.

## Comparability and next step

`82/220` is a fair Decision-SQL product evaluation because it faithfully replays the frozen production contract. It is not a fair raw Text-to-SQL comparison: most non-answer observations are governed-context refusals, and raw dbt Text-to-SQL has different inferential freedom. The evidence supports `FAIR_PRODUCT_EVALUATION_NOT_RAW_T2SQL_COMPARISON` and recommends `CREATE_DBT_COMPARABLE_EXTERNAL_ARM` as a separate future experiment, without changing Candidate C or production runtime.

## README recommendation

`KEEP_WITH_DIAGNOSTIC_CONTEXT`: the current `82/220` statement is factually correct, but future public wording should link this audit or briefly explain the 89 ANSWER / 131 no-SQL split. No README change was made in M61R.1.

## Integrity

M61R evidence remains immutable. Candidate C, benchmark semantics, adapter, runtime safety, comparator, and the provider corpus were not changed. No provider/model calls occurred.
