# Decision-SQL Bench v0.1 normative specification

## Contract

The benchmark contract is `question + GOVERNED_CONTEXT_V1` → governed behavior. The visible context is the database schema catalog, attribute descriptions, authorized relationships, metrics, business rules, temporal rules, and read-only policy. Semantic targets, reference implementations, counterfactual patches, expected results, and mutants are evaluator-only.

The four and only four pilot behaviors are `ANSWERABLE`, `AUTHORITY_BLOCKED`, `AMBIGUOUS`, and `POLICY_BLOCKED`. Answerable tasks require a read-only answer. Authority-blocked tasks fail closed when the physical database contains a tempting but unauthorized access path. Ambiguous tasks require clarification when two visible-context-consistent interpretations produce different fixture results. Policy-blocked tasks preserve the requested intent and reject writes/DDL rather than silently converting them to a SELECT.

## Truth and equivalence

`semantic_target` is the authoring and audit truth. Reference SQL A and B are implementation witnesses. An answerable case is machine-valid only if both references execute and agree on base data and every counterfactual fixture. Candidate SQL is never compared as a string or required to match an AST. Typed comparison preserves INTEGER/NUMERIC/TEXT/BOOLEAN/DATE/TIMESTAMP/NULL, duplicates, and declared order semantics. Aliases are non-semantic by default; unordered results are duplicate-preserving multisets.

## Projection policy

The final projection is strict when the question defines the requested output. A
candidate must return only the fields requested by the question and visible task
semantics. Descriptive, diagnostic, intermediate, helper, grouping, ordering, and
qualification fields are not automatically part of the answer. A field used only
for filtering, joining, grouping, ordering, qualification, or an intermediate
calculation must be omitted unless the question explicitly requests it. Each
answerable case carries evaluator-only projection metadata with visible provenance;
that metadata is never sent to the model. Aliases are non-semantic. Column order is
semantic when the question names output fields in order, while row order remains a
separate contract and is unordered unless requested.

## Authority and metrics

Every relationship has a stable ID and explicit `authorized` flag. Similar physical columns do not imply a join. Metric metadata records operands, grain, NULL/default behavior, precision, rounding stage, and temporal basis. Filter scope distinguishes row predicates from aggregate filters and HAVING conditions. Every database has a fixed UTC benchmark clock and explicit boundary rules.

## Counterfactuals and mutations

Each answerable case has at least two transactional data patches. They target population, join path, temporal anchor, JSON path, grain, rounding, NULL, ranking, or other real semantic distinctions. Each has at least three case-specific plausible wrong SQL mutants. A case cannot be machine-validated if an accepted mutant survives the complete fixture suite.

## Splits and provenance

The current split is `pilot`, a benchmark-development split, not a holdout. Future `dev`, `confirmation`, and `final` splits must be separated at database level: final databases must be unseen, never random question slices from a shared database. All pilot data is original, deterministic, machine-authored, and not human-reviewed. Future rule-drift support may version authority while keeping schema stable.

## Environment

PostgreSQL 16 is the reference environment, timezone UTC, ISO YMD dates, bounded statement timeout, and read-only candidate transaction. Fixture patches run in a transaction and roll back. The benchmark core does not depend on Decision-SQL runtime Python objects or any provider.
