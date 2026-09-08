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
