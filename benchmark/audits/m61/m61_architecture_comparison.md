# M61 architecture comparison boundary

M61 was designed to evaluate the frozen Decision-SQL production contract on
the exact first-party dbt ACME question set, using the dbt result comparator.
It did not reach live acquisition because the official dbt Semantic Layer
execution credentials were unavailable.

The architectures remain distinct:

- A raw SQL strategy presents a schema to a model and executes generated SQL.
- A dbt Semantic Layer strategy presents governed metrics/entities through the
  dbt service.
- PyDough, where discussed by external benchmark material, compiles a
  constrained DSL through relationship knowledge into SQL.
- Decision-SQL presents governed context, receives one typed model decision and
  SQL proposal, then applies deterministic SQL policy, request-scoped
  authority, grain validation, `EXPLAIN`, cost policy, and restricted
  read-only execution.

No numerical architecture comparison is claimed by M61.
