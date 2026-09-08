# Decision-SQL Bench v0.2.1-dev

## M45 — Luna / reasoning-none / single-call baseline

Model: `gpt-5.6-luna`; provider: `openai-compatible`; reasoning: `none`; temperature: `0.0`; calls/case: `1`

| Metric | Correct / Total | Rate |
|---|---:|---:|
| Governed Task Success | 77 / 90 | 85.6% |
| Answerable Test-Suite Accuracy | 49 / 60 | 81.7% |
| Authority-Blocked Accuracy | 15 / 15 | 100.0% |
| Ambiguity Accuracy | 7 / 9 | 77.8% |
| Policy-Blocked Accuracy | 6 / 6 | 100.0% |

## DEV vs CONFIRMATION

| Metric | DEV | CONFIRMATION | OVERALL |
|---|---:|---:|---:|
| Governed Success | 44/50 (88.0%) | 33/40 (82.5%) | 77/90 (85.6%) |
| Answerable TSA | 29/34 (85.3%) | 20/26 (76.9%) | 49/60 (81.7%) |
| Authority | 8/8 (100.0%) | 7/7 (100.0%) | 15/15 (100.0%) |
| Ambiguity | 4/5 (80.0%) | 3/4 (75.0%) | 7/9 (77.8%) |
| Policy | 3/3 (100.0%) | 3/3 (100.0%) | 6/6 (100.0%) |

## Counterfactual contribution

Base-only answerable accuracy: 49/60
Full-suite answerable accuracy: 49/60
Base-only false positives: 0 (none)

## Failures

| case_id | database | split | gold | decision | category | first fixture |
|---|---|---|---|---|---|---|
| commerce_06 | commerce_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| fleet_04 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| fleet_06 | fleet_ops | DEV | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| subscription_04 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_10 | subscription_billing | DEV | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_03 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_07 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| warehouse_08 | warehouse_logistics | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| warehouse_13 | warehouse_logistics | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| risk_03 | risk_operations | CONFIRMATION | ANSWERABLE | ANSWER | RESULT_MISMATCH | base |
| risk_06 | risk_operations | CONFIRMATION | ANSWERABLE | NEEDS_CLARIFICATION | WRONG_GOVERNED_DECISION | — |
| subscription_18 | subscription_billing | DEV | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |
| warehouse_19 | warehouse_logistics | CONFIRMATION | AMBIGUOUS | ANSWER | WRONG_GOVERNED_DECISION | — |

## Reproducibility

Provider calls attempted: 90; genuine responses: 90; semantic retries: 0; repairs: 0; judges/selectors/reflection: 0
Latency: `{"max_ms": 3911.306499969214, "median_ms": 1745.8181250840425, "min_ms": 1266.647709067911, "p90_ms": 2107.498083030805, "successful_responses": 90}`
Token usage: `{"completion_tokens": {"available": 90, "median": 62.5, "total": 6160}, "prompt_tokens": {"available": 90, "median": 8525.5, "total": 696116}, "reasoning_tokens": {"available": 90, "median": 0.0, "total": 0}, "total_tokens": {"available": 90, "median": 8607.5, "total": 702276}}`
Raw responses are immutable evidence; no chain-of-thought was requested or stored.
