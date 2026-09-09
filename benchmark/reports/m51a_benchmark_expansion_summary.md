# M51A Benchmark Expansion Summary

## Historical preservation

The legacy 90-case corpus remains byte-for-byte unchanged. Official M48B.2 metrics remain 78/90 governed and 51/60 answerable runtime TSA.

## Scope and zero-call accounting

This was deterministic benchmark authoring and validation only: provider calls 0, model calls 0, LLM calls 0. No evaluation was run.

## Legacy benchmark inventory

Legacy: 90 cases, 6 domains, distribution preserved. See `m51a_legacy_inventory.json`.

## Expansion objective

Six structurally distinct domains were added with 15 cases each.

## Final 180-case composition

| Corpus | Cases | ANSWERABLE | AUTHORITY_BLOCKED | AMBIGUOUS | POLICY_BLOCKED |
|---|---:|---:|---:|---:|---:|
| Legacy | 90 | 60 | 15 | 9 | 6 |
| Expansion | 90 | 60 | 15 | 9 | 6 |
| Full | 180 | 120 | 30 | 18 | 12 |

## New domain selection

`procurement_ops`, `insurance_claims`, `telecom_billing`, `marketplace_ops`, `workforce_ops`, and `healthcare_billing`; the first three are Type A and the last three Type B.

## New database architecture

Each database has an isolated PostgreSQL schema, 7–9 domain tables, explicit PK/FK structure, an unauthorized external-directory relationship trap, authority metadata, business rules, a fixed UTC clock, deterministic seed data, and a read-only policy.

## Case distribution

The expansion is exactly 60/15/9/6; case suffixes use a deterministic shuffle per domain.

## Case-origin provenance

Legacy and expansion origins are separated in `m51a_case_origin_manifest.json`.

## Model-visible context contracts

New case files expose only the case identity, database, question, task type, context profile, and provenance. Semantic targets, truth, references, fixtures, and mutants remain evaluator-only ground truth.

## Authorized relationships

Relationship authority is explicit in each domain authority package; matching physical columns do not grant authorization.

## Semantic definitions

Each domain has a metric/rule package and case-level typed semantic targets.

## Temporal definitions

All domains use the frozen UTC benchmark clock `2026-06-30T12:00:00Z` and explicit half-open intervals where applicable.

## Policy contracts

All six policy cases request a prohibited mutation against the visible read-only policy.

## ANSWERABLE cases

All 60 new answerable cases have two references and two discriminating counterfactual fixtures.

## AUTHORITY_BLOCKED cases

All 15 require the unowned external relationship and have no authorized alternative path.

## AMBIGUOUS cases

All 9 document two legitimate interpretations in evaluator-only evidence.

## POLICY_BLOCKED cases

All 6 are independently invalid read-only operations.

## Reference SQL witnesses

120 new read-only reference witnesses were executed; RefA and RefB agree across 360 state runs.

## ResultContracts

Typed column-count, ordering, duplicate, null, and numeric comparison metadata is attached to each answerable truth contract.

## BASE states

Each case starts from its deterministic domain seed.

## Counterfactual fixtures

Each answerable case has two counterfactual patch states targeted to its semantics.

## Mutation testing

180 non-equivalent mutants were executed and killed: 180/180.

## Population/group-survival coverage

Coverage includes matching-only and base-entity-preserving group populations, outer-join aggregates, anti-joins, and conditional measures.

## Grain/fanout coverage

Parent/child aggregates and multi-step joins are included across procurement, marketplace, workforce, and healthcare cases.

## NULL/anti-join coverage

The corpus includes `COUNT`/outer-join null behavior, `IS`-style preservation through anti-joins, and `NOT EXISTS` patterns.

## Temporal coverage

June windows, latest rows, fixed benchmark clock, and inclusive date calculations are represented.

## JSON typing coverage

Four new cases use explicit JSON scalar extraction and numeric casts.

## Window/CTE/subquery coverage

Window ranking, derived tables, correlated subqueries, and pre-aggregation patterns are present.

## Mechanism coverage matrix

The deterministic tag audit covers at least 12 distinct mechanism families.

## Query-structure statistics

Reference AST structural statistics are stored in `m51a_query_structure_stats.json`.

## Context-sufficiency audit

60/60 answerable contracts passed the deterministic visible-fact sufficiency audit.

## Authority audit

15/15 authority cases passed the no-authorized-alternative audit.

## Ambiguity audit

9/9 ambiguity cases have two recorded legitimate interpretations.

## Policy audit

6/6 policy cases are invalid under the read-only policy.

## Duplicate-case audit

No duplicate expansion questions were found; domain-specific semantic diversity is recorded in the case and mechanism manifests.

## Leakage audit

Evaluator-only truth and reference fields are absent from model-visible case payloads; leakage count 0.

## Shortcut audit

Task type is not encoded by case suffix; no case-ID or domain runtime logic was added.

## Evaluator compatibility

The existing generic ResultContract comparator and read-only PostgreSQL execution path were used; no `app/` changes were made.

## Reference runtime validation

All new references passed parse, read-only policy, execution, and RefA/RefB equivalence checks.

## Determinism

Case files, truth, fixtures, mutations, and manifests are generated from fixed source definitions and hashes.

## Expansion truth freeze

Expansion truth hash: `7240dedfe1abc9f539fb1a873b2d021a33fa2268c9cc1b18a442f2f15b8646a9`.

## Full 180-case truth freeze

Full truth hash: `b70964d09c64c7296124d1a4b14b079fe931f3b928d830137ee1a6357c926173`.

## Tests

Deterministic authoring validation passed; full repository checks are reported at handoff.

## Repository state

M51A commits are pushed with a clean working tree at the final handoff.

## Final M51A verdict

`BENCHMARK_180_FROZEN`.

## Recommended next milestone

`M51B — Independent 90-Case Expansion Confirmation`.

## M51B readiness

YES; run only after this freeze, using the untouched expansion manifest and retained mainline.
