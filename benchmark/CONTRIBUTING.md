# Contributing cases

Do not add a question and one gold query. A contribution requires a model-visible authority package, evaluator-only semantic target, two labeled reference implementations, at least two discriminating counterfactual fixtures, at least three case-specific semantic mutants, context/authority audits, provenance, and a human review queue entry.

Keep model-visible records under `cases/` free of target, SQL, fixture, expected-result, and mutant data. Put those records under `ground_truth/`. Use stable authority IDs, explicit temporal/NULL/rounding semantics, and deterministic seeds. Physical column resemblance is never sufficient to authorize a relationship.

Run the full validation, mutation test, leakage audit, repository tests, Ruff, format check, mypy, and `git diff --check` before proposing a case. Never mark a case `HUMAN_ACCEPTED` automatically. If a case is fundamentally ambiguous or non-discriminating, preserve its audit record as rejected and replace it so the pilot gate remains exact.
