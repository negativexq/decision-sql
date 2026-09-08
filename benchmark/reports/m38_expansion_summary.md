# Decision-SQL Bench v0.2.0-dev — M38 Expansion

## Status

The 6-database / 90-case development benchmark passed the offline authoring gates. No model evaluation was run.

## Size

| Task type | Count |
| --- | --- |
| AMBIGUOUS | 9 |
| ANSWERABLE | 60 |
| AUTHORITY_BLOCKED | 15 |
| POLICY_BLOCKED | 6 |

## Quality

- Context sufficient: 60/60 answerable
- Authority sufficient: 60/60 answerable
- Projection sufficient: 60/60 answerable
- Reference pairs: 120/120
- Fixture comparisons: 182/182
- Active mutants: 188; killed 188/188; invalid 0; surviving 0
- Adversarial audit: 90/90 CLEAN
- Request leakage: 0
- Provider calls: 0
- Model baseline: NOT_RUN

## Split

DEV: commerce_ops, fleet_ops, support_ops, subscription_billing. CONFIRMATION: warehouse_logistics, risk_operations. Database-level isolation is preserved.

## Interpretation

This is a development benchmark freeze, not a model result and not a final hidden test set.
