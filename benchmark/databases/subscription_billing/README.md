# Subscription billing

Synthetic SaaS billing operations with accounts, plans, subscriptions, invoices, payments, refunds, credits, usage, entitlements, plan changes, and dunning.

Authorized relationships are declared in `authority/relationships.json`; matching codes and usage-to-plan joins are deliberately not authorized. Active subscriptions use the visible status rule. The benchmark clock is fixed at 2026-06-30 UTC. Monetary measures use PostgreSQL NUMERIC and preserve NULL unless a question declares a default.

Cases cover projection, MRR, invoice populations, payment/refund calculation, current plan, JSON usage, latest dunning, correlated credit exclusion, authority traps, ambiguity, and read-only policy.
