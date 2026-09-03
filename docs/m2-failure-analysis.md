# M2 Failure Analysis

This is a read-only forensic analysis of the persisted M2 run. No SQL was regenerated, no LLM was called, and the evaluator, prompt, retrieval, model, policy, and gold SQL were not changed.

## Evaluator sanity check

Replayed cases: full=48, retrieved=48. Persisted/recomputed verdict mismatches: 0.

The existing evaluator compares result column-name sets exactly. The audit separately flags cases where values and row shape match positionally but aliases differ; these are possible evaluator false negatives and are not silently corrected.

Likely alias-only false-negative candidates: 21. For example, m2-009 returns the same count value under different output aliases (total_customers versus customer_count).
Fixture-equivalence semantic warnings: 3. These are current evaluator positives whose SQL expressions differ in a way that may only coincide on this seeded dataset.

## Failure taxonomy

| Primary class | Full | Retrieved |
|---|---:|---:|
| GOLD_OR_EVAL_ISSUE | 12 | 9 |
| SCHEMA_CONTEXT_MISSING | 0 | 1 |
| WRONG_AGGREGATION | 1 | 1 |
| WRONG_COLUMN | 13 | 21 |
| WRONG_DATE_BOUNDARY | 1 | 1 |
| WRONG_FILTER | 0 | 1 |
| WRONG_GROUPING | 1 | 1 |
| WRONG_JOIN | 1 | 1 |
| WRONG_LIMIT | 3 | 2 |
| WRONG_ORDERING | 0 | 1 |
| WRONG_RATIO_DENOMINATOR | 1 | 1 |
| WRONG_RATIO_SCALING | 1 | 1 |
| WRONG_TABLE | 3 | 0 |
| WRONG_WINDOW_LOGIC | 3 | 1 |

## Full vs retrieved

| Classification | Count |
|---|---:|
| BOTH_CORRECT | 5 |
| BOTH_INCORRECT_DIFFERENT_REASON | 11 |
| BOTH_INCORRECT_SAME_REASON | 28 |
| FULL_ONLY_CORRECT | 3 |
| RETRIEVED_ONLY_CORRECT | 1 |

Full-only correct cases: m2-004, m2-021, m2-032. Retrieved-only correct cases: m2-034.
The retrieved context did not omit required gold tables in the full-only cases. m2-032 is a full-only label caused by the retrieved variant's alias-only false negative, not a demonstrated context-quality win. The retrieved-only case, m2-034, is also flagged as a possible fixture-equivalence false positive because its generated aggregation uses a different semantic expression.

## Category × failure type

| Category | Mode | Failure type | Count |
|---|---|---|---:|
| date_filtering | full | GOLD_OR_EVAL_ISSUE | 2 |
| date_filtering | full | WRONG_COLUMN | 1 |
| date_filtering | full | WRONG_DATE_BOUNDARY | 1 |
| date_filtering | retrieved | GOLD_OR_EVAL_ISSUE | 2 |
| date_filtering | retrieved | WRONG_COLUMN | 2 |
| date_filtering | retrieved | WRONG_DATE_BOUNDARY | 1 |
| group_by | full | GOLD_OR_EVAL_ISSUE | 2 |
| group_by | full | WRONG_JOIN | 1 |
| group_by | retrieved | GOLD_OR_EVAL_ISSUE | 1 |
| group_by | retrieved | WRONG_JOIN | 1 |
| joins | full | GOLD_OR_EVAL_ISSUE | 1 |
| joins | full | WRONG_AGGREGATION | 1 |
| joins | full | WRONG_COLUMN | 3 |
| joins | retrieved | GOLD_OR_EVAL_ISSUE | 1 |
| joins | retrieved | WRONG_AGGREGATION | 1 |
| joins | retrieved | WRONG_COLUMN | 4 |
| multi_table_joins | full | WRONG_COLUMN | 5 |
| multi_table_joins | full | WRONG_GROUPING | 1 |
| multi_table_joins | retrieved | SCHEMA_CONTEXT_MISSING | 1 |
| multi_table_joins | retrieved | WRONG_COLUMN | 4 |
| multi_table_joins | retrieved | WRONG_GROUPING | 1 |
| ratios | full | GOLD_OR_EVAL_ISSUE | 1 |
| ratios | full | WRONG_RATIO_DENOMINATOR | 1 |
| ratios | full | WRONG_RATIO_SCALING | 1 |
| ratios | retrieved | WRONG_COLUMN | 1 |
| ratios | retrieved | WRONG_RATIO_DENOMINATOR | 1 |
| ratios | retrieved | WRONG_RATIO_SCALING | 1 |
| simple_aggregation | full | GOLD_OR_EVAL_ISSUE | 6 |
| simple_aggregation | retrieved | GOLD_OR_EVAL_ISSUE | 5 |
| simple_aggregation | retrieved | WRONG_FILTER | 1 |
| simple_filters | full | WRONG_COLUMN | 3 |
| simple_filters | full | WRONG_TABLE | 3 |
| simple_filters | retrieved | WRONG_COLUMN | 7 |
| top_k | full | WRONG_COLUMN | 1 |
| top_k | full | WRONG_LIMIT | 3 |
| top_k | retrieved | WRONG_COLUMN | 2 |
| top_k | retrieved | WRONG_LIMIT | 2 |
| window_functions | full | WRONG_WINDOW_LOGIC | 3 |
| window_functions | retrieved | WRONG_COLUMN | 1 |
| window_functions | retrieved | WRONG_ORDERING | 1 |
| window_functions | retrieved | WRONG_WINDOW_LOGIC | 1 |

## Retrieved context adequacy

Among 42 retrieved-context failures: required tables were present in 41 and missing in 1; required columns were present in 37 and missing in 5 where determinable; required relationships were present in 41 and missing in 1.

Join/grain-related primary failures across both generated variants: 9.

## Join and grain findings

The nine primary join/grain failures comprise WRONG_TABLE=3, WRONG_JOIN=2, WRONG_AGGREGATION=2, and WRONG_GROUPING=2. The reviewed errors include unnecessary or incorrect join paths, line-item versus order-level aggregation, and grouping at the wrong grain. No separate JOIN_CARDINALITY_ERROR or WRONG_GRAIN label was needed; those effects were assigned to the primary structural cause.

## Date findings

The date failures are concentrated in boundary and column selection. Both modes fail m2-031 with a strict boundary where the gold query is inclusive; retrieved m2-030 also selects a discount-related expression instead of the requested order revenue. No reviewed failure required a current-date assumption, quarter interpretation, or DATE_TRUNC diagnosis.

## Top-k and ordering findings

WRONG_LIMIT appears five times across both modes, mainly as an omitted or misplaced top-k limit. The remaining top-k/window ordering cases include wrong output columns and an outer ordering/window-shape error; no clear wrong sort-direction case was identified.

## Ratio findings

Ratio failures include two WRONG_RATIO_SCALING cases and two WRONG_RATIO_DENOMINATOR cases. The denominator cases use an average of per-row ratios or a line-item population instead of the gold population ratio. One happens to equal the gold value on this fixture, which is why result execution alone cannot establish metric correctness. Explicit business metric contracts would likely help these cases; other arithmetic and SQL-shape failures remain ordinary generation problems.

## M3 relevance

HIGH=9, MEDIUM=5, LOW=68 across incorrect generated variants. HIGH is reserved for explicit metric/ratio or business-definition dependence; ordinary SQL selection, filtering, ordering, joins, and windows are LOW.

## Representative failed cases

The JSON audit contains complete generated/gold SQL and replayed result rows for these cases. The table below summarizes the manual/structural assessment.

| Mode | ID | Category | Current verdict | Primary assessment |
|---|---|---|---|---|
| full | m2-009 | simple_aggregation | False | GOLD_OR_EVAL_ISSUE |
| full | m2-017 | joins | False | WRONG_AGGREGATION |
| full | m2-024 | multi_table_joins | False | WRONG_COLUMN |
| full | m2-031 | date_filtering | False | WRONG_DATE_BOUNDARY |
| full | m2-039 | top_k | False | WRONG_LIMIT |
| full | m2-044 | ratios | False | WRONG_RATIO_DENOMINATOR |
| full | m2-046 | window_functions | False | WRONG_WINDOW_LOGIC |
| retrieved | m2-001 | simple_filters | False | WRONG_COLUMN |
| retrieved | m2-015 | simple_aggregation | False | WRONG_FILTER |
| retrieved | m2-019 | joins | False | WRONG_COLUMN |
| retrieved | m2-028 | multi_table_joins | False | WRONG_GROUPING |
| retrieved | m2-035 | group_by | False | WRONG_JOIN |
| retrieved | m2-043 | ratios | False | WRONG_RATIO_SCALING |
| retrieved | m2-048 | window_functions | False | WRONG_WINDOW_LOGIC |

## Conclusions

The main observed problem is not parsing, authorization, or execution. It is result semantics: over-selection of columns, unnecessary joins, incorrect aggregation/grain, omitted LIMIT/order/window logic, and ratio definitions. The current evaluator also has alias-sensitive false-negative candidates, so the 16.67%/12.50% baseline should not be interpreted as a perfect semantic score until that evaluator issue is resolved in a separately authorized change.

The evaluator sanity result is therefore mixed: replay is deterministic and agrees with persisted verdicts, but the current label metric is alias-sensitive and the finite fixture permits a small number of structurally different queries to pass. This is a diagnosis blocker for interpreting the headline score, not a reason to change the evaluator in this read-only task.

M3 remains relevant for the high-value metric/ratio subset, but it will not by itself solve the dominant ordinary SQL-generation failures. No M3 implementation was started.
