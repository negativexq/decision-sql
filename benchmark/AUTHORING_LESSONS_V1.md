# Decision-SQL benchmark authoring lessons v1

These lessons are the quality template for future benchmark development.

- The semantic target is the truth contract; reference SQL is an independent implementation, not the definition of meaning.
- Use two independently written references and require execution agreement.
- Use counterfactual fixtures that make each important semantic distinction observable.
- Require an active mutation kill gate, including projection mutants where exact output shape is semantic.
- Put all required schema, relationship, business-rule, and temporal evidence in visible governed authority.
- State population, filter scope, temporal boundaries, latest-row rules, NULL behavior, and precision explicitly.
- Make the final projection contract public; never rely on a hidden exact-output convention.
- Keep authority traps, ambiguity cases, and policy cases separate from answerable SQL quality.
- Machine validation is not human acceptance; retain adversarial review evidence and unresolved-review labels.
- Do not repair benchmark truth from provider output. Resolve question, authority, and references independently first.
- Aliases, column order, and row order are separate semantics and must be documented independently.
- Future cases may reuse mechanism families, but must not be paraphrases or cosmetic copies of pilot questions.

The current pilot is a development set, not a final holdout. Future confirmation and
final partitions must be isolated at the database level.
## M38 expansion lessons

- Keep new domains in a separate versioned authoring path so the repaired pilot remains reproducible.
- Make development/confirmation boundaries database-level, never random case-level splits.
- Require at least two independent ambiguity interpretations and a fixture that makes their results differ.
- Require every new answerable case to carry explicit final projection, population, grain, temporal, NULL, and precision evidence.
- Treat counterfactuals as minimal discriminating fixtures and record their semantic purpose.
- Keep mutation families executable and require a full base-plus-fixture kill gate before freezing.
- Do not use model outputs to author schemas, questions, references, fixtures, or mutants.

## M40 integrity-repair lessons

- Treat the serialized governed context, rather than the physical schema file, as the model's actual evidence boundary.
- Validate source-column, JSON-path, and latest-row tie-break visibility against both independent references.
- Parse required-context fact identifiers structurally and fail closed on malformed, unknown, unauthorized, or wrong-database facts.
- Keep semantic ordering separate from deterministic reference ordering; an `ORDER BY` in a reference is not by itself a public row-order contract.
- Make population choices explicit in natural language; do not hide all-anchor versus matching-only behavior in the evaluator.
- Preserve historical model evidence byte-for-byte and create a new benchmark version whenever contract repairs change request bytes.
