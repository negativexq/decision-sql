# Decision-SQL Bench v0.2.1-dev

## M44 — Luna / reasoning-none / single-call baseline

Model: `gpt-5.6-luna`; provider: `openai-compatible`; reasoning: `none`; temperature: `0.0`; calls/case: `1`

| Metric | Correct / Total | Rate |
|---|---:|---:|
| Governed Task Success | 75 / 90 | 83.3% |
| Answerable Test-Suite Accuracy | 48 / 60 | 80.0% |
| Authority-Blocked Accuracy | 14 / 15 | 93.3% |
| Ambiguity Accuracy | 7 / 9 | 77.8% |
| Policy-Blocked Accuracy | 6 / 6 | 100.0% |

## DEV vs CONFIRMATION

| Metric | DEV | CONFIRMATION | OVERALL |
|---|---:|---:|---:|
| Governed Success | 43/50 (86.0%) | 32/40 (80.0%) | 75/90 (83.3%) |
| Answerable TSA | 29/34 (85.3%) | 19/26 (73.1%) | 48/60 (80.0%) |
| Authority | 7/8 (87.5%) | 7/7 (100.0%) | 14/15 (93.3%) |
| Ambiguity | 4/5 (80.0%) | 3/4 (75.0%) | 7/9 (77.8%) |
| Policy | 3/3 (100.0%) | 3/3 (100.0%) | 6/6 (100.0%) |

## Counterfactual contribution

Base-only answerable accuracy: 48/60
Full-suite answerable accuracy: 48/60
Base-only false positives: 0 (none)

## Failures

| case_id | database | split | gold | decision | category | first fixture |
|---|---|---|---|---|---|---|
| fleet_04 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| subscription_06 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_08 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_10 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_14 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_03 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_07 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_08 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_12 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| warehouse_13 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| risk_05 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | EXECUTION_FAILURE | base |
| risk_11 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_17 | subscription_billing | DEV | AUTHORITY_BLOCKED | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_18 | subscription_billing | DEV | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| warehouse_19 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |

## Reproducibility

Provider calls attempted: 90; genuine responses: 90; semantic retries: 0; repairs: 0; judges/selectors/reflection: 0
Latency: `{"max_ms": 2331.4383341930807, "median_ms": 1414.2916459823027, "min_ms": 1046.8307500705123, "p90_ms": 1775.011584162712, "successful_responses": 90}`
Token usage: `{"completion_tokens": {"available": 90, "median": 58.0, "total": 6036}, "prompt_tokens": {"available": 90, "median": 8437.5, "total": 688196}, "reasoning_tokens": {"available": 90, "median": 0.0, "total": 0}, "total_tokens": {"available": 90, "median": 8524.5, "total": 694232}}`
Raw responses are immutable evidence; no chain-of-thought was requested or stored.
