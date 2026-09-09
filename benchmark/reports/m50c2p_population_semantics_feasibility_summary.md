# M50C.2P Population Semantics Feasibility

## Historical preservation

Starting HEAD `38d8d8cc0f4ccd6695f3fa9e9b97a40da4450c50`; parent contracts, truth, references, fixtures, README, prompts, and runtime semantics unchanged.

## Scope and zero-call accounting

Provider calls: **0**. Model calls: **0**. Database access by core analyzer: **0**. No prompt, model-context, output-schema, or runtime behavior change.

## Parent evidence

M50C.2P preserves M50C.2's `POPULATION_SEMANTICS` catalog limitation: intended population is not inferred. It analyzes only actual SQL structure.

## Problem boundary

`PopulationIntent` and `PopulationBehavior` remain separate. A future claim may be checked against observed structure, but the checker does not compute answerability, correctness, or uniqueness.

## Population intent vs population behavior

Intent modes are `MATCHING_ONLY`, `PRESERVE_BASE_ENTITIES`, and `UNSPECIFIED`. Behavior reports preservation/qualification/conditionality or `UNRESOLVED`.

## Negative capabilities

Question text, truth, references, database contents, and evaluator labels are not inputs to `app/semantics/population.py`. Intended population, answerability, semantic uniqueness, and correctness are not computed.

## Phase A reference-blind contract

Contract `population-behavior-1` was frozen before Phase B. Supported structural rules include joins, group carriers, aggregate-local predicates, row predicates, conservative NULL rejection, and positive COUNT/HAVING survival.

## Supported SQL patterns

INNER/LEFT joins, GROUP BY, aggregate FILTER, `SUM(CASE)`, WHERE, JOIN ON, simple HAVING COUNT, and simple structural aggregate/group analysis.

## Unsupported SQL patterns

Arbitrary CTE/subquery propagation, UNION population propagation, complete RIGHT/FULL preservation proof, complex NULL logic, and all natural-language intent inference remain unresolved/fail-closed.

## Null-rejection analysis

Conservative safe subset passed; OR, IS NULL, COALESCE, CASE, custom functions, and unknown operators remain unresolved rather than guessed.

## Predicate-scope analysis

`WHERE` is row-population, `JOIN ON` is join-local, aggregate FILTER/CASE is aggregate-local, and COUNT/HAVING is group-survival evidence.

## Group-survival analysis

Base-driven outer joins preserve groups; nullable-side WHERE predicates defeat that preservation; aggregate-local qualification can leave groups supported by nonqualifying rows.

## Join-preservation analysis

LEFT/INNER topology is classified generically. No catalog or question intent is required.

## Subquery/CTE handling

Simple top-level structure is retained; arbitrary propagation is explicitly `UNRESOLVED` or marked for review.

## Synthetic property tests

14/14 predeclared structural cases passed.

## Mutation tests

4/5 topology/placement mutations changed behavior as expected.

## Alpha-renaming invariance

`PASS`.

## State independence

The core analyzer accepts only SQL and performs no database access; behavior is therefore state-independent.

## M48B.2 ANSWER population

Analyzed **56/56** frozen ANSWER SQL submissions, with no truth or reference inputs in the analyzer.

## Population behavior distribution

{"CONDITIONALLY_PRESERVES_BASE_GROUPS": 3, "NOT_APPLICABLE": 25, "PRESERVES_BASE_GROUPS": 5, "REQUIRES_QUALIFYING_ROWS": 21, "UNRESOLVED": 2}; useful non-UNRESOLVED coverage is **54/56 (96.4%)**.

## Correct-answer negative control

Correct ANSWER population produced **0** evaluator-known potential structural contradictions under post-freeze intent comparison.

## Historical RESULT_BASE analysis

There are **2** historical cases. Both are structurally analyzable at the behavior level, but neither is fully checkable solely from a compact declared intent without identifying intended qualifying-source semantics.

## Reference witness structural comparison

[{"agreement": false, "case_id": "risk_03", "model": "CONDITIONALLY_PRESERVES_BASE_GROUPS", "reference_a": "REQUIRES_QUALIFYING_ROWS", "reference_b": "UNRESOLVED"}, {"agreement": false, "case_id": "warehouse_03", "model": "REQUIRES_QUALIFYING_ROWS", "reference_a": "CONDITIONALLY_PRESERVES_BASE_GROUPS", "reference_b": "UNRESOLVED"}]

## Future population-intent claim feasibility

Minimal candidate is `{"mode": "MATCHING_ONLY|PRESERVE_BASE_ENTITIES", "entity_id": optional}`. It is compact and requires no input-context growth, but it cannot identify intended qualifying sources by itself.

## Output-size estimate

Estimated synthetic sidecar footprint: median 13, p90 17, max 17 token proxy; both <=50/80 gates.

## Validator feasibility

`POPULATION_INTENT_VALIDATOR_PARTIAL`: declared intent can support future deterministic checks, but the two historical failures are not both fully decidable without semantic population identification.

## Normalizer feasibility

`POPULATION_NORMALIZER_NOT_JUSTIFIED`: detection does not prove a semantics-preserving rewrite across duplicates, NULLs, grain, authorization, projection, and counterfactual states.

## Oracle-dependency audit

App oracle dependency: **0**. Case branches: **0**. Domain branches: **0**. Benchmark-question keyword rules: **0**.

## Case/domain independence

Alpha-renaming and literal invariance passed; generic analyzer has no benchmark imports or domain/case branches.

## Determinism

Repeated structural hashes and synthetic replay passed.

## Tests

Relevant analyzer tests, ruff, formatting, and mypy are run before final commit.

## Repository state

Historical benchmark and README unchanged; no runtime integration, SQL repair, model-facing claim, or A/B experiment.

## Population analyzer verdict

`POPULATION_BEHAVIOR_ANALYZER_PARTIAL`.

## Population validator candidacy

`POPULATION_INTENT_VALIDATOR_PARTIAL`.

## Population normalizer candidacy

`POPULATION_NORMALIZER_NOT_JUSTIFIED`.

## Recommended next milestone

Keep the analyzer as shadow telemetry. If pursued, first freeze a minimal `PopulationIntent` sidecar in shadow only; do not add runtime rejection or SQL repair.

## M51 readiness

**NO**. M50C.2P runs no experiment and retains no model-facing intervention.
