# M49 — Fresh Residual Failure Forensics

## Verdict

M49_RESIDUAL_FORENSICS_SUPPORTED

Provider calls: 0  
Model calls: 0

## Frozen residual population

| Case | Truth | Model decision | Family | Primary mechanism | Evidence | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| subscription_06 | ANSWERABLE | NEEDS_CLARIFICATION | DECISIONING_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | E2 | CONFIRMED |
| subscription_10 | ANSWERABLE | NEEDS_CLARIFICATION | DECISIONING_ABSTENTION | CALCULATION_DEFINITION_AVAILABILITY_MISREAD | E2 | CONFIRMED |
| warehouse_03 | ANSWERABLE | ANSWER | SQL_SEMANTIC_RESULT | FILTER_SEMANTICS | E3 | CONFIRMED |
| warehouse_07 | ANSWERABLE | BLOCKED_AUTHORITY | DECISIONING_ABSTENTION | TEMPORAL_RULE_AVAILABILITY_MISREAD | E2 | CONFIRMED |
| warehouse_08 | ANSWERABLE | NEEDS_CLARIFICATION | DECISIONING_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | E2 | CONFIRMED |
| warehouse_12 | ANSWERABLE | BLOCKED_AUTHORITY | DECISIONING_ABSTENTION | EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD | E2 | CONFIRMED |
| warehouse_13 | ANSWERABLE | NEEDS_CLARIFICATION | DECISIONING_ABSTENTION | CONTEXT_SUFFICIENCY_MISREAD | E2 | CONFIRMED |
| risk_03 | ANSWERABLE | ANSWER | SQL_SEMANTIC_RESULT | FILTER_SEMANTICS | E3 | CONFIRMED |
| risk_06 | ANSWERABLE | NEEDS_CLARIFICATION | DECISIONING_ABSTENTION | CALCULATION_DEFINITION_AVAILABILITY_MISREAD | E2 | CONFIRMED |
| subscription_18 | AMBIGUOUS | ANSWER | GOVERNANCE_UNDER_ABSTENTION | UNRESOLVED_TIME_SCOPE_ASSUMED | E2 | CONFIRMED |
| warehouse_19 | AMBIGUOUS | ANSWER | GOVERNANCE_UNDER_ABSTENTION | UNRESOLVED_TIME_SCOPE_ASSUMED | E2 | CONFIRMED |
| warehouse_20 | AMBIGUOUS | ANSWER | GOVERNANCE_UNDER_ABSTENTION | UNRESOLVED_STATUS_DEFINITION_ASSUMED | E2 | CONFIRMED |

## Primary mechanism distribution

| Primary mechanism | Count | Share | Domains | E2/E3 | Classification |
| --- | ---: | ---: | --- | ---: | --- |
| CONTEXT_SUFFICIENCY_MISREAD | 3 | 25.0% | subscription_billing, warehouse_logistics | 3 | DOMINANT_SYSTEMATIC |
| CALCULATION_DEFINITION_AVAILABILITY_MISREAD | 2 | 16.7% | risk_operations, subscription_billing | 2 | LOCALIZED_REPEATED |
| FILTER_SEMANTICS | 2 | 16.7% | risk_operations, warehouse_logistics | 2 | LOCALIZED_REPEATED |
| UNRESOLVED_TIME_SCOPE_ASSUMED | 2 | 16.7% | subscription_billing, warehouse_logistics | 2 | LOCALIZED_REPEATED |
| EXPLICIT_RELATIONSHIP_AUTHORITY_MISREAD | 1 | 8.3% | warehouse_logistics | 1 | SINGLETON |
| TEMPORAL_RULE_AVAILABILITY_MISREAD | 1 | 8.3% | warehouse_logistics | 1 | SINGLETON |
| UNRESOLVED_STATUS_DEFINITION_ASSUMED | 1 | 8.3% | warehouse_logistics | 1 | SINGLETON |

## BASE versus counterfactual case sets

BASE correct count: 51
Full counterfactual correct count: 51
Case sets equal: True
Base-only false positives: []
Counterfactual-only transitions: []

## Interpretation

The dominant actionable leaf is CONTEXT_SUFFICIENCY_MISREAD: three answerable refusals occurred despite all required context facts being model-visible, across subscription and warehouse. The two SQL errors share a narrower filter/population shape but are localized. No fix is implemented by M49.

M50_READY: YES

Recommended M50 target: a narrow answerability/abstention intervention for visible-fact sufficiency, preserving non-answerable governance blocks. Technology selection belongs to M50.
