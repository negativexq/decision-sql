# Decision-SQL Bench v0.2.1-dev

## M42 — Luna / reasoning-none / single-call baseline

Model: `gpt-5.6-luna`; provider: `openai-compatible`; reasoning: `none`; temperature: `0.0`; calls/case: `1`

| Metric | Correct / Total | Rate |
|---|---:|---:|
| Governed Task Success | 70 / 90 | 77.8% |
| Answerable Test-Suite Accuracy | 46 / 60 | 76.7% |
| Authority-Blocked Accuracy | 13 / 15 | 86.7% |
| Ambiguity Accuracy | 5 / 9 | 55.6% |
| Policy-Blocked Accuracy | 6 / 6 | 100.0% |

## DEV vs CONFIRMATION

| Metric | DEV | CONFIRMATION | OVERALL |
|---|---:|---:|---:|
| Governed Success | 43/50 (86.0%) | 27/40 (67.5%) | 70/90 (77.8%) |
| Answerable TSA | 29/34 (85.3%) | 17/26 (65.4%) | 46/60 (76.7%) |
| Authority | 7/8 (87.5%) | 6/7 (85.7%) | 13/15 (86.7%) |
| Ambiguity | 4/5 (80.0%) | 1/4 (25.0%) | 5/9 (55.6%) |
| Policy | 3/3 (100.0%) | 3/3 (100.0%) | 6/6 (100.0%) |

## Counterfactual contribution

Base-only answerable accuracy: 46/60
Full-suite answerable accuracy: 46/60
Base-only false positives: 0 (none)

## Failures

| case_id | database | split | gold | decision | category | first fixture |
|---|---|---|---|---|---|---|
| fleet_04 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| fleet_06 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| subscription_04 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_06 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_10 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_03 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| warehouse_04 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_07 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| warehouse_08 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | EXECUTION_FAILURE | base |
| warehouse_09 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | EXECUTION_FAILURE | base |
| warehouse_12 | warehouse_logistics | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| risk_06 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| risk_10 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| risk_11 | risk_operations | CONFIRMATION | ANSWERABLE | BLOCKED_AUTHORITY | WRONG_GOVERNED_DECISION | — |
| subscription_17 | subscription_billing | DEV | AUTHORITY_BLOCKED | ANSWER | WRONG_GOVERNED_DECISION | — |
| subscription_18 | subscription_billing | DEV | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| warehouse_19 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| warehouse_20 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| risk_16 | risk_operations | CONFIRMATION | AUTHORITY_BLOCKED | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| risk_19 | risk_operations | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |

## Reproducibility

Provider calls attempted: 90; genuine responses: 90; semantic retries: 0; repairs: 0; judges/selectors/reflection: 0
Latency: `{"max_ms": 6345.8621660247445, "median_ms": 1636.0703749815002, "min_ms": 1092.1471249312162, "p90_ms": 2238.5128329042345, "successful_responses": 90}`
Token usage: `{"completion_tokens": {"available": 90, "median": 58.5, "total": 6164}, "prompt_tokens": {"available": 90, "median": 8321.5, "total": 677756}, "reasoning_tokens": {"available": 90, "median": 0.0, "total": 0}, "total_tokens": {"available": 90, "median": 8409.0, "total": 683920}}`
Raw responses are immutable evidence; no chain-of-thought was requested or stored.
